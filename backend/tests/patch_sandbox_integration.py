import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from issuelens.investigation import PatchValidationPlan, run_investigation
from issuelens.patch_sandbox import PatchSandboxTools, load_patch_sandbox_manifest
from issuelens.report import ModelResponse, ReportConfig, load_versioned_three_way
from issuelens.retrieval import RerankedRun, RetrievalCandidate, load_retrieval_input

ROOT = Path(__file__).resolve().parents[2]
CASE_ID = "starlette-issue-1552"
PATCH_SOURCE = ROOT / ".retrieval-cache/campaign-sources" / CASE_ID


class FixedReportModel:
    def __init__(self) -> None:
        path = ROOT / "data/reports" / CASE_ID / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))["groups"]["g3_reranked"][
            "report"
        ]
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


class FixedRetrievalTools:
    def __init__(self, candidates: tuple[RetrievalCandidate, ...]) -> None:
        self.candidates = candidates

    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        return list(self.candidates)


def test_starlette_1552_patch_validation_isolated_in_docker() -> None:
    case_path = ROOT / "data/cases" / f"{CASE_ID}.json"
    case = load_retrieval_input(case_path)
    g3 = load_versioned_three_way(
        case_path, ROOT / "data/retrieval" / CASE_ID / "three-way.json"
    ).g3_reranked
    assert isinstance(g3, RerankedRun)
    manifest = load_patch_sandbox_manifest(
        ROOT / "data/patch-validation" / CASE_ID / "manifest.json"
    )
    tools = PatchSandboxTools(
        manifest=manifest,
        source_repository=PATCH_SOURCE,
        workspace_root=ROOT,
        retrieval_tools=FixedRetrievalTools(g3.candidates),
    )

    result = run_investigation(
        case,
        g3,
        ROOT / ".retrieval-cache/starlette",
        FixedReportModel(),
        ReportConfig("fixed-g3-report-v1", "agent-v1", "0", "0", "USD"),
        tools,
        patch_validation=PatchValidationPlan(
            patch_id="route-name-patch",
            target_test_id="route-name-target",
            regression_test_id="routing-regression",
        ),
    )

    validation = result["patch_validation"]
    assert validation["reproduction"]["status"] == "reproduced"
    assert [entry["status"] for entry in validation["executions"][:2]] == [
        "failed", "failed"
    ]
    assert all(
        "AssertionError" in entry["output"]
        for entry in validation["executions"][:2]
    )
    assert validation["executions"][0]["output"] == validation["executions"][1]["output"]
    assert len(validation["executions"]) == 4
    assert validation["patch_application"]["status"] == "applied"
    assert "diff --git a/starlette/routing.py b/starlette/routing.py" in validation["patch"]["diff"]
    assert validation["target_test"]["status"] == "passed"
    assert validation["regression_test"]["status"] == "passed"
    assert validation["source_repository"]["unchanged"] is True
    assert validation["worktree"] == {"created": True, "cleaned": True}
    assert validation["limits"]["memory_swap_bytes"] == 536_870_912
    assert all(execution["limits_verified"] for execution in validation["executions"])


def test_sandbox_stops_at_the_output_limit() -> None:
    manifest = load_patch_sandbox_manifest(
        ROOT / "data/patch-validation" / CASE_ID / "manifest.json"
    )
    tools = PatchSandboxTools(
        manifest=manifest,
        source_repository=PATCH_SOURCE,
        workspace_root=ROOT,
        retrieval_tools=FixedRetrievalTools(()),
    )

    result = tools.invoke("run_tests", {"test_id": "output-limit-probe"})
    summary = tools.finish_patch_validation()

    assert result == {"passed": False, "status": "output_limited"}
    assert summary["executions"][0]["output_bytes"] == 1_048_576
    assert summary["executions"][0]["limits_verified"] is True
    assert summary["worktree"] == {"created": True, "cleaned": True}


def test_sandbox_enforces_the_shared_deadline() -> None:
    manifest = load_patch_sandbox_manifest(
        ROOT / "data/patch-validation" / CASE_ID / "manifest.json"
    )
    manifest = replace(manifest, limits=replace(manifest.limits, timeout_seconds=6))
    tools = PatchSandboxTools(
        manifest=manifest,
        source_repository=PATCH_SOURCE,
        workspace_root=ROOT,
        retrieval_tools=FixedRetrievalTools(()),
    )

    result = tools.invoke("run_tests", {"test_id": "timeout-probe"})
    summary = tools.finish_patch_validation()

    assert result == {"passed": False, "status": "timed_out"}
    assert summary["executions"][0]["limits_verified"] is True
    assert summary["worktree"] == {"created": True, "cleaned": True}


def test_sandbox_cannot_reach_network_or_host_secret_paths() -> None:
    manifest = load_patch_sandbox_manifest(
        ROOT / "data/patch-validation" / CASE_ID / "manifest.json"
    )
    tools = PatchSandboxTools(
        manifest=manifest,
        source_repository=PATCH_SOURCE,
        workspace_root=ROOT,
        retrieval_tools=FixedRetrievalTools(()),
    )

    result = tools.invoke("run_tests", {"test_id": "isolation-probe"})
    summary = tools.finish_patch_validation()

    assert result == {"passed": True, "status": "passed"}
    assert summary["executions"][0]["limits_verified"] is True
    assert summary["source_repository"]["unchanged"] is True


def test_patch_validation_persists_complete_local_result_without_langfuse(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run.json"
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_patch_validation.py"), str(output)],
        cwd=ROOT,
        env={
            **os.environ,
            "LANGFUSE_ENABLED": "false",
            "PYTHONPATH": str(ROOT / "backend/src"),
        },
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(output.read_text(encoding="utf-8"))

    validation = result["patch_validation"]
    assert validation["patch_application"]["status"] == "applied"
    assert validation["target_test"]["status"] == "passed"
    assert validation["regression_test"]["status"] == "passed"
    assert validation["source_repository"]["unchanged"] is True
