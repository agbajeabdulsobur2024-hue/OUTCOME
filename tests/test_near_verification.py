import hashlib

from eth_account import Account
from eth_account.messages import encode_defunct

from app.near_verification import _verify_signature_payload, VerificationError


def signed_fixture():
    request_body = '{"model":"test"}'
    response_text = '{"id":"chat-1","choices":[]}'
    request_hash = hashlib.sha256(request_body.encode()).hexdigest()
    response_hash = hashlib.sha256(response_text.encode()).hexdigest()
    signed_text = f"ecdsa:{request_hash}:{response_hash}"
    signer = Account.create()
    signature = Account.sign_message(encode_defunct(text=signed_text), signer.key).signature.hex()
    payload = {"text": signed_text, "signature": signature, "signing_address": signer.address}
    return request_body, response_text, signer, payload


def test_near_ai_signed_response_hashes_and_signer_are_verified():
    request_body, response_text, signer, payload = signed_fixture()
    assert _verify_signature_payload(request_body, response_text, payload) == signer.address


def test_near_ai_signed_response_rejects_tampered_response():
    request_body, response_text, _, payload = signed_fixture()
    try:
        _verify_signature_payload(request_body, response_text + " ", payload)
    except VerificationError as exc:
        assert "response hash" in str(exc)
    else:
        raise AssertionError("tampered response must be rejected")


def test_near_ai_signed_response_rejects_wrong_signer():
    request_body, response_text, _, payload = signed_fixture()
    payload["signing_address"] = Account.create().address
    try:
        _verify_signature_payload(request_body, response_text, payload)
    except VerificationError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("wrong signer must be rejected")
