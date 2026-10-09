"""Campaign report for a fixed set of patch validation cases."""

from decimal import Decimal
from typing import Any


def evaluate_patch_runs(
    case_ids: tuple[str, ...], runs: dict[str, dict[str, Any] | list[dict[str, Any]]]
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    counts = {
        "reproduction_rate": 0,
        "patch_apply_rate": 0,
        "target_test_pass_rate": 0,
        "regression_free_rate": 0,
        "pass_at_1": 0,
        "pass_at_2": 0,
    }
    for case_id in case_ids:
        records = runs.get(case_id)
        if records is None or (isinstance(records, dict) and "error" in records):
            cases.append({
                "case_id": case_id,
                "termination_reason": (
                    "run_missing" if records is None else records["error"]
                ),
                "run_time_ns": 0,
                "tool_calls": 0,
                "total_tokens": 0,
                "cost": "0",
                "attempts": [],
            })
            continue
        attempts = [records] if isinstance(records, dict) else records
        if len(attempts) > 2:
            raise ValueError("a case may have at most two patch attempts")
        results = []
        for run in attempts:
            if run["case_id"] != case_id:
                raise ValueError("run case_id does not match frozen case")
            validation = run["patch_validation"]
            results.append({
                "reproduced": validation["reproduction"]["status"] == "reproduced",
                "applied": validation["patch_application"]["status"] == "applied",
                "target_passed": validation["target_test"]["status"] == "passed",
                "regression_passed": validation["regression_test"]["status"] == "passed",
                "source_unchanged": validation["source_repository"]["unchanged"],
                "worktree_cleaned": validation["worktree"]["cleaned"],
                "candidate_origin": run.get("candidate_origin", "legacy_record"),
                "patch_id": validation["patch_application"]["patch_id"],
                "target_test_status": validation["target_test"]["status"],
                "regression_test_status": validation["regression_test"]["status"],
                "diff": validation["patch"].get("diff"),
                "diff_sha256": validation["patch"].get("diff_sha256"),
            })
        successes = [
            item["reproduced"] and item["applied"]
            and item["target_passed"] and item["regression_passed"]
            and item["source_unchanged"] and item["worktree_cleaned"]
            for item in results
        ]
        counts["reproduction_rate"] += any(item["reproduced"] for item in results)
        counts["patch_apply_rate"] += any(item["applied"] for item in results)
        counts["target_test_pass_rate"] += any(item["target_passed"] for item in results)
        counts["regression_free_rate"] += any(
            item["target_passed"] and item["regression_passed"] for item in results
        )
        counts["pass_at_1"] += bool(successes and successes[0])
        counts["pass_at_2"] += any(successes)
        cases.append({
            "case_id": case_id,
            "termination_reason": attempts[-1]["outcome"]["stop_reason"],
            "run_time_ns": sum(run["statistics"]["run_latency_ns"] for run in attempts),
            "tool_calls": sum(run["statistics"]["tool_calls"] for run in attempts),
            "total_tokens": sum(run["statistics"]["total_tokens"] for run in attempts),
            "cost": str(sum(Decimal(run["statistics"]["cost"]) for run in attempts)),
            "attempts": results,
        })
    metrics = {name: count / len(case_ids) for name, count in counts.items()}
    return {
        "case_count": len(case_ids),
        "cases": cases,
        "metrics": metrics,
        "project_summary": (
            "Patch validation is exploratory; automatic repair success is not claimed."
            if metrics["pass_at_2"] < 0.5
            else "Fixed candidate validation on ten frozen cases; no autonomous repair claim."
        ),
    }
