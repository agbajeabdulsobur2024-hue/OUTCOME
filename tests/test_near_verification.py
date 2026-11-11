import base64
import hashlib
import json
import sys
from types import SimpleNamespace

from eth_account import Account
from eth_account.messages import encode_defunct

from app.near_verification import (
    _check_nvidia_payload,
    _select_preflight_match,
    _verify_signature_payload,
    _verify_tdx_and_binding,
    replay_rtmr3,
    VerificationError,
)


def signed_fixture(model="test-model", kind="provider_tee"):
    request_body = '{"model":"test-model"}'
    response_text = '{"id":"chat-1","choices":[]}'
    request_hash = hashlib.sha256(request_body.encode()).hexdigest()
    response_hash = hashlib.sha256(response_text.encode()).hexdigest()
    signed_text = (
        f"{model}:{request_hash}:{response_hash}"
        if kind == "provider_tee" else f"{request_hash}:{response_hash}"
    )
    signer = Account.create()
    signature = Account.sign_message(encode_defunct(text=signed_text), signer.key).signature.hex()
    payload = {
        "text": signed_text,
        "signature": signature,
        "signing_address": signer.address,
        "signing_algo": "ecdsa",
        "signature_kind": kind,
    }
    return request_body, response_text, signer, payload


def _td10_attestation(signer, nonce, *, debug=False, mr_flip=False, rtmr_flip=False, echo_nonce=None):
    signer_bytes = bytes.fromhex(signer.removeprefix("0x"))
    nonce_bytes = bytes.fromhex(nonce)
    report_data = signer_bytes.ljust(32, b"\x00") + nonce_bytes
    app_compose = '{"manifest_version":2,"name":"test"}'
    mr_config = b"\x01" + hashlib.sha256(app_compose.encode("utf-8")).digest() + bytes(15)
    if mr_flip:
        mr_config = bytearray(mr_config)
        mr_config[-1] ^= 0x01
        mr_config = bytes(mr_config)
    rt_mr3 = replay_rtmr3([])
    if rtmr_flip:
        rt_mr3 = bytes([0xFF]) + rt_mr3[1:]
    td_attributes = bytearray(8)
    if debug:
        td_attributes[0] = 1
    attestation = {
        "intel_quote": "01",
        "signing_address": signer,
        "signing_algo": "ecdsa",
        "request_nonce": nonce if echo_nonce is None else echo_nonce,
        "event_log": [],
        "info": {"tcb_info": {"app_compose": app_compose}},
    }

    class FakeResult:
        status = "UpToDate"

        def to_json(self):
            return json.dumps({
                "status": "UpToDate",
                "advisory_ids": [],
                "report": {
                    "TD10": {
                        "report_data": report_data.hex(),
                        "td_attributes": bytes(td_attributes).hex(),
                        "mr_config_id": mr_config.hex(),
                        "rt_mr3": rt_mr3.hex(),
                    }
                },
            })

    async def fake_verify(_quote):
        return FakeResult()

    return attestation, fake_verify


def _install_quote(monkeypatch, fake_verify):
    monkeypatch.setitem(sys.modules, "dcap_qvl", SimpleNamespace(
        get_collateral_and_verify=fake_verify
    ))


def test_provider_signature_hashes_model_and_signer_are_verified():
    request_body, response_text, signer, payload = signed_fixture()
    result = _verify_signature_payload(request_body, response_text, payload, "test-model")
    assert result["signing_address"] == signer.address
    assert result["signature_kind"] == "provider_tee"
    assert result["signing_algo"] == "ecdsa"


def test_gateway_signature_format_is_supported():
    request_body, response_text, signer, payload = signed_fixture(kind="gateway")
    result = _verify_signature_payload(request_body, response_text, payload, "test-model")
    assert result["signing_address"] == signer.address
    assert result["signature_kind"] == "gateway"


def test_provider_signature_accepts_colons_inside_the_model_id():
    model = "org:name/model"
    request_body, response_text, signer, payload = signed_fixture(model=model)
    result = _verify_signature_payload(request_body, response_text, payload, model)
    assert result["signature_kind"] == "provider_tee"
    assert result["signing_address"] == signer.address


def test_missing_signature_kind_is_rejected():
    request_body, response_text, _, payload = signed_fixture()
    del payload["signature_kind"]
    try:
        _verify_signature_payload(request_body, response_text, payload, "test-model")
    except VerificationError as exc:
        assert "signature_kind" in str(exc)
    else:
        raise AssertionError("missing signature_kind must be rejected")


def test_provider_signature_rejects_wrong_model():
    request_body, response_text, _, payload = signed_fixture()
    try:
        _verify_signature_payload(request_body, response_text, payload, "different-model")
    except VerificationError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("wrong model must be rejected")


def test_signed_response_rejects_tampered_response():
    request_body, response_text, _, payload = signed_fixture()
    try:
        _verify_signature_payload(request_body, response_text + " ", payload, "test-model")
    except VerificationError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("tampered response must be rejected")


def test_signed_response_hashes_raw_bytes():
    request_body, response_text, _, payload = signed_fixture()
    result = _verify_signature_payload(request_body.encode(), response_text.encode(), payload, "test-model")
    assert result["signature_kind"] == "provider_tee"


def test_signed_response_rejects_wrong_signer():
    request_body, response_text, _, payload = signed_fixture()
    payload["signing_address"] = Account.create().address
    try:
        _verify_signature_payload(request_body, response_text, payload, "test-model")
    except VerificationError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("wrong signer must be rejected")


def test_tdx_quote_must_bind_signer_nonce_and_measurements(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce)
    _install_quote(monkeypatch, fake_verify)
    result = _verify_tdx_and_binding(attestation, nonce)
    assert result["tdx_quote_verified"] is True
    assert result["tdx_status"] == "UpToDate"
    assert result["signer_bound_to_quote"] is True
    assert result["fresh_nonce_bound_to_quote"] is True
    assert result["mr_config_id_matches_app_compose"] is True
    assert result["rtmr3_matches_event_log"] is True


def test_tdx_quote_rejects_nonce_mismatch(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce)
    _install_quote(monkeypatch, fake_verify)
    try:
        _verify_tdx_and_binding(attestation, "cd" * 32)
    except VerificationError as exc:
        assert "nonce" in str(exc).lower()
    else:
        raise AssertionError("nonce mismatch must be rejected")


def test_tdx_quote_rejects_debug_mode(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce, debug=True)
    _install_quote(monkeypatch, fake_verify)
    try:
        _verify_tdx_and_binding(attestation, nonce)
    except VerificationError as exc:
        assert "debug" in str(exc).lower()
    else:
        raise AssertionError("debug TDX quote must be rejected")


def test_tdx_quote_rejects_mr_config_mismatch(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce, mr_flip=True)
    _install_quote(monkeypatch, fake_verify)
    try:
        _verify_tdx_and_binding(attestation, nonce)
    except VerificationError as exc:
        assert "MRCONFIGID" in str(exc)
    else:
        raise AssertionError("MRCONFIGID mismatch must be rejected")


def test_tdx_quote_rejects_rtmr_mismatch(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce, rtmr_flip=True)
    _install_quote(monkeypatch, fake_verify)
    try:
        _verify_tdx_and_binding(attestation, nonce)
    except VerificationError as exc:
        assert "RTMR3" in str(exc)
    else:
        raise AssertionError("RTMR3 mismatch must be rejected")


def _jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{payload}.sig"


def test_nvidia_boolean_true_and_eat_nonce_are_accepted(monkeypatch):
    nonce = "ab" * 32

    def fake_post(url, json=None, timeout=None, headers=None):
        assert url.endswith("/v3/attest/gpu")
        token = _jwt({"x-nvidia-overall-att-result": True, "eat_nonce": nonce})
        return SimpleNamespace(status_code=200, json=lambda: [["JWT", token]])

    monkeypatch.setattr("app.near_verification.requests.post", fake_post)
    result = _check_nvidia_payload(
        {"nvidia_payload": json.dumps({"nonce": nonce, "evidence_list": []})},
        nonce,
        5,
        required=True,
    )
    assert result["gpu_nras_verdict"] is True
    assert result["gpu_eat_nonce_matches"] is True
    assert result["gpu_jwt_signature_verified_locally"] is False


def test_nvidia_true_with_wrong_eat_nonce_is_rejected(monkeypatch):
    nonce = "ab" * 32

    def fake_post(url, json=None, timeout=None, headers=None):
        token = _jwt({"x-nvidia-overall-att-result": True, "eat_nonce": "cd" * 32})
        return SimpleNamespace(status_code=200, json=lambda: [["JWT", token]])

    monkeypatch.setattr("app.near_verification.requests.post", fake_post)
    try:
        _check_nvidia_payload(
            {"nvidia_payload": json.dumps({"nonce": nonce})},
            nonce,
            5,
            required=True,
        )
    except VerificationError as exc:
        assert "EAT nonce" in str(exc)
    else:
        raise AssertionError("wrong EAT nonce must be rejected")


def test_nvidia_string_pass_is_not_accepted(monkeypatch):
    nonce = "ab" * 32

    def fake_post(url, json=None, timeout=None, headers=None):
        token = _jwt({"x-nvidia-overall-att-result": "PASS", "eat_nonce": nonce})
        return SimpleNamespace(status_code=200, json=lambda: [["JWT", token]])

    monkeypatch.setattr("app.near_verification.requests.post", fake_post)
    try:
        _check_nvidia_payload(
            {"nvidia_payload": json.dumps({"nonce": nonce})},
            nonce,
            5,
            required=True,
        )
    except VerificationError as exc:
        assert "not true" in str(exc)
    else:
        raise AssertionError("string PASS must be rejected")


def test_missing_nvidia_evidence_fails_when_required():
    try:
        _check_nvidia_payload({}, "ab" * 32, 5, required=True)
    except VerificationError as exc:
        assert "NVIDIA" in str(exc)
    else:
        raise AssertionError("missing GPU evidence must be rejected for a provider TEE")


def test_provider_signature_must_match_exactly_one_model_report():
    signer = "0x" + "ab" * 20
    preflight = {
        "gateway": {"signing_address": "0x" + "11" * 20, "signing_algo": "ecdsa"},
        "models": [
            {"signing_address": signer, "signing_algo": "ecdsa"},
            {"signing_address": signer, "signing_algo": "ecdsa"},
        ],
    }
    try:
        _select_preflight_match(preflight, signer, "ecdsa", "provider_tee")
    except VerificationError as exc:
        assert "exactly one" in str(exc)
    else:
        raise AssertionError("ambiguous model signer must be rejected")
    preflight["models"].pop()
    matched = _select_preflight_match(preflight, signer, "ecdsa", "provider_tee")
    assert matched["signing_address"] == signer


def test_signature_kind_is_not_inferred_from_colon_count():
    request_body, response_text, _, payload = signed_fixture()
    del payload["signature_kind"]
    try:
        _verify_signature_payload(request_body, response_text, payload, "test-model")
    except VerificationError as exc:
        assert "signature_kind" in str(exc)
    else:
        raise AssertionError("colon-shaped text must not imply signature_kind")
    payload["signature_kind"] = "gateway"
    try:
        _verify_signature_payload(request_body, response_text, payload, "test-model")
    except VerificationError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("gateway kind must not accept provider-shaped text")


def test_missing_signing_algo_is_rejected():
    request_body, response_text, _, payload = signed_fixture(kind="gateway")
    del payload["signing_algo"]
    try:
        _verify_signature_payload(request_body, response_text, payload, "test-model")
    except VerificationError as exc:
        assert "signing_algo" in str(exc)
    else:
        raise AssertionError("missing signing_algo must be rejected")


def test_tdx_quote_rejects_out_of_date_status(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce)
    original = fake_verify

    async def out_of_date(quote):
        result = await original(quote)
        result.status = "OutOfDate"
        return result

    _install_quote(monkeypatch, out_of_date)
    try:
        _verify_tdx_and_binding(attestation, nonce)
    except VerificationError as exc:
        assert "UpToDate" in str(exc)
        assert "OutOfDate" in str(exc)
    else:
        raise AssertionError("OutOfDate TCB status must be rejected")


def test_gateway_quote_requires_echoed_report_data(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    attestation, fake_verify = _td10_attestation(signer, nonce)
    _install_quote(monkeypatch, fake_verify)
    try:
        _verify_tdx_and_binding(attestation, nonce, require_echoed_report_data=True)
    except VerificationError as exc:
        assert "report_data" in str(exc)
    else:
        raise AssertionError("gateway quote without echoed report_data must be rejected")


def test_preflight_sends_no_aliasing_header(monkeypatch):
    from app import near_verification as nv
    seen = {}

    class Response:
        status_code = 200

        def json(self):
            return {}

    def fake_get(url, params=None, headers=None, timeout=None, allow_redirects=None):
        seen["headers"] = headers
        seen["params"] = params
        return Response()

    monkeypatch.setattr(nv.requests, "get", fake_get)
    try:
        nv.preflight_near_ai_evidence(base_url="https://cloud-api.near.ai", api_key="k", model="m")
    except nv.VerificationError:
        pass
    else:
        raise AssertionError("empty attestation report must fail closed")
    assert seen["headers"]["x-no-aliasing"] == "true"
    assert seen["headers"]["Accept-Encoding"] == "identity"
    assert seen["params"]["provider"] == "near"
    assert seen["params"]["signing_algo"] == "ecdsa"
    assert seen["params"]["include_tls_fingerprint"] == "false"
