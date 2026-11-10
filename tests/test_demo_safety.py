from fastapi.testclient import TestClient

from app import main


def test_demo_reset_is_disabled_unless_explicitly_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.delenv('OUTCOME_ALLOW_DEMO_RESET', raising=False)
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme', 'description': 'Parts',
            'quantity': 10, 'amount': 100, 'due_date': '2026-12-01',
        })
        assert created.status_code == 201
        denied = client.post('/api/demo/reset')
        assert denied.status_code == 403
        assert len(client.get('/api/commitments').json()) == 1
