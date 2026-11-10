from fastapi.testclient import TestClient

from app import main


def test_audit_anchor_returns_only_a_privacy_minimized_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as client:
        created = client.post("/api/commitments", json={
            "supplier": "PRIVATE SUPPLIER NAME",
            "description": "PRIVATE PURCHASE DETAILS",
            "quantity": 12,
            "amount": 240,
            "due_date": "2026-12-10",
            "payment_condition": "Pay after verified delivery",
        })
        assert created.status_code == 201
        commitment_id = created.json()["commitment"]["id"]
        evidence = client.post(f"/api/commitments/{commitment_id}/evidence", json={
            "kind": "note",
            "note": "PRIVATE RECEIVING NOTE",
            "source": "private source",
        })
        assert evidence.status_code == 201

        response = client.get(f"/api/commitments/{commitment_id}/audit-anchor")
        assert response.status_code == 200
        body = response.json()
        assert len(body["payload_hash"]) == 64
        assert body["event_type"] == "COMMITMENT_STATE_ANCHORED"
        serialized = response.text
        assert "PRIVATE SUPPLIER NAME" not in serialized
        assert "PRIVATE PURCHASE DETAILS" not in serialized
        assert "PRIVATE RECEIVING NOTE" not in serialized
        assert "private source" not in serialized
