"""Run the fixed Starlette patch case and save the authoritative local result."""

import json
import os
import sys
from pathlib import Path
from typing import Any

from issuelens.investigation import (
    PatchValidationPlan,
    run_investigation,
    write_investigation_result,
)
from issuelens.observability import export_to_langfuse
from issuelens.patch_sandbox import PatchSandboxTools, load_patch_sandbox_manifest
from issuelens.report import ModelResponse, ReportConfig, load_versioned_three_way
from issuelens.retrieval import RerankedRun, RetrievalCandidate, load_retrieval_input

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


def run(output_path: Path | None = None) -> dict[str, Any]:
    case_path = ROOT / "data/cases" / f"{CASE_ID}.json"
    case = load_retrieval_input(case_path)
    g3 = load_versioned_three_way(
        case_path, ROOT / "data/retrieval" / CASE_ID / "three-way.json"
    ).g3_reranked
    if not isinstance(g3, RerankedRun):
        raise ValueError("the approved case must use the fixed reranked retrieval run")
    manifest = load_patch_sandbox_manifest(
        ROOT / "data/patch-validation" / CASE_ID / "manifest.json"
    )
    if manifest.case_id != case.case_id or manifest.snapshot_commit != case.snapshot_commit:
        raise ValueError("the patch manifest does not match the approved case")
    source = ROOT / ".retrieval-cache/starlette"
    tools = PatchSandboxTools(
        manifest=manifest,
        source_repository=source,
        workspace_root=ROOT,
        retrieval_tools=FixedTools(g3.candidates),
    )
    result = run_investigation(
        case,
        g3,
        source,
        FixedReportModel(ROOT / "data/reports" / CASE_ID / "report.json"),
        ReportConfig("fixed-g3-report-v1", "agent-v1", "0", "0", "USD"),
        tools,
        patch_validation=PatchValidationPlan(
            patch_id="route-name-patch",
            target_test_id="route-name-target",
            regression_test_id="routing-regression",
        ),
    )
    export_to_langfuse(result, environ=os.environ)
    write_investigation_result(
        output_path or ROOT / "data/patch-validation" / CASE_ID / "run.json", result
    )
    return result


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else None
    print(json.dumps(run(path), indent=2))
