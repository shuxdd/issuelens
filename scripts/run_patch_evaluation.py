"""Summarize the fixed ten-case patch evaluation from local run records."""

import json
import sys
from pathlib import Path
from typing import Any

from issuelens.patch_evaluation import evaluate_patch_runs

ROOT = Path(__file__).resolve().parents[1]


def run(output_path: Path, *, runs_directory: Path | None = None) -> dict[str, Any]:
    dataset = json.loads(
        (ROOT / "data/evaluation/dataset-manifest.json").read_text(encoding="utf-8")
    )
    campaign = json.loads(
        (ROOT / "data/patch-evaluation/campaign.json").read_text(encoding="utf-8")
    )
    case_ids = tuple(item["case_id"] for item in campaign["cases"])
    if (
        len(case_ids) != 10
        or len(set(case_ids)) != 10
        or list(case_ids) != dataset["splits"]["primary"][:10]
        or campaign["dataset_version"] != dataset["dataset_version"]
    ):
        raise ValueError("patch campaign must match the first ten frozen primary cases")
    for item in campaign["cases"]:
        case = json.loads(
            (ROOT / "data/cases" / f"{item['case_id']}.json").read_text(encoding="utf-8")
        )
        if case["repository_snapshot"]["commit"] != item["snapshot_commit"]:
            raise ValueError("patch campaign snapshot differs from frozen case")
    runs: dict[str, dict[str, Any] | list[dict[str, Any]]] = {}
    run_root = runs_directory or ROOT / "data/patch-validation"
    for case_id in case_ids:
        case_root = run_root / case_id
        attempt_paths = [case_root / "attempt-1.json", case_root / "attempt-2.json"]
        if attempt_paths[1].is_file() and not attempt_paths[0].is_file():
            raise ValueError(f"{case_id}: attempt-2 requires attempt-1")
        if attempt_paths[0].is_file():
            runs[case_id] = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in attempt_paths
                if path.is_file()
            ]
        elif (case_root / "run.json").is_file():
            runs[case_id] = json.loads((case_root / "run.json").read_text(encoding="utf-8"))
    report = evaluate_patch_runs(case_ids, runs)
    report["dataset_version"] = dataset["dataset_version"]
    report["campaign_version"] = campaign["campaign_version"]
    report["environment"] = campaign["environment"]
    for row, item in zip(report["cases"], campaign["cases"], strict=True):
        row["snapshot_commit"] = item["snapshot_commit"]
        preflight = run_root / item["case_id"] / "preflight-failure.json"
        if preflight.is_file():
            try:
                row["preflight_failure_record"] = preflight.relative_to(ROOT).as_posix()
            except ValueError:
                row["preflight_failure_record"] = str(preflight)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: run_patch_evaluation.py OUTPUT.json [RUNS_DIRECTORY]")
    run(
        Path(sys.argv[1]),
        runs_directory=Path(sys.argv[2]) if len(sys.argv) == 3 else None,
    )
