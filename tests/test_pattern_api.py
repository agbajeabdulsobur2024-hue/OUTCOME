from fastapi.testclient import TestClient

from app import main


def test_pattern_api_only_surfaces_repeated_verified_short_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        ids = []
        for quantity, received in [(100, 80), (50, 40)]:
            created = client.post('/api/commitments', json={
                'supplier': 'Acme Supplies',
                'description': 'Packaging',
                'quantity': quantity,
                'unit': 'boxes',
                'amount': 500,
                'currency': 'USD',
                'due_date': '2026-12-20',
            })
            cid = created.json()['commitment']['id']
            evidence = client.post(f'/api/commitments/{cid}/evidence', json={
                'kind': 'quantity_received', 'value': received,
            }).json()['evidence'][-1]
            client.post(f"/api/evidence/{evidence['id']}/reviews", json={
                'status': 'VERIFIED', 'reviewer': 'Owner',
            })
            ids.append(cid)

        patterns = client.get('/api/patterns').json()
        short = [p for p in patterns if p['pattern_type'] == 'REPEATED_SHORT_DELIVERY']
        assert len(short) == 1
        assert short[0]['supplier'] == 'Acme Supplies'
        assert short[0]['occurrences'] == 2
        assert len(short[0]['incidents']) == 2
        assert 'does not establish intent' in short[0]['interpretation']
