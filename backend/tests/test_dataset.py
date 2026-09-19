import json
import shutil
from pathlib import Path

from issuelens.cases import build_cases, check_cases

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def test_versioned_dataset_has_ten_traceable_leak_free_cases() -> None:
    assert {path.stem for path in (DATA / "cases").glob("*.json")} == {
        "starlette-issue-1552",
        "starlette-issue-2298",
        "starlette-issue-2306",
        "starlette-issue-2516",
        "starlette-issue-2625",
        "starlette-issue-2646",
        "starlette-issue-2692",
        "starlette-issue-2785",
        "starlette-issue-3357",
        "starlette-issue-3388",
    }
    assert check_cases(DATA) == []


def test_versioned_screening_report_explains_the_candidate_funnel() -> None:
    report = json.loads((DATA / "screening-report.json").read_text(encoding="utf-8"))

    assert report["funnel"] == {
        "collected": 17,
        "relationship_recovered": 14,
        "category_eligible": 14,
        "analyzable": 10,
        "time_slice_valid": 10,
        "manual_reviewed": 10,
        "accepted": 10,
    }
    assert {item["reason_code"] for item in report["excluded"]} == {
        "security",
        "documentation_only",
        "dependency_bot",
        "cross_repository_root_cause",
        "unanalyzable",
    }


def test_versioned_inputs_rebuild_the_dataset_idempotently(tmp_path: Path) -> None:
    cache = shutil.copytree(DATA / "github-cache", tmp_path / "github-cache")
    snapshot = shutil.copytree(
        DATA / "repository-snapshots" / "starlette", tmp_path / "snapshot"
    )
    reviews = Path(shutil.copy2(DATA / "reviews.json", tmp_path / "reviews.json"))
    output = tmp_path / "output"

    build_cases(cache, snapshot, output, reviews_path=reviews)
    first = {
        path.name: path.read_bytes()
        for path in sorted((output / "cases").glob("*.json"))
    }
    first_report = (output / "screening-report.json").read_bytes()

    paths = build_cases(cache, snapshot, output, reviews_path=reviews)

    assert len(paths) == 10
    assert {
        path.name: path.read_bytes()
        for path in sorted((output / "cases").glob("*.json"))
    } == first
    assert (output / "screening-report.json").read_bytes() == first_report
