"""Replay the fixed supported investigation without network or model calls."""

import json
from pathlib import Path
from typing import Any

from issuelens.investigation import run_investigation, write_investigation_result
from issuelens.report import (
    ModelResponse,
    ReportConfig,
    load_versioned_three_way,
)
from issuelens.retrieval import RetrievalCandidate, load_retrieval_input

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "starlette-issue-1552"


class FixedReportModel:
    def __init__(self, path: Path) -> None:
        report = json.loads(path.read_text(encoding="utf-8"))["groups"]["g3_reranked"]["report"]
        report.pop("schema_version")
        for section in ("facts", "root_cause_hypotheses"):
            for claim in report[section]:
                claim.pop("verification", None)
                claim["evidence"] = [
                    {
                        key: evidence[key]
                        for key in ("candidate_rank", "start_line", "end_line", "excerpt")
                    }
                    for evidence in claim["evidence"]
                ]
        self.content = json.dumps(report)

    def generate(
        self, messages: list[dict[str, str]], config: ReportConfig
    ) -> ModelResponse:
        return ModelResponse(self.content, 0, 0, 0)


class FixedTools:
    def __init__(self, candidates: tuple[RetrievalCandidate, ...]) -> None:
        self.candidates = candidates

    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        return list(self.candidates) if name in {"search_code", "semantic_search"} else []


def run(root: Path = ROOT) -> dict[str, Any]:
    case_path = root / "data/cases" / f"{CASE_ID}.json"
    case = load_retrieval_input(case_path)
    g3 = load_versioned_three_way(
        case_path, root / "data/retrieval" / CASE_ID / "three-way.json"
    ).g3_reranked
    snapshot = (
        root / ".retrieval-cache" / f"starlette-{case.snapshot_commit}"
        / f"starlette-{case.snapshot_commit}"
    )
    result = run_investigation(
        case, g3, snapshot,
        FixedReportModel(root / "data/reports" / CASE_ID / "report.json"),
        ReportConfig("fixed-g3-report-v1", "agent-v1", "0", "0", "USD"),
        FixedTools(g3.candidates),
        clock=lambda: 0,
        run_clock=lambda: 0,
    )
    write_investigation_result(root / "data/investigations" / CASE_ID / "run.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
