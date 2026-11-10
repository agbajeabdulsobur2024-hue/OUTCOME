import json

import httpx
import pytest

from app import ai


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


def test_ai_brief_uses_near_ai_cloud_and_returns_unverified_attestation_label(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    content = {
        "summary": "80 of 100 boxes are recorded as received.",
        "why_it_matters": "The shortfall conflicts with the delivery condition.",
        "recommended_actions": ["Reconcile the missing 20 boxes."],
        "uncertainties": ["The evidence source is manually supplied."],
        "questions_for_owner": ["Has the warehouse confirmed the count?"],
    }
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": json.dumps(content)}}],
        })

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    result = ai.generate_operational_brief(sample_record())
    assert result["provider"] == "NEAR AI Cloud"
    assert result["attestation_status"] == "NOT_INDEPENDENTLY_VERIFIED_BY_OUTCOME"
    assert result["brief"] == content
    assert captured["url"] == ai.NEAR_AI_URL
    assert captured["kwargs"]["json"]["model"] == ai.NEAR_AI_MODEL
    user_context = captured["kwargs"]["json"]["messages"][1]["content"]
    assert "review_history" not in user_context
    assert "secret" not in user_context


def test_ai_brief_rejects_malformed_model_output(monkeypatch):
    monkeypatch.setenv("NEARAI_API_KEY", "test-key")
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", ai.NEAR_AI_URL),
        json={"choices": [{"message": {"content": "not json"}}]}
    ))
    with pytest.raises(ai.AIProviderError):
        ai.generate_operational_brief(sample_record())
