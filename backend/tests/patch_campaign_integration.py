import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_1355_fixed_candidate_runs_in_isolated_sandbox(tmp_path: Path) -> None:
    output = tmp_path / "attempt-1.json"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_patch_campaign_case.py"),
            "starlette-issue-1355",
            str(output),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend/src"), "LANGFUSE_ENABLED": "false"},
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    validation = result["patch_validation"]
    assert validation["reproduction"]["status"] == "reproduced"
    assert validation["patch_application"]["status"] == "applied"
    assert validation["target_test"]["status"] == "passed"
    assert validation["regression_test"]["status"] == "passed"
    assert validation["source_repository"]["unchanged"] is True
    assert validation["worktree"]["cleaned"] is True
    assert len(validation["executions"]) == 4
    assert result["candidate_origin"] == "reference_repair"
