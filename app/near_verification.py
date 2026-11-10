"""Fail-closed verification for NEAR AI Cloud signed chat responses.

Follows the official nearai-cloud-verifier chat flow:
- hash the exact request body and raw HTTP response body;
- verify the ECDSA signature and recover its signer;
- distinguish provider-TEE signatures from gateway signatures;
- fetch a fresh nonce-bound attestation for that signer;
- verify the Intel TDX quote and its report-data binding.

This does not independently verify NVIDIA's JWT signature, Sigstore provenance,
or the model/container measurement chain. Those limitations are returned
explicitly; a successful result is not a claim that model reasoning is correct.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import quote

import requests

SIGNATURE_PATH = "/v1/signature/{chat_id}"
ATTESTATION_PATH = "/v1/attestation/report"


class VerificationError(Exception):
    """Raised when a NEAR AI Cloud response cannot be independently verified."""


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _response_json(response: requests.Response, label: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        raise VerificationError(f"{label} returned HTTP {response.status_code}.")
    try:
        value = response.json()
    except ValueError as exc:
        raise VerificationError(f"{label} returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} returned an unexpected response.")
    return value


def _verify_signature_payload(
    request_body: str,
    response_text: str,
    payload: dict[str, Any],
    model: str,
) -> dict[str, str]:
    """Verify exact hashes and ECDSA signer; return signer and signature kind."""
    try:
        signed_text = payload["text"]
        signature = payload["signature"]
        claimed_signer = payload["signing_address"]
    except (KeyError, TypeError) as exc:
        raise VerificationError("Signature endpoint omitted required fields.") from exc

    if not all(isinstance(x, str) and x for x in (signed_text, signature, claimed_signer)):
        raise VerificationError("Signature endpoint returned invalid fields.")

    parts = signed_text.split(":")
    declared_kind = str(payload.get("signature_kind", "")).lower()
    if len(parts) == 3:
        # Provider TEE signs model_id:request_hash:response_hash.
        signature_kind = "provider_tee"
        signed_model, signed_request_hash, signed_response_hash = parts
        if signed_model != model:
            raise VerificationError("Signed model identifier does not match the requested model.")
    elif len(parts) == 2:
        # Gateway TEE signs request_hash:response_hash for bytes it rewrites.
        signature_kind = "gateway"
        signed_request_hash, signed_response_hash = parts
    else:
        raise VerificationError("Signature payload has an unsupported signed-text format.")

    if declared_kind and declared_kind != signature_kind:
        raise VerificationError("Signature kind does not match the signed-text format.")

    if not secrets.compare_digest(signed_request_hash.lower(), sha256_hex(request_body)):
        raise VerificationError("Signed request hash does not match the request sent.")
    if not secrets.compare_digest(signed_response_hash.lower(), sha256_hex(response_text)):
        raise VerificationError("Signed response hash does not match the raw response received.")

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(encode_defunct(text=signed_text), signature=signature)
    except Exception as exc:
        raise VerificationError("ECDSA response signature could not be recovered.") from exc

    if recovered.lower() != claimed_signer.lower():
        raise VerificationError("ECDSA signer does not match the claimed signing address.")
    return {"signing_address": claimed_signer, "signature_kind": signature_kind}


def _extract_report_data(verification_result: Any) -> tuple[bytes, str]:
    try:
        result_json = json.loads(verification_result.to_json())
        # Current dcap-qvl output shape used by NEAR's official verifier.
        quote_body = result_json.get("quote", {}).get("body", {})
        report_data = quote_body.get("reportdata")
        mr_config = quote_body.get("mrconfig", "")
        # Compatibility with TD10-shaped output from some dcap-qvl versions.
        if report_data is None:
            td10 = result_json["report"]["TD10"]
            report_data = td10.get("report_data")
            mr_config = td10.get("mr_config_id", mr_config)
        raw = bytes.fromhex(str(report_data).removeprefix("0x"))
    except Exception as exc:
        raise VerificationError("Verified TDX quote did not expose report data.") from exc
    if len(raw) != 64:
        raise VerificationError("TDX report data must be exactly 64 bytes.")
    return raw, str(mr_config or "")


def _verify_tdx_and_binding(attestation: dict[str, Any], nonce: str) -> dict[str, Any]:
    try:
        import asyncio
        import dcap_qvl
        quote = bytes.fromhex(attestation["intel_quote"])
        if not quote:
            raise ValueError("empty quote")
        result = asyncio.run(dcap_qvl.get_collateral_and_verify(quote))
    except Exception as exc:
        raise VerificationError(f"Intel TDX quote verification failed: {type(exc).__name__}.") from exc

    status_obj = getattr(result, "status", None)
    status = str(getattr(status_obj, "value", status_obj)).split(".")[-1]
    if status not in {"UpToDate", "OutOfDate"}:
        raise VerificationError(f"Intel TDX quote was not accepted (status: {status or 'unknown'}).")

    report_data, mr_config = _extract_report_data(result)
    signer = str(attestation.get("signing_address", ""))
    if not signer:
        raise VerificationError("Attestation omitted its signing address.")
    try:
        signer_bytes = bytes.fromhex(signer.removeprefix("0x"))
        nonce_bytes = bytes.fromhex(nonce)
    except ValueError as exc:
        raise VerificationError("Signer or nonce is malformed hexadecimal.") from exc
    if len(signer_bytes) > 32 or len(nonce_bytes) != 32:
        raise VerificationError("Signer or nonce has an invalid length.")

    expected_signer = signer_bytes.ljust(32, b"\x00")
    if not secrets.compare_digest(report_data[:32], expected_signer):
        raise VerificationError("TDX quote does not bind the ECDSA signing address.")
    if not secrets.compare_digest(report_data[32:], nonce_bytes):
        raise VerificationError("TDX quote does not bind the fresh verification nonce.")

    return {
        "tdx_quote_verified": True,
        "tdx_status": status,
        "tdx_freshness_warning": status == "OutOfDate",
        "signer_bound_to_quote": True,
        "fresh_nonce_bound_to_quote": True,
        "mr_config_id": mr_config or None,
    }


def _check_nvidia_payload(attestation: dict[str, Any], nonce: str, timeout: float) -> dict[str, Any]:
    """Check the NRAS verdict and nonce; JWT signature is not verified locally."""
    raw_payload = attestation.get("nvidia_payload")
    if not raw_payload:
        return {"gpu_attestation_checked": False, "gpu_note": "No NVIDIA payload supplied."}
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        if not isinstance(payload, dict) or str(payload.get("nonce", "")).lower() != nonce.lower():
            raise VerificationError("NVIDIA payload nonce does not match the fresh nonce.")
        response = requests.post(
            "https://nras.attestation.nvidia.com/v3/attest/gpu",
            json=payload,
            timeout=timeout,
        )
        if not 200 <= response.status_code < 300:
            raise VerificationError(f"NVIDIA NRAS returned HTTP {response.status_code}.")
        body = response.json()
        jwt_token = body[0][1]
        encoded = jwt_token.split(".")[1]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        verdict = json.loads(decoded)["x-nvidia-overall-att-result"]
        if str(verdict).upper() != "PASS":
            raise VerificationError("NVIDIA NRAS did not return PASS.")
        return {
            "gpu_attestation_checked": True,
            "gpu_nras_verdict": str(verdict),
            "gpu_nonce_matches": True,
            "gpu_jwt_signature_verified_locally": False,
        }
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError(f"NVIDIA supplemental attestation check failed: {type(exc).__name__}.") from exc


def verify_near_ai_response(
    *,
    base_url: str,
    api_key: str,
    chat_id: str,
    model: str,
    request_body: str,
    response_text: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Verify a NEAR AI Cloud chat completion before consuming its content."""
    if not chat_id:
        raise VerificationError("NEAR AI response omitted its chat ID.")
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    signature_url = base + SIGNATURE_PATH.format(chat_id=quote(chat_id, safe=""))
    signature_response = requests.get(
        signature_url,
        params={"model": model, "signing_algo": "ecdsa"},
        headers=headers,
        timeout=timeout,
    )
    signature_payload = _response_json(signature_response, "NEAR AI signature endpoint")
    signature = _verify_signature_payload(request_body, response_text, signature_payload, model)
    signer = signature["signing_address"]
    signature_kind = signature["signature_kind"]

    nonce = secrets.token_hex(32)
    attestation_response = requests.get(
        base + ATTESTATION_PATH,
        params={
            "model": model,
            "nonce": nonce,
            "signing_algo": "ecdsa",
            "signing_address": signer,
        },
        headers=headers,
        timeout=timeout,
    )
    report = _response_json(attestation_response, "NEAR AI attestation endpoint")

    if signature_kind == "provider_tee":
        candidates = report.get("model_attestations", [])
        attestation = next(
            (item for item in candidates
             if isinstance(item, dict)
             and str(item.get("signing_address", "")).lower() == signer.lower()),
            None,
        ) if isinstance(candidates, list) else None
    else:
        candidate = report.get("gateway_attestation")
        attestation = candidate if (
            isinstance(candidate, dict)
            and str(candidate.get("signing_address", "")).lower() == signer.lower()
        ) else None

    if not isinstance(attestation, dict) or attestation.get("error"):
        raise VerificationError("No matching TEE attestation was returned for the response signer.")
    if attestation.get("signing_algo", "ecdsa").lower() != "ecdsa":
        raise VerificationError("Attestation signing algorithm is not ECDSA.")

    tdx = _verify_tdx_and_binding(attestation, nonce)
    gpu = _check_nvidia_payload(attestation, nonce, timeout) if signature_kind == "provider_tee" else {
        "gpu_attestation_checked": False,
        "gpu_note": "Gateway signature path; model GPU attestation is not asserted here.",
    }
    return {
        "verified": True,
        "verification_level": "ECDSA_RESPONSE_SIGNATURE_AND_INTEL_TDX_ATTESTATION",
        "signature_kind": signature_kind,
        "signing_address": signer,
        "request_hash_verified": True,
        "response_hash_verified": True,
        **tdx,
        **gpu,
        "verified_at_nonce": nonce,
        "limitations": [
            "NVIDIA NRAS verdict JWT signature is not independently verified by OUTCOME.",
            "Model/container measurements, compose-manifest binding, Sigstore provenance and gateway TLS binding are not independently verified by OUTCOME.",
            "This establishes response integrity and signer-to-TEE binding; it does not prove the model's reasoning is correct.",
        ],
    }
