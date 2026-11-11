import shutil
import subprocess
from pathlib import Path


def test_onboarding_state_and_landing_contract():
    node = shutil.which("node")
    assert node, "node is required to execute the onboarding regression"
    script = Path(__file__).resolve().parents[1] / "tests" / "onboarding.test.cjs"
    result = subprocess.run([node, str(script)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
