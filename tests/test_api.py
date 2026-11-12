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

        # The quarantine is scoped to the unowned global workspace only.
        demo = client.post('/api/demo/trials')
        assert demo.status_code in (200, 201)
        assert demo.cookies.get('outcome_demo')
