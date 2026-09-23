import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, cast
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


@dataclass(frozen=True)
class InvestigationBudget:
    tool_calls: int = 20
    retrieval_rewrites: int = 2
    root_cause_hypotheses: int = 3
    patch_attempts: int = 2
    run_time_ns: int = 300_000_000_000

    def __post_init__(self) -> None:
        maxima = {
            "tool_calls": 20,
            "retrieval_rewrites": 2,
            "root_cause_hypotheses": 3,
            "patch_attempts": 2,
            "run_time_ns": 300_000_000_000,
        }
        if any(
            type(value) is not int or not 0 <= value <= maxima[name]
            for name, value in (
                ("tool_calls", self.tool_calls),
                ("retrieval_rewrites", self.retrieval_rewrites),
                ("root_cause_hypotheses", self.root_cause_hypotheses),
                ("patch_attempts", self.patch_attempts),
                ("run_time_ns", self.run_time_ns),
            )
        ):
            raise ValueError("investigation budget must stay within specification")


class _BudgetExhausted(RuntimeError):
    def __init__(self, dimension: str) -> None:
        super().__init__(dimension)
        self.dimension = dimension


class ToolGateway:
    def __init__(
        self, retrieval_input: RetrievalInput, snapshot_root: Path, backend: InvestigationTools,
        *, allowed_tests: set[str] | None = None,
        allowed_patches: set[str] | None = None,
        investigation_cutoff: str | None = None,
        budget: InvestigationBudget | None = None,
        run_clock: Callable[[], int] | None = None,
        started_ns: int | None = None,
    ) -> None:
        self.retrieval_input = retrieval_input
        self.snapshot_root = snapshot_root.resolve()
        self.backend = backend
        self.allowed_tests = allowed_tests or set()
        self.allowed_patches = allowed_patches or set()
        self.investigation_cutoff = investigation_cutoff
        self.budget = budget or InvestigationBudget()
        self.tool_calls = 0
        self.patch_attempts = 0
        self.run_clock = run_clock or time.perf_counter_ns
        self.started_ns = self.run_clock() if started_ns is None else started_ns
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
        elapsed = self.run_clock() - self.started_ns
        if elapsed >= self.budget.run_time_ns:
            self.events.append({
                "sequence": len(self.events) + 1,
                "kind": "budget",
                "dimension": "run_time_ns",
                "status": "exhausted",
                "limit": self.budget.run_time_ns,
                "used": elapsed,
            })
            raise _BudgetExhausted("run_time_ns")
        if self.tool_calls >= self.budget.tool_calls:
            self.events.append({
                "sequence": len(self.events) + 1,
                "kind": "budget",
                "dimension": "tool_calls",
                "status": "exhausted",
                "limit": self.budget.tool_calls,
                "used": self.tool_calls,
            })
            raise _BudgetExhausted("tool_calls")
        self.tool_calls += 1
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
            if name == "submit_patch":
                if self.patch_attempts >= self.budget.patch_attempts:
                    self.events.append({
                        "sequence": len(self.events) + 1,
                        "kind": "budget",
                        "dimension": "patch_attempts",
                        "status": "exhausted",
                        "limit": self.budget.patch_attempts,
                        "used": self.patch_attempts,
                    })
                    raise _BudgetExhausted("patch_attempts")
                self.patch_attempts += 1
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
        except _BudgetExhausted:
            raise
        except (PermissionError, ValueError) as error:
            self._record(
                name, arguments,
                "denied" if isinstance(error, PermissionError) else "invalid_result",
            )
            raise
        except Exception:
            self._record(name, arguments, "error")
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
    budget: InvestigationBudget | None = None,
    run_clock: Callable[[], int] | None = None,
    mode: Literal["evaluation", "interactive"] = "evaluation",
) -> dict[str, Any]:
    if mode not in ("evaluation", "interactive"):
        raise ValueError("mode must be evaluation or interactive")
    budget = budget or InvestigationBudget()
    run_clock = run_clock or time.perf_counter_ns
    started = run_clock()
    gateway = ToolGateway(
        retrieval_input,
        snapshot_root,
        tools,
        budget=budget,
        run_clock=run_clock,
        started_ns=started,
    )

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

    def terminal_state(
        events: list[dict[str, Any]], status: str, stop_reason: str, questions: list[str]
    ) -> _State:
        outcome: dict[str, Any] = {
            "status": status,
            "stop_reason": stop_reason,
            "allowed_evidence": [],
            "unresolved_questions": questions,
        }
        if mode == "interactive" and status == "insufficient_evidence":
            outcome["clarification_questions"] = questions[:3]
        return {
            "events": events,
            "draft": {
                "report": {
                    "schema_version": "1.0",
                    "issue_classification": status.replace("_", " "),
                    "facts": [],
                    "root_cause_hypotheses": [],
                    "confidence": "low",
                    "unresolved_questions": questions,
                },
                "metadata": {
                    "model": config.model,
                    "prompt_version": config.prompt_version,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": "0",
                    "latency_ns": 0,
                },
            },
            "outcome": outcome,
        }

    def retrieve(state: _State) -> _State:
        events = list(state.get("events", []))
        references: set[str] = set()
        tool_failures = 0
        for name in ("search_code", "semantic_search"):
            try:
                candidates = cast(
                    list[RetrievalCandidate],
                    gateway.invoke(name, {"query": retrieval_input.query}),
                )
            except _BudgetExhausted as error:
                events.append({**gateway.events[-1], "stage": "retrieved"})
                return terminal_state(
                    events,
                    "budget_exhausted",
                    (
                        "time_budget_exhausted"
                        if error.dimension == "run_time_ns"
                        else "tool_call_budget_exhausted"
                    ),
                    ["Which evidence should be investigated in a new budgeted run?"],
                )
            except PermissionError:
                events.append({**gateway.events[-1], "stage": "retrieved"})
                return terminal_state(
                    events,
                    "unsafe_request",
                    "tool_permission_denied",
                    ["Can the issue be investigated using only allowed snapshot operations?"],
                )
            except Exception:
                events.append({**gateway.events[-1], "stage": "retrieved"})
                tool_failures += 1
                continue
            references.update(item.reference for item in candidates)
            events.append({**gateway.events[-1], "stage": "retrieved"})
        selected = tuple(
            item for item in g3_run.candidates if item.reference in references
        )
        if not selected:
            questions = [
                "What exact call failed, and what was the complete error?",
                "What is the smallest input that reproduces the issue?",
                "Which Starlette version or snapshot exhibits the issue?",
            ]
            return terminal_state(
                [*events, {"kind": "stage", "stage": "retrieved"}],
                "insufficient_evidence",
                "tool_recovery_exhausted" if tool_failures else "no_validated_evidence",
                questions,
            )
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
        events = list(state["events"])
        output_error_prefixes = (
            "model output:", "model usage:", "issue_classification", "facts",
            "root_cause_hypotheses", "confidence", "unresolved_questions",
        )
        for attempt in (1, 2):
            elapsed = run_clock() - started
            if elapsed >= budget.run_time_ns:
                events.append({
                    "kind": "budget",
                    "dimension": "run_time_ns",
                    "status": "exhausted",
                    "limit": budget.run_time_ns,
                    "used": elapsed,
                })
                terminal = terminal_state(
                    events,
                    "budget_exhausted",
                    "time_budget_exhausted",
                    ["Which evidence should be investigated in a new budgeted run?"],
                )
                terminal["draft"]["metadata"]["model_calls"] = attempt - 1
                terminal["draft"]["metadata"]["structured_output_retries"] = max(
                    0, attempt - 2
                )
                return terminal
            try:
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
                draft["metadata"]["model_calls"] = attempt
                draft["metadata"]["structured_output_retries"] = attempt - 1
                break
            except ValueError as error:
                if not str(error).startswith(output_error_prefixes):
                    raise
                if attempt == 2:
                    events.append({
                        "kind": "recovery",
                        "target": "structured_output",
                        "status": "exhausted",
                        "attempt": 2,
                        "max_retries": 1,
                    })
                    terminal = terminal_state(
                        events,
                        "insufficient_evidence",
                        "structured_output_recovery_exhausted",
                        ["Which evidence is needed to produce a valid investigation report?"],
                    )
                    terminal["draft"]["metadata"]["model_calls"] = 2
                    terminal["draft"]["metadata"]["structured_output_retries"] = 1
                    return terminal
                events.append({
                    "kind": "recovery",
                    "target": "structured_output",
                    "status": "retrying",
                    "attempt": 1,
                    "max_retries": 1,
                })
        events.append({
            "kind": "stage", "stage": "hypotheses_formed",
            "hypotheses": [
                item["text"] for item in draft["report"]["root_cause_hypotheses"]
            ],
        })
        return {"draft": draft, "events": events}

    def verify(state: _State) -> _State:
        report = state["draft"]["report"]
        if len(report["root_cause_hypotheses"]) > budget.root_cause_hypotheses:
            report["root_cause_hypotheses"] = report["root_cause_hypotheses"][
                :budget.root_cause_hypotheses
            ]
            questions = report["unresolved_questions"] or [
                "Which root cause hypothesis should be investigated in a new budgeted run?"
            ]
            report["unresolved_questions"] = questions
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
                "kind": "budget",
                "dimension": "root_cause_hypotheses",
                "status": "exhausted",
                "limit": budget.root_cause_hypotheses,
                "used": len(report["root_cause_hypotheses"]),
            })
            return {
                "outcome": {
                    "status": "budget_exhausted",
                    "stop_reason": "hypothesis_budget_exhausted",
                    "allowed_evidence": sorted(
                        evidence.values(), key=lambda item: (item["path"], item["start_line"])
                    ),
                    "unresolved_questions": questions,
                },
                "events": events,
            }
        evidence = {
            item["reference"]: {
                key: item[key]
                for key in ("commit", "path", "start_line", "end_line", "reference")
            }
            for section in ("facts", "root_cause_hypotheses")
            for claim in report[section]
            for item in claim["evidence"]
        }
        elapsed = run_clock() - started
        if elapsed >= budget.run_time_ns:
            questions = report["unresolved_questions"] or [
                "Which remaining evidence should be investigated in a new budgeted run?"
            ]
            report["unresolved_questions"] = questions
            events = list(state["events"])
            events.append({
                "kind": "budget",
                "dimension": "run_time_ns",
                "status": "exhausted",
                "limit": budget.run_time_ns,
                "used": elapsed,
            })
            return {
                "outcome": {
                    "status": "budget_exhausted",
                    "stop_reason": "time_budget_exhausted",
                    "allowed_evidence": sorted(
                        evidence.values(), key=lambda item: (item["path"], item["start_line"])
                    ),
                    "unresolved_questions": questions,
                },
                "events": events,
            }
        supported_hypotheses = [
            item for item in report["root_cause_hypotheses"]
            if item["verification"] == "supported"
        ]
        if not supported_hypotheses:
            questions = report["unresolved_questions"] or [
                "Which evidence would verify the leading root cause hypothesis?"
            ]
            report["unresolved_questions"] = questions
            outcome: dict[str, Any] = {
                "status": "insufficient_evidence",
                "stop_reason": "no_supported_root_cause",
                "allowed_evidence": sorted(
                    evidence.values(), key=lambda item: (item["path"], item["start_line"])
                ),
                "unresolved_questions": questions,
            }
            if mode == "interactive":
                outcome["clarification_questions"] = questions[:3]
            events = list(state["events"])
            events.append({
                "kind": "stage",
                "stage": "verified",
                "supported_hypotheses": 0,
                "references": [],
            })
            return {"outcome": outcome, "events": events}
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
        terminal_event = {
            "kind": "stage",
            "stage": "reported",
            "outcome": state["outcome"]["status"],
            "stop_reason": state["outcome"]["stop_reason"],
        }
        events = [
            {**event, "sequence": number}
            for number, event in enumerate(
                [*state.get("events", []), terminal_event], start=1
            )
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
                    "model_calls": metadata.get("model_calls", 0),
                    "structured_output_retries": metadata.get(
                        "structured_output_retries", 0
                    ),
                    "retrieval": {
                        key: g3_run.metadata[key]
                        for key in (
                            "embedding_identifier", "fusion_strategy",
                            "reranker_identifier", "top_k", "score_precision",
                        )
                        if key in g3_run.metadata
                    },
                    "budgets": {
                        "tool_calls": {
                            "limit": budget.tool_calls,
                            "used": sum(tool_counts.values()),
                        },
                        "retrieval_rewrites": {
                            "limit": budget.retrieval_rewrites, "used": 0,
                        },
                        "root_cause_hypotheses": {
                            "limit": budget.root_cause_hypotheses,
                            "used": len(state["draft"]["report"]["root_cause_hypotheses"]),
                        },
                        "patch_attempts": {"limit": budget.patch_attempts, "used": 0},
                        "run_time_ns": {
                            "limit": budget.run_time_ns, "used": run_clock() - started,
                        },
                    },
                },
            },
        }

    graph = StateGraph(_State)
    steps = (parse, plan, retrieve, summarize, hypotheses, verify, report)
    for step in steps:
        graph.add_node(step.__name__, step)
    graph.add_edge(START, parse.__name__)
    graph.add_edge(parse.__name__, plan.__name__)
    graph.add_edge(plan.__name__, retrieve.__name__)
    graph.add_conditional_edges(
        retrieve.__name__,
        lambda state: report.__name__ if "outcome" in state else summarize.__name__,
        {report.__name__: report.__name__, summarize.__name__: summarize.__name__},
    )
    graph.add_edge(summarize.__name__, hypotheses.__name__)
    graph.add_conditional_edges(
        hypotheses.__name__,
        lambda state: report.__name__ if "outcome" in state else verify.__name__,
        {report.__name__: report.__name__, verify.__name__: verify.__name__},
    )
    graph.add_edge(verify.__name__, report.__name__)
    graph.add_edge(report.__name__, END)
    return cast(dict[str, Any], graph.compile().invoke({})["result"])


def write_investigation_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
