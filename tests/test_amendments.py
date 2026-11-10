from fastapi.testclient import TestClient

from app import main


def create_commitment(client):
    response = client.post('/api/commitments', json={
        'supplier': 'Acme Supplies',
        'description': 'Packaging materials',
        'quantity': 100,
        'unit': 'boxes',
        'amount': 500,
        'currency': 'USD',
        'due_date': '2026-12-01',
        'payment_condition': 'Pay after verified delivery',
    })
    assert response.status_code == 201
    return response.json()['commitment']['id']


def test_amendment_preserves_original_terms_and_appends_new_version(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        cid = create_commitment(client)
        evidence = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 80, 'note': 'Initial delivery',
        })
        assert evidence.status_code == 201

        response = client.post(f'/api/commitments/{cid}/amendments', json={
            'reason': 'Buyer and supplier agreed to revise the order quantity',
            'quantity': 80,
            'due_date': '2026-12-05',
        })
        assert response.status_code == 201
        record = response.json()
        assert record['commitment']['quantity'] == 80
        assert record['commitment']['due_date'] == '2026-12-05'
        assert len(record['commitment_versions']) == 2
        assert record['commitment_versions'][0]['version'] == 1
        assert record['commitment_versions'][1]['version'] == 2
        assert record['commitment_versions'][1]['reason'] == 'Buyer and supplier agreed to revise the order quantity'
        assert record['evidence'][0]['value'] == 80
        assert record['assessment']['status'] != 'DISCREPANCY'


def test_amendment_requires_reason_and_at_least_one_changed_term(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        cid = create_commitment(client)
        no_change = client.post(f'/api/commitments/{cid}/amendments', json={'reason': 'No change'})
        assert no_change.status_code == 422
        no_reason = client.post(f'/api/commitments/{cid}/amendments', json={
            'reason': '  ', 'quantity': 90,
        })
        assert no_reason.status_code == 422
        record = client.get(f'/api/commitments/{cid}').json()
        assert len(record['commitment_versions']) == 1
        assert record['commitment']['quantity'] == 100


def test_amount_amendment_cannot_exceed_existing_policy_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        cid = create_commitment(client)
        policy = client.put(f'/api/commitments/{cid}/payment-policy', json={
            'enabled': True, 'max_amount': 450,
            'allowed_destination': 'supplier-wallet-001',
        })
        assert policy.status_code == 200
        # The policy cap remains valid when the agreement is reduced below it only if
        # the owner first lowers the cap; this protects against a stale permissive policy.
        blocked = client.post(f'/api/commitments/{cid}/amendments', json={
            'reason': 'Reduce agreed amount',
            'amount': 400,
        })
        assert blocked.status_code == 409
        unchanged = client.get(f'/api/commitments/{cid}').json()
        assert unchanged['commitment']['amount'] == 500
        assert len(unchanged['commitment_versions']) == 1
