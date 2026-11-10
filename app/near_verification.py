"""Fail-closed verification for NEAR AI Cloud signed chat responses.

Follows the current NEAR AI Cloud verification docs:
https://docs.near.ai/cloud/verification/reference/quote-nonce-signer
https://docs.near.ai/cloud/verification/cloud-api/response-signatures

- Preflight a client nonce and verify the gateway quote and every model quote
  before the completion is treated as evidence.
- Require an explicit signature_kind. Do not infer it from colon count.
- Hash the exact request bytes and raw response bytes.
- Recover the EIP-191 ECDSA signer and require exactly one matching preflight
  report for that signer and algorithm.
- Verify the Intel TDX quote (UpToDate only), reject debug TDs, bind the
  signing address and nonce in report_data, and pin MRCONFIGID and RTMR3
  to app_compose and the event log.
- For a provider-TEE signature, require an NVIDIA NRAS verdict of boolean true
  whose eat_nonce matches the preflight nonce.

This does not verify the NRAS JWT signature locally, Sigstore provenance, or
the live gateway TLS peer certificate. Those limits are returned explicitly.
A successful result is not a claim that the model's reasoning is correct.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import struct
from typing import Any
from urllib.parse import quote

import requests

SIGNATURE_PATH = "/v1/signature/{chat_id}"
ATTESTATION_PATH = "/v1/attestation/report"
NRAS_GPU_URL = "https://nras.attestation.nvidia.com/v3/attest/gpu"
ACCEPTED_TCB_STATUS = "UpToDate"
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class VerificationError(Exception):
    """Raised when a NEAR AI Cloud response cannot be independently verified."""


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


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


def _parse_signed_text(signed_text: str, signature_kind: str, model: str) -> tuple[str, str]:
    """Return request and response hashes. Model ids may contain colons."""
    parts = signed_text.split(":")
    if signature_kind == "provider_tee":
        if len(parts) < 3 or not _SHA256_HEX.fullmatch(parts[-2]) or not _SHA256_HEX.fullmatch(parts[-1]):
            raise VerificationError("Provider signature text must end with request and response hashes.")
        signed_model = ":".join(parts[:-2])
        if signed_model != model:
            raise VerificationError("Signed model identifier does not match the requested model.")
        return parts[-2], parts[-1]
    if signature_kind == "gateway":
        if len(parts) != 2 or not _SHA256_HEX.fullmatch(parts[0]) or not _SHA256_HEX.fullmatch(parts[1]):
            raise VerificationError("Gateway signature text must be request_hash:response_hash.")
        return parts[0], parts[1]
    raise VerificationError("Signature kind is missing or unfamiliar.")


def _verify_signature_payload(
    request_body: str | bytes,
    response_body: str | bytes,
    payload: dict[str, Any],
    model: str,
) -> dict[str, str]:
    """Verify exact hashes and ECDSA signer; return signer, kind, and algorithm."""
    try:
        signed_text = payload["text"]
        signature = payload["signature"]
        claimed_signer = payload["signing_address"]
    except (KeyError, TypeError) as exc:
        raise VerificationError("Signature endpoint omitted required fields.") from exc

    if not all(isinstance(item, str) and item for item in (signed_text, signature, claimed_signer)):
        raise VerificationError("Signature endpoint returned invalid fields.")

    declared_kind = payload.get("signature_kind")
    if declared_kind not in ("provider_tee", "gateway"):
        raise VerificationError("signature_kind is missing or unfamiliar.")
    declared_algo = str(payload.get("signing_algo", "ecdsa")).lower()
    if declared_algo != "ecdsa":
        raise VerificationError("Signature algorithm is not ECDSA.")

    signed_request_hash, signed_response_hash = _parse_signed_text(signed_text, declared_kind, model)
    if not secrets.compare_digest(signed_request_hash.lower(), sha256_hex(request_body)):
        raise VerificationError("Signed request hash does not match the request sent.")
    if not secrets.compare_digest(signed_response_hash.lower(), sha256_hex(response_body)):
        raise VerificationError("Signed response hash does not match the raw response received.")

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(encode_defunct(text=signed_text), signature=signature)
    except Exception as exc:
        raise VerificationError("ECDSA response signature could not be recovered.") from exc

    if recovered.lower() != claimed_signer.lower():
        raise VerificationError("ECDSA signer does not match the claimed signing address.")
    return {
        "signing_address": claimed_signer,
        "signature_kind": declared_kind,
        "signing_algo": declared_algo,
    }


def _td10_quote(verification_result: Any) -> dict[str, Any]:
    try:
        result_json = json.loads(verification_result.to_json())
        td10 = result_json["report"]["TD10"]
    except Exception as exc:
        raise VerificationError("Verified TDX quote did not expose a TD10 report.") from exc
    if not isinstance(td10, dict):
        raise VerificationError("Verified TDX quote did not expose a TD10 report.")
    return td10


def _hex_bytes(value: Any, label: str, expected_len: int | None = None) -> bytes:
    try:
        raw = bytes.fromhex(str(value).removeprefix("0x"))
    except ValueError as exc:
        raise VerificationError(f"TDX {label} is not hexadecimal.") from exc
    if expected_len is not None and len(raw) != expected_len:
        raise VerificationError(f"TDX {label} must be exactly {expected_len} bytes.")
    return raw


def _app_compose(attestation: dict[str, Any]) -> str:
    info = attestation.get("info") or {}
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except ValueError as exc:
            raise VerificationError("Attestation info is not JSON.") from exc
    if not isinstance(info, dict):
        raise VerificationError("Attestation omitted tcb_info.")
    tcb = info.get("tcb_info")
    if isinstance(tcb, str):
        try:
            tcb = json.loads(tcb)
        except ValueError as exc:
            raise VerificationError("Attestation tcb_info is not JSON.") from exc
    if not isinstance(tcb, dict) or not isinstance(tcb.get("app_compose"), str) or not tcb["app_compose"]:
        raise VerificationError("Attestation omitted app_compose.")
    return tcb["app_compose"]


def _event_log(attestation: dict[str, Any]) -> list[Any]:
    raw = attestation.get("event_log")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise VerificationError("Attestation event log is not JSON.") from exc
    if not isinstance(raw, list):
        raise VerificationError("Attestation omitted its event log.")
    return raw


def replay_rtmr3(event_log: Any) -> bytes:
    """Replay IMR 3 the way the NEAR quote-nonce doc specifies."""
    if isinstance(event_log, str):
        event_log = json.loads(event_log)
    if not isinstance(event_log, list):
        raise VerificationError("Attestation event log is not a list.")
    rtmr3 = bytes(48)
    for event in event_log:
        if not isinstance(event, dict) or event.get("imr") != 3:
            continue
        event_name = str(event.get("event", ""))
        try:
            event_type = int(event["event_type"])
            payload = bytes.fromhex(event.get("event_payload") or "")
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError("RTMR3 event log entry is malformed.") from exc
        digest = hashlib.sha384(
            struct.pack("<I", event_type) + b":" + event_name.encode("utf-8") + b":" + payload
        ).digest()
        claimed = event.get("digest") or ""
        if claimed and bytes.fromhex(str(claimed)) != digest:
            raise VerificationError(f"RTMR3 digest mismatch for event {event_name or event_type}.")
        rtmr3 = hashlib.sha384(rtmr3 + digest).digest()
    return rtmr3


def _expected_report_prefix(attestation: dict[str, Any]) -> bytes:
    algo = str(attestation.get("signing_algo", "")).lower()
    if algo != "ecdsa":
        raise VerificationError("Attestation signing algorithm is not ECDSA.")
    try:
        identity = bytes.fromhex(str(attestation.get("signing_address", "")).removeprefix("0x"))
    except ValueError as exc:
        raise VerificationError("Signer is malformed hexadecimal.") from exc
    if len(identity) != 20:
        raise VerificationError("ECDSA signing address must be 20 bytes.")
    fingerprint = attestation.get("tls_cert_fingerprint")
    if fingerprint:
        try:
            fingerprint_bytes = bytes.fromhex(str(fingerprint).removeprefix("0x"))
        except ValueError as exc:
            raise VerificationError("TLS certificate fingerprint is malformed hexadecimal.") from exc
        # Quote binds the fingerprint the report claims. The live peer certificate
        # is not observed here; that gap stays in the returned limitations.
        return hashlib.sha256(identity + fingerprint_bytes).digest()
    return identity.ljust(32, b"\x00")


def _verify_tdx_and_binding(attestation: dict[str, Any], nonce: str) -> dict[str, Any]:
    try:
        import asyncio
        import dcap_qvl
        quote_bytes = bytes.fromhex(attestation["intel_quote"])
        if not quote_bytes:
            raise ValueError("empty quote")
        result = asyncio.run(dcap_qvl.get_collateral_and_verify(quote_bytes))
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError(f"Intel TDX quote verification failed: {type(exc).__name__}.") from exc

    status_obj = getattr(result, "status", None)
    status = str(getattr(status_obj, "value", status_obj)).split(".")[-1]
    if status != ACCEPTED_TCB_STATUS:
        raise VerificationError(f"Intel TDX quote was not UpToDate (status: {status or 'unknown'}).")

    td10 = _td10_quote(result)
    report_data = _hex_bytes(td10.get("report_data"), "report data", 64)
    td_attributes = _hex_bytes(td10.get("td_attributes"), "td_attributes", 8)
    if td_attributes[0] & 1:
        raise VerificationError("TDX quote is a debug quote and is not accepted.")
    mr_config = _hex_bytes(td10.get("mr_config_id"), "mr_config_id", 48)
    rt_mr3 = _hex_bytes(td10.get("rt_mr3"), "rt_mr3", 48)

    echoed = str(attestation.get("request_nonce", ""))
    if echoed.lower() != nonce.lower():
        raise VerificationError("Attestation echoed nonce does not match the preflight nonce.")
    try:
        nonce_bytes = bytes.fromhex(nonce)
    except ValueError as exc:
        raise VerificationError("Nonce is malformed hexadecimal.") from exc
    if len(nonce_bytes) != 32:
        raise VerificationError("Nonce must be 32 bytes.")

    expected = _expected_report_prefix(attestation) + nonce_bytes
    if not secrets.compare_digest(report_data, expected):
        raise VerificationError("TDX report data does not bind the signing address and fresh nonce.")

    app_compose = _app_compose(attestation)
    expected_config = b"\x01" + hashlib.sha256(app_compose.encode("utf-8")).digest() + bytes(15)
    if not secrets.compare_digest(mr_config, expected_config):
        raise VerificationError("TDX MRCONFIGID does not match app_compose.")
    if not secrets.compare_digest(rt_mr3, replay_rtmr3(_event_log(attestation))):
        raise VerificationError("TDX RTMR3 does not match the event log.")

    return {
        "tdx_quote_verified": True,
        "tdx_status": status,
        "tdx_debug_rejected": True,
        "signer_bound_to_quote": True,
        "fresh_nonce_bound_to_quote": True,
        "mr_config_id_matches_app_compose": True,
        "rtmr3_matches_event_log": True,
        "mr_config_id": mr_config.hex(),
        "tls_peer_certificate_compared": False,
    }


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        encoded = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        claims = json.loads(decoded)
    except (IndexError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationError("NVIDIA NRAS JWT payload could not be decoded.") from exc
    if not isinstance(claims, dict):
        raise VerificationError("NVIDIA NRAS JWT payload was not an object.")
    return claims


def _check_nvidia_payload(attestation: dict[str, Any], nonce: str, timeout: float, *, required: bool) -> dict[str, Any]:
    """Require a TLS NRAS verdict of boolean true and a matching eat_nonce.

    The JWT signature is not checked locally. The NEAR minimal flow trusts the
    TLS response and still requires the verdict and nonce inside that payload.
    """
    raw_payload = attestation.get("nvidia_payload")
    if not raw_payload:
        if required:
            raise VerificationError("Provider TEE attestation omitted NVIDIA GPU evidence.")
        return {"gpu_attestation_checked": False, "gpu_note": "No NVIDIA payload supplied."}
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        if not isinstance(payload, dict) or str(payload.get("nonce", "")).lower() != nonce.lower():
            raise VerificationError("NVIDIA payload nonce does not match the fresh nonce.")
        response = requests.post(NRAS_GPU_URL, json=payload, timeout=timeout)
        if not 200 <= response.status_code < 300:
            raise VerificationError(f"NVIDIA NRAS returned HTTP {response.status_code}.")
        body = response.json()
        claims = _jwt_payload(body[0][1])
        verdict = claims.get("x-nvidia-overall-att-result")
        eat_nonce = str(claims.get("eat_nonce", ""))
        if verdict is not True:
            raise VerificationError("NVIDIA NRAS overall result was not true.")
        if eat_nonce.lower() != nonce.lower():
            raise VerificationError("NVIDIA EAT nonce does not match the fresh nonce.")
        return {
            "gpu_attestation_checked": True,
            "gpu_nras_verdict": True,
            "gpu_nonce_matches": True,
            "gpu_eat_nonce_matches": True,
            "gpu_jwt_signature_verified_locally": False,
        }
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError(f"NVIDIA attestation check failed: {type(exc).__name__}.") from exc


def _verify_one_attestation(
    attestation: dict[str, Any],
    nonce: str,
    timeout: float,
    *,
    require_gpu: bool,
) -> dict[str, Any]:
    signer = str(attestation.get("signing_address", ""))
    algo = str(attestation.get("signing_algo", "")).lower()
    if not signer or algo != "ecdsa":
        raise VerificationError("Attestation signer is missing or is not ECDSA.")
    checked = _verify_tdx_and_binding(attestation, nonce)
    gpu = _check_nvidia_payload(attestation, nonce, timeout, required=require_gpu)
    return {
        "signing_address": signer,
        "signing_algo": algo,
        **checked,
        **gpu,
    }


def _select_preflight_match(preflight: dict[str, Any], signer: str, algo: str, kind: str) -> dict[str, Any]:
    if kind == "provider_tee":
        matches = [
            item for item in preflight.get("models", [])
            if isinstance(item, dict)
            and str(item.get("signing_address", "")).lower() == signer.lower()
            and str(item.get("signing_algo", "")).lower() == algo
        ]
        if len(matches) != 1:
            raise VerificationError(
                "Provider signature does not match exactly one verified model report."
            )
        return matches[0]
    gateway = preflight.get("gateway")
    if not isinstance(gateway, dict):
        raise VerificationError("Preflight gateway evidence is missing.")
    if (
        str(gateway.get("signing_address", "")).lower() != signer.lower()
        or str(gateway.get("signing_algo", "")).lower() != algo
    ):
        raise VerificationError("Gateway signature does not match the verified gateway report.")
    return gateway


def preflight_near_ai_evidence(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch and verify gateway and model quotes before a completion is sent."""
    nonce = secrets.token_hex(32)
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(
        base_url.rstrip("/") + ATTESTATION_PATH,
        params={
            "model": model,
            "nonce": nonce,
            "signing_algo": "ecdsa",
            "provider": "near",
        },
        headers=headers,
        timeout=timeout,
    )
    report = _response_json(response, "NEAR AI attestation endpoint")
    gateway = report.get("gateway_attestation")
    if not isinstance(gateway, dict) or gateway.get("error"):
        raise VerificationError("Attestation report omitted the gateway quote.")
    verified_gateway = _verify_one_attestation(gateway, nonce, timeout, require_gpu=False)
    candidates = report.get("model_attestations")
    if not isinstance(candidates, list) or not candidates:
        raise VerificationError("Attestation report omitted NEAR model evidence.")
    verified_models = []
    for item in candidates:
        if not isinstance(item, dict) or item.get("error"):
            raise VerificationError("A model attestation candidate was unusable.")
        stated_model = item.get("model_name")
        if stated_model and stated_model != model:
            raise VerificationError("Model attestation name does not match the requested model.")
        verified_models.append(_verify_one_attestation(item, nonce, timeout, require_gpu=True))
    return {"nonce": nonce, "gateway": verified_gateway, "models": verified_models}


def verify_near_ai_response(
    *,
    base_url: str,
    api_key: str,
    chat_id: str,
    model: str,
    request_body: str | bytes,
    response_body: str | bytes,
    preflight: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Verify a completion against evidence fetched before the request was sent."""
    if not chat_id:
        raise VerificationError("NEAR AI response omitted its chat ID.")
    if not isinstance(preflight, dict) or not preflight.get("nonce"):
        raise VerificationError("Preflight NEAR AI evidence is missing.")
    headers = {"Authorization": f"Bearer {api_key}"}
    signature_response = requests.get(
        base_url.rstrip("/") + SIGNATURE_PATH.format(chat_id=quote(chat_id, safe="")),
        params={"model": model, "signing_algo": "ecdsa"},
        headers=headers,
        timeout=timeout,
    )
    signature_payload = _response_json(signature_response, "NEAR AI signature endpoint")
    if signature_payload.get("error_code") or signature_payload.get("error"):
        raise VerificationError("Signature endpoint returned an unavailable result.")
    signature = _verify_signature_payload(request_body, response_body, signature_payload, model)
    matched = _select_preflight_match(
        preflight,
        signature["signing_address"],
        signature["signing_algo"],
        signature["signature_kind"],
    )
    return {
        "verified": True,
        "verification_level": "ECDSA_PREFLIGHT_TDX_MEASUREMENTS",
        "signature_kind": signature["signature_kind"],
        "signing_address": signature["signing_address"],
        "signing_algo": signature["signing_algo"],
        "request_hash_verified": True,
        "response_hash_verified": True,
        "preflight_nonce": preflight["nonce"],
        "matched_preflight_signer": matched["signing_address"],
        **{key: value for key, value in matched.items() if key not in {"signing_address", "signing_algo"}},
        "limitations": [
            "NVIDIA NRAS JWT signature is not verified locally; the verdict is the TLS response and its eat_nonce.",
            "The gateway TLS peer certificate and Sigstore image provenance are not verified.",
            "A provider-TEE signature matches one preflight model report. It does not prove that report and the completion came from the same serving instance.",
            "This checks response integrity and measured TDX binding. It does not prove the model's reasoning is correct.",
        ],
    }
