from fastapi.testclient import TestClient

from app import main


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
        monkeypatch.setattr(main, 'generate_operational_brief', lambda record: {
            'provider': 'NEAR AI Cloud',
            'model': 'Qwen/Qwen3.6-35B-A3B-FP8',
            'privacy_tier': 'TEE model',
            'attestation_status': 'NOT_INDEPENDENTLY_VERIFIED_BY_OUTCOME',
            'brief': {
                'summary': 'Awaiting receiving evidence.',
                'why_it_matters': 'Delivery is not yet verified.',
                'recommended_actions': ['Record receiving evidence.'],
                'uncertainties': ['No delivery evidence exists.'],
                'questions_for_owner': ['Has the supplier delivered?'],
            },
            'disclaimer': 'Advisory only.',
        })
        response = client.post(f'/api/commitments/{cid}/ai-brief')
        assert response.status_code == 201
        body = response.json()
        assert body['attestation_status'] == 'NOT_INDEPENDENTLY_VERIFIED_BY_OUTCOME'
        record = client.get(f'/api/commitments/{cid}').json()
        assert len(record['ai_brief_history']) == 1
        assert record['ai_brief_history'][0]['brief']['summary'] == 'Awaiting receiving evidence.'
