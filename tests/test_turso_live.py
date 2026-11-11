import os

import pytest

from app import main


@pytest.mark.skipif(
    not (os.getenv("TURSO_DATABASE_URL") and os.getenv("TURSO_AUTH_TOKEN")),
    reason="Set Turso credentials to run the live remote-database smoke test.",
)
def test_turso_remote_schema_and_foreign_key_enforcement(monkeypatch):
    monkeypatch.setenv("OUTCOME_DB_BACKEND", "turso")
    main.init_db()

    with main.connect() as db:
        foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()
        assert foreign_keys[0] == 1

        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    required = {
        "commitments",
        "evidence",
        "commitment_versions",
        "assessment_history",
        "payment_policies",
        "evidence_reviews",
        "payment_proposals",
        "payment_policy_history",
        "commitment_actions",
        "action_events",
        "ai_briefs",
        "ai_request_limits",
        "workspaces",
        "users",
        "sessions",
        "audit_receipts",
        "request_limits",
        "account_onboarding",
    }
    assert required.issubset(tables)


@pytest.mark.skipif(
    not (os.getenv("TURSO_DATABASE_URL") and os.getenv("TURSO_AUTH_TOKEN")),
    reason="Set Turso credentials to run the live end-to-end workflow test.",
)
def test_turso_remote_commitment_payment_and_action_lifecycle(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OUTCOME_DB_BACKEND", "turso")
    commitment_id = None

    try:
        with TestClient(main.app) as client:
            created = client.post("/api/commitments", json={
                "supplier": "Turso Integration Fixture",
                "description": "Disposable remote adapter verification",
                "quantity": 100,
                "unit": "units",
                "amount": 500,
                "currency": "USD",
                "due_date": "2026-12-20",
                "payment_condition": "Pay after verified delivery",
            })
            assert created.status_code == 201, created.text
            commitment_id = created.json()["commitment"]["id"]

            quantity = client.post(f"/api/commitments/{commitment_id}/evidence", json={
                "kind": "quantity_received",
                "value": 100,
                "note": "Synthetic Turso integration fixture",
                "source": "test_fixture",
            })
            assert quantity.status_code == 201, quantity.text
            quantity_id = quantity.json()["evidence"][-1]["id"]

            invoice = client.post(f"/api/commitments/{commitment_id}/evidence", json={
                "kind": "invoice_amount",
                "value": 500,
                "note": "Synthetic Turso integration fixture",
                "source": "test_fixture",
            })
            assert invoice.status_code == 201, invoice.text
            invoice_id = invoice.json()["evidence"][-1]["id"]

            for evidence_id in (quantity_id, invoice_id):
                reviewed = client.post(f"/api/evidence/{evidence_id}/reviews", json={
                    "status": "VERIFIED",
                    "reviewer": "AUTOMATED_TEST",
                    "note": "Synthetic fixture verified by integration test",
                })
                assert reviewed.status_code == 201, reviewed.text

            policy = client.put(f"/api/commitments/{commitment_id}/payment-policy", json={
                "enabled": True,
                "max_amount": 500,
                "allowed_destination": "integration-test-destination",
                "require_full_delivery": True,
                "require_invoice_match": True,
                "require_verified_evidence": True,
                "allow_partial_payments": False,
            })
            assert policy.status_code == 200, policy.text

            proposal = client.post(
                f"/api/commitments/{commitment_id}/payment-proposals",
                json={
                    "amount": 500,
                    "currency": "USD",
                    "destination": "integration-test-destination",
                },
            )
            assert proposal.status_code == 201, proposal.text
            assert proposal.json()["proposal"]["status"] == "AWAITING_HUMAN_APPROVAL"

            action = client.post(f"/api/commitments/{commitment_id}/actions", json={
                "action_type": "RECONCILE_DELIVERY",
                "description": "Verify the supplier delivery record",
            })
            assert action.status_code == 201, action.text
            action_id = action.json()["id"]

            for event_type in ("APPROVED", "EXECUTION_REPORTED", "OUTCOME_VERIFIED"):
                event = client.post(f"/api/actions/{action_id}/events", json={
                    "event_type": event_type,
                    "actor": "AUTOMATED_TEST",
                    "note": "Synthetic lifecycle verification",
                    "evidence_id": quantity_id if event_type == "OUTCOME_VERIFIED" else None,
                })
                assert event.status_code == 201, event.text

            record = client.get(f"/api/commitments/{commitment_id}")
            assert record.status_code == 200, record.text
            body = record.json()
            assert body["payment_proposals"][0]["status"] == "AWAITING_HUMAN_APPROVAL"
            assert body["actions"][0]["status"] == "OUTCOME_VERIFIED"
            assert any(item["event_type"] == "PAYMENT_PROPOSAL_EVALUATED" for item in body["timeline"])
    finally:
        if commitment_id:
            with main.connect() as db:
                db.execute(
                    "DELETE FROM action_events WHERE action_id IN "
                    "(SELECT id FROM commitment_actions WHERE commitment_id=?)",
                    (commitment_id,),
                )
                db.execute("DELETE FROM commitment_actions WHERE commitment_id=?", (commitment_id,))
                db.execute("DELETE FROM payment_proposals WHERE commitment_id=?", (commitment_id,))
                db.execute("DELETE FROM payment_policy_history WHERE commitment_id=?", (commitment_id,))
                db.execute(
                    "DELETE FROM evidence_reviews WHERE evidence_id IN "
                    "(SELECT id FROM evidence WHERE commitment_id=?)",
                    (commitment_id,),
                )
                db.execute("DELETE FROM payment_policies WHERE commitment_id=?", (commitment_id,))
                db.execute("DELETE FROM assessment_history WHERE commitment_id=?", (commitment_id,))
                db.execute("DELETE FROM commitment_versions WHERE commitment_id=?", (commitment_id,))
                db.execute("DELETE FROM evidence WHERE commitment_id=?", (commitment_id,))
                db.execute("DELETE FROM commitments WHERE id=?", (commitment_id,))
