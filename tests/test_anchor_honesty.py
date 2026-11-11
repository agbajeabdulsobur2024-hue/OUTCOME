import shutil
import subprocess
from pathlib import Path

from app import anchors


def test_anchor_copy_matches_the_live_upgrade():
    node = shutil.which("node")
    assert node, "node is required to execute the audit-copy regression"
    script = Path(__file__).resolve().parents[1] / "tests" / "anchor_honesty.test.cjs"
    result = subprocess.run([node, str(script)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_contract_reports_the_deployed_upgrade():
    facts = anchors.public_contract()
    assert facts["code_hash"] == "5UNnZ4KvwoCxHt5ADhJnkCchP2h1xR6fnCXPP8c1rgpr"
    assert facts["upgrade_transaction"] == "FNzw1EPWRwepL2Bni1XH9Wz2nWJyqxeV4tPCpS5joXrc"
    assert facts["owner_account_id"] == "thewordweaver.testnet"
    assert facts["owner_event_id"] == "OUTCOME-DEMO-PO-104"
    assert facts["tester_signature_recorded"] is False
    assert facts["app_wallet_signature_recorded"] is False
    non_owner = facts["non_owner_event"]
    assert non_owner["account_id"] == "nocgykc7.thewordweaver.testnet"
    assert non_owner["sequence"] == 2
    assert non_owner["transaction"] == "7ZXWXUjPtWFQiYG6FKH91WimEmQWMwibSRoxifeweHk3"
    assert non_owner["status"] == "FINAL"
    assert non_owner["filed_through_app_sign_button"] is False
    assert non_owner["is_sample_digest"] is False
    recorded = [item for item in facts["known_transactions"] if item["label"] == "record_event"]
    assert {item["hash"] for item in recorded} >= {
        "3N8VVzFr3QLm6iJKvS2twM4VxCWZyrec9FfqP2zNb2kj",
        "7ZXWXUjPtWFQiYG6FKH91WimEmQWMwibSRoxifeweHk3",
    }
    assert facts["policy_after_upgrade"] == "any_account"
    upgrade = next(item for item in facts["known_transactions"] if item["label"] == "Upgrade")
    assert upgrade["hash"] == facts["upgrade_transaction"]
    assert upgrade["status"] == "FINAL"
