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
    }
    assert required.issubset(tables)
