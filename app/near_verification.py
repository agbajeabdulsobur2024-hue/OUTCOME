"""Fail-closed verification for NEAR AI Cloud signed chat responses.

Follows the current NEAR AI Cloud verification docs:
https://docs.near.ai/cloud/verification/reference/quote-nonce-signer
https://docs.near.ai/cloud/verification/reference/response-signature-verification
https://docs.near.ai/cloud/verification/cloud-api/response-signatures
https://docs.near.ai/cloud/verification/cloud-api/gateway-attestation
https://docs.near.ai/cloud/verification/cloud-api/model-attestations

- Preflight a client nonce and verify the gateway quote and every model quote
  before the completion is treated as evidence.
- Require an explicit signature_kind. Do not infer it from colon count.
- Build the expected signature text from that kind and require an exact match
  to the SHA-256 of the request bytes and raw response bytes.
- Recover the EIP-191 ECDSA signer from a 65-byte signature and require exactly
  one matching preflight report for that signer and algorithm.
- Verify the Intel TDX quote (UpToDate only), reject debug TDs, bind the
  signing address and nonce in report_data, and pin MRCONFIGID and RTMR3
  to app_compose and the event log.
- For a provider-TEE signature, require an NVIDIA NRAS verdict of boolean true
  whose eat_nonce matches the preflight nonce.

This does not verify the NRAS JWT signature locally, Sigstore provenance, or
the live gateway TLS peer certificate. Those limits are returned explicitly.
HTTPS to the gateway is not a substitute for attestation.
A successful result is not a claim that the model's reasoning is correct.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import struct
import time
from typing import Any
from urllib.parse import quote

import requests

SIGNATURE_PATH = "/v1/signature/{chat_id}"
ATTESTATION_PATH = "/v1/attestation/report"
NRAS_GPU_URL = "https://nras.attestation.nvidia.com/v3/attest/gpu"
ACCEPTED_TCB_STATUS = "UpToDate"
SIGNATURE_FETCH_ATTEMPTS = 3
SIGNATURE_RETRY_SECONDS = 0.25
_HEX = re.compile(r"^[0-9a-fA-F]+$")


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


def _near_headers(api_key: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "x-no-aliasing": "true",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _require_hex(value: object, label: str, expected_len: int) -> bytes:
    if not isinstance(value, str):
        raise VerificationError(f"{label} must be hexadecimal.")
    text = value[2:] if value.startswith(("0x", "0X")) else value
    if not text or len(text) % 2 or not _HEX.fullmatch(text):
        raise VerificationError(f"{label} must be hexadecimal.")
    raw = bytes.fromhex(text)
    if len(raw) != expected_len:
        raise VerificationError(f"{label} must be exactly {expected_len} bytes.")
    return raw


def _expected_signature_text(
    kind: str,
    model: str,
    request_body: str | bytes,
    response_body: str | bytes,
) -> str:
    """Build the signed text selected by signature_kind. Do not inspect colons."""
    request_hash = sha256_hex(request_body)
    response_hash = sha256_hex(response_body)
    if kind == "provider_tee":
        if not isinstance(model, str) or not model:
            raise VerificationError("Provider signature requires the requested model identifier.")
        return f"{model}:{request_hash}:{response_hash}"
    if kind == "gateway":
        return f"{request_hash}:{response_hash}"
    raise VerificationError("Signature kind is missing or unfamiliar.")


def _verify_signature_payload(
    request_body: str | bytes,
    response_body: str | bytes,
    payload: dict[str, Any],
    model: str,
) -> dict[str, str]:
    """Verify exact signature text and the EIP-191 ECDSA signer."""
    if not isinstance(payload, dict):
        raise VerificationError("Signature endpoint returned an unexpected response.")
    try:
        signed_text = payload["text"]
        signature = payload["signature"]
        claimed_signer = payload["signing_address"]
    except KeyError as exc:
        raise VerificationError("Signature endpoint omitted required fields.") from exc

    if not isinstance(signed_text, str) or not signed_text:
        raise VerificationError("Signature endpoint returned invalid fields.")
    if not isinstance(signature, str) or not signature:
        raise VerificationError("Signature endpoint returned invalid fields.")
    if not isinstance(claimed_signer, str) or not claimed_signer:
        raise VerificationError("Signature endpoint returned invalid fields.")

    # Kind is required. A colon count is not a substitute for this field.
    declared_kind = payload.get("signature_kind")
    if declared_kind not in ("provider_tee", "gateway"):
        raise VerificationError("signature_kind is missing or unfamiliar.")
    if payload.get("signing_algo") != "ecdsa":
        raise VerificationError("signing_algo is missing or is not ECDSA.")

    expected = _expected_signature_text(declared_kind, model, request_body, response_body)
    if not secrets.compare_digest(signed_text, expected):
        raise VerificationError("Signature text does not match the exact request and response bytes.")

    signature_bytes = _require_hex(signature, "ECDSA signature", 65)
    claimed_bytes = _require_hex(claimed_signer, "signing address", 20)
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(
            encode_defunct(text=signed_text),
            signature="0x" + signature_bytes.hex(),
        )
        recovered_bytes = _require_hex(recovered, "recovered signer", 20)
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError("ECDSA response signature could not be recovered.") from exc

    if not secrets.compare_digest(recovered_bytes, claimed_bytes):
        raise VerificationError("ECDSA signer does not match the claimed signing address.")
    return {
        "signing_address": claimed_signer,
        "signature_kind": declared_kind,
        "signing_algo": "ecdsa",
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
        try:
            event_log = json.loads(event_log)
        except ValueError as exc:
            raise VerificationError("Attestation event log is not JSON.") from exc
    if not isinstance(event_log, list):
        raise VerificationError("Attestation event log is not a list.")
    rtmr3 = bytes(48)
    for event in event_log:
        if not isinstance(event, dict) or event.get("imr") != 3:
            continue
        event_name = event.get("event")
        event_type = event.get("event_type")
        payload_hex = event.get("event_payload") or ""
        if not isinstance(event_name, str) or isinstance(event_type, bool) or not isinstance(event_type, int):
            raise VerificationError("RTMR3 event log entry is malformed.")
        if not isinstance(payload_hex, str):
            raise VerificationError("RTMR3 event log entry is malformed.")
        try:
            payload = bytes.fromhex(payload_hex)
            digest = hashlib.sha384(
                struct.pack("<I", event_type) + b":" + event_name.encode("utf-8") + b":" + payload
            ).digest()
        except (ValueError, struct.error) as exc:
            raise VerificationError("RTMR3 event log entry is malformed.") from exc
        claimed = event.get("digest") or ""
        if claimed:
            try:
                claimed_bytes = bytes.fromhex(str(claimed))
            except ValueError as exc:
                raise VerificationError("RTMR3 event log entry is malformed.") from exc
            if claimed_bytes != digest:
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


def _tcb_status_name(result: Any) -> str:
    """Accept only the exact TCB status UpToDate. OutOfDate is not enough."""
    status = getattr(result, "status", None)
    if status == ACCEPTED_TCB_STATUS:
        return ACCEPTED_TCB_STATUS
    if isinstance(status, str):
        name = status
    else:
        value = getattr(status, "value", None)
        if isinstance(value, str):
            name = value
        else:
            name = "" if status is None else str(status).rsplit(".", 1)[-1]
    if name != ACCEPTED_TCB_STATUS:
        raise VerificationError(f"Intel TDX quote was not UpToDate (status: {name or 'unknown'}).")
    return name


def _echoed_report_data(attestation: dict[str, Any], quote_report_data: bytes, *, required: bool) -> None:
    """Compare HTTP report_data with the verified quote when the field is present.

    Gateway reports must echo it. Model reports often omit it; the quote remains
    the source of truth, and a present echo that differs is still rejected.
    """
    if "report_data" not in attestation or attestation.get("report_data") in ("", None):
        if required:
            raise VerificationError("Attestation omitted echoed report_data.")
        return
    echoed = attestation.get("report_data")
    if not isinstance(echoed, str):
        raise VerificationError("Attestation echoed report_data is malformed.")
    try:
        echoed_bytes = _require_hex(echoed, "echoed report_data", 64)
    except VerificationError as exc:
        raise VerificationError("Attestation echoed report_data is malformed.") from exc
    if not secrets.compare_digest(echoed_bytes, quote_report_data):
        raise VerificationError("Attestation echoed report_data does not match the verified quote.")


def _verify_tdx_and_binding(
    attestation: dict[str, Any],
    nonce: str,
    *,
    require_echoed_report_data: bool = False,
) -> dict[str, Any]:
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

    status = _tcb_status_name(result)
    td10 = _td10_quote(result)
    report_data = _hex_bytes(td10.get("report_data"), "report data", 64)
    td_attributes = _hex_bytes(td10.get("td_attributes"), "td_attributes", 8)
    if td_attributes[0] & 1:
        raise VerificationError("TDX quote is a debug quote and is not accepted.")
    mr_config = _hex_bytes(td10.get("mr_config_id"), "mr_config_id", 48)
    rt_mr3 = _hex_bytes(td10.get("rt_mr3"), "rt_mr3", 48)
    _echoed_report_data(attestation, report_data, required=require_echoed_report_data)

    echoed = attestation.get("request_nonce")
    if not isinstance(echoed, str) or echoed.lower() != nonce.lower():
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
        payload_nonce = payload.get("nonce") if isinstance(payload, dict) else None
        if not isinstance(payload_nonce, str) or payload_nonce.lower() != nonce.lower():
            raise VerificationError("NVIDIA payload nonce does not match the fresh nonce.")
        response = requests.post(
            NRAS_GPU_URL,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout,
        )
        if not 200 <= response.status_code < 300:
            raise VerificationError(f"NVIDIA NRAS returned HTTP {response.status_code}.")
        body = response.json()
        if (
            not isinstance(body, list) or not body or not isinstance(body[0], list)
            or len(body[0]) < 2 or body[0][0] != "JWT" or not isinstance(body[0][1], str)
        ):
            raise VerificationError("NVIDIA NRAS response was malformed.")
        claims = _jwt_payload(body[0][1])
        verdict = claims.get("x-nvidia-overall-att-result")
        eat_nonce = claims.get("eat_nonce")
        if verdict is not True:
            raise VerificationError("NVIDIA NRAS overall result was not true.")
        if not isinstance(eat_nonce, str) or eat_nonce.lower() != nonce.lower():
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
    require_echoed_report_data: bool,
) -> dict[str, Any]:
    signer = attestation.get("signing_address")
    algo = attestation.get("signing_algo")
    if not isinstance(signer, str) or not signer or algo != "ecdsa":
        raise VerificationError("Attestation signer is missing or is not ECDSA.")
    _require_hex(signer, "signing address", 20)
    checked = _verify_tdx_and_binding(
        attestation,
        nonce,
        require_echoed_report_data=require_echoed_report_data,
    )
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
    response = requests.get(
        base_url.rstrip("/") + ATTESTATION_PATH,
        params={
            "model": model,
            "nonce": nonce,
            "signing_algo": "ecdsa",
            "provider": "near",
            # TLS peer binding is a separate same-connection flow. Do not request
            # a fingerprint unless that peer certificate is observed and compared.
            "include_tls_fingerprint": "false",
        },
        headers=_near_headers(api_key),
        timeout=timeout,
        allow_redirects=False,
    )
    report = _response_json(response, "NEAR AI attestation endpoint")
    gateway = report.get("gateway_attestation")
    if not isinstance(gateway, dict) or gateway.get("error"):
        raise VerificationError("Attestation report omitted the gateway quote.")
    verified_gateway = _verify_one_attestation(
        gateway, nonce, timeout, require_gpu=False, require_echoed_report_data=True,
    )
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
        verified_models.append(_verify_one_attestation(
            item, nonce, timeout, require_gpu=True, require_echoed_report_data=False,
        ))
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
    if not isinstance(preflight, dict) or not isinstance(preflight.get("nonce"), str) or not preflight.get("nonce"):
        raise VerificationError("Preflight NEAR AI evidence is missing.")
    signature_payload = _fetch_signature_payload(
        base_url=base_url,
        api_key=api_key,
        chat_id=chat_id,
        model=model,
        timeout=timeout,
    )
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
            "NVIDIA NRAS JWT signature is not verified locally. The check uses the TLS response from nras.attestation.nvidia.com and requires x-nvidia-overall-att-result to be boolean true and eat_nonce to match the preflight nonce.",
            "Sigstore image provenance is not verified. A quote measurement is not a provenance statement, and an HTTP 200 from a search page is not one either.",
            "The live gateway TLS peer certificate was not compared on the same connection. HTTPS to cloud-api.near.ai is not attestation.",
            "A provider-TEE signature matches one preflight model report. Instances of the same model share a signing key, so this does not prove the report and the completion came from the same serving instance.",
            "A gateway signature attests the gateway quote, not the model quote.",
            "This checks response integrity and measured TDX binding. It does not prove the model's reasoning is correct.",
        ],
    }


def _signature_unavailable(payload: dict[str, Any]) -> bool:
    return bool(payload.get("error_code") or payload.get("error"))


def _fetch_signature_payload(
    *,
    base_url: str,
    api_key: str,
    chat_id: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    """Fetch a signature, retrying only a short bounded window of unavailability.

    A 404 or an explicit unavailable body can arrive before the signature is
    recorded. Cryptographic mismatches are not retried. Exhausting the window
    still fails closed.
    """
    url = base_url.rstrip("/") + SIGNATURE_PATH.format(chat_id=quote(str(chat_id), safe=""))
    last_unavailable: VerificationError | None = None
    for attempt in range(SIGNATURE_FETCH_ATTEMPTS):
        response = requests.get(
            url,
            params={"model": model, "signing_algo": "ecdsa"},
            headers=_near_headers(api_key),
            timeout=timeout,
            allow_redirects=False,
        )
        if response.status_code == 404:
            last_unavailable = VerificationError("Signature endpoint returned an unavailable result.")
        elif 200 <= response.status_code < 300:
            payload = _response_json(response, "NEAR AI signature endpoint")
            if _signature_unavailable(payload):
                last_unavailable = VerificationError("Signature endpoint returned an unavailable result.")
            else:
                return payload
        else:
            raise VerificationError(f"NEAR AI signature endpoint returned HTTP {response.status_code}.")
        if attempt + 1 < SIGNATURE_FETCH_ATTEMPTS:
            time.sleep(SIGNATURE_RETRY_SECONDS)
    raise last_unavailable or VerificationError("Signature endpoint returned an unavailable result.")


def verification_is_complete(verification: Any) -> bool:
    """True only when the required cryptographic checks actually succeeded.

    A bare verified flag is not enough. Local NRAS JWT verification and
    same-connection TLS peer comparison are not implemented, so a result that
    claims either of those checks is refused rather than displayed.
    """
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        return False
    if verification.get("signing_algo") != "ecdsa":
        return False
    if verification.get("tdx_status") != ACCEPTED_TCB_STATUS:
        return False
    if verification.get("tls_peer_certificate_compared") is True:
        return False
    if verification.get("gpu_jwt_signature_verified_locally") is True:
        return False
    required = (
        "request_hash_verified",
        "response_hash_verified",
        "tdx_quote_verified",
        "tdx_debug_rejected",
        "signer_bound_to_quote",
        "fresh_nonce_bound_to_quote",
        "mr_config_id_matches_app_compose",
        "rtmr3_matches_event_log",
    )
    if any(verification.get(flag) is not True for flag in required):
        return False
    kind = verification.get("signature_kind")
    if kind == "provider_tee":
        return all(
            verification.get(flag) is True
            for flag in (
                "gpu_attestation_checked",
                "gpu_nras_verdict",
                "gpu_nonce_matches",
                "gpu_eat_nonce_matches",
            )
        )
    if kind == "gateway":
        verdict = verification.get("gpu_nras_verdict", None)
        if "gpu_nras_verdict" in verification and verdict is not True:
            return False
        if verdict is True and verification.get("gpu_attestation_checked") is not True:
            return False
        return True
    return False
