import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from tests.test_public_hackathon import login, use_db


def test_onboarding_state_and_landing_contract():
    node = shutil.which("node")
    assert node, "node is required to execute the onboarding regression"
    script = Path(__file__).resolve().parents[1] / "tests" / "onboarding.test.cjs"
    result = subprocess.run([node, str(script)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_welcome_state_is_not_only_local():
    node = shutil.which("node")
    assert node, "node is required to execute the welcome regression"
    script = Path(__file__).resolve().parents[1] / "tests" / "welcome.test.cjs"
    result = subprocess.run([node, str(script)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_account_onboarding_is_remembered_per_user(tmp_path, monkeypatch):
    use_db(tmp_path, monkeypatch)
    keys = {}
    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        visitor = alice.get("/api/session")
        assert visitor.json()["onboarding"] == {"status": "anonymous", "step": 0}
        assert alice.put("/api/onboarding", json={"status": "completed", "step": 5}).status_code == 401

        login(alice, monkeypatch, "alice.testnet", keys)
        login(bob, monkeypatch, "bob.testnet", keys)
        assert alice.get("/api/session").json()["onboarding"]["status"] == "new"
        assert bob.get("/api/session").json()["onboarding"]["status"] == "new"

        saved = alice.put("/api/onboarding", json={"status": "in_progress", "step": 3})
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"status": "in_progress", "step": 3}
        assert bob.get("/api/session").json()["onboarding"]["status"] == "new"

        assert alice.put("/api/onboarding", json={"status": "completed", "step": 5}).status_code == 200
        assert bob.put("/api/onboarding", json={"status": "dismissed", "step": 1}).status_code == 200
        assert alice.put("/api/onboarding", json={"status": "new", "step": 0}).status_code == 422
        assert alice.put("/api/onboarding", json={"status": "completed", "step": 9}).status_code == 422

    with TestClient(main.app) as returning:
        login(returning, monkeypatch, "alice.testnet", keys)
        assert returning.get("/api/session").json()["onboarding"] == {"status": "completed", "step": 5}
    with TestClient(main.app) as returning_bob:
        login(returning_bob, monkeypatch, "bob.testnet", keys)
        assert returning_bob.get("/api/session").json()["onboarding"] == {"status": "dismissed", "step": 1}
