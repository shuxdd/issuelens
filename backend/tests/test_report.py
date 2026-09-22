from pathlib import Path

import pytest
from issuelens.retrieval import RetrievalCandidate, RetrievalInput, RetrievalMetadata, RetrievalRun


def test_versioned_three_way_loader_reuses_existing_candidate_models() -> None:
    from issuelens.report import load_versioned_three_way
    from issuelens.retrieval import HybridCandidate, RerankedCandidate

    root = Path(__file__).resolve().parents[2]
    case = root / "data/cases/starlette-issue-1552.json"
    frozen = root / "data/retrieval/starlette-issue-1552/three-way.json"
    comparison = load_versioned_three_way(case, frozen)

    assert len(comparison.g1_vector.candidates) == 10
    assert len(comparison.g2_hybrid.candidates) == 10
    assert len(comparison.g3_reranked.candidates) == 10
    assert type(comparison.g1_vector.candidates[0]) is RetrievalCandidate
    assert type(comparison.g2_hybrid.candidates[0]) is HybridCandidate
    assert type(comparison.g3_reranked.candidates[0]) is RerankedCandidate


def test_versioned_report_run_uses_three_fixed_offline_responses(tmp_path: Path) -> None:
    import importlib.util

    from issuelens.report import ModelResponse, ReportConfig

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "report_script", root / "scripts/run_one_shot_report.py"
    )
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    class FixedModel:
        calls = 0

        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":'
                '[{"text":"Needs investigation","confidence":"low","evidence":[]}],'
                '"confidence":"low","unresolved_questions":[]}',
                100,
                20,
                120,
            )

    model = FixedModel()
    output = tmp_path / "report.json"
    runner.run(root, model, ReportConfig("fixed", "one-shot-v1", "0", "0", "USD"), output)

    import json

    groups = json.loads(output.read_text(encoding="utf-8"))["groups"]
    assert model.calls == 3
    assert set(groups) == {"g1_vector", "g2_hybrid", "g3_reranked"}
    assert all(
        group["report"]["root_cause_hypotheses"][0]["verification"] == "unverified_hypothesis"
        for group in groups.values()
    )


def test_prompt_shows_the_exact_report_json_shape(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    case = RetrievalInput("fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url")
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            prompt = messages[0]["content"]
            assert '"issue_classification"' in prompt
            assert '"root_cause_hypotheses"' in prompt
            assert '"candidate_rank"' in prompt
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],'
                '"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    generate_report(
        case, run, tmp_path, FixedModel(), ReportConfig("fixed", "one-shot-v1", "0", "0", "USD")
    )


def test_valid_fixed_response_becomes_citable_english_report(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    source = tmp_path / "starlette" / "routing.py"
    source.parent.mkdir()
    source.write_text("def get_name(endpoint):\n    return endpoint.__name__\n", encoding="utf-8")
    (tmp_path / ".issuelens-snapshot.json").write_text(
        '{"repository":"Kludex/starlette","commit":"abc123"}', encoding="utf-8"
    )
    retrieval_input = RetrievalInput(
        "fixture",
        "cases-v1",
        "Kludex/starlette",
        "abc123",
        "Route name is wrong",
        "https://github.com/Kludex/starlette/issues/1",
    )
    candidate = RetrievalCandidate(
        1,
        "0.9",
        "abc123",
        "starlette/routing.py",
        1,
        2,
        "python_source",
        "v1",
        "def get_name(endpoint):\n    return endpoint.__name__",
        "https://github.com/Kludex/starlette/blob/abc123/starlette/routing.py#L1-L2",
    )
    run = RetrievalRun(
        "Route name is wrong",
        (candidate,),
        RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 1, "retrieval-v1", 1),
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            import json

            candidate_text = json.loads(messages[1]["content"])["candidates"][0]["content"]
            assert "1: def get_name(endpoint):" in candidate_text
            assert "2:     return endpoint.__name__" in candidate_text
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"The name uses '
                'endpoint.__name__.","evidence":[{"candidate_rank":1,"start_line":1,'
                '"end_line":2,"excerpt":"return endpoint.__name__"}]}],'
                '"root_cause_hypotheses":[],"confidence":"medium",'
                '"unresolved_questions":["Which endpoint type fails?"]}',
                100,
                40,
                140,
            )

    result = generate_report(
        retrieval_input,
        run,
        tmp_path,
        FixedModel(),
        ReportConfig("fixed-model", "one-shot-v1", "0.00", "0.00", "USD"),
    )

    assert result["report"]["schema_version"] == "1.0"
    assert result["report"]["facts"][0]["evidence"][0]["reference"] == candidate.reference
    assert result["report"]["unresolved_questions"] == ["Which endpoint type fails?"]


def test_invalid_candidate_reference_reports_field_path(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    (tmp_path / "routing.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / ".issuelens-snapshot.json").write_text(
        '{"repository":"Kludex/starlette","commit":"abc123"}', encoding="utf-8"
    )
    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    candidate = RetrievalCandidate(
        1,
        "0.9",
        "abc123",
        "routing.py",
        1,
        1,
        "python_source",
        "v1",
        "pass",
        "https://github.com/Kludex/starlette/blob/abc123/routing.py#L1-L1",
    )
    run = RetrievalRun(
        "Issue", (candidate,), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 1, "v1", 1)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"Wrong fact",'
                '"evidence":[{"candidate_rank":2,"start_line":1,"end_line":1,'
                '"excerpt":"pass"}]}],"root_cause_hypotheses":[],"confidence":"low",'
                '"unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match=r"facts\[0\]\.evidence\[0\].*candidate_rank"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_root_cause_without_evidence_is_explicitly_unverified(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":'
                '[{"text":"A name fallback may be wrong","confidence":"low","evidence":[]}],'
                '"confidence":"low","unresolved_questions":["What path reproduces it?"]}',
                1,
                1,
                2,
            )

    result = generate_report(
        retrieval_input,
        run,
        tmp_path,
        FixedModel(),
        ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
    )

    assert result["report"]["root_cause_hypotheses"][0]["verification"] == "unverified_hypothesis"


def test_malformed_structured_output_has_diagnostic_error(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse("{broken", 1, 1, 2)

    with pytest.raises(ValueError, match="model output: invalid JSON"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_four_root_cause_hypotheses_are_rejected(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )
    hypothesis = {"text": "Possible cause", "confidence": "low", "evidence": []}

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            import json

            return ModelResponse(
                json.dumps(
                    {
                        "issue_classification": "routing",
                        "facts": [],
                        "root_cause_hypotheses": [hypothesis] * 4,
                        "confidence": "low",
                        "unresolved_questions": [],
                    }
                ),
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="root_cause_hypotheses: expected at most 3"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_citation_must_resolve_in_investigation_snapshot(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    (tmp_path / ".issuelens-snapshot.json").write_text(
        '{"repository":"Kludex/starlette","commit":"abc123"}', encoding="utf-8"
    )
    candidate = RetrievalCandidate(
        1,
        "0.9",
        "abc123",
        "missing.py",
        1,
        1,
        "python_source",
        "v1",
        "pass",
        "https://github.com/Kludex/starlette/blob/abc123/missing.py#L1-L1",
    )
    run = RetrievalRun(
        "Issue", (candidate,), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 1, "v1", 1)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"A claim","evidence":'
                '[{"candidate_rank":1,"start_line":1,"end_line":1,"excerpt":"pass"}]}],'
                '"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match=r"candidate\[0\].*path does not resolve"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_root_cause_evidence_cannot_use_unknown_candidate(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":'
                '[{"text":"Cause","confidence":"high","evidence":[{"candidate_rank":1,'
                '"start_line":1,"end_line":1,"excerpt":"pass"}]}],'
                '"confidence":"high","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(
        ValueError, match=r"root_cause_hypotheses\[0\]\.evidence\[0\].*candidate_rank"
    ):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_citation_excerpt_must_match_cited_snapshot_lines(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    (tmp_path / "routing.py").write_text("return endpoint.__name__\n", encoding="utf-8")
    (tmp_path / ".issuelens-snapshot.json").write_text(
        '{"repository":"Kludex/starlette","commit":"abc123"}', encoding="utf-8"
    )
    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    candidate = RetrievalCandidate(
        1,
        "0.9",
        "abc123",
        "routing.py",
        1,
        1,
        "python_source",
        "v1",
        "return endpoint.__name__",
        "https://github.com/Kludex/starlette/blob/abc123/routing.py#L1-L1",
    )
    run = RetrievalRun(
        "Issue", (candidate,), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 1, "v1", 1)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"A claim","evidence":'
                '[{"candidate_rank":1,"start_line":1,"end_line":1,"excerpt":"return other"}]}],'
                '"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match=r"facts\[0\]\.evidence\[0\].*excerpt"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_missing_required_schema_field_has_diagnostic_error(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="confidence: required"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_wrong_schema_type_has_diagnostic_error(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":"oops","root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="facts: expected array"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_fact_without_evidence_is_rejected_as_unverified(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":'
                '[{"text":"Unverified claim","evidence":[]}],'
                '"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match=r"facts\[0\].*requires evidence"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_three_groups_share_one_prompt_model_config_and_generator(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_three_way_reports
    from issuelens.retrieval import HybridRun, RerankedRun, RerankerComparison

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    g1 = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )
    g2 = HybridRun("Issue", (), {"strategy": "rrf-v1"})
    g3 = RerankedRun("Issue", (), {"reranker_identifier": "fixed"})
    comparison = RerankerComparison({"query": "Issue"}, g1, g2, g3, {})
    observed: list[tuple[list[dict[str, str]], ReportConfig]] = []

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            observed.append((messages, config))
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    config = ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD")
    result = generate_three_way_reports(retrieval_input, comparison, tmp_path, FixedModel(), config)

    assert set(result) == {"g1_vector", "g2_hybrid", "g3_reranked"}
    assert len(observed) == 3
    assert all(
        messages == observed[0][0] and used_config == config for messages, used_config in observed
    )


def test_report_records_provider_usage_cost_latency_and_versions(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                100,
                40,
                140,
            )

    ticks = iter((10, 110))
    result = generate_report(
        retrieval_input,
        run,
        tmp_path,
        FixedModel(),
        ReportConfig("fixed-model", "one-shot-v1", "2", "4", "USD"),
        clock=lambda: next(ticks),
    )

    assert result["metadata"] == {
        "model": "fixed-model",
        "prompt_version": "one-shot-v1",
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "missed_input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
        "cost": "0.00036",
        "currency": "USD",
        "latency_ns": 100,
        "input_price_per_million": "2",
        "cached_input_price_per_million": "0",
        "output_price_per_million": "4",
    }


def test_inconsistent_provider_usage_is_not_recorded_as_real_cost(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                100,
                40,
                100,
            )

    with pytest.raises(ValueError, match="model usage: inconsistent token counts"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "2", "4", "USD"),
        )


def test_rest_review_returns_versioned_report_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from fastapi.testclient import TestClient
    from issuelens import api

    result = {
        "schema_version": "1.0",
        "case_id": "fixture",
        "groups": {"g1_vector": {"report": {}}},
    }
    path = tmp_path / "fixture" / "report.json"
    path.parent.mkdir()
    path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(api, "REPORT_DIRECTORY", tmp_path, raising=False)

    response = TestClient(api.app).get("/reports/three-way/fixture")

    assert response.status_code == 200
    assert response.json() == result


def test_nested_schema_error_identifies_claim_field(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"Claim","evidence":"bad"}],'
                '"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match=r"facts\[0\]\.evidence: expected array"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_repair_evidence_and_gold_never_reach_report_model(tmp_path: Path) -> None:
    import json

    from issuelens.report import ModelResponse, ReportConfig, generate_three_way_reports
    from issuelens.retrieval import (
        HybridRun,
        RerankedRun,
        RerankerComparison,
        load_retrieval_input,
    )

    marker = "REPAIR_ONLY_SENTINEL_07"
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "case_id": "fixture",
                "schema_version": "cases-v1",
                "issue": {"repository": "Kludex/starlette", "title": "Issue", "source_url": "url"},
                "repository_snapshot": {"commit": "abc123"},
                "repair_evidence": {"pull_request": {"title": marker}},
            }
        ),
        encoding="utf-8",
    )
    retrieval_input = load_retrieval_input(case_path)
    g1 = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )
    comparison = RerankerComparison(
        {"query": "Issue"},
        g1,
        HybridRun("Issue", (), {}),
        RerankedRun("Issue", (), {}),
        {"gold": marker, "repair_evidence": marker},
    )
    observed: list[str] = []

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            observed.append(json.dumps(messages))
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    generate_three_way_reports(
        retrieval_input,
        comparison,
        tmp_path,
        FixedModel(),
        ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
    )

    assert len(observed) == 3
    assert all(marker not in prompt for prompt in observed)
    assert all("untrusted" in prompt for prompt in observed)


def test_three_way_reports_write_versioned_review_result(tmp_path: Path) -> None:
    import json

    from issuelens.report import write_three_way_reports

    path = tmp_path / "fixture" / "report.json"
    groups = {
        name: {"report": {"schema_version": "1.0"}, "metadata": {"model": "fixed"}}
        for name in ("g1_vector", "g2_hybrid", "g3_reranked")
    }

    write_three_way_reports(path, "fixture", groups)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "case_id": "fixture",
        "groups": groups,
    }


def test_confidence_must_use_stable_schema_value(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"certain","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="confidence: expected low, medium, or high"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_classification_requires_english_text_field(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":42,"facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="issue_classification: expected nonempty string"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_missing_evidence_line_has_diagnostic_error(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"Claim","evidence":'
                '[{"candidate_rank":1,"end_line":1,"excerpt":"pass"}]}],'
                '"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match=r"facts\[0\]\.evidence\[0\]\.start_line: required"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_unknown_output_field_cannot_carry_repair_evidence(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[],"repair_evidence":"future"}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="repair_evidence: unexpected field"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_citation_requires_matching_snapshot_manifest(tmp_path: Path) -> None:
    import json

    from issuelens.report import ModelResponse, ReportConfig, generate_report

    (tmp_path / "routing.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / ".issuelens-snapshot.json").write_text(
        json.dumps(
            {
                "repository": "Kludex/starlette",
                "commit": "future-commit",
            }
        ),
        encoding="utf-8",
    )
    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    candidate = RetrievalCandidate(
        1,
        "0.9",
        "abc123",
        "routing.py",
        1,
        1,
        "python_source",
        "v1",
        "pass",
        "https://github.com/Kludex/starlette/blob/abc123/routing.py#L1-L1",
    )
    run = RetrievalRun(
        "Issue", (candidate,), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 1, "v1", 1)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[{"text":"Claim","evidence":'
                '[{"candidate_rank":1,"start_line":1,"end_line":1,"excerpt":"pass"}]}],'
                '"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                1,
                1,
                2,
            )

    with pytest.raises(ValueError, match="snapshot manifest does not match retrieval input"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_prompt_version_must_match_actual_template(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse("{}", 1, 1, 2)

    with pytest.raises(ValueError, match="unsupported prompt version"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "invented-v2", "0", "0", "USD"),
        )


def test_future_candidate_is_rejected_before_model_receives_it(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    (tmp_path / "routing.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / ".issuelens-snapshot.json").write_text(
        '{"repository":"Kludex/starlette","commit":"abc123"}', encoding="utf-8"
    )
    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    candidate = RetrievalCandidate(
        1,
        "0.9",
        "future-commit",
        "routing.py",
        1,
        1,
        "python_source",
        "v1",
        "pass",
        "https://github.com/Kludex/starlette/blob/future-commit/routing.py#L1-L1",
    )
    run = RetrievalRun(
        "Issue", (candidate,), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 1, "v1", 1)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            pytest.fail("future candidate reached model")

    with pytest.raises(ValueError, match=r"candidate\[0\].*commit or reference"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_negative_price_is_rejected_before_model_call(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            pytest.fail("invalid pricing reached model")

    with pytest.raises(ValueError, match="model pricing: nonnegative finite rates required"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "-1", "0", "USD"),
        )


def test_three_groups_cannot_change_the_issue_query(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_three_way_reports
    from issuelens.retrieval import HybridRun, RerankedRun, RerankerComparison

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    g1 = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )
    comparison = RerankerComparison(
        {"query": "Issue"},
        g1,
        HybridRun("different issue", (), {}),
        RerankedRun("Issue", (), {}),
        {},
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            pytest.fail("mismatched query reached model")

    with pytest.raises(ValueError, match="three-way report requires one shared query"):
        generate_three_way_reports(
            retrieval_input,
            comparison,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_single_report_rejects_query_from_another_issue(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Another issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            pytest.fail("wrong issue reached model")

    with pytest.raises(ValueError, match="retrieval query does not match investigation issue"):
        generate_report(
            retrieval_input,
            run,
            tmp_path,
            FixedModel(),
            ReportConfig("fixed-model", "one-shot-v1", "0", "0", "USD"),
        )


def test_cost_uses_provider_cache_hits_and_misses(tmp_path: Path) -> None:
    from issuelens.report import ModelResponse, ReportConfig, generate_report

    retrieval_input = RetrievalInput(
        "fixture", "cases-v1", "Kludex/starlette", "abc123", "Issue", "url"
    )
    run = RetrievalRun(
        "Issue", (), RetrievalMetadata("cases-v1", "abc123", "v1", "fixed", 2, 0, "v1", 0)
    )

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                100,
                40,
                140,
                cached_input_tokens=60,
            )

    result = generate_report(
        retrieval_input,
        run,
        tmp_path,
        FixedModel(),
        ReportConfig(
            "fixed-model", "one-shot-v1", "2", "4", "USD", cached_input_price_per_million="1"
        ),
    )

    assert result["metadata"]["cost"] == "0.0003"
    assert result["metadata"]["cached_input_tokens"] == 60
