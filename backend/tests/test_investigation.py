import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from issuelens.investigation import InvestigationBudget, ObservationContext
from issuelens.report import ModelResponse, ReportConfig
from issuelens.retrieval import RerankedCandidate, RerankedRun, RetrievalCandidate, RetrievalInput


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_calls": 21},
        {"retrieval_rewrites": 3},
        {"root_cause_hypotheses": 4},
        {"patch_attempts": 3},
        {"run_time_ns": 300_000_000_001},
        {"tool_calls": -1},
        {"retrieval_rewrites": -1},
        {"root_cause_hypotheses": -1},
        {"patch_attempts": -1},
        {"run_time_ns": -1},
    ],
)
def test_investigation_budget_cannot_exceed_or_bypass_specification(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="budget"):
        InvestigationBudget(**overrides)


def _fixed_investigation(
    tmp_path: Path, *, invalid_tool_candidate: bool = False, empty_tool_results: bool = False,
    observed_messages: list[list[dict[str, str]]] | None = None,
    unverified_report: bool = False,
    clock: Callable[[], int] | None = None,
    mode: Literal["evaluation", "interactive"] = "evaluation",
    unsafe_tool_request: bool = False,
    observed_tool_calls: list[str] | None = None,
    failing_tool: str | None = None,
    budget: InvestigationBudget | None = None,
    run_clock: Callable[[], int] | None = None,
    structured_failures: int = 0,
    observed_model_calls: list[int] | None = None,
    observation: ObservationContext | None = None,
) -> dict[str, Any]:
    from issuelens.investigation import run_investigation

    source = tmp_path / "starlette" / "routing.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def get_name(endpoint):\n    return endpoint.__class__.__name__\n", encoding="utf-8"
    )
    (tmp_path / ".issuelens-snapshot.json").write_text(
        '{"repository":"Kludex/starlette","commit":"abc123"}', encoding="utf-8"
    )
    case = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123",
        'Route naming introspection returns "method"',
        "https://github.com/Kludex/starlette/issues/1",
    )
    candidate = RerankedCandidate(
        rank=1, score="0.9", commit="abc123", path="starlette/routing.py",
        start_line=1, end_line=2, source_type="python_source", index_version="v1",
        content="def get_name(endpoint):\n    return endpoint.__class__.__name__",
        reference="https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L2",
        vector_rank=1, vector_score="0.8", vector_contribution="0.01",
        bm25_rank=1, bm25_score="2.0", bm25_contribution="0.01",
        g2_rank=1, rrf_score="0.02", reranker_score="0.9",
    )
    g3 = RerankedRun(case.query, (candidate,), {"reranker_identifier": "fixed-g3"})

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            if invalid_tool_candidate or empty_tool_results or unsafe_tool_request:
                pytest.fail("unretrieved candidate reached model")
            if observed_model_calls is not None:
                observed_model_calls.append(1)
                if len(observed_model_calls) <= structured_failures:
                    return ModelResponse("{invalid", 10, 5, 15)
            if observed_messages is not None:
                observed_messages.append(messages)
            if unverified_report:
                return ModelResponse(
                    '{"issue_classification":"route naming","facts":[],"root_cause_hypotheses":'
                    '[{"text":"Maybe routing","confidence":"low","evidence":[]}],'
                    '"confidence":"low","unresolved_questions":["What caused it?"]}',
                    100, 20, 120,
                )
            return ModelResponse(
                '{"issue_classification":"route naming","facts":[{"text":"The fallback '
                'uses the endpoint class name.","evidence":[{"candidate_rank":1,'
                '"start_line":2,"end_line":2,"excerpt":"return endpoint.__class__.__name__"}]}],'
                '"root_cause_hypotheses":[{"text":"A bound method falls through to its '
                'class name, method.","confidence":"high","evidence":[{"candidate_rank":1,'
                '"start_line":1,"end_line":2,"excerpt":"return endpoint.__class__.__name__"}]}],'
                '"confidence":"high","unresolved_questions":[]}',
                100, 20, 120,
            )

    class FixedTools:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            if observed_tool_calls is not None:
                observed_tool_calls.append(name)
            if unsafe_tool_request:
                raise PermissionError("untrusted request tried to exceed tool permissions")
            if name == failing_tool:
                raise RuntimeError("fixed tool failure")
            if name in {"search_code", "semantic_search"}:
                if empty_tool_results:
                    return []
                return (
                    [replace(candidate, commit="future")]
                    if invalid_tool_candidate else [candidate]
                )
            return []

    result = run_investigation(
        case, g3, tmp_path, FixedModel(),
        ReportConfig("fixed", "agent-v1", "0", "0", "USD"), FixedTools(), clock=clock,
        mode=mode, budget=budget, run_clock=run_clock, observation=observation,
    )

    return result


def test_fixed_evidence_sufficient_case_finishes_supported(tmp_path: Path) -> None:
    result = _fixed_investigation(tmp_path)

    assert result["report"]["schema_version"] == "1.0"
    assert result["outcome"]["status"] == "supported"
    assert result["outcome"]["stop_reason"] == "verified_root_cause"


def test_supported_run_exposes_investigation_stages_and_retrieval_events(tmp_path: Path) -> None:
    result = _fixed_investigation(tmp_path)

    assert [event["stage"] for event in result["events"] if event["kind"] == "stage"] == [
        "issue_parsed", "planned", "retrieved", "evidence_summarized",
        "hypotheses_formed", "verified", "reported",
    ]
    assert {event["tool"] for event in result["events"] if event["kind"] == "tool"} >= {
        "search_code", "semantic_search",
    }


def test_supported_run_reports_tool_and_model_usage(tmp_path: Path) -> None:
    result = _fixed_investigation(tmp_path)

    assert result["statistics"]["tool_calls"] == 2
    assert result["statistics"]["tools"] == {"search_code": 1, "semantic_search": 1}
    assert result["statistics"]["input_tokens"] == 100
    assert result["statistics"]["output_tokens"] == 20
    assert result["statistics"]["cost"] == "0"
    assert result["statistics"]["model"] == "fixed"


def test_supported_run_reports_all_controlled_budgets(tmp_path: Path) -> None:
    budgets = _fixed_investigation(tmp_path)["statistics"]["budgets"]

    assert budgets == {
        "tool_calls": {"limit": 20, "used": 2},
        "retrieval_rewrites": {"limit": 2, "used": 0},
        "root_cause_hypotheses": {"limit": 3, "used": 1},
        "patch_attempts": {"limit": 2, "used": 0},
        "run_time_ns": {"limit": 300_000_000_000, "used": budgets["run_time_ns"]["used"]},
    }
    assert 0 <= budgets["run_time_ns"]["used"] <= budgets["run_time_ns"]["limit"]


def test_supported_outcome_contains_only_cited_snapshot_evidence(tmp_path: Path) -> None:
    result = _fixed_investigation(tmp_path)

    assert result["outcome"]["allowed_evidence"] == [
        {
            "commit": "abc123",
            "path": "starlette/routing.py",
            "start_line": 1,
            "end_line": 2,
            "reference": "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L2",
        },
        {
            "commit": "abc123",
            "path": "starlette/routing.py",
            "start_line": 2,
            "end_line": 2,
            "reference": "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L2-L2",
        },
    ]


def test_tool_gateway_rejects_paths_outside_snapshot_without_calling_backend(
    tmp_path: Path,
) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        calls = 0

        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            self.calls += 1
            return "secret"

    backend = Backend()
    gateway = ToolGateway(case, tmp_path, backend)

    with pytest.raises(PermissionError, match="snapshot"):
        gateway.invoke(
            "read_file", {"path": "../repair_evidence.json", "start_line": 1, "end_line": 1}
        )

    assert backend.calls == 0
    assert gateway.events[0]["status"] == "denied"


def test_run_tests_requires_preapproved_identifier_and_never_accepts_shell(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        calls = 0

        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            self.calls += 1
            return {"passed": True}

    backend = Backend()
    gateway = ToolGateway(case, tmp_path, backend, allowed_tests={"routing-test"})

    with pytest.raises(PermissionError, match="test identifier"):
        gateway.invoke("run_tests", {"command": "powershell -Command Get-ChildItem"})

    assert backend.calls == 0
    assert gateway.events[-1]["status"] == "denied"


def test_submit_patch_requires_preapproved_identifier_without_writing_files(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        calls = 0

        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            self.calls += 1
            return {"accepted": True}

    backend = Backend()
    gateway = ToolGateway(case, tmp_path, backend)

    with pytest.raises(PermissionError, match="patch identifier"):
        gateway.invoke("submit_patch", {"patch": "--- a/routing.py\n+++ b/routing.py"})

    assert backend.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_submit_patch_budget_blocks_the_third_fixed_attempt(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        calls = 0

        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            self.calls += 1
            return {"accepted": True}

    backend = Backend()
    gateway = ToolGateway(
        case,
        tmp_path,
        backend,
        allowed_patches={"first", "second", "third"},
        budget=InvestigationBudget(patch_attempts=2),
    )

    gateway.invoke("submit_patch", {"patch_id": "first"})
    gateway.invoke("submit_patch", {"patch_id": "second"})
    with pytest.raises(RuntimeError, match="patch_attempts"):
        gateway.invoke("submit_patch", {"patch_id": "third"})

    assert backend.calls == 2
    assert gateway.events[-1] == {
        "sequence": 3,
        "kind": "budget",
        "dimension": "patch_attempts",
        "status": "exhausted",
        "limit": 2,
        "used": 2,
    }


def test_invalid_tool_results_exhaust_recovery_without_reaching_model(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(tmp_path, invalid_tool_candidate=True)

    assert result["outcome"]["status"] == "insufficient_evidence"
    assert result["outcome"]["stop_reason"] == "tool_recovery_exhausted"
    assert [
        event["status"] for event in result["events"] if event["kind"] == "tool"
    ] == ["invalid_result", "invalid_result"]


def test_read_file_returns_only_snapshot_lines_with_fixed_commit_reference(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    source = tmp_path / "starlette" / "routing.py"
    source.parent.mkdir()
    source.write_text("first\nsecond\nthird\n", encoding="utf-8")
    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            pytest.fail("read_file must use the fixed snapshot")

    gateway = ToolGateway(case, tmp_path, Backend())
    result = gateway.invoke(
        "read_file", {"path": "starlette/routing.py", "start_line": 2, "end_line": 3}
    )

    assert result == {
        "commit": "abc123", "path": "starlette/routing.py",
        "start_line": 2, "end_line": 3, "content": "second\nthird",
        "reference": "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L2-L3",
    }
    assert gateway.events[-1]["status"] == "ok"


def test_history_tool_rejects_items_after_investigation_cutoff(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            return [{
                "repository": "Kludex/starlette", "commit": "future",
                "observed_at": "2022-03-26T00:00:00Z",
                "source_url": "https://github.com/Kludex/starlette/commit/future",
            }]

    gateway = ToolGateway(case, tmp_path, Backend(), investigation_cutoff="2022-03-25T10:25:47Z")

    with pytest.raises(ValueError, match="investigation cutoff"):
        gateway.invoke("inspect_history", {"query": "routing"})

    assert gateway.events[-1]["status"] == "invalid_result"


def test_six_approved_tools_share_structured_event_contract(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    source = tmp_path / "starlette" / "routing.py"
    source.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")
    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")
    candidate = RetrievalCandidate(
        1, "0.9", "abc123", "starlette/routing.py", 1, 1, "python_source", "v1",
        "pass", "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L1",
    )

    class Backend:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            if name in {"search_code", "semantic_search"}:
                return [candidate]
            if name == "inspect_history":
                return []
            if name == "run_tests":
                return {"passed": True}
            if name == "submit_patch":
                return {"accepted": True}
            pytest.fail(f"unexpected backend call: {name}")

    gateway = ToolGateway(
        case, tmp_path, Backend(), allowed_tests={"routing-test"},
        allowed_patches={"routing-patch"}, investigation_cutoff="2022-03-25T10:25:47Z",
    )
    calls: tuple[tuple[str, dict[str, object]], ...] = (
        ("search_code", {"query": "Issue"}),
        ("semantic_search", {"query": "Issue"}),
        ("read_file", {"path": "starlette/routing.py", "start_line": 1, "end_line": 1}),
        ("inspect_history", {"query": "routing"}),
        ("run_tests", {"test_id": "routing-test"}),
        ("submit_patch", {"patch_id": "routing-patch"}),
    )
    for name, arguments in calls:
        gateway.invoke(name, arguments)

    assert [event["sequence"] for event in gateway.events] == [1, 2, 3, 4, 5, 6]
    assert [event["tool"] for event in gateway.events] == [name for name, _ in calls]
    assert all(
        event["kind"] == "tool" and event["status"] == "ok"
        and "arguments" in event and "references" in event
        for event in gateway.events
    )


def test_plan_event_explains_how_issue_will_be_searched(tmp_path: Path) -> None:
    result = _fixed_investigation(tmp_path)
    plan = next(event for event in result["events"] if event["stage"] == "planned")

    assert plan["query"] == 'Route naming introspection returns "method"'
    assert plan["tools"] == ["search_code", "semantic_search"]


def test_evaluation_mode_stops_safely_when_no_evidence_is_retrieved(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(tmp_path, empty_tool_results=True)

    assert result["outcome"] == {
        "status": "insufficient_evidence",
        "stop_reason": "no_validated_evidence",
        "allowed_evidence": [],
        "unresolved_questions": [
            "What exact call failed, and what was the complete error?",
            "What is the smallest input that reproduces the issue?",
            "Which Starlette version or snapshot exhibits the issue?",
        ],
    }
    assert result["report"]["schema_version"] == "1.0"
    assert "clarification_questions" not in result["outcome"]


def test_interactive_mode_returns_at_most_three_answerable_questions(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(tmp_path, empty_tool_results=True, mode="interactive")

    questions = result["outcome"]["clarification_questions"]
    assert questions == result["outcome"]["unresolved_questions"]
    assert len(questions) == 3
    assert all(question.endswith("?") for question in questions)


def test_permission_denial_stops_as_unsafe_request_before_another_tool(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    result = _fixed_investigation(
        tmp_path, unsafe_tool_request=True, observed_tool_calls=calls
    )

    assert result["outcome"] == {
        "status": "unsafe_request",
        "stop_reason": "tool_permission_denied",
        "allowed_evidence": [],
        "unresolved_questions": [
            "Can the issue be investigated using only allowed snapshot operations?"
        ],
    }
    assert calls == ["search_code"]
    denied = [event for event in result["events"] if event.get("status") == "denied"]
    assert len(denied) == 1
    assert denied[0]["tool"] == "search_code"


def test_one_tool_failure_uses_the_other_retrieval_tool_without_retry(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    result = _fixed_investigation(
        tmp_path, failing_tool="search_code", observed_tool_calls=calls
    )

    assert result["outcome"]["status"] == "supported"
    assert calls == ["search_code", "semantic_search"]
    failed = [event for event in result["events"] if event.get("status") == "error"]
    assert len(failed) == 1
    assert failed[0]["tool"] == "search_code"


def test_tool_call_budget_blocks_the_next_tool_before_backend_invocation(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    result = _fixed_investigation(
        tmp_path,
        budget=InvestigationBudget(tool_calls=1),
        observed_tool_calls=calls,
    )

    assert result["outcome"]["status"] == "budget_exhausted"
    assert result["outcome"]["stop_reason"] == "tool_call_budget_exhausted"
    assert calls == ["search_code"]
    assert result["statistics"]["budgets"]["tool_calls"] == {"limit": 1, "used": 1}


def test_time_budget_preserves_validated_report_and_stops_further_tools(
    tmp_path: Path,
) -> None:
    limit = 300_000_000_000
    readings = iter((0, 0, 0, 0, limit + 1, limit + 1))
    calls: list[str] = []

    result = _fixed_investigation(
        tmp_path,
        observed_tool_calls=calls,
        run_clock=lambda: next(readings),
    )

    assert result["outcome"]["status"] == "budget_exhausted"
    assert result["outcome"]["stop_reason"] == "time_budget_exhausted"
    assert result["outcome"]["allowed_evidence"]
    assert result["report"]["facts"]
    assert calls == ["search_code", "semantic_search"]
    assert any(
        event.get("kind") == "budget" and event.get("dimension") == "run_time_ns"
        for event in result["events"]
    )


def test_expired_time_budget_blocks_the_first_tool_call(tmp_path: Path) -> None:
    limit = 300_000_000_000
    readings = iter((0, limit, limit))
    calls: list[str] = []

    result = _fixed_investigation(
        tmp_path,
        observed_tool_calls=calls,
        run_clock=lambda: next(readings),
    )

    assert result["outcome"]["status"] == "budget_exhausted"
    assert result["outcome"]["stop_reason"] == "time_budget_exhausted"
    assert calls == []
    assert result["statistics"]["budgets"]["tool_calls"]["used"] == 0


def test_expired_time_budget_blocks_the_report_model_call(tmp_path: Path) -> None:
    limit = 300_000_000_000
    readings = iter((0, 0, 0, limit, limit))
    model_calls: list[int] = []

    result = _fixed_investigation(
        tmp_path,
        observed_model_calls=model_calls,
        run_clock=lambda: next(readings),
    )

    assert result["outcome"]["status"] == "budget_exhausted"
    assert result["outcome"]["stop_reason"] == "time_budget_exhausted"
    assert model_calls == []


def test_hypothesis_budget_excludes_excess_hypotheses_from_the_report(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(
        tmp_path,
        budget=InvestigationBudget(root_cause_hypotheses=0),
    )

    assert result["outcome"]["status"] == "budget_exhausted"
    assert result["outcome"]["stop_reason"] == "hypothesis_budget_exhausted"
    assert result["report"]["root_cause_hypotheses"] == []
    assert result["report"]["facts"]
    assert result["statistics"]["budgets"]["root_cause_hypotheses"] == {
        "limit": 0,
        "used": 0,
    }


def test_invalid_structured_output_recovers_once_with_a_fixed_response(
    tmp_path: Path,
) -> None:
    model_calls: list[int] = []

    result = _fixed_investigation(
        tmp_path,
        structured_failures=1,
        observed_model_calls=model_calls,
    )

    assert result["outcome"]["status"] == "supported"
    assert model_calls == [1, 1]
    assert result["statistics"]["model_calls"] == 2
    assert result["statistics"]["structured_output_retries"] == 1
    assert any(
        event.get("kind") == "recovery" and event.get("status") == "retrying"
        for event in result["events"]
    )


def test_recovered_model_calls_all_contribute_events_and_usage(tmp_path: Path) -> None:
    result = _fixed_investigation(
        tmp_path,
        structured_failures=1,
        observed_model_calls=[],
    )
    model_events = [event for event in result["events"] if event["kind"] == "model"]

    assert [event["status"] for event in model_events] == ["invalid_output", "ok"]
    assert result["statistics"]["model_calls"] == 2
    assert result["statistics"]["input_tokens"] == 110
    assert result["statistics"]["output_tokens"] == 25
    assert result["statistics"]["total_tokens"] == 135
    assert result["statistics"]["cost"] == "0"


def test_structured_output_recovery_exhaustion_is_insufficient_evidence(
    tmp_path: Path,
) -> None:
    model_calls: list[int] = []

    result = _fixed_investigation(
        tmp_path,
        structured_failures=2,
        observed_model_calls=model_calls,
    )

    assert result["outcome"]["status"] == "insufficient_evidence"
    assert result["outcome"]["stop_reason"] == "structured_output_recovery_exhausted"
    assert result["outcome"]["allowed_evidence"] == []
    assert result["report"]["root_cause_hypotheses"] == []
    assert model_calls == [1, 1]
    assert result["statistics"]["model_calls"] == 2
    assert result["statistics"]["structured_output_retries"] == 1


@pytest.mark.parametrize(
    ("kwargs", "status", "stop_reason"),
    [
        ({}, "supported", "verified_root_cause"),
        ({"empty_tool_results": True}, "insufficient_evidence", "no_validated_evidence"),
        (
            {"budget": InvestigationBudget(tool_calls=1)},
            "budget_exhausted",
            "tool_call_budget_exhausted",
        ),
        ({"unsafe_tool_request": True}, "unsafe_request", "tool_permission_denied"),
    ],
)
def test_each_outcome_has_a_terminal_event_and_complete_budget_statistics(
    tmp_path: Path,
    kwargs: dict[str, object],
    status: str,
    stop_reason: str,
) -> None:
    result = _fixed_investigation(tmp_path, **kwargs)  # type: ignore[arg-type]

    assert {
        key: result["events"][-1][key]
        for key in ("kind", "stage", "outcome", "stop_reason", "sequence")
    } == {
        "kind": "stage",
        "stage": "reported",
        "outcome": status,
        "stop_reason": stop_reason,
        "sequence": len(result["events"]),
    }
    assert set(result["statistics"]["budgets"]) == {
        "tool_calls",
        "retrieval_rewrites",
        "root_cause_hypotheses",
        "patch_attempts",
        "run_time_ns",
    }


def test_investigation_events_have_one_ordered_sequence(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]

    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


def test_investigation_has_stable_run_trace_and_event_identifiers(tmp_path: Path) -> None:
    result = _fixed_investigation(
        tmp_path,
        observation=ObservationContext(
            run_id="fixed-run",
            started_at=datetime(2026, 9, 23, tzinfo=UTC),
            clock=lambda: 0,
        ),
        clock=lambda: 0,
    )

    assert result["run_id"] == "fixed-run"
    assert result["trace_id"]
    assert {event["run_id"] for event in result["events"]} == {"fixed-run"}
    assert {event["trace_id"] for event in result["events"]} == {result["trace_id"]}
    assert len({event["event_id"] for event in result["events"]}) == len(result["events"])
    assert [event["sequence"] for event in result["events"]] == list(
        range(1, len(result["events"]) + 1)
    )
    assert all(event["started_at"] == "2026-09-23T00:00:00Z" for event in result["events"])
    assert all(event["finished_at"] == "2026-09-23T00:00:00Z" for event in result["events"])
    assert all(event["duration_ns"] == 0 for event in result["events"])


def test_investigation_events_expose_stage_and_tool_parent_relationships(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(tmp_path)
    retrieved = next(
        event
        for event in result["events"]
        if event["kind"] == "stage" and event["stage"] == "retrieved"
    )

    assert all(
        event["parent_id"] == result["trace_id"]
        for event in result["events"]
        if event["kind"] == "stage"
    )
    assert all(
        event["parent_id"] == retrieved["event_id"]
        for event in result["events"]
        if event["kind"] == "tool"
    )


def test_model_event_reports_usage_cost_latency_and_parent(tmp_path: Path) -> None:
    readings = iter((10, 25))
    result = _fixed_investigation(tmp_path, clock=lambda: next(readings))
    hypotheses = next(
        event
        for event in result["events"]
        if event["kind"] == "stage" and event["stage"] == "hypotheses_formed"
    )
    model = next(event for event in result["events"] if event["kind"] == "model")

    assert model["parent_id"] == hypotheses["event_id"]
    assert model["status"] == "ok"
    assert model["model"] == "fixed"
    assert model["prompt_version"] == "agent-v1"
    assert model["input_tokens"] == 100
    assert model["cached_input_tokens"] == 0
    assert model["output_tokens"] == 20
    assert model["total_tokens"] == 120
    assert model["cost"] == "0"
    assert model["currency"] == "USD"
    assert model["duration_ns"] == 15


def test_langfuse_disabled_keeps_investigation_offline(tmp_path: Path) -> None:
    from issuelens.observability import export_to_langfuse

    result = _fixed_investigation(tmp_path)
    initialized: list[bool] = []

    exported = export_to_langfuse(
        result,
        environ={"LANGFUSE_ENABLED": "false"},
        client_factory=lambda **kwargs: initialized.append(True),
    )

    assert result["outcome"]["status"] == "supported"
    assert exported is False
    assert initialized == []


def test_fixed_langfuse_client_receives_investigation_hierarchy(tmp_path: Path) -> None:
    from issuelens.observability import export_to_langfuse

    class FixedObservation:
        def __init__(self, name: str, kind: str) -> None:
            self.name = name
            self.kind = kind
            self.children: list[FixedObservation] = []

        def start_observation(self, *, name: str, as_type: str, **kwargs: object) -> object:
            child = FixedObservation(name, as_type)
            self.children.append(child)
            return child

        def end(self) -> None:
            pass

    class FixedClient:
        def __init__(self) -> None:
            self.root: FixedObservation | None = None
            self.root_arguments: dict[str, object] = {}
            self.flushed = False

        def start_observation(self, *, name: str, as_type: str, **kwargs: object) -> object:
            self.root_arguments = kwargs
            self.root = FixedObservation(name, as_type)
            return self.root

        def flush(self) -> None:
            self.flushed = True

    client = FixedClient()
    result = _fixed_investigation(tmp_path)

    assert export_to_langfuse(
        result,
        environ={
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "fixed-public",
            "LANGFUSE_SECRET_KEY": "fixed-secret",
            "LANGFUSE_HOST": "https://fixed.invalid",
        },
        client_factory=lambda **kwargs: client,
    )
    assert client.flushed
    assert client.root is not None
    assert (client.root.name, client.root.kind) == ("investigation", "agent")
    assert client.root_arguments["trace_context"] == {"trace_id": result["trace_id"]}
    stages = {child.name: child for child in client.root.children}
    assert set(stages) == {
        "issue_parsed", "planned", "retrieved", "evidence_summarized",
        "hypotheses_formed", "verified", "reported",
    }
    assert {(child.name, child.kind) for child in stages["retrieved"].children} == {
        ("search_code", "tool"),
        ("semantic_search", "tool"),
    }
    assert [(child.name, child.kind) for child in stages["hypotheses_formed"].children] == [
        ("model", "generation")
    ]


@pytest.mark.parametrize("failure_stage", ["initialization", "send", "flush"])
def test_langfuse_failure_preserves_result_and_local_events(
    tmp_path: Path, failure_stage: str,
) -> None:
    from issuelens.observability import export_to_langfuse

    class FixedObservation:
        def start_observation(self, **kwargs: object) -> object:
            return self

        def end(self) -> None:
            pass

    class FixedClient:
        def start_observation(self, **kwargs: object) -> object:
            if failure_stage == "send":
                raise RuntimeError("Authorization: Bearer secret-value")
            return FixedObservation()

        def flush(self) -> None:
            if failure_stage == "flush":
                raise RuntimeError("Authorization: Bearer secret-value")

    result = _fixed_investigation(tmp_path)
    original_events = json.loads(json.dumps(result["events"]))

    exported = export_to_langfuse(
        result,
        environ={"LANGFUSE_ENABLED": "true"},
        client_factory=(
            (lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret-value")))
            if failure_stage == "initialization"
            else lambda **kwargs: FixedClient()
        ),
    )

    assert exported is False
    assert result["outcome"]["status"] == "supported"
    assert result["events"][:-1] == original_events
    assert result["events"][-1]["kind"] == "observation"
    assert result["events"][-1]["status"] == "degraded"
    assert result["events"][-1]["failure_stage"] == failure_stage
    assert "secret-value" not in json.dumps(result["events"][-1])


def test_langfuse_payload_is_sanitized_at_the_single_export_boundary(tmp_path: Path) -> None:
    from issuelens.observability import export_to_langfuse

    payloads: list[dict[str, object]] = []

    class FixedObservation:
        def start_observation(self, **kwargs: object) -> object:
            payloads.append(kwargs)
            return self

        def end(self) -> None:
            pass

    class FixedClient:
        def start_observation(self, **kwargs: object) -> object:
            payloads.append(kwargs)
            return FixedObservation()

        def flush(self) -> None:
            pass

    result = _fixed_investigation(tmp_path)
    tool = next(event for event in result["events"] if event["kind"] == "tool")
    tool["status"] = "Authorization: Bearer secret-value"
    tool["arguments"] = {
        "api_key": "secret-value",
        "raw_output": "container-secret-value",
    }
    result["environment"] = {"COMPLETE_ENVIRONMENT": "environment-secret-value"}

    assert export_to_langfuse(
        result,
        environ={
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_SECRET_KEY": "secret-value",
        },
        client_factory=lambda **kwargs: FixedClient(),
    )
    uploaded = json.dumps(payloads)
    assert "secret-value" not in uploaded
    assert "container-secret-value" not in uploaded
    assert "environment-secret-value" not in uploaded
    assert "[REDACTED]" in uploaded
    assert '"total": 120' in uploaded


def test_evaluation_score_links_completed_trace_and_experiment_run(tmp_path: Path) -> None:
    from issuelens.observability import export_to_langfuse, record_evaluation_score

    scores: list[dict[str, object]] = []

    class FixedObservation:
        def start_observation(self, **kwargs: object) -> object:
            return self

        def end(self) -> None:
            pass

    class FixedClient:
        def start_observation(self, **kwargs: object) -> object:
            return FixedObservation()

        def create_score(self, **kwargs: object) -> None:
            scores.append(kwargs)

        def flush(self) -> None:
            pass

    result = _fixed_investigation(tmp_path)
    event = record_evaluation_score(
        result,
        experiment_run_id="experiment-fixed-1",
        name="root_cause_verdict",
        value=2.0,
        evaluator_version="verdict-v1",
    )

    assert event["trace_id"] == result["trace_id"]
    assert event["parent_id"] == result["events"][-2]["event_id"]
    assert event["experiment_run_id"] == "experiment-fixed-1"
    assert export_to_langfuse(
        result,
        environ={"LANGFUSE_ENABLED": "true"},
        client_factory=lambda **kwargs: FixedClient(),
    )
    assert scores == [{
        "name": "root_cause_verdict",
        "value": 2.0,
        "trace_id": result["trace_id"],
        "dataset_run_id": "experiment-fixed-1",
        "score_id": event["event_id"],
        "data_type": "NUMERIC",
        "metadata": {"evaluator_version": "verdict-v1"},
    }]


def test_state_and_retrieval_events_include_safe_transition_and_timing_summaries(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(tmp_path)
    stages = [event for event in result["events"] if event["kind"] == "stage"]
    tools = [event for event in result["events"] if event["kind"] == "tool"]

    assert [(event["from_stage"], event["to_stage"]) for event in stages] == [
        (None, "issue_parsed"),
        ("issue_parsed", "planned"),
        ("planned", "retrieved"),
        ("retrieved", "evidence_summarized"),
        ("evidence_summarized", "hypotheses_formed"),
        ("hypotheses_formed", "verified"),
        ("verified", "reported"),
    ]
    assert all(event["candidate_count"] == 1 for event in tools)
    assert all(event["candidate_ranks"] == [1] for event in tools)
    assert all(event["duration_ns"] >= 0 for event in tools)
    assert result["statistics"]["run_latency_ns"] >= 0


def test_concurrent_async_investigations_keep_separate_parent_trees(tmp_path: Path) -> None:
    async def run_both() -> tuple[dict[str, Any], dict[str, Any]]:
        first, second = await asyncio.gather(
            asyncio.to_thread(_fixed_investigation, tmp_path / "first"),
            asyncio.to_thread(_fixed_investigation, tmp_path / "second"),
        )
        return first, second

    first, second = asyncio.run(run_both())

    assert first["trace_id"] != second["trace_id"]
    for result in (first, second):
        event_ids = {event["event_id"] for event in result["events"]}
        assert [event["sequence"] for event in result["events"]] == list(
            range(1, len(result["events"]) + 1)
        )
        assert all(
            event["parent_id"] == result["trace_id"] or event["parent_id"] in event_ids
            for event in result["events"]
        )


def test_issue_parse_event_identifies_source_and_snapshot(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]
    parsed = next(event for event in events if event["stage"] == "issue_parsed")

    assert parsed["case_id"] == "fixture"
    assert parsed["issue_source_url"] == "https://github.com/Kludex/starlette/issues/1"
    assert parsed["snapshot_commit"] == "abc123"


def test_evidence_summary_names_validated_g3_candidates(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]
    summary = next(event for event in events if event["stage"] == "evidence_summarized")

    assert summary["candidate_references"] == [
        "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L2"
    ]


def test_hypothesis_event_exposes_bounded_model_proposals(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]
    hypotheses = next(
        event for event in events if event.get("stage") == "hypotheses_formed"
    )

    assert hypotheses["hypotheses"] == [
        "A bound method falls through to its class name, method."
    ]


def test_verification_event_names_supported_evidence(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]
    verification = next(event for event in events if event.get("stage") == "verified")

    assert verification["supported_hypotheses"] == 1
    assert verification["references"] == [
        "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L2"
    ]


def test_agent_report_uses_validated_plan_and_evidence_summary(tmp_path: Path) -> None:
    observed: list[list[dict[str, str]]] = []
    _fixed_investigation(tmp_path, observed_messages=observed)

    payload = json.loads(observed[0][1]["content"])
    assert payload["investigation"]["plan"]["tools"] == [
        "search_code", "semantic_search"
    ]
    assert payload["investigation"]["evidence_references"] == [
        "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L2"
    ]


def test_run_statistics_identify_the_g3_retrieval_configuration(tmp_path: Path) -> None:
    result = _fixed_investigation(tmp_path)

    assert result["statistics"]["retrieval"] == {"reranker_identifier": "fixed-g3"}


def test_versioned_g3_loader_exposes_only_retrieval_configuration() -> None:
    from issuelens.report import load_versioned_three_way

    root = Path(__file__).resolve().parents[2]
    case = root / "data/cases/starlette-issue-1552.json"
    frozen = root / "data/retrieval/starlette-issue-1552/three-way.json"
    g3 = load_versioned_three_way(case, frozen).g3_reranked

    assert g3.metadata["top_k"] == 10
    assert g3.metadata["fusion_strategy"] == "rrf-v1"
    reranker_identifier = g3.metadata["reranker_identifier"]
    assert isinstance(reranker_identifier, str)
    assert reranker_identifier.startswith("fastembed:Xenova/ms-marco-MiniLM")
    assert set(g3.metadata) == {
        "embedding_identifier", "fusion_strategy", "reranker_identifier", "top_k",
        "score_precision",
    }


def test_unverified_root_cause_finishes_as_insufficient_evidence(
    tmp_path: Path,
) -> None:
    result = _fixed_investigation(tmp_path, unverified_report=True)

    assert result["outcome"] == {
        "status": "insufficient_evidence",
        "stop_reason": "no_supported_root_cause",
        "allowed_evidence": [],
        "unresolved_questions": ["What caused it?"],
    }
    assert result["report"]["root_cause_hypotheses"][0]["verification"] == (
        "unverified_hypothesis"
    )


def test_run_tests_rejects_unstructured_tool_output(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            return {"passed": True, "repair_evidence": "future fix"}

    gateway = ToolGateway(case, tmp_path, Backend(), allowed_tests={"routing-test"})

    with pytest.raises(ValueError, match="test result"):
        gateway.invoke("run_tests", {"test_id": "routing-test"})

    assert gateway.events[-1]["status"] == "invalid_result"


def test_submit_patch_rejects_unstructured_tool_output(tmp_path: Path) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            return {"accepted": True, "patch": "hidden file change"}

    gateway = ToolGateway(case, tmp_path, Backend(), allowed_patches={"routing-patch"})

    with pytest.raises(ValueError, match="patch result"):
        gateway.invoke("submit_patch", {"patch_id": "routing-patch"})

    assert gateway.events[-1]["status"] == "invalid_result"


@pytest.mark.parametrize("name", ["search_code", "semantic_search"])
def test_retrieval_tools_reject_extra_command_fields_before_backend(
    name: str, tmp_path: Path,
) -> None:
    from issuelens.investigation import ToolGateway

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")

    class Backend:
        calls = 0

        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            self.calls += 1
            return []

    backend = Backend()
    gateway = ToolGateway(case, tmp_path, backend)

    with pytest.raises(PermissionError, match="query"):
        gateway.invoke(name, {"query": "routing", "command": "echo secret"})

    assert backend.calls == 0


def test_versioned_starlette_case_completes_supported_with_fixed_responses(
    tmp_path: Path,
) -> None:
    from issuelens.investigation import run_investigation
    from issuelens.report import load_versioned_three_way
    from issuelens.retrieval import load_retrieval_input

    root = Path(__file__).resolve().parents[2]
    case_path = root / "data/cases/starlette-issue-1552.json"
    case = load_retrieval_input(case_path)
    g3 = load_versioned_three_way(
        case_path, root / "data/retrieval/starlette-issue-1552/three-way.json"
    ).g3_reranked
    candidate = next(item for item in g3.candidates if item.path == "starlette/routing.py")
    assert isinstance(candidate, RerankedCandidate)
    source = tmp_path / candidate.path
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n" * (candidate.start_line - 1) + candidate.content + "\n", encoding="utf-8"
    )
    (tmp_path / ".issuelens-snapshot.json").write_text(
        json.dumps({"repository": case.repository, "commit": case.snapshot_commit}),
        encoding="utf-8",
    )
    selected = RerankedRun(case.query, (candidate,), g3.metadata)

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"route naming","facts":[{"text":"Route names '
                'default through get_name.","evidence":[{"candidate_rank":2,"start_line":202,'
                '"end_line":202,"excerpt":"self.name = get_name(endpoint) if name is None '
                'else name"}]}],'
                '"root_cause_hypotheses":[{"text":"Bound method endpoints fall back to the '
                'runtime class name method.","confidence":"high","evidence":'
                '[{"candidate_rank":2,"start_line":202,"end_line":202,'
                '"excerpt":"self.name = get_name(endpoint) if name is None else name"}]}],'
                '"confidence":"high","unresolved_questions":[]}',
                100, 20, 120,
            )

    class FixedTools:
        def invoke(self, name: str, arguments: dict[str, object]) -> object:
            return [candidate]

    result = run_investigation(
        case, selected, tmp_path, FixedModel(),
        ReportConfig("fixed", "agent-v1", "0", "0", "USD"), FixedTools(),
    )

    assert result["case_id"] == "starlette-issue-1552"
    assert result["outcome"]["status"] == "supported"
    assert result["statistics"]["retrieval"]["fusion_strategy"] == "rrf-v1"


def test_supported_run_can_be_written_for_rest_review(tmp_path: Path) -> None:
    from issuelens.investigation import write_investigation_result

    result = _fixed_investigation(tmp_path / "snapshot")
    path = tmp_path / "investigations" / "fixture" / "run.json"

    write_investigation_result(path, result)

    assert json.loads(path.read_text(encoding="utf-8")) == result


def test_run_uses_injected_clock_for_reproducible_statistics(tmp_path: Path) -> None:
    readings = iter((10, 25))

    result = _fixed_investigation(tmp_path, clock=lambda: next(readings))

    assert result["statistics"]["latency_ns"] == 15
