from fastapi.testclient import TestClient

from app import main


def setup_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    client = TestClient(main.app)
    client.__enter__()
    created = client.post('/api/commitments', json={
        'supplier': 'Northstar Supplies',
        'description': 'Office paper',
        'quantity': 50,
        'unit': 'reams',
        'amount': 250,
        'currency': 'USD',
        'due_date': '2026-12-20',
    })
    return client, created.json()['commitment']['id']


def test_evidence_reviews_are_append_only_and_latest_review_controls_state(tmp_path, monkeypatch):
    client, cid = setup_client(tmp_path, monkeypatch)
    try:
        evidence = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 50, 'note': 'Receiving slip',
        }).json()['evidence'][0]
        assert evidence['review_status'] == 'UNREVIEWED'

        first = client.post(f"/api/evidence/{evidence['id']}/reviews", json={
            'status': 'VERIFIED', 'reviewer': 'Owner', 'note': 'Count checked',
        })
        assert first.status_code == 201
        current = client.get(f'/api/commitments/{cid}').json()['evidence'][0]
        assert current['review_status'] == 'VERIFIED'
        assert current['reviewer'] == 'Owner'

        second = client.post(f"/api/evidence/{evidence['id']}/reviews", json={
            'status': 'REJECTED', 'reviewer': 'Owner', 'note': 'Source document later found unreliable',
        })
        assert second.status_code == 201
        current = client.get(f'/api/commitments/{cid}').json()['evidence'][0]
        assert current['review_status'] == 'REJECTED'
        assert current['review_note'] == 'Source document later found unreliable'

        with main.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM evidence_reviews WHERE evidence_id=?', (evidence['id'],)).fetchone()[0]
        assert count == 2
    finally:
        client.__exit__(None, None, None)


def test_latest_unreviewed_evidence_supersedes_old_verified_evidence(tmp_path, monkeypatch):
    client, cid = setup_client(tmp_path, monkeypatch)
    try:
        first = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 50,
        }).json()['evidence'][0]
        client.post(f"/api/evidence/{first['id']}/reviews", json={
            'status': 'VERIFIED', 'reviewer': 'Owner',
        })
        newer = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 10,
        }).json()['evidence'][-1]
        record = client.get(f'/api/commitments/{cid}').json()
        assert record['evidence'][-1]['review_status'] == 'UNREVIEWED'
        assert record['evidence'][0]['review_status'] == 'VERIFIED'
        assert newer['id'] == record['evidence'][-1]['id']
    finally:
        client.__exit__(None, None, None)
