import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from issuelens import api


def test_supported_investigation_is_available_for_read_only_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "case_id": "starlette-issue-1552",
        "outcome": {"status": "supported", "stop_reason": "verified_root_cause"},
        "report": {"schema_version": "1.0"},
        "events": [],
        "statistics": {},
    }
    path = tmp_path / "starlette-issue-1552" / "run.json"
    path.parent.mkdir()
    path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(api, "INVESTIGATION_DIRECTORY", tmp_path)

    response = TestClient(api.app).get("/investigations/starlette-issue-1552")

    assert response.status_code == 200
    assert response.json() == result
