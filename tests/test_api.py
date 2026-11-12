from fastapi.testclient import TestClient

from app import main


def test_commitment_versions_and_assessment_snapshots_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')

    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme Supplies',
            'description': 'Packaging materials',
            'quantity': 100,
            'unit': 'boxes',
            'amount': 500,
            'currency': 'usd',
            'due_date': '2026-12-01',
            'payment_condition': 'Pay after verified delivery',
        })
        assert created.status_code == 201
        record = created.json()
        cid = record['commitment']['id']
        assert record['commitment']['currency'] == 'USD'
        assert len(record['commitment_versions']) == 1
        assert record['commitment_versions'][0]['version'] == 1
        assert len(record['assessment_history']) == 1
        assert record['assessment']['status'] == 'AWAITING_EVIDENCE'

        evidence = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 80,
            'note': 'Receiving note DN-104', 'source': 'warehouse_log',
        })
        assert evidence.status_code == 201
        updated = evidence.json()
        assert len(updated['evidence']) == 1
        assert updated['evidence'][0]['source'] == 'warehouse_log'
        assert len(updated['assessment_history']) == 2
        assert updated['assessment']['status'] == 'DISCREPANCY'
        assert updated['assessment_history'][-1]['status'] == 'DISCREPANCY'


def test_note_evidence_rejects_numeric_value(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Acme', 'description': 'Parts', 'quantity': 10,
            'amount': 100, 'due_date': '2026-12-01',
        })
        cid = created.json()['commitment']['id']
        response = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'note', 'value': 3, 'note': 'Should be text only'
        })
        assert response.status_code == 422



def test_hosted_global_scope_quarantines_legacy_null_workspace_records(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setenv('OUTCOME_DB_BACKEND', 'sqlite')
    monkeypatch.setenv('VERCEL', '1')

    with TestClient(main.app) as client:
        # Simulate a legacy record without changing or deleting production data.
        with main.connect() as db:
            db.execute(
                '''INSERT INTO commitments
                   (id, supplier, description, quantity, unit, amount, currency,
                    due_date, payment_condition, created_at, workspace_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)''',
                ('legacy-global-1', 'Legacy supplier', 'Legacy sample', 1, 'unit',
                 10, 'USD', '2026-12-01', 'After verification', main.utc_now()),
            )

        assert client.get('/api/commitments').status_code == 200
        assert client.get('/api/commitments').json() == []
        assert client.get('/api/commitments/legacy-global-1').status_code == 404

        payload = {
            'supplier': 'New supplier', 'description': 'Test order',
            'quantity': 2, 'unit': 'boxes', 'amount': 20,
            'currency': 'USD', 'due_date': '2026-12-10',
            'payment_condition': 'Pay after verified delivery',
        }
        blocked = client.post('/api/commitments', json=payload)
        assert blocked.status_code == 409
        assert client.get('/api/commitments').json() == []

        # The quarantine is scoped to the unowned global workspace only.
        demo = client.post('/api/demo/trials')
        assert demo.status_code == 201
        assert demo.cookies.get('outcome_demo')
        assert demo.json()['synthetic_data'] is True
        assert len(demo.json()['commitments']) == 2
        assert len(client.get('/api/commitments').json()) == 2

        created = client.post('/api/commitments', json=payload)
        assert created.status_code == 201
        assert len(client.get('/api/commitments').json()) == 3

        # Removing the demo cookie returns to the empty quarantine scope.
        client.cookies.delete('outcome_demo')
        assert client.get('/api/commitments').json() == []
        assert client.get('/api/commitments/legacy-global-1').status_code == 404



def test_evidence_review_reassesses_and_duplicate_taps_do_not_duplicate_history(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'DB_PATH', tmp_path / 'test.db')

    with TestClient(main.app) as client:
        created = client.post('/api/commitments', json={
            'supplier': 'Northwind Supplies',
            'description': 'Delivery of boxes',
            'quantity': 10,
            'unit': 'boxes',
            'amount': 80,
            'currency': 'USD',
            'due_date': '2026-12-20',
            'payment_condition': 'Complete and verify delivery before payment',
        })
        assert created.status_code == 201
        cid = created.json()['commitment']['id']

        evidence = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 8,
            'note': 'Eight boxes received', 'source': 'manual',
        })
        assert evidence.status_code == 201
        evidence_id = evidence.json()['evidence'][0]['id']
        assert evidence.json()['assessment']['status'] == 'DISCREPANCY'
        assert len(evidence.json()['assessment_history']) == 2

        review_payload = {
            'status': 'VERIFIED',
            'reviewer': 'Business owner',
            'note': 'Manually checked',
        }
        verified = client.post(f'/api/evidence/{evidence_id}/reviews', json=review_payload)
        assert verified.status_code == 201
        after_review = client.get(f'/api/commitments/{cid}').json()
        assert len(after_review['assessment_history']) == 3
        assert after_review['assessment_history'][-1]['status'] == 'DISCREPANCY'

        repeated = client.post(f'/api/evidence/{evidence_id}/reviews', json=review_payload)
        assert repeated.status_code == 201
        assert repeated.json()['duplicate'] is True
        unchanged = client.get(f'/api/commitments/{cid}').json()
        assert len(unchanged['assessment_history']) == 3
        assert len(unchanged['evidence'][0]['review_history']) == 1

        # A newer unverified observation cannot clear the verified shortfall.
        newer = client.post(f'/api/commitments/{cid}/evidence', json={
            'kind': 'quantity_received', 'value': 10,
            'note': 'Supplier says remaining boxes arrived', 'source': 'manual',
        })
        assert newer.status_code == 201
        assert newer.json()['assessment']['status'] == 'DISCREPANCY'

        # Only verification of the newer observation changes the governing result.
        newer_id = newer.json()['evidence'][-1]['id']
        final_review = client.post(
            f'/api/evidence/{newer_id}/reviews',
            json={'status': 'VERIFIED', 'reviewer': 'Business owner', 'note': 'Checked delivery'},
        )
        assert final_review.status_code == 201
        final_record = client.get(f'/api/commitments/{cid}').json()
        assert final_record['assessment']['status'] == 'ON_TRACK'
        assert final_record['assessment_history'][-1]['status'] == 'ON_TRACK'
