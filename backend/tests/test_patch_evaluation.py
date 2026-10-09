import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from issuelens.patch_evaluation import evaluate_patch_runs

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_case_without_run_remains_in_campaign_denominator() -> None:
    case_ids = tuple(f"starlette-issue-{number}" for number in range(100, 110))

    report = evaluate_patch_runs(case_ids, {})

    assert report["case_count"] == 10
    assert len(report["cases"]) == 10
    assert report["cases"][0]["case_id"] == "starlette-issue-100"
    assert report["cases"][0]["termination_reason"] == "run_missing"
    assert report["cases"][0]["run_time_ns"] == 0
    assert report["cases"][0]["tool_calls"] == 0
    assert report["cases"][0]["total_tokens"] == 0
    assert report["cases"][0]["cost"] == "0"
    assert report["cases"][0]["attempts"] == []
    assert report["metrics"]["reproduction_rate"] == 0.0


def test_existing_success_counts_once_against_ten_frozen_cases() -> None:
    case_ids = ("starlette-issue-1552",) + tuple(
        f"starlette-issue-{number}" for number in range(100, 109)
    )
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )

    report = evaluate_patch_runs(case_ids, {"starlette-issue-1552": run})

    assert report["metrics"]["reproduction_rate"] == 0.1
    assert report["metrics"]["patch_apply_rate"] == 0.1
    assert report["metrics"]["target_test_pass_rate"] == 0.1
    assert report["metrics"]["regression_free_rate"] == 0.1
    assert report["metrics"]["pass_at_1"] == 0.1
    assert report["metrics"]["pass_at_2"] == 0.1


def test_second_success_increases_pass_at_two_without_changing_pass_at_one() -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )
    failed_first = deepcopy(run)
    failed_first["patch_validation"]["target_test"]["status"] = "failed"
    case_ids = ("starlette-issue-1552",) + tuple(
        f"starlette-issue-{number}" for number in range(100, 109)
    )

    report = evaluate_patch_runs(
        case_ids, {"starlette-issue-1552": [failed_first, run]}
    )

    assert report["metrics"]["pass_at_1"] == 0.0
    assert report["metrics"]["pass_at_2"] == 0.1
    assert len(report["cases"][0]["attempts"]) == 2


def test_report_keeps_per_case_cost_latency_tools_and_attempt_results() -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )

    report = evaluate_patch_runs(("starlette-issue-1552",), {"starlette-issue-1552": run})
    case = report["cases"][0]

    assert case["run_time_ns"] == run["statistics"]["run_latency_ns"]
    assert case["tool_calls"] == 6
    assert case["total_tokens"] == 0
    assert case["cost"] == "0"
    assert case["termination_reason"] == "verified_root_cause"
    assert case["attempts"][0]["patch_id"] == "route-name-patch"
    assert case["attempts"][0]["target_test_status"] == "passed"
    assert case["attempts"][0]["regression_test_status"] == "passed"


def test_failed_reproduction_stays_in_the_report_with_its_reason() -> None:
    report = evaluate_patch_runs(
        ("starlette-issue-1355",),
        {
            "starlette-issue-1355": {
                "case_id": "starlette-issue-1355",
                "error": "snapshot_unavailable",
            }
        },
    )

    assert report["case_count"] == 1
    assert report["cases"][0]["termination_reason"] == "snapshot_unavailable"
    assert report["cases"][0]["attempts"] == []
    assert report["metrics"]["reproduction_rate"] == 0.0


def test_pass_at_one_rejects_source_repository_change_even_when_tests_pass() -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )
    run["patch_validation"]["source_repository"]["unchanged"] = False

    report = evaluate_patch_runs(("starlette-issue-1552",), {"starlette-issue-1552": run})

    assert report["metrics"]["pass_at_1"] == 0.0


def test_run_from_another_case_cannot_be_attributed_to_frozen_case() -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(ValueError, match="case_id"):
        evaluate_patch_runs(("starlette-issue-1355",), {"starlette-issue-1355": run})


def test_low_patch_success_is_described_as_exploratory_validation() -> None:
    case_ids = tuple(f"starlette-issue-{number}" for number in range(100, 110))

    report = evaluate_patch_runs(case_ids, {})

    assert report["project_summary"] == (
        "Patch validation is exploratory; automatic repair success is not claimed."
    )


def test_report_retains_each_attempt_diff_text() -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )
    run["patch_validation"]["patch"]["diff"] = "diff --git a/routing.py b/routing.py\n"

    report = evaluate_patch_runs(("starlette-issue-1552",), {"starlette-issue-1552": run})

    assert report["cases"][0]["attempts"][0]["diff"] == "diff --git a/routing.py b/routing.py\n"


def test_report_command_uses_the_preselected_ten_frozen_cases(tmp_path: Path) -> None:
    output = tmp_path / "patch-report.json"

    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_patch_evaluation.py"), str(output)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend/src")},
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["case_count"] == 10
    assert report["campaign_version"] == "starlette-patch-v1"
    assert report["cases"][0]["case_id"] == "starlette-issue-1355"
    assert report["cases"][0]["snapshot_commit"] == "f53faba229e3fa2844bc3753e233d9c1f54cca52"
    assert report["cases"][5]["case_id"] == "starlette-issue-1552"
    assert report["cases"][5]["attempts"][0]["target_passed"] is True


def test_report_reads_two_separate_attempt_records_for_one_case(tmp_path: Path) -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )
    failed_first = deepcopy(run)
    failed_first["patch_validation"]["target_test"]["status"] = "failed"
    case_directory = tmp_path / "starlette-issue-1552"
    case_directory.mkdir()
    (case_directory / "attempt-1.json").write_text(json.dumps(failed_first), encoding="utf-8")
    (case_directory / "attempt-2.json").write_text(json.dumps(run), encoding="utf-8")

    output = tmp_path / "report.json"
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_patch_evaluation.py"), str(output), str(tmp_path)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend/src")},
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["metrics"]["pass_at_1"] == 0.0
    assert report["metrics"]["pass_at_2"] == 0.1
    assert len(report["cases"][5]["attempts"]) == 2


def test_second_attempt_without_first_is_rejected(tmp_path: Path) -> None:
    run = json.loads(
        (ROOT / "data/patch-validation/starlette-issue-1552/run.json").read_text(
            encoding="utf-8"
        )
    )
    case_directory = tmp_path / "starlette-issue-1552"
    case_directory.mkdir()
    (case_directory / "attempt-2.json").write_text(json.dumps(run), encoding="utf-8")

    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_patch_evaluation.py"),
            str(tmp_path / "report.json"),
            str(tmp_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend/src")},
        capture_output=True,
        text=True,
    )

    assert process.returncode != 0
    assert "attempt-1" in process.stderr
