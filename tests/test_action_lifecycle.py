from fastapi.testclient import TestClient

from app import main


def test_action_lifecycle_requires_approval_and_verified_outcome_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme Supplies',
            'description': 'Packaging materials',
            'quantity': 100,
            'unit': 'boxes',
            'amount': 500,
            'currency': 'USD',
            'due_date': '2026-12-01',
        })
        cid = created.json()['commitment']['id']
        action = client.post(f'/api/commitments/{cid}/actions', json={
            'action_type': 'CONTACT_SUPPLIER',
            'description': 'Ask supplier to reconcile the missing 20 boxes',
        })
        assert action.status_code == 201
        action_id = action.json()['id']
        assert action.json()['status'] == 'PROPOSED'

        premature = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'EXECUTION_REPORTED', 'actor': 'Owner',
            'note': 'Tried to contact supplier',
        })
        assert premature.status_code == 409

        approved = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'APPROVED', 'actor': 'Owner',
            'note': 'Approved to contact supplier',
        })
        assert approved.status_code == 201
        reported = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'EXECUTION_REPORTED', 'actor': 'Owner',
            'note': 'Supplier contacted by phone',
        })
        assert reported.status_code == 201

        missing_proof = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'OUTCOME_VERIFIED', 'actor': 'Owner',
            'note': 'Supplier says delivery completed',
        })
        assert missing_proof.status_code == 422

        evidence = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 100,
            'note': 'Final signed receiving note',
        }).json()['evidence'][-1]
        unverified = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'OUTCOME_VERIFIED', 'actor': 'Owner',
            'evidence_id': evidence['id'], 'note': 'Attempted verification',
        })
        assert unverified.status_code == 422

        client.post(f"/api/evidence/{evidence['id']}/reviews", json={
            'status': 'VERIFIED', 'reviewer': 'Owner', 'note': 'Signed receiving note checked',
        })
        verified = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'OUTCOME_VERIFIED', 'actor': 'Owner',
            'evidence_id': evidence['id'], 'note': 'Quantity reconciled and independently checked',
        })
        assert verified.status_code == 201

        record = client.get(f'/api/commitments/{cid}').json()
        stored = next(item for item in record['actions'] if item['id'] == action_id)
        assert stored['status'] == 'OUTCOME_VERIFIED'
        assert [event['event_type'] for event in stored['events']] == [
            'APPROVED', 'EXECUTION_REPORTED', 'OUTCOME_VERIFIED',
        ]
        timeline_types = [event['event_type'] for event in record['timeline']]
        assert 'COMMITMENT_CREATED' in timeline_types
        assert 'EVIDENCE_RECORDED' in timeline_types
        assert 'EVIDENCE_REVIEWED' in timeline_types
        assert 'ACTION_PROPOSED' in timeline_types
        assert 'ACTION_EVENT' in timeline_types
        assert [event['created_at'] for event in record['timeline']] == sorted(
            event['created_at'] for event in record['timeline']
        )

        terminal = client.post(f'/api/actions/{action_id}/events', json={
            'event_type': 'EXECUTION_REPORTED', 'actor': 'Owner',
        })
        assert terminal.status_code == 409
