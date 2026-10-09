from pathlib import Path

import issuelens.api as demo_api
import pytest
from fastapi.testclient import TestClient
from issuelens.api import app


def test_demo_lists_exactly_five_frozen_investigation_outcomes() -> None:
    response = TestClient(app).get("/demo/cases")

    assert response.status_code == 200
    assert [(case["case_id"], case["outcome"]) for case in response.json()["cases"]] == [
        ("starlette-issue-1552", "supported"),
        ("starlette-issue-886", "supported"),
        ("starlette-issue-1355", "insufficient_evidence"),
        ("starlette-issue-1552-budget", "budget_exhausted"),
        ("safe-dangerous-01", "unsafe_request"),
    ]


def test_demo_replays_supported_report_with_evidence_and_patch_verification() -> None:
    response = TestClient(app).get("/demo/cases/starlette-issue-1552")

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["outcome"]["status"] == "supported"
    citation = body["run"]["report"]["root_cause_hypotheses"][0]["evidence"][0]
    assert citation["commit"] == "e086fc2da361767b532cf690e5203619bbae98aa"
    assert citation["path"] == "starlette/schemas.py"
    assert citation["start_line"] == 66
    assert citation["excerpt"]
    assert body["patch_validation"]["attempts"][0]["candidate_origin"] == "fixed_candidate"
    assert body["patch_validation"]["attempts"][0]["target_passed"] is True
    assert body["patch_validation"]["attempts"][0]["regression_passed"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/demo/cases/starlette-issue-0000",
        "/demo/cases/https:%2F%2Fexample.com",
        "/demo/cases/starlette-issue-1552?command=cat%20.env",
    ],
)
def test_demo_rejects_unlisted_cases_and_user_supplied_inputs(path: str) -> None:
    response = TestClient(app).get(path)

    assert response.status_code in (400, 404)
    assert "Choose" in response.json()["detail"] or "input" in response.json()["detail"]


def test_public_demo_mode_exposes_only_health_and_read_only_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUELENS_PUBLIC_DEMO", "true")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/demo/cases").status_code == 200
    assert client.get("/cases/starlette-issue-1552").status_code == 404
    assert client.post("/demo/cases", json={"command": "cat .env"}).status_code == 405


def test_demo_rate_limits_each_client_ip_with_http_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUELENS_DEMO_RATE_LIMIT", "2")
    first = TestClient(app, client=("203.0.113.71", 5000))
    second = TestClient(app, client=("203.0.113.72", 5000))

    assert first.get("/demo/cases").status_code == 200
    assert first.get("/demo/cases").status_code == 200
    limited = first.get("/demo/cases")
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert second.get("/demo/cases").status_code == 200


def test_demo_polling_recovers_recorded_events_after_a_sequence() -> None:
    response = TestClient(app).get("/demo/cases/starlette-issue-1552/progress/1")

    assert response.status_code == 200
    body = response.json()
    assert body["events"][0]["sequence"] == 2
    assert body["events"][-1]["outcome"] == "supported"
    assert body["complete"] is True


def test_demo_streams_the_recorded_timeline_as_sse() -> None:
    response = TestClient(app).get("/demo/cases/starlette-issue-1552/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"sequence": 1' in response.text
    assert "event: done" in response.text


def test_demo_returns_429_when_global_stream_capacity_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUELENS_DEMO_MAX_STREAMS", "0")

    response = TestClient(app).get("/demo/cases/starlette-issue-1552/events")

    assert response.status_code == 429
    assert response.headers["Retry-After"]


def test_demo_applies_global_concurrency_limit_to_json_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUELENS_DEMO_MAX_CONCURRENT", "0")

    response = TestClient(app).get("/demo/cases")

    assert response.status_code == 429
    assert response.headers["Retry-After"]


def test_every_demo_case_replays_the_declared_terminal_outcome() -> None:
    client = TestClient(app)

    for case in client.get("/demo/cases").json()["cases"]:
        response = client.get(f'/demo/cases/{case["case_id"]}')
        assert response.status_code == 200, case["case_id"]
        assert response.json()["run"]["outcome"]["status"] == case["outcome"]


def test_missing_frozen_replay_returns_actionable_service_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(demo_api, "DATA_DIRECTORY", tmp_path)

    response = TestClient(app).get("/demo/cases/starlette-issue-1552")

    assert response.status_code == 503
    assert response.json()["detail"] == "Replay unavailable. Please try again later."
