import json

import httpx
import pytest

from app import ai
from app.near_verification import VerificationError


def verified_provider_result():
    """The flags generate_operational_brief requires before it parses a brief."""
    return {
        "verified": True,
        "signature_kind": "provider_tee",
        "signing_algo": "ecdsa",
        "tdx_status": "UpToDate",
        "request_hash_verified": True,
        "response_hash_verified": True,
        "tdx_quote_verified": True,
        "tdx_debug_rejected": True,
        "signer_bound_to_quote": True,
        "fresh_nonce_bound_to_quote": True,
        "mr_config_id_matches_app_compose": True,
        "rtmr3_matches_event_log": True,
        "tls_peer_certificate_compared": False,
        "gpu_jwt_signature_verified_locally": False,
        "gpu_attestation_checked": True,
        "gpu_nras_verdict": True,
        "gpu_nonce_matches": True,
        "gpu_eat_nonce_matches": True,
    }


def completion(content, finish_reason="stop"):
    return {
        "id": "chat-123",
        "model": ai.NEAR_AI_MODEL,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    }


def sample_record():
    return {
        "commitment": {
            "id": "commit-1", "supplier": "Acme", "description": "Boxes",
            "quantity": 100, "unit": "boxes", "amount": 500, "currency": "USD",
            "due_date": "2026-12-01", "payment_condition": "Pay after verified delivery",
        },
        "assessment": {
            "status": "DISCREPANCY", "summary": "Short delivery",
            "issues": [{"code": "QUANTITY_SHORTFALL"}],
            "next_actions": ["Hold payment approval"], "human_review_required": True,
        },
        "evidence": [{
            "id": "e1", "kind": "quantity_received", "value": 80,
            "source": "warehouse_log", "created_at": "2026-11-10T10:00:00+00:00",
            "review_status": "VERIFIED", "review_history": [{"reviewer": "secret"}],
        }],
        "payment_policy": {"enabled": True, "max_amount": 500},
        "actions": [],
    }


def test_ai_provider_requires_explicit_api_key(monkeypatch):
    monkeypatch.delenv("NEARAI_API_KEY", raising=False)
    monkeypatch.delenv("NEAR_AI_KEY", raising=False)
    assert ai.is_configured() is False
    with pytest.raises(ai.AIConfigurationError):
        ai.generate_operational_brief(sample_record())


def test_ai_brief_uses_near_ai_cloud_and_returns_verified_attestation(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    content = {
        "summary": "80 of 100 boxes are recorded as received.",
        "why_it_matters": "The shortfall conflicts with the delivery condition.",
        "recommended_actions": ["Reconcile the missing 20 boxes."],
        "uncertainties": ["The evidence source is manually supplied."],
        "questions_for_owner": ["Has the warehouse confirmed the count?"],
    }
    captured = {}
    order = []

    def fake_preflight(**kwargs):
        order.append("preflight")
        assert kwargs["model"] == ai.NEAR_AI_MODEL
        return {"nonce": "ab" * 32, "gateway": {}, "models": []}

    def fake_post(url, **kwargs):
        order.append("post")
        captured["url"] = url
        captured["kwargs"] = kwargs
        return httpx.Response(
            200, request=httpx.Request("POST", url), json=completion(json.dumps(content)),
        )

    def fake_verify(**kwargs):
        order.append("verify")
        assert kwargs["preflight"]["nonce"] == "ab" * 32
        assert isinstance(kwargs["response_body"], bytes)
        assert b"chat-123" in kwargs["response_body"]
        return verified_provider_result()

    monkeypatch.setattr(ai, "preflight_near_ai_evidence", fake_preflight)
    monkeypatch.setattr(ai.httpx, "post", fake_post)
    monkeypatch.setattr(ai, "verify_near_ai_response", fake_verify)
    result = ai.generate_operational_brief(sample_record())
    assert order == ["preflight", "post", "verify"]
    assert result["provider"] == "NEAR AI Cloud"
    assert result["attestation_status"] == "PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED"
    assert result["brief"] == content
    assert captured["url"] == ai.NEAR_AI_URL
    headers = captured["kwargs"]["headers"]
    assert headers["x-no-aliasing"] == "true"
    assert headers["Accept-Encoding"] == "identity"
    request_body = json.loads(captured["kwargs"]["content"])
    assert request_body["model"] == ai.NEAR_AI_MODEL
    assert request_body["max_tokens"] == 700
    assert request_body["chat_template_kwargs"] == {"enable_thinking": False}
    user_context = json.loads(captured["kwargs"]["content"])["messages"][1]["content"]
    assert "review_history" not in user_context
    assert "secret" not in user_context


def test_ai_brief_rejects_malformed_model_output(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    monkeypatch.setattr(ai, "preflight_near_ai_evidence", lambda **kwargs: {"nonce": "ab" * 32})
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", ai.NEAR_AI_URL),
        json=completion("not json"),
    ))
    monkeypatch.setattr(ai, "verify_near_ai_response", lambda **kwargs: verified_provider_result())
    with pytest.raises(ai.AIProviderError):
        ai.generate_operational_brief(sample_record())

def test_ai_brief_fails_closed_when_cryptographic_verification_fails(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    monkeypatch.setattr(ai, "preflight_near_ai_evidence", lambda **kwargs: {"nonce": "ab" * 32})
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", ai.NEAR_AI_URL),
        json={"id": "chat-123", "choices": [{"message": {"content": "{}"}}]}
    ))
    monkeypatch.setattr(ai, "verify_near_ai_response", lambda **kwargs: (_ for _ in ()).throw(
        VerificationError("bad signature")
    ))
    with pytest.raises(ai.AIProviderError, match="failed closed"):
        ai.generate_operational_brief(sample_record())


def test_ai_request_disables_reasoning_and_rejects_empty_content(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    captured = {}
    monkeypatch.setattr(ai, "preflight_near_ai_evidence", lambda **kwargs: {"nonce": "ab" * 32})

    def fake_post(url, **kwargs):
        captured["body"] = json.loads(kwargs["content"])
        return httpx.Response(
            200,
            json=completion(None, finish_reason="length"),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    monkeypatch.setattr(ai, "verify_near_ai_response", lambda **kwargs: verified_provider_result())
    with pytest.raises(ai.AIProviderError, match="no final content"):
        ai.generate_operational_brief(sample_record())
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_incomplete_verification_does_not_return_a_valid_brief(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    monkeypatch.setattr(ai, "preflight_near_ai_evidence", lambda **kwargs: {"nonce": "ab" * 32})
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", ai.NEAR_AI_URL),
        json=completion(json.dumps({
            "summary": "Should not be shown",
            "why_it_matters": "Hidden",
            "recommended_actions": [],
            "uncertainties": [],
            "questions_for_owner": [],
        })),
    ))
    monkeypatch.setattr(ai, "verify_near_ai_response", lambda **kwargs: {
        "verified": True, "signature_kind": "provider_tee",
    })
    with pytest.raises(ai.AIProviderError, match="did not pass cryptographic verification"):
        ai.generate_operational_brief(sample_record())
