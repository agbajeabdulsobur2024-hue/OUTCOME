"""Fail-closed verification for NEAR AI Cloud signed chat responses.

Implements the protocol used by the official nearai-cloud-verifier chat verifier:
- hashes the exact request body and raw HTTP response body;
- verifies the ECDSA signature and recovered signing address;
- fetches a fresh nonce-bound model attestation;
- cryptographically verifies the Intel TDX quote and report-data binding.

GPU NRAS verdict is treated as supplemental provider evidence, not as a locally
verified JWT signature. No model output is trusted unless the ECDSA + TDX checks pass.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

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
) -> str:
    """Verify hashes, ECDSA signature and signer address; return signer."""
    try:
        signed_text = payload["text"]
        signature = payload["signature"]
        claimed_signer = payload["signing_address"]
    except (KeyError, TypeError) as exc:
        raise VerificationError("Signature endpoint omitted required fields.") from exc

    if not all(isinstance(x, str) and x for x in (signed_text, signature, claimed_signer)):
        raise VerificationError("Signature endpoint returned invalid fields.")

    parts = signed_text.split(":")
    if len(parts) == 3:
        signed_request_hash, signed_response_hash = parts[1], parts[2]
    elif len(parts) == 2:
        signed_request_hash, signed_response_hash = parts
    else:
        raise VerificationError("Signature payload has an unsupported format.")

    actual_request_hash = sha256_hex(request_body)
    actual_response_hash = sha256_hex(response_text)
    if not secrets.compare_digest(signed_request_hash.lower(), actual_request_hash):
        raise VerificationError("Signed request hash does not match the request sent.")
    if not secrets.compare_digest(signed_response_hash.lower(), actual_response_hash):
        raise VerificationError("Signed response hash does not match the raw response received.")

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(encode_defunct(text=signed_text), signature=signature)
    except Exception as exc:
        raise VerificationError("ECDSA response signature could not be recovered.") from exc

    if recovered.lower() != claimed_signer.lower():
        raise VerificationError("ECDSA signer does not match the attested signing address.")
    return claimed_signer


def _decode_tdx_report_data(verification_result: Any) -> tuple[str, str]:
    try:
        result_json = json.loads(verification_result.to_json())
        td10 = result_json["report"]["TD10"]
        report_data = str(td10["report_data"]).removeprefix("0x")
        mr_config = str(td10.get("mr_config_id", ""))
    except Exception as exc:
        raise VerificationError("Verified TDX quote did not expose TD10 report data.") from exc
    try:
        raw = bytes.fromhex(report_data)
    except ValueError as exc:
        raise VerificationError("TDX report data is not valid hexadecimal.") from exc
    if len(raw) != 64:
        raise VerificationError("TDX report data must be exactly 64 bytes.")
    return report_data.lower(), mr_config


def _verify_tdx_and_binding(attestation: dict[str, Any], nonce: str) -> dict[str, Any]:
    try:
        import asyncio
        import dcap_qvl
        quote_hex = attestation["intel_quote"]
        quote = bytes.fromhex(quote_hex)
        if not quote:
            raise ValueError("empty quote")
        result = asyncio.run(dcap_qvl.get_collateral_and_verify(quote))
    except Exception as exc:
        raise VerificationError(f"Intel TDX quote verification failed: {type(exc).__name__}.") from exc

    status_obj = getattr(result, "status", None)
    status = str(getattr(status_obj, "value", status_obj)).split(".")[-1]
    if status not in {"UpToDate", "OutOfDate"}:
        raise VerificationError(f"Intel TDX quote was not accepted (status: {status or 'unknown'}).")

    report_data_hex, mr_config = _decode_tdx_report_data(result)
    report_data = bytes.fromhex(report_data_hex)
    signer = str(attestation.get("signing_address", ""))
    if not signer:
        raise VerificationError("Attestation omitted its signing address.")
    try:
        signer_bytes = bytes.fromhex(signer.removeprefix("0x"))
    except ValueError as exc:
        raise VerificationError("Attestation signing address is malformed.") from exc
    if len(signer_bytes) > 32:
        raise VerificationError("Attestation signing address exceeds 32 bytes.")
    expected_signer = signer_bytes.ljust(32, b"\x00")
    expected_nonce = bytes.fromhex(nonce)
    if len(expected_nonce) != 32:
        raise VerificationError("Internal nonce must be 32 bytes.")
    if not secrets.compare_digest(report_data[:32], expected_signer):
        raise VerificationError("TDX quote does not bind the ECDSA signing address.")
    if not secrets.compare_digest(report_data[32:], expected_nonce):
        raise VerificationError("TDX quote does not bind the fresh verification nonce.")

    return {
        "tdx_quote_verified": True,
        "tdx_status": status,
        "signer_bound_to_quote": True,
        "fresh_nonce_bound_to_quote": True,
        "mr_config_id": mr_config or None,
    }


def _check_nvidia_payload(attestation: dict[str, Any], nonce: str, timeout: float) -> dict[str, Any]:
    """Supplemental NRAS verdict check; does not independently verify JWT signing."""
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
    """Verify a NEAR AI Cloud chat completion before its content is consumed."""
    if not chat_id:
        raise VerificationError("NEAR AI response omitted its chat ID.")
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}

    signature_response = requests.get(
        base + SIGNATURE_PATH.format(chat_id=chat_id),
        params={"model": model, "signing_algo": "ecdsa"},
        headers=headers,
        timeout=timeout,
    )
    signature_payload = _response_json(signature_response, "NEAR AI signature endpoint")
    signer = _verify_signature_payload(request_body, response_text, signature_payload)

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
    candidates = report.get("model_attestations")
    if isinstance(candidates, list):
        attestation = next(
            (item for item in candidates
             if isinstance(item, dict)
             and str(item.get("signing_address", "")).lower() == signer.lower()),
            None,
        )
    else:
        attestation = report
    if not isinstance(attestation, dict) or attestation.get("error"):
        raise VerificationError("No model attestation was returned for the response signer.")

    tdx = _verify_tdx_and_binding(attestation, nonce)
    gpu = _check_nvidia_payload(attestation, nonce, timeout)
    return {
        "verified": True,
        "verification_level": "ECDSA_RESPONSE_SIGNATURE_AND_INTEL_TDX_ATTESTATION",
        "signing_address": signer,
        "request_hash_verified": True,
        "response_hash_verified": True,
        **tdx,
        **gpu,
        "verified_at_nonce": nonce,
        "limitations": [
            "NVIDIA NRAS verdict JWT signature is not independently verified by OUTCOME.",
            "This verification establishes response integrity and signer-to-TEE binding; it does not prove the model's reasoning is correct.",
        ],
    }
