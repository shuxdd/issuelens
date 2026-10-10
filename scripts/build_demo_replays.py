"""Freeze the two offline safety scenarios used by the controlled Web Demo."""

import json
from datetime import UTC, datetime
from pathlib import Path

from issuelens.investigation import (
    InvestigationBudget,
    ObservationContext,
    run_investigation,
    write_investigation_result,
)
from issuelens.report import ModelResponse, ReportConfig
from issuelens.retrieval import RerankedRun, RetrievalInput

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STARTED_AT = datetime(2026, 10, 9, tzinfo=UTC)


class NoModel:
    def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
        raise AssertionError("safety replay must stop before model generation")


class StoppedTools:
    def __init__(self, deny: bool) -> None:
        self.deny = deny

    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        if self.deny:
            raise PermissionError("unsafe request")
        raise AssertionError("budget replay must stop before invoking a tool")


def build(output_dir: Path = DATA / "demo" / "runs") -> None:
    source = json.loads((DATA / "cases" / "starlette-issue-1552.json").read_text(encoding="utf-8"))
    safety = json.loads(
        (DATA / "evaluation" / "safe-termination-v1.json").read_text(encoding="utf-8")
    )
    dangerous = next(
        case for case in safety["cases"] if case["case_id"] == "safe-dangerous-01"
    )
    scenarios = (
        (
            "starlette-issue-1552-budget",
            source["issue"]["title"],
            source["issue"]["source_url"],
            source["repository_snapshot"]["commit"],
            InvestigationBudget(tool_calls=0),
            False,
            "budget_exhausted",
        ),
        (
            dangerous["case_id"],
            dangerous["issue"],
            dangerous["issue_source_url"],
            dangerous["snapshot_commit"],
            InvestigationBudget(),
            True,
            "unsafe_request",
        ),
    )
    for case_id, title, url, commit, budget, deny, expected in scenarios:
        case = RetrievalInput(case_id, "demo-v1", "Kludex/starlette", commit, title, url)
        result = run_investigation(
            case,
            RerankedRun(title, (), {}),
            DATA,
            NoModel(),
            ReportConfig("offline", "agent-v1", "0", "0", "USD"),
            StoppedTools(deny),
            budget=budget,
            clock=lambda: 0,
            run_clock=lambda: 0,
            observation=ObservationContext(
                run_id=f"{case_id}-demo-v1", started_at=STARTED_AT, clock=lambda: 0
            ),
        )
        if result["outcome"]["status"] != expected:
            raise ValueError(f"{case_id}: expected {expected}, got {result['outcome']['status']}")
        write_investigation_result(output_dir / f"{case_id}.json", result)


if __name__ == "__main__":
    build()
