"""Run one fixed patch candidate through the local isolated sandbox."""

import json
import sys
import time
from pathlib import Path
from typing import Any

from issuelens.patch_sandbox import PatchSandboxTools, load_patch_sandbox_manifest

ROOT = Path(__file__).resolve().parents[1]


class NoRetrievalTools:
    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        raise PermissionError(f"retrieval tool {name} is not part of patch evaluation")


def run(case_id: str, output: Path) -> dict[str, Any]:
    campaign = json.loads(
        (ROOT / "data/patch-evaluation/campaign.json").read_text(encoding="utf-8")
    )
    cases = {item["case_id"]: item for item in campaign["cases"]}
    if case_id not in cases:
        raise ValueError("case_id is not in the frozen patch campaign")
    manifest = load_patch_sandbox_manifest(
        ROOT / "data/patch-validation" / case_id / "manifest.json"
    )
    if manifest.case_id != case_id or manifest.snapshot_commit != cases[case_id]["snapshot_commit"]:
        raise ValueError("patch manifest does not match the frozen case")
    if len(manifest.patches) > 1:
        raise ValueError("at most one candidate patch is allowed per run")
    if output.name not in {"attempt-1.json", "attempt-2.json"}:
        raise ValueError("only the first two patch attempts may be recorded")
    if output.name == "attempt-2.json" and not output.with_name("attempt-1.json").is_file():
        raise ValueError("attempt-2 requires an existing attempt-1 record")
    if output.exists():
        raise FileExistsError(f"run record already exists: {output}")
    started = time.perf_counter_ns()
    source = ROOT / ".retrieval-cache/campaign-sources" / case_id
    try:
        tools = PatchSandboxTools(
            manifest=manifest,
            source_repository=source,
            workspace_root=ROOT,
            retrieval_tools=NoRetrievalTools(),
        )
        tool_calls = 1
        first = tools.invoke("run_tests", {"test_id": manifest.reproduction_test_id})
        assert isinstance(first, dict)
        reproduced = first["status"] == "failed"
        patch_id = next(iter(manifest.patches), None)
        patch_status = "not_run"
        target_status = "not_run"
        regression_status = "not_run"
        if reproduced and patch_id is not None:
            tool_calls += 1
            applied = tools.invoke("submit_patch", {"patch_id": patch_id})
            assert isinstance(applied, dict)
            patch_status = "applied" if applied["accepted"] else "rejected"
            if patch_status == "applied":
                tool_calls += 1
                target = tools.invoke("run_tests", {"test_id": manifest.reproduction_test_id})
                assert isinstance(target, dict)
                target_status = str(target["status"])
                regression_id = next(
                    test_id for test_id in manifest.tests if test_id.endswith("regression")
                )
                tool_calls += 1
                regression = tools.invoke("run_tests", {"test_id": regression_id})
                assert isinstance(regression, dict)
                regression_status = str(regression["status"])
        summary = tools.finish_patch_validation()
        validation = {
            **summary,
            "reproduction": {
                "status": "reproduced" if reproduced else str(first["status"]),
                "test_id": manifest.reproduction_test_id,
            },
            "patch_application": {"status": patch_status, "patch_id": patch_id},
            "target_test": {"status": target_status, "test_id": manifest.reproduction_test_id},
            "regression_test": {"status": regression_status},
        }
        success = (
            reproduced
            and patch_status == "applied"
            and target_status == regression_status == "passed"
            and summary["source_repository"]["unchanged"]
            and summary["worktree"]["cleaned"]
        )
        result = {
            "case_id": case_id,
            "candidate_origin": (
                "no_candidate" if patch_id is None
                else "reference_repair" if patch_id == "reference-repair"
                else "fixed_candidate"
            ),
            "patch_validation": validation,
            "statistics": {
                "run_latency_ns": time.perf_counter_ns() - started,
                "tool_calls": tool_calls,
                "total_tokens": 0,
                "cost": "0",
            },
            "outcome": {"stop_reason": (
                "patch_validated" if success else
                "no_candidate" if patch_id is None else "patch_not_validated"
            )},
        }
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        tools.finish_patch_validation()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: run_patch_campaign_case.py CASE_ID OUTPUT.json")
    run(sys.argv[1], Path(sys.argv[2]))
