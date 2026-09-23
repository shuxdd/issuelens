import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from issuelens.report import ModelResponse, ReportConfig
from issuelens.retrieval import RerankedCandidate, RerankedRun, RetrievalCandidate, RetrievalInput


def _fixed_investigation(
    tmp_path: Path, *, invalid_tool_candidate: bool = False, empty_tool_results: bool = False,
    observed_messages: list[list[dict[str, str]]] | None = None,
    unverified_report: bool = False,
    clock: Callable[[], int] | None = None,
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
            if invalid_tool_candidate or empty_tool_results:
                pytest.fail("unretrieved candidate reached model")
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


def test_untrusted_retrieval_result_is_rejected_before_model_call(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="commit or reference"):
        _fixed_investigation(tmp_path, invalid_tool_candidate=True)


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


def test_no_retrieved_evidence_cannot_reach_report_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no validated retrieval candidates"):
        _fixed_investigation(tmp_path, empty_tool_results=True)


def test_investigation_events_have_one_ordered_sequence(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]

    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


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
    hypotheses = next(event for event in events if event["stage"] == "hypotheses_formed")

    assert hypotheses["hypotheses"] == [
        "A bound method falls through to its class name, method."
    ]


def test_verification_event_names_supported_evidence(tmp_path: Path) -> None:
    events = _fixed_investigation(tmp_path)["events"]
    verification = next(event for event in events if event["stage"] == "verified")

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


def test_issue_08_does_not_claim_a_terminal_state_without_verified_root_cause(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="supported path requires verified root cause"):
        _fixed_investigation(tmp_path, unverified_report=True)


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
