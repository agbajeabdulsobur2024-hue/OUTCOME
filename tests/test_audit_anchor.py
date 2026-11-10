import re

from fastapi.testclient import TestClient

from app import main


def test_audit_anchor_exports_only_opaque_reference_and_structured_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "audit-anchor.db")

    with TestClient(main.app) as client:
        created = client.post("/api/commitments", json={
            "supplier": "Secret Supplier Ltd",
            "description": "Confidential shipment of medical equipment",
            "quantity": 100,
            "unit": "boxes",
            "amount": 500,
            "currency": "USD",
            "due_date": "2026-12-01",
            "payment_condition": "Only pay after private warehouse confirmation",
        })
        assert created.status_code == 201
        commitment_id = created.json()["commitment"]["id"]

        evidence = client.post(f"/api/commitments/{commitment_id}/evidence", json={
            "kind": "note",
            "value": None,
            "note": "PRIVATE NOTE: invoice 9911 and bank account details",
            "source": "PRIVATE SOURCE: named warehouse manager",
        })
        assert evidence.status_code == 201

        response = client.get(f"/api/commitments/{commitment_id}/audit-anchor")
        assert response.status_code == 200
        payload = response.json()

        assert payload["commitment_id"].startswith("outcome-")
        assert payload["commitment_id"] != commitment_id
        assert re.fullmatch(r"[a-f0-9]{64}", payload["payload_hash"])
        serialized = str(payload)
        for private_value in (
            "Secret Supplier Ltd",
            "Confidential shipment",
            "private warehouse confirmation",
            "PRIVATE NOTE",
            "PRIVATE SOURCE",
            "invoice 9911",
            "bank account details",
            "named warehouse manager",
        ):
            assert private_value not in serialized
        assert "supplier identity" in payload["privacy"]
