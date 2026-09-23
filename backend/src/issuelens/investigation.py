import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast
from urllib.parse import quote

from langgraph.graph import END, START, StateGraph

from issuelens.report import ReportConfig, ReportModel, generate_report
from issuelens.retrieval import (
    RerankedRun,
    RetrievalCandidate,
    RetrievalInput,
    validate_candidate_references,
)


class InvestigationTools(Protocol):
    def invoke(self, name: str, arguments: dict[str, object]) -> object: ...


class ToolGateway:
    def __init__(
        self, retrieval_input: RetrievalInput, snapshot_root: Path, backend: InvestigationTools,
        *, allowed_tests: set[str] | None = None,
        allowed_patches: set[str] | None = None,
        investigation_cutoff: str | None = None,
    ) -> None:
        self.retrieval_input = retrieval_input
        self.snapshot_root = snapshot_root.resolve()
        self.backend = backend
        self.allowed_tests = allowed_tests or set()
        self.allowed_patches = allowed_patches or set()
        self.investigation_cutoff = investigation_cutoff
        self.events: list[dict[str, Any]] = []

    def _record(
        self, name: str, arguments: dict[str, object], status: str, result: object = None
    ) -> None:
        summary: dict[str, object] = {"keys": sorted(arguments)}
        if status == "ok":
            query = arguments.get("query")
            if isinstance(query, str):
                summary = {"query_sha256": sha256(query.encode()).hexdigest()}
            else:
                summary = {
                    key: value for key, value in arguments.items()
                    if key in {"path", "start_line", "end_line", "test_id", "patch_id"}
                }
        references: list[str] = []
        if isinstance(result, (list, tuple)):
            references = [
                item.reference if isinstance(item, RetrievalCandidate) else item["source_url"]
                for item in result
                if isinstance(item, RetrievalCandidate)
                or isinstance(item, dict) and isinstance(item.get("source_url"), str)
            ]
        elif isinstance(result, dict) and isinstance(result.get("reference"), str):
            references = [result["reference"]]
        self.events.append({
            "sequence": len(self.events) + 1,
            "kind": "tool", "tool": name, "status": status,
            "arguments": summary, "references": references,
        })

    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        try:
            if name not in {
                "search_code", "semantic_search", "read_file", "inspect_history",
                "run_tests", "submit_patch",
            }:
                raise PermissionError("tool is not allowed")
            query = arguments.get("query")
            if name in {"search_code", "semantic_search"} and (
                set(arguments) != {"query"}
                or not isinstance(query, str)
                or not query.strip()
                or len(query) > 256
            ):
                raise PermissionError("retrieval query is not allowed")
            if name == "read_file":
                path = arguments.get("path")
                if (
                    set(arguments) != {"path", "start_line", "end_line"}
                    or not isinstance(path, str)
                    or not path
                    or Path(path).is_absolute()
                ):
                    raise PermissionError("path is outside snapshot")
                source = (self.snapshot_root / path).resolve()
                if not source.is_relative_to(self.snapshot_root) or not source.is_file():
                    raise PermissionError("path is outside snapshot")
                start = arguments["start_line"]
                end = arguments["end_line"]
                lines = source.read_text(encoding="utf-8").splitlines()
                if (
                    type(start) is not int or type(end) is not int
                    or not 1 <= start <= end <= len(lines)
                    or end - start >= 200
                ):
                    raise PermissionError("line range is outside snapshot")
                relative = source.relative_to(self.snapshot_root).as_posix()
                result: object = {
                    "commit": self.retrieval_input.snapshot_commit,
                    "path": relative,
                    "start_line": start,
                    "end_line": end,
                    "content": "\n".join(lines[start - 1:end]),
                    "reference": (
                        f"https://github.com/{self.retrieval_input.repository}/blob/"
                        f"{self.retrieval_input.snapshot_commit}/"
                        f"{quote(relative, safe='/')}#L{start}-L{end}"
                    ),
                }
            if name == "run_tests" and (
                set(arguments) != {"test_id"}
                or arguments.get("test_id") not in self.allowed_tests
            ):
                raise PermissionError("test identifier is not approved")
            if name == "submit_patch" and (
                set(arguments) != {"patch_id"}
                or arguments.get("patch_id") not in self.allowed_patches
            ):
                raise PermissionError("patch identifier is not approved")
            if name == "inspect_history" and (
                set(arguments) != {"query"}
                or not isinstance(arguments.get("query"), str)
                or not arguments["query"]
                or self.investigation_cutoff is None
            ):
                raise PermissionError("history query requires an investigation cutoff")
            if name != "read_file":
                result = self.backend.invoke(name, arguments)
            if name in {"search_code", "semantic_search"}:
                if not isinstance(result, (list, tuple)) or any(
                    not isinstance(item, RetrievalCandidate) for item in result
                ):
                    raise ValueError("retrieval tool must return candidates")
                errors = validate_candidate_references(
                    result, self.snapshot_root,
                    self.retrieval_input.repository,
                    self.retrieval_input.snapshot_commit,
                )
                if errors:
                    raise ValueError(errors[0])
            if name == "inspect_history":
                if not isinstance(result, list):
                    raise ValueError("history result must be a list")
                assert self.investigation_cutoff is not None
                cutoff = datetime.fromisoformat(
                    self.investigation_cutoff.replace("Z", "+00:00")
                )
                for item in result:
                    if not isinstance(item, dict):
                        raise ValueError("history item must be an object")
                    commit = item.get("commit")
                    if (
                        not isinstance(commit, str) or not commit
                        or item.get("repository") != self.retrieval_input.repository
                        or item.get("source_url") != (
                            f"https://github.com/{self.retrieval_input.repository}/commit/{commit}"
                        )
                    ):
                        raise ValueError("history item is outside investigation repository")
                    observed = item.get("observed_at")
                    if not isinstance(observed, str) or datetime.fromisoformat(
                        observed.replace("Z", "+00:00")
                    ) >= cutoff:
                        raise ValueError("history item is after investigation cutoff")
            if name == "run_tests" and (
                not isinstance(result, dict)
                or set(result) != {"passed"}
                or type(result["passed"]) is not bool
            ):
                raise ValueError("test result must contain only a passed flag")
            if name == "submit_patch" and (
                not isinstance(result, dict)
                or set(result) != {"accepted"}
                or type(result["accepted"]) is not bool
            ):
                raise ValueError("patch result must contain only an accepted flag")
        except (PermissionError, ValueError) as error:
            self._record(
                name, arguments,
                "denied" if isinstance(error, PermissionError) else "invalid_result",
            )
            raise
        self._record(name, arguments, "ok", result)
        return result


class _State(TypedDict, total=False):
    events: list[dict[str, Any]]
    retrieved: RerankedRun
    draft: dict[str, Any]
    outcome: dict[str, Any]
    result: dict[str, Any]


def run_investigation(
    retrieval_input: RetrievalInput,
    g3_run: RerankedRun,
    snapshot_root: Path,
    model: ReportModel,
    config: ReportConfig,
    tools: InvestigationTools,
    *, clock: Callable[[], int] | None = None,
) -> dict[str, Any]:
    gateway = ToolGateway(retrieval_input, snapshot_root, tools)

    def stage(state: _State, name: str) -> list[dict[str, Any]]:
        return [*state.get("events", []), {"kind": "stage", "stage": name}]

    def parse(state: _State) -> _State:
        events = list(state.get("events", []))
        events.append({
            "kind": "stage", "stage": "issue_parsed",
            "case_id": retrieval_input.case_id,
            "issue_source_url": retrieval_input.issue_source_url,
            "snapshot_commit": retrieval_input.snapshot_commit,
        })
        return {"events": events}

    def plan(state: _State) -> _State:
        events = list(state.get("events", []))
        events.append({
            "kind": "stage", "stage": "planned",
            "query": retrieval_input.query,
            "tools": ["search_code", "semantic_search"],
        })
        return {"events": events}

    def retrieve(state: _State) -> _State:
        events = list(state.get("events", []))
        references: set[str] = set()
        for name in ("search_code", "semantic_search"):
            candidates = cast(
                list[RetrievalCandidate],
                gateway.invoke(name, {"query": retrieval_input.query}),
            )
            references.update(item.reference for item in candidates)
            events.append({**gateway.events[-1], "stage": "retrieved"})
        selected = tuple(
            item for item in g3_run.candidates if item.reference in references
        )
        if not selected:
            raise ValueError("no validated retrieval candidates")
        events.append({"kind": "stage", "stage": "retrieved"})
        return {
            "events": events,
            "retrieved": RerankedRun(g3_run.query, selected, g3_run.metadata),
        }

    def summarize(state: _State) -> _State:
        events = list(state["events"])
        events.append({
            "kind": "stage", "stage": "evidence_summarized",
            "candidate_references": [
                item.reference for item in state["retrieved"].candidates
            ],
        })
        return {"events": events}

    def hypotheses(state: _State) -> _State:
        plan_event = next(event for event in state["events"] if event["stage"] == "planned")
        draft = generate_report(
            retrieval_input, state["retrieved"], snapshot_root, model, config,
            investigation_context={
                "plan": {"query": plan_event["query"], "tools": plan_event["tools"]},
                "evidence_references": [
                    item.reference for item in state["retrieved"].candidates
                ],
            },
            clock=clock,
        )
        events = list(state["events"])
        events.append({
            "kind": "stage", "stage": "hypotheses_formed",
            "hypotheses": [
                item["text"] for item in draft["report"]["root_cause_hypotheses"]
            ],
        })
        return {"draft": draft, "events": events}

    def verify(state: _State) -> _State:
        report = state["draft"]["report"]
        supported_hypotheses = [
            item for item in report["root_cause_hypotheses"]
            if item["verification"] == "supported"
        ]
        if not supported_hypotheses:
            raise ValueError("supported path requires verified root cause")
        evidence = {
            item["reference"]: {
                key: item[key]
                for key in ("commit", "path", "start_line", "end_line", "reference")
            }
            for section in ("facts", "root_cause_hypotheses")
            for claim in report[section]
            for item in claim["evidence"]
        }
        events = list(state["events"])
        events.append({
            "kind": "stage", "stage": "verified",
            "supported_hypotheses": len(supported_hypotheses),
            "references": sorted({
                item["reference"]
                for hypothesis in supported_hypotheses
                for item in hypothesis["evidence"]
            }),
        })
        return {
            "outcome": {
                "status": "supported",
                "stop_reason": "verified_root_cause",
                "allowed_evidence": sorted(
                    evidence.values(), key=lambda item: (item["path"], item["start_line"])
                ),
                "unresolved_questions": report["unresolved_questions"],
            },
            "events": events,
        }

    def report(state: _State) -> _State:
        events = [
            {**event, "sequence": number}
            for number, event in enumerate(stage(state, "reported"), start=1)
        ]
        tool_counts = Counter(
            event["tool"] for event in events if event["kind"] == "tool"
        )
        metadata = state["draft"]["metadata"]
        return {
            "result": {
                "case_id": retrieval_input.case_id,
                "report": state["draft"]["report"],
                "outcome": state["outcome"],
                "events": events,
                "statistics": {
                    "tool_calls": sum(tool_counts.values()),
                    "tools": dict(tool_counts),
                    "input_tokens": metadata["input_tokens"],
                    "output_tokens": metadata["output_tokens"],
                    "cost": metadata["cost"],
                    "model": metadata["model"],
                    "prompt_version": metadata["prompt_version"],
                    "latency_ns": metadata["latency_ns"],
                    "retrieval": {
                        key: g3_run.metadata[key]
                        for key in (
                            "embedding_identifier", "fusion_strategy",
                            "reranker_identifier", "top_k", "score_precision",
                        )
                        if key in g3_run.metadata
                    },
                },
            },
        }

    graph = StateGraph(_State)
    steps = (parse, plan, retrieve, summarize, hypotheses, verify, report)
    for step in steps:
        graph.add_node(step.__name__, step)
    graph.add_edge(START, steps[0].__name__)
    for first, second in zip(steps, steps[1:], strict=False):
        graph.add_edge(first.__name__, second.__name__)
    graph.add_edge(steps[-1].__name__, END)
    return cast(dict[str, Any], graph.compile().invoke({})["result"])


def write_investigation_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
