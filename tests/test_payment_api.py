from fastapi.testclient import TestClient

from app import main


def make_commitment(client):
    response = client.post('/api/commitments', json={
        'supplier': 'Acme Supplies', 'description': 'Packaging materials',
        'quantity': 100, 'unit': 'boxes', 'amount': 500,
        'currency': 'USD', 'due_date': '2026-12-01',
    })
    assert response.status_code == 201
    return response.json()['commitment']['id']


def test_payment_proposal_is_blocked_until_policy_and_evidence_are_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        cid = make_commitment(client)
        proposal = client.post(f'/api/commitments/{cid}/payment-proposals', json={
            'amount': 500, 'currency': 'USD', 'destination': 'supplier-wallet-001',
        })
        assert proposal.status_code == 201
        assert proposal.json()['proposal']['status'] == 'BLOCKED'
        assert 'POLICY_DISABLED' in {r['code'] for r in proposal.json()['proposal']['reasons']}

        policy = client.put(f'/api/commitments/{cid}/payment-policy', json={
            'enabled': True, 'max_amount': 500,
            'allowed_destination': 'supplier-wallet-001',
            'require_full_delivery': True, 'require_invoice_match': True,
        })
        assert policy.status_code == 200
        client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 80, 'note': 'Short delivery',
        })
        client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'invoice_amount', 'value': 500, 'note': 'Invoice',
        })
        blocked = client.post(f'/api/commitments/{cid}/payment-proposals', json={
            'amount': 500, 'currency': 'USD', 'destination': 'supplier-wallet-001',
        })
        assert blocked.json()['proposal']['status'] == 'BLOCKED'
        assert 'DELIVERY_SHORTFALL' in {r['code'] for r in blocked.json()['proposal']['reasons']}

        client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 100, 'note': 'Balance delivered',
        })
        passed = client.post(f'/api/commitments/{cid}/payment-proposals', json={
            'amount': 500, 'currency': 'USD', 'destination': 'supplier-wallet-001',
        })
        result = passed.json()['proposal']
        assert result['status'] == 'AWAITING_HUMAN_APPROVAL'
        assert result['human_approval_required'] is True
        assert result['transfer_executed'] is False
        assert result['transaction_hash'] is None
        assert len(client.get(f'/api/commitments/{cid}/payment-proposals').json()) == 3


def test_payment_policy_cannot_exceed_commitment_value(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        cid = make_commitment(client)
        response = client.put(f'/api/commitments/{cid}/payment-policy', json={
            'enabled': True, 'max_amount': 501,
            'allowed_destination': 'supplier-wallet-001',
        })
        assert response.status_code == 422
