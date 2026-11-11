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
    assert facts["policy_after_upgrade"] == "any_account"
    upgrade = next(item for item in facts["known_transactions"] if item["label"] == "Upgrade")
    assert upgrade["hash"] == facts["upgrade_transaction"]
    assert upgrade["status"] == "FINAL"
