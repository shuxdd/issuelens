import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from issuelens import api


def test_retrieval_review_shows_query_candidates_scores_and_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = {
        "schema_version": "1.0",
        "case_id": "starlette-issue-1552",
        "query": 'Route naming introspection always return "method"',
        "candidates": [
            {
                "rank": 1,
                "score": "0.912345",
                "commit": "e086fc2",
                "path": "starlette/routing.py",
                "start_line": 200,
                "end_line": 220,
                "source_type": "python_source",
                "index_version": "vector-v1",
                "content": "class Route:",
                "reference": (
                    "https://github.com/Kludex/starlette/blob/e086fc2/"
                    "starlette/routing.py#L200-L220"
                ),
            }
        ],
        "metadata": {"top_k": 10},
        "recall": {"root_cause_files": {"hits": 1, "total": 1, "recall": 1.0}},
    }
    result_path = tmp_path / "starlette-issue-1552" / "result.json"
    result_path.parent.mkdir()
    result_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(api, "RETRIEVAL_DIRECTORY", tmp_path)

    response = TestClient(api.app).get("/retrieval/cases/starlette-issue-1552")

    assert response.status_code == 200
    assert response.json() == result
