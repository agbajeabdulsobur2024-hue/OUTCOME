import json
import os
from typing import Any

import httpx

from app.near_verification import (
    preflight_near_ai_evidence,
    verification_is_complete,
    verify_near_ai_response,
)

NEAR_AI_URL = "https://cloud-api.near.ai/v1/chat/completions"
NEAR_AI_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"


class AIConfigurationError(Exception):
    pass


class AIProviderError(Exception):
    pass


def is_configured() -> bool:
    return bool(os.getenv("NEARAI_API_KEY") or os.getenv("NEAR_AI_KEY"))


def _attestation_label(signature_kind: str) -> str:
    if signature_kind == "provider_tee":
        return "PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED"
    if signature_kind == "gateway":
        return "GATEWAY_SIGNATURE_AND_GATEWAY_TDX_VERIFIED"
    raise AIProviderError("NEAR AI response verification did not classify the signer.")


def brief_is_displayable(result: Any) -> bool:
    """A brief may be stored or shown only after the cryptographic checks succeed."""
    if not isinstance(result, dict):
        return False
    if result.get("provider") != "NEAR AI Cloud" or result.get("model") != NEAR_AI_MODEL:
        return False
    verification = result.get("verification")
    if not verification_is_complete(verification):
        return False
    return result.get("attestation_status") == _attestation_label(verification["signature_kind"])


def public_ai_brief_entry(
    entry_id: str,
    provider: str,
    model: str,
    created_at: str,
    stored: Any,
) -> dict[str, Any]:
    """Return a history entry, withholding model text when verification is incomplete."""
    if brief_is_displayable(stored):
        return {
            "id": entry_id,
            "provider": provider,
            "model": model,
            "created_at": created_at,
            "displayable": True,
            "brief": stored,
        }
    return {
        "id": entry_id,
        "provider": provider,
        "model": model,
        "created_at": created_at,
        "displayable": False,
        "brief": None,
        "withheld_reason": (
            "AI brief withheld because cryptographic verification is missing or incomplete."
        ),
    }


EXTRACTION_SYSTEM = (
    "You extract financial accountability records for OUTCOME. "
    "Return only JSON with keys commitment, evidence, and ambiguities. "
    "commitment uses only these keys: counterparty, commitment_kind, description, quantity, unit, "
    "amount, currency, due_date, payment_condition. "
    "evidence is an array of objects with kind, value, note, and source. "
    "kind is one of quantity_received, obligation_fulfilled, invoice_amount, amount_claimed, note. "
    "Use null for every value the text does not explicitly support. "
    "Never invent a party, description, quantity, unit, amount, currency, date, payment condition, "
    "or evidence value. Never copy a value from an existing record unless the text states it. "
    "Do not approve, submit, or mark a payment. Do not set a review status. "
    "due_date must be YYYY-MM-DD only when the text states that date. "
    "currency must be an ISO code only when the text names that currency. "
    "If a number could be either the agreed amount or an invoice, leave both null and say so in ambiguities. "
    "ambiguities is an array of short strings and is empty when nothing is missing or ambiguous. "
    "Do not guess which existing id a description belongs to."
)


def _complete_json(*, system: str, user_content: str, max_tokens: int, temperature: float = 0.2) -> tuple[Any, dict[str, Any]]:
    """Call NEAR AI Cloud and return parsed JSON only after the same checks as a brief."""
    api_key = os.getenv("NEARAI_API_KEY") or os.getenv("NEAR_AI_KEY")
    if not api_key:
        raise AIConfigurationError("Set NEARAI_API_KEY to enable optional NEAR AI Cloud briefs.")

    request_body = {
        "model": NEAR_AI_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        # Qwen3.6 reasons by default. Reasoning tokens count toward max_tokens and
        # left the brief content null in the 11 Nov 2026 live run. The caller needs
        # the final JSON. This does not skip signature or attestation checks.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request_body_text = json.dumps(request_body, separators=(",", ":"), ensure_ascii=False)
    base_url = NEAR_AI_URL.rsplit("/v1/", 1)[0]
    try:
        # Quotes are verified before the completion. The signature is checked
        # against that preflight evidence, not against a quote fetched afterwards.
        try:
            preflight = preflight_near_ai_evidence(
                base_url=base_url,
                api_key=api_key,
                model=NEAR_AI_MODEL,
                timeout=30.0,
            )
        except Exception as exc:
            raise AIProviderError(
                f"NEAR AI preflight verification failed closed ({type(exc).__name__})."
            ) from exc
        response = httpx.post(
            NEAR_AI_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "x-no-aliasing": "true",
            },
            content=request_body_text,
            timeout=30.0,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise AIProviderError(f"NEAR AI Cloud returned HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError as exc:
            raise AIProviderError("NEAR AI Cloud returned invalid JSON.") from exc
        chat_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(chat_id, str) or not chat_id:
            raise AIProviderError("NEAR AI Cloud response omitted its chat ID.")
        # Fail closed: do not parse the model JSON until the exact response
        # bytes match a signer already verified in preflight.
        try:
            verification = verify_near_ai_response(
                base_url=base_url,
                api_key=api_key,
                chat_id=chat_id,
                model=NEAR_AI_MODEL,
                request_body=request_body_text,
                response_body=response.content,
                preflight=preflight,
                timeout=30.0,
            )
        except Exception as exc:
            raise AIProviderError(
                f"NEAR AI response verification failed closed ({type(exc).__name__})."
            ) from exc
        if not verification_is_complete(verification):
            raise AIProviderError("NEAR AI response did not pass cryptographic verification.")
        if body.get("model") != NEAR_AI_MODEL:
            raise AIProviderError("NEAR AI Cloud response model does not match the configured model.")
        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("NEAR AI Cloud returned an incomplete completion.") from exc
        content = message.get("content") if isinstance(message, dict) else None
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError(
                "NEAR AI Cloud returned no final content "
                f"(finish_reason={finish_reason!r})."
            )
        if content.startswith(chr(96) * 3):
            content = content.split(chr(10), 1)[-1].rsplit(chr(96) * 3, 1)[0].strip()
        parsed = json.loads(content)
    except AIProviderError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise AIProviderError(f"NEAR AI Cloud brief could not be generated: {exc}") from exc
    return parsed, verification


def generate_operational_brief(record: dict[str, Any]) -> dict[str, Any]:
    commitment = record["commitment"]
    evidence = [
        {
            "id": item["id"],
            "kind": item["kind"],
            "value": item["value"],
            "source": item["source"],
            "created_at": item["created_at"],
            "review_status": item["review_status"],
        }
        for item in record["evidence"][-10:]
    ]
    context = {
        "commitment": {
            key: commitment[key] for key in (
                "id", "supplier", "description", "quantity", "unit",
                "amount", "currency", "due_date", "payment_condition",
            )
        },
        "assessment": {
            "status": record["assessment"]["status"],
            "summary": record["assessment"]["summary"],
            "issues": record["assessment"]["issues"],
            "next_actions": record["assessment"]["next_actions"],
            "human_review_required": record["assessment"]["human_review_required"],
        },
        "evidence": evidence,
        "payment_policy": record["payment_policy"],
        "actions": [
            {
                "action_type": item["action_type"],
                "description": item["description"],
                "status": item["status"],
            }
            for item in record.get("actions", [])
        ],
    }
    system = (
        "You are OUTCOME's financial accountability analyst. Explain the supplied commitment "
        "and evidence for a small-business owner. Treat the deterministic assessment as authoritative. "
        "Do not change its status, authorize payment, claim a payment is safe, or suggest bypassing policy. "
        "Distinguish verified from unreviewed evidence. Never infer fraud, dishonesty or intent. "
        "State uncertainty plainly. Return only valid JSON with keys: summary (string), "
        "why_it_matters (string), recommended_actions (array of strings), "
        "uncertainties (array of strings), questions_for_owner (array of strings)."
    )
    brief, verification = _complete_json(
        system=system,
        user_content=json.dumps(context, separators=(",", ":"), default=str),
        max_tokens=700,
    )
    required = ("summary", "why_it_matters", "recommended_actions", "uncertainties", "questions_for_owner")
    if not isinstance(brief, dict) or any(key not in brief for key in required):
        raise AIProviderError("NEAR AI Cloud returned an incomplete operational brief.")
    if not isinstance(brief["summary"], str) or not isinstance(brief["why_it_matters"], str):
        raise AIProviderError("NEAR AI Cloud returned invalid summary fields.")
    for key in ("recommended_actions", "uncertainties", "questions_for_owner"):
        if not isinstance(brief[key], list) or any(not isinstance(item, str) for item in brief[key]):
            raise AIProviderError(f"NEAR AI Cloud returned an invalid {key} field.")

    result = {
        "provider": "NEAR AI Cloud",
        "model": NEAR_AI_MODEL,
        "privacy_tier": "TEE model",
        "attestation_status": _attestation_label(verification.get("signature_kind")),
        "verification": verification,
        "brief": brief,
        "disclaimer": (
            "Advisory explanation only. Deterministic checks and owner approval remain authoritative. "
            "OUTCOME verified the exact request and response bytes, the EIP-191 ECDSA signer, and a preflight "
            "UpToDate Intel TDX quote bound to that signer, including a clear debug bit, MRCONFIGID and RTMR3. "
            "A provider-TEE signature also required an NVIDIA NRAS verdict of boolean true and a matching eat_nonce. "
            "A gateway signature attests the gateway quote, not the model quote. "
            "HTTPS is not attestation. The NRAS JWT signature, Sigstore provenance, and the live TLS peer "
            "certificate were not verified. This is not proof that the model's reasoning is correct."
        ),
    }
    if not brief_is_displayable(result):
        raise AIProviderError("NEAR AI response did not pass cryptographic verification.")
    return result


def extract_business_event(text: str, catalogue: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract commitment and evidence JSON. Unverified output is not returned."""
    parsed, verification = _complete_json(
        system=EXTRACTION_SYSTEM,
        user_content=json.dumps({"text": text, "existing_records": catalogue}, separators=(",", ":"), default=str),
        max_tokens=900,
        temperature=0,
    )
    if not isinstance(parsed, dict):
        raise AIProviderError("NEAR AI Cloud returned an invalid extraction.")
    if not verification_is_complete(verification):
        raise AIProviderError("NEAR AI response did not pass cryptographic verification.")
    return {
        "provider": "NEAR AI Cloud",
        "model": NEAR_AI_MODEL,
        "attestation_status": _attestation_label(verification.get("signature_kind")),
        "verification": verification,
        "extraction": parsed,
    }
