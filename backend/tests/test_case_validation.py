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
