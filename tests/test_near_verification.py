import hashlib
import json
import sys
from types import SimpleNamespace

from eth_account import Account
from eth_account.messages import encode_defunct

from app.near_verification import (
    _verify_signature_payload,
    _verify_tdx_and_binding,
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
        "signature_kind": kind,
    }
    return request_body, response_text, signer, payload


def test_provider_signature_hashes_model_and_signer_are_verified():
    request_body, response_text, signer, payload = signed_fixture()
    result = _verify_signature_payload(request_body, response_text, payload, "test-model")
    assert result == {"signing_address": signer.address, "signature_kind": "provider_tee"}


def test_gateway_signature_format_is_supported():
    request_body, response_text, signer, payload = signed_fixture(kind="gateway")
    result = _verify_signature_payload(request_body, response_text, payload, "test-model")
    assert result == {"signing_address": signer.address, "signature_kind": "gateway"}


def test_provider_signature_rejects_wrong_model():
    request_body, response_text, _, payload = signed_fixture()
    try:
        _verify_signature_payload(request_body, response_text, payload, "different-model")
    except VerificationError as exc:
        assert "model identifier" in str(exc)
    else:
        raise AssertionError("wrong model must be rejected")


def test_signed_response_rejects_tampered_response():
    request_body, response_text, _, payload = signed_fixture()
    try:
        _verify_signature_payload(request_body, response_text + " ", payload, "test-model")
    except VerificationError as exc:
        assert "response hash" in str(exc)
    else:
        raise AssertionError("tampered response must be rejected")


def test_signed_response_rejects_wrong_signer():
    request_body, response_text, _, payload = signed_fixture()
    payload["signing_address"] = Account.create().address
    try:
        _verify_signature_payload(request_body, response_text, payload, "test-model")
    except VerificationError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("wrong signer must be rejected")


def test_tdx_quote_must_bind_signer_and_fresh_nonce(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    signer_bytes = bytes.fromhex(signer.removeprefix("0x"))
    report_data = signer_bytes.ljust(32, b"\\x00") + bytes.fromhex(nonce)

    class FakeResult:
        status = "UpToDate"

        def to_json(self):
            return json.dumps({
                "quote": {"body": {"reportdata": report_data.hex(), "mrconfig": "test-measurement"}}
            })

    async def fake_verify(_quote):
        return FakeResult()

    monkeypatch.setitem(sys.modules, "dcap_qvl", SimpleNamespace(
        get_collateral_and_verify=fake_verify
    ))
    result = _verify_tdx_and_binding({"intel_quote": "01", "signing_address": signer}, nonce)
    assert result["tdx_quote_verified"] is True
    assert result["signer_bound_to_quote"] is True
    assert result["fresh_nonce_bound_to_quote"] is True


def test_tdx_quote_rejects_nonce_mismatch(monkeypatch):
    signer = Account.create().address
    nonce = "ab" * 32
    report_data = bytes.fromhex(signer.removeprefix("0x")).ljust(32, b"\\x00") + bytes(32)

    class FakeResult:
        status = "UpToDate"

        def to_json(self):
            return json.dumps({"quote": {"body": {"reportdata": report_data.hex()}}})

    async def fake_verify(_quote):
        return FakeResult()

    monkeypatch.setitem(sys.modules, "dcap_qvl", SimpleNamespace(
        get_collateral_and_verify=fake_verify
    ))
    try:
        _verify_tdx_and_binding({"intel_quote": "01", "signing_address": signer}, nonce)
    except VerificationError as exc:
        assert "nonce" in str(exc).lower()
    else:
        raise AssertionError("nonce mismatch must be rejected")
