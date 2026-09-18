from fastapi.testclient import TestClient
from issuelens.api import app


def test_case_review_shows_sources_boundary_permissions_and_validation() -> None:
    response = TestClient(app).get("/cases/starlette-issue-3497")

    assert response.status_code == 200
    assert response.json() == {
        "case_id": "starlette-issue-3497",
        "issue": {
            "repository": "Kludex/starlette",
            "number": 3497,
            "title": "`testclient` imports the deprecated `anyio.abc.BlockingPortal` alias",
            "source_url": "https://github.com/Kludex/starlette/issues/3497",
        },
        "investigation_cutoff": "2026-09-03T06:58:28Z",
        "repository_snapshot": {
            "commit": "39fd0ffac25593fce39466320c9a666957ce8b8c",
            "source_url": "https://github.com/Kludex/starlette/commit/39fd0ffac25593fce39466320c9a666957ce8b8c",
        },
        "allowed_evidence": {
            "read_access": "investigation",
            "references": ["https://github.com/Kludex/starlette/issues/3497"],
        },
        "repair_evidence": {
            "read_access": "evaluation_only",
            "references": ["https://github.com/Kludex/starlette/pull/3498"],
        },
        "validation_status": "valid",
    }
