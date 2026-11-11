from fastapi.testclient import TestClient

from app import main


def test_demo_seed_builds_synthetic_scenario_and_never_overwrites_existing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        seeded = client.post('/api/demo/seed')
        assert seeded.status_code == 201
        body = seeded.json()
        assert body['synthetic_data'] is True
        assert len(body['commitments']) == 2
        assert len(body['patterns']) == 1
        assert body['patterns'][0]['pattern_type'] == 'REPEATED_SHORT_DELIVERY'
        first = next(x for x in body['commitments'] if x['commitment']['description'].startswith('Printer paper — purchase order PO-104'))
        assert first['commitment']['quantity'] == 50
        assert first['commitment']['unit'] == 'cartons'
        assert {item['kind']: item['value'] for item in first['evidence']}['quantity_received'] == 42
        assert first['payment_proposals'][0]['status'] == 'BLOCKED'
        assert first['actions'][0]['status'] == 'PROPOSED'
        assert all(ev['review_status'] == 'VERIFIED' for ev in first['evidence'])

        again = client.post('/api/demo/seed')
        assert again.status_code == 409
        assert len(client.get('/api/commitments').json()) == 2
