import json
import os
from typing import Any

import httpx

NEAR_AI_URL = "https://cloud-api.near.ai/v1/chat/completions"
NEAR_AI_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"


class AIConfigurationError(Exception):
    pass


class AIProviderError(Exception):
    pass


def is_configured() -> bool:
    return bool(os.getenv("NEARAI_API_KEY") or os.getenv("NEAR_AI_KEY"))


def generate_operational_brief(record: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("NEARAI_API_KEY") or os.getenv("NEAR_AI_KEY")
    if not api_key:
        raise AIConfigurationError("Set NEARAI_API_KEY to enable optional NEAR AI Cloud briefs.")

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
        "You are OUTCOME's business operations analyst. Explain the supplied supplier commitment "
        "and evidence for a small-business owner. Treat the deterministic assessment as authoritative. "
        "Do not change its status, authorize payment, claim a payment is safe, or suggest bypassing policy. "
        "Distinguish verified from unreviewed evidence. Never infer fraud, dishonesty or intent. "
        "State uncertainty plainly. Return only valid JSON with keys: summary (string), "
        "why_it_matters (string), recommended_actions (array of strings), "
        "uncertainties (array of strings), questions_for_owner (array of strings)."
    )
    try:
        response = httpx.post(
            NEAR_AI_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": NEAR_AI_MODEL,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(context, separators=(",", ":"), default=str)},
                ],
            },
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if content.startswith("```"):
            content = content.split("
", 1)[-1].rsplit("```", 1)[0].strip()
        brief = json.loads(content)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(f"NEAR AI Cloud brief could not be generated: {exc}") from exc

    required = ("summary", "why_it_matters", "recommended_actions", "uncertainties", "questions_for_owner")
    if any(key not in brief for key in required):
        raise AIProviderError("NEAR AI Cloud returned an incomplete operational brief.")
    if not isinstance(brief["summary"], str) or not isinstance(brief["why_it_matters"], str):
        raise AIProviderError("NEAR AI Cloud returned invalid summary fields.")
    for key in ("recommended_actions", "uncertainties", "questions_for_owner"):
        if not isinstance(brief[key], list) or any(not isinstance(item, str) for item in brief[key]):
            raise AIProviderError(f"NEAR AI Cloud returned an invalid {key} field.")

    return {
        "provider": "NEAR AI Cloud",
        "model": NEAR_AI_MODEL,
        "privacy_tier": "TEE model",
        "attestation_status": "NOT_INDEPENDENTLY_VERIFIED_BY_OUTCOME",
        "brief": brief,
        "disclaimer": "Advisory explanation only. Deterministic checks and owner approval remain authoritative. OUTCOME has not independently verified this model response's TEE attestation.",
    }
