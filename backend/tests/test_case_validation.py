import json
from pathlib import Path
from typing import Any

from issuelens.cases import build_case, check_cases, validate_case

FIXTURES = Path(__file__).parent / "fixtures"


def valid_case() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "issue": {"source_url": "https://github.com/Kludex/starlette/issues/3497"},
        "investigation_cutoff": "2026-09-03T06:58:28Z",
        "repository_snapshot": {
            "commit": "snapshot-commit",
            "source_url": "https://github.com/Kludex/starlette/commit/snapshot-commit",
        },
        "allowed_evidence": {
            "read_access": "investigation",
            "items": [
                {
                    "source_url": "https://github.com/Kludex/starlette/issues/3497",
                    "observed_at": "2026-09-03T04:02:00Z",
                }
            ],
        },
        "repair_evidence": {
            "read_access": "evaluation_only",
            "pull_request": {
                "source_url": "https://github.com/Kludex/starlette/pull/1"
            },
            "commits": [
                {
                    "sha": "repair-commit",
                    "source_url": "https://github.com/Kludex/starlette/commit/repair-commit",
                }
            ],
            "changed_files": [
                {
                    "path": "starlette/example.py",
                    "source_url": "https://github.com/example.py",
                }
            ],
            "test_files": [
                {
                    "path": "tests/test_example.py",
                    "source_url": "https://github.com/test_example.py",
                }
            ],
        },
        "manual_review": {
            "relationship_basis": "GitHub timeline cross-reference",
            "investigation_cutoff_basis": "Closing PR creation time",
            "snapshot_basis": "Latest default-branch commit before cutoff",
            "exclusion_assessment": "No exclusion category applies",
            "repair_files": ["starlette/example.py"],
            "test_files": ["tests/test_example.py"],
            "future_information_risk": "No future information found",
            "verdict": "accepted",
            "notes": "Direct code and regression-test changes",
        },
    }


def test_future_allowed_evidence_fails_leak_check() -> None:
    case = valid_case()
    case["allowed_evidence"]["items"][0]["observed_at"] = "2026-09-03T06:58:29Z"

    assert validate_case(case) == [
        "allowed_evidence[0] is later than investigation_cutoff"
    ]


def test_allowed_and_repair_evidence_require_separate_read_permissions() -> None:
    case = valid_case()
    case["allowed_evidence"]["read_access"] = "evaluation_only"
    case["repair_evidence"]["read_access"] = "investigation"

    assert validate_case(case) == [
        "allowed_evidence.read_access must be investigation",
        "repair_evidence.read_access must be evaluation_only",
    ]


def test_dataset_leak_check_reports_injected_future_evidence(tmp_path: Path) -> None:
    case_path = build_case(
        FIXTURES / "github" / "complete-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case["allowed_evidence"]["items"][0]["observed_at"] = "2026-09-04T00:00:00Z"
    case_path.write_text(json.dumps(case), encoding="utf-8")

    assert check_cases(tmp_path) == [
        "starlette-issue-3497: allowed_evidence[0] is later than investigation_cutoff"
    ]


def test_case_schema_requires_traceable_sources_snapshot_tests_and_review(
    tmp_path: Path,
) -> None:
    case_path = build_case(
        FIXTURES / "github" / "complete-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case["schema_version"] = "0"
    case["issue"]["source_url"] = ""
    case["repository_snapshot"]["commit"] = ""
    case["repair_evidence"]["test_files"] = []
    del case["manual_review"]["relationship_basis"]

    assert validate_case(case) == [
        "schema_version must be 1.0",
        "issue.source_url is required",
        "repository_snapshot.commit is required",
        "repair_evidence.test_files must not be empty",
        "manual_review.relationship_basis is required",
    ]


def test_case_times_must_use_utc_iso_8601() -> None:
    case = valid_case()
    case["investigation_cutoff"] = "2026-09-03T14:58:28+08:00"

    assert validate_case(case) == [
        "investigation_cutoff must use UTC ISO 8601",
    ]


def test_all_persisted_case_times_must_use_utc_iso_8601() -> None:
    case = valid_case()
    case["repository_snapshot"]["committed_at"] = "2026-09-03T10:00:00+08:00"
    case["allowed_evidence"]["items"][0]["observed_at"] = (
        "2026-09-03T12:02:00+08:00"
    )
    case["repair_evidence"]["pull_request"]["created_at"] = (
        "2026-09-03T14:58:28+08:00"
    )

    assert validate_case(case) == [
        "repository_snapshot.committed_at must use UTC ISO 8601",
        "allowed_evidence[0].observed_at must use UTC ISO 8601",
        "repair_evidence.pull_request.created_at must use UTC ISO 8601",
    ]


def test_manual_review_must_record_each_review_decision() -> None:
    case = valid_case()
    case["manual_review"] = {"relationship_basis": "GitHub timeline cross-reference"}

    assert validate_case(case) == [
        "manual_review.investigation_cutoff_basis is required",
        "manual_review.snapshot_basis is required",
        "manual_review.exclusion_assessment is required",
        "manual_review.repair_files is required",
        "manual_review.test_files is required",
        "manual_review.future_information_risk is required",
        "manual_review.verdict is required",
        "manual_review.notes is required",
    ]


def test_manual_review_file_classification_must_match_repair_evidence() -> None:
    case = valid_case()
    case["manual_review"]["repair_files"] = ["starlette/other.py"]
    case["manual_review"]["test_files"] = ["tests/test_other.py"]

    assert validate_case(case) == [
        "manual_review.repair_files must match repair_evidence.changed_files",
        "manual_review.test_files must match repair_evidence.test_files",
    ]


def test_case_evidence_must_keep_traceable_source_urls() -> None:
    case = valid_case()
    case["repository_snapshot"]["source_url"] = ""
    case["allowed_evidence"]["items"][0]["source_url"] = ""
    case["repair_evidence"]["pull_request"]["source_url"] = ""
    case["repair_evidence"]["commits"][0]["source_url"] = ""
    case["repair_evidence"]["changed_files"][0]["source_url"] = ""
    case["repair_evidence"]["test_files"][0]["source_url"] = ""

    assert validate_case(case) == [
        "repository_snapshot.source_url is required",
        "allowed_evidence[0].source_url is required",
        "repair_evidence.pull_request.source_url is required",
        "repair_evidence.commits[0].source_url is required",
        "repair_evidence.changed_files[0].source_url is required",
        "repair_evidence.test_files[0].source_url is required",
    ]


def test_formal_gold_requires_evaluation_only_mechanism_and_evidence_locations(
    tmp_path: Path,
) -> None:
    gold_directory = tmp_path / "evaluation" / "gold"
    gold_directory.mkdir(parents=True)
    (gold_directory / "starlette-issue-3497.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "case_id": "starlette-issue-3497",
                "read_access": "investigation",
                "root_cause_files": [{"path": "starlette/testclient.py", "relevance": 2}],
                "changed_files": [{"path": "starlette/testclient.py"}],
                "test_files": [{"path": "tests/test_testclient.py"}],
                "root_cause_mechanism": "",
                "evidence_locations": [],
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "starlette-issue-3497 gold: read_access must be evaluation_only",
        "starlette-issue-3497 gold: root_cause_mechanism is required",
        "starlette-issue-3497 gold: evidence_locations must not be empty",
    ]


def test_repair_series_cannot_cross_development_and_primary_sets(tmp_path: Path) -> None:
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "splits": {
                    "development": ["starlette-issue-100"],
                    "primary": ["starlette-issue-101"]
                    + [f"starlette-issue-{number}" for number in range(102, 131)],
                    "stability": ["starlette-issue-101"]
                    + [f"starlette-issue-{number}" for number in range(102, 111)],
                },
                "repair_series": {
                    "starlette-repair-series-200": [
                        "starlette-issue-100",
                        "starlette-issue-101",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "starlette-repair-series-200 crosses development and primary sets"
    ]


def test_primary_evaluation_set_must_contain_exactly_thirty_cases(
    tmp_path: Path,
) -> None:
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "splits": {
                    "development": [],
                    "primary": [f"starlette-issue-{number}" for number in range(29)],
                    "stability": [f"starlette-issue-{number}" for number in range(10)],
                },
                "repair_series": {},
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "primary evaluation set must contain exactly 30 cases, found 29"
    ]


def test_stability_subset_must_contain_exactly_ten_cases(tmp_path: Path) -> None:
    primary = [f"starlette-issue-{number}" for number in range(30)]
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "splits": {
                    "development": [],
                    "primary": primary,
                    "stability": primary[:9],
                },
                "repair_series": {},
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "stability subset must contain exactly 10 cases, found 9"
    ]


def test_stability_subset_must_only_contain_primary_cases(tmp_path: Path) -> None:
    primary = [f"starlette-issue-{number}" for number in range(30)]
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "splits": {
                    "development": [],
                    "primary": primary,
                    "stability": primary[:9] + ["starlette-issue-999"],
                },
                "repair_series": {},
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "stability subset contains cases outside the primary set: starlette-issue-999"
    ]


def test_frozen_manifest_requires_auditable_build_metrics_hashes_and_leak_result(
    tmp_path: Path,
) -> None:
    primary = [f"starlette-issue-{number}" for number in range(30)]
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "frozen": True,
                "splits": {
                    "development": [],
                    "primary": primary,
                    "stability": primary[::3],
                },
                "repair_series": {},
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "frozen manifest requires build_metadata",
        "frozen manifest requires preregistered_metrics",
        "frozen manifest requires file_hashes",
        "frozen manifest requires leak_check",
    ]


def test_frozen_manifest_rejects_file_hash_drift(tmp_path: Path) -> None:
    primary = [f"starlette-issue-{number}" for number in range(30)]
    (tmp_path / "reviews.json").write_text("{}\n", encoding="utf-8")
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "frozen": True,
                "splits": {
                    "development": [],
                    "primary": primary,
                    "stability": primary[::3],
                },
                "repair_series": {},
                "build_metadata": {"builder_version": "cases-v2"},
                "preregistered_metrics": {"root_cause_recall_at_10": 0.7},
                "file_hashes": {"reviews.json": "0" * 64},
                "leak_check": {"status": "passed"},
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == ["file hash mismatch: reviews.json"]


def test_primary_evaluation_set_must_be_sorted_by_investigation_cutoff(
    tmp_path: Path,
) -> None:
    cases_directory = tmp_path / "cases"
    cases_directory.mkdir()
    primary = [f"starlette-issue-{number}" for number in range(30)]
    for number, case_id in enumerate(primary, start=1):
        case = valid_case()
        case["case_id"] = case_id
        case["investigation_cutoff"] = f"2026-01-{number:02d}T00:00:00Z"
        case["allowed_evidence"]["items"][0]["observed_at"] = (
            "2025-12-01T00:00:00Z"
        )
        (cases_directory / f"{case_id}.json").write_text(
            json.dumps(case), encoding="utf-8"
        )
    primary[0], primary[1] = primary[1], primary[0]
    evaluation_directory = tmp_path / "evaluation"
    evaluation_directory.mkdir()
    (evaluation_directory / "dataset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_version": "starlette-eval-v1",
                "splits": {
                    "development": [],
                    "primary": primary,
                    "stability": [f"starlette-issue-{number}" for number in range(0, 30, 3)],
                },
                "repair_series": {},
            }
        ),
        encoding="utf-8",
    )

    assert check_cases(tmp_path) == [
        "primary evaluation set must be sorted by investigation cutoff and case id"
    ]
