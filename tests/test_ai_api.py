import json
import sqlite3

from fastapi.testclient import TestClient

from app import main


def displayable_brief(summary="Awaiting receiving evidence."):
    return {
        "provider": "NEAR AI Cloud",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "privacy_tier": "TEE model",
        "attestation_status": "PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED",
        "verification": {
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
        },
        "brief": {
            "summary": summary,
            "why_it_matters": "Delivery is not yet verified.",
            "recommended_actions": ["Record receiving evidence."],
            "uncertainties": ["No delivery evidence exists."],
            "questions_for_owner": ["Has the supplier delivered?"],
        },
        "disclaimer": "Advisory only.",
    }


def test_ai_brief_is_optional_and_persisted_as_advisory_output(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.delenv('NEARAI_API_KEY', raising=False)
    monkeypatch.delenv('NEAR_AI_KEY', raising=False)
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme', 'description': 'Boxes',
            'quantity': 100, 'amount': 500, 'due_date': '2026-12-01',
        })
        cid = created.json()['commitment']['id']
        assert client.get('/api/ai/status').json()['configured'] is False
        disabled = client.post(f'/api/commitments/{cid}/ai-brief')
        assert disabled.status_code == 503

        monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
        monkeypatch.setattr(main, 'generate_operational_brief', lambda record: displayable_brief())
        response = client.post(f'/api/commitments/{cid}/ai-brief')
        assert response.status_code == 201
        body = response.json()
        assert body['attestation_status'] == 'PROVIDER_TEE_SIGNATURE_AND_MODEL_TDX_VERIFIED'
        record = client.get(f'/api/commitments/{cid}').json()
        assert len(record['ai_brief_history']) == 1
        assert record['ai_brief_history'][0]['displayable'] is True
        assert record['ai_brief_history'][0]['brief']['brief']['summary'] == 'Awaiting receiving evidence.'



def test_ai_brief_endpoint_enforces_per_client_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setenv('NEARAI_API_KEY', 'test-key')
    monkeypatch.setenv('OUTCOME_AI_RATE_LIMIT_PER_HOUR', '2')
    monkeypatch.setattr(main, 'generate_operational_brief', lambda record: displayable_brief('Summary'))
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme', 'description': 'Boxes',
            'quantity': 100, 'amount': 500, 'due_date': '2026-12-01',
        })
        cid = created.json()['commitment']['id']
        assert client.post(f'/api/commitments/{cid}/ai-brief').status_code == 201
        assert client.post(f'/api/commitments/{cid}/ai-brief').status_code == 201
        assert client.post(f'/api/commitments/{cid}/ai-brief').status_code == 429


def test_unverified_stored_brief_is_not_displayed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.delenv('NEARAI_API_KEY', raising=False)
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme', 'description': 'Boxes',
            'quantity': 100, 'amount': 500, 'due_date': '2026-12-01',
        })
        cid = created.json()['commitment']['id']
        secret = 'SECRET_UNVERIFIED_BRIEF'
        stored = {'brief': {'summary': secret}, 'verification': {'verified': True}}
        with sqlite3.connect(tmp_path / 'test.db') as db:
            db.execute(
                'INSERT INTO ai_briefs (id, commitment_id, provider, model, brief_json, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                ('brief-1', cid, 'NEAR AI Cloud', 'Qwen/Qwen3.6-35B-A3B-FP8',
                 json.dumps(stored), '2026-11-11T00:00:00+00:00'),
            )
        record = client.get(f'/api/commitments/{cid}').json()
        rendered = json.dumps(record)
        assert secret not in rendered
        assert record['ai_brief_history'][0]['displayable'] is False
        assert record['ai_brief_history'][0]['brief'] is None
