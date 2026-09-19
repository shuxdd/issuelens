import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from issuelens import api
from issuelens.cases import build_case

FIXTURES = Path(__file__).parent / "fixtures"


def test_case_review_shows_sources_boundary_permissions_and_validation() -> None:
    response = TestClient(api.app).get("/cases/starlette-issue-1552")

    assert response.status_code == 200
    case = response.json()
    assert case["case_id"] == "starlette-issue-1552"
    assert case["issue"] == {
        "repository": "Kludex/starlette",
        "number": 1552,
        "title": 'Route naming introspection always return "method" for method endpoints ',
        "source_url": "https://github.com/Kludex/starlette/issues/1552",
    }
    assert case["investigation_cutoff"] == "2022-03-25T10:25:47Z"
    assert case["repository_snapshot"] == {
        "commit": "e086fc2da361767b532cf690e5203619bbae98aa",
        "committed_at": "2022-03-09T18:43:11Z",
        "source_url": (
            "https://github.com/Kludex/starlette/commit/"
            "e086fc2da361767b532cf690e5203619bbae98aa"
        ),
    }
    assert case["allowed_evidence"]["read_access"] == "investigation"
    assert case["allowed_evidence"]["references"] == [
        "https://github.com/Kludex/starlette/issues/1552"
    ]
    assert case["repair_evidence"]["read_access"] == "evaluation_only"
    assert case["repair_evidence"]["pull_request"] == {
        "number": 1553,
        "source_url": "https://github.com/Kludex/starlette/pull/1553",
        "created_at": "2022-03-25T10:25:47Z",
    }
    assert [item["path"] for item in case["repair_evidence"]["test_files"]] == [
        "tests/test_routing.py"
    ]
    assert case["manual_review"]["relationship_basis"].startswith("GitHub timeline")
    assert case["validation_status"] == "valid"


def test_case_review_reports_future_evidence_as_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_path = build_case(
        FIXTURES / "github" / "complete-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case["allowed_evidence"]["items"][0]["observed_at"] = "2026-09-04T00:00:00Z"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    monkeypatch.setattr(api, "CASES_DIRECTORY", tmp_path / "cases")

    response = TestClient(api.app).get("/cases/starlette-issue-3497")

    assert response.status_code == 200
    assert response.json()["validation_status"] == "invalid"
    assert response.json()["validation_errors"] == [
        "allowed_evidence[0] is later than investigation_cutoff"
    ]
