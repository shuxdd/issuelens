import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from issuelens.retrieval import (
    BM25Config,
    Chunk,
    ChunkingConfig,
    Corpus,
    FusionConfig,
    HybridCandidate,
    HybridRun,
    RecallMetric,
    RecallReport,
    RerankedRun,
    RetrievalCandidate,
    RetrievalComparison,
    RetrievalMetadata,
    RetrievalMetrics,
    RetrievalRun,
    VectorIndexConfig,
    build_corpus,
    load_retrieval_input,
    run_retrieval_comparison,
)


def test_document_vector_cache_survives_new_embedder_and_changes_with_input(
    tmp_path: Path,
) -> None:
    from issuelens.retrieval import CachedDocumentEmbedder

    class CountingEmbedder:
        identifier = "fixed-model@revision-1"
        dimension = 2

        def __init__(self) -> None:
            self.calls = 0

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
            self.calls += 1
            return [(float(len(text)), 1.0) for text in texts]

        def embed_query(self, text: str) -> tuple[float, ...]:
            return (float(len(text)), 1.0)

    first = CountingEmbedder()
    assert CachedDocumentEmbedder(first, tmp_path).embed_documents(["a"]) == [(1.0, 1.0)]
    second = CountingEmbedder()
    cache = CachedDocumentEmbedder(second, tmp_path)
    assert cache.embed_documents(["a"]) == [(1.0, 1.0)]
    assert second.calls == 0
    assert cache.embed_documents(["bb"]) == [(2.0, 1.0)]
    assert second.calls == 1


def test_reranker_scores_g2_candidates_and_preserves_original_trace() -> None:
    from issuelens.retrieval import RerankerConfig, rerank_hybrid_run

    def candidate(rank: int, path: str, score: str) -> HybridCandidate:
        return HybridCandidate(
            rank=rank,
            score=score,
            commit="e086fc2",
            path=path,
            start_line=10,
            end_line=12,
            source_type="python_source",
            index_version="hybrid-v1",
            content=f"content for {path}",
            reference=f"https://example.test/{path}#L10-L12",
            vector_rank=rank,
            vector_score="0.500000",
            vector_contribution="0.01600000",
            bm25_rank=rank,
            bm25_score="2.000000",
            bm25_contribution="0.01600000",
        )

    g2 = HybridRun(
        query="route name",
        candidates=(candidate(1, "a.py", "0.03200000"), candidate(2, "b.py", "0.03100000")),
        metadata={"strategy": "rrf-v1"},
    )

    class FixedReranker:
        identifier = "fixed-test-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            assert query == "route name"
            assert candidates == g2.candidates
            return [0.25, 0.75]

    result = rerank_hybrid_run(
        g2, FixedReranker(), RerankerConfig(enabled=True, top_k=2, score_precision=6)
    )

    assert isinstance(result, RerankedRun)
    assert [
        (
            item.rank,
            item.path,
            item.score,
            item.g2_rank,
            item.rrf_score,
            item.vector_score,
            item.bm25_score,
            item.reference,
        )
        for item in result.candidates
    ] == [
        (1, "b.py", "0.750000", 2, "0.03100000", "0.500000", "2.000000", "https://example.test/b.py#L10-L12"),
        (2, "a.py", "0.250000", 1, "0.03200000", "0.500000", "2.000000", "https://example.test/a.py#L10-L12"),
    ]


def test_disabled_reranker_returns_g2_without_constructing_a_model() -> None:
    from issuelens.retrieval import RerankerConfig, rerank_hybrid_run

    g2 = HybridRun(query="route name", candidates=(), metadata={"strategy": "rrf-v1"})

    result = rerank_hybrid_run(
        g2, config=RerankerConfig(enabled=False, top_k=10, score_precision=6)
    )

    assert result is g2


def test_g3_comparison_uses_the_existing_g1_g2_shared_input() -> None:
    from issuelens.retrieval import RerankerConfig, add_reranker_comparison

    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            assert len(texts) == 2
            return [(1.0, 0.0), (0.0, 1.0)]

        def embed_query(self, text: str) -> tuple[float, float]:
            assert text == "route name"
            return (1.0, 0.0)

    corpus = Corpus(
        chunks=tuple(
            Chunk("python_source", "Kludex/starlette", "e086fc2", path, 1, 1, content, "v1")
            for path, content in (("a.py", "route"), ("b.py", "name"))
        ),
        chunking_config=ChunkingConfig("v1", 120, 20),
    )
    baseline = run_retrieval_comparison(
        corpus,
        "route name",
        FixedEmbedder(),
        VectorIndexConfig(2, 6, "retrieval-v1", "cases-v1"),
        BM25Config(2, 6, "code-text-v1", 1.2, 0.75),
        FusionConfig(2, 8, 60, 1.0, 1.0),
    )

    class FixedReranker:
        identifier = "fixed-test-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            assert query == baseline.shared_input["query"]
            assert tuple(candidates) == baseline.g2_hybrid.candidates
            return [float(-candidate.rank) for candidate in candidates]

    comparison = add_reranker_comparison(
        baseline, FixedReranker(), RerankerConfig(True, 2, 6)
    )

    assert comparison.shared_input == baseline.shared_input
    assert comparison.g1_vector is baseline.g1_vector
    assert comparison.g2_hybrid is baseline.g2_hybrid
    assert [item.path for item in comparison.g3_reranked.candidates] == [
        item.path for item in baseline.g2_hybrid.candidates
    ]


def test_fastembed_reranker_uses_only_a_pinned_local_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issuelens.retrieval import FastEmbedRerankerAdapter

    captured: dict[str, object] = {}

    class FakeCrossEncoder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def rerank(self, query: str, documents: Sequence[str], batch_size: int) -> list[float]:
            captured["query"] = query
            captured["documents"] = list(documents)
            captured["batch_size"] = batch_size
            return [0.75]

    monkeypatch.setattr(
        "issuelens.retrieval.import_module",
        lambda name: SimpleNamespace(TextCrossEncoder=FakeCrossEncoder),
    )
    adapter = FastEmbedRerankerAdapter(
        model_name="Xenova/ms-marco-MiniLM-L-6-v2",
        model_revision="fixed-revision",
        model_path=tmp_path,
    )
    candidate = HybridCandidate(
        1, "0.03200000", "e086fc2", "routing.py", 1, 1, "python_source", "v1",
        "route body", "https://example.test/routing.py#L1-L1", 1, "0.5", "0.016",
        1, "2.0", "0.016",
    )

    assert adapter.score("route name", (candidate,)) == [0.75]
    assert captured == {
        "model_name": "Xenova/ms-marco-MiniLM-L-6-v2",
        "specific_model_path": str(tmp_path),
        "local_files_only": True,
        "threads": 1,
        "cuda": False,
        "query": "route name",
        "documents": ["route body"],
        "batch_size": 10,
    }
    assert adapter.identifier == "fastembed:Xenova/ms-marco-MiniLM-L-6-v2@fixed-revision"


def test_root_cause_regression_gate_rejects_a_metric_below_preregistered_threshold() -> None:
    from issuelens.retrieval import check_retrieval_thresholds

    def report(root_recall: float) -> RecallReport:
        return RecallReport(
            top_k=5,
            root_cause_files=RecallMetric(1 if root_recall else 0, 1, root_recall),
            changed_files=RecallMetric(0, 1, 0.0),
            test_files=RecallMetric(0, 1, 0.0),
        )

    thresholds = {
        "root_cause_recall_at_5": 1.0,
        "root_cause_recall_at_10": 1.0,
        "mrr": 0.5,
        "ndcg_at_10": 0.63,
    }
    passing = RetrievalMetrics(report(1.0), report(1.0), 0.5, 0.63)
    failing = RetrievalMetrics(report(0.0), report(1.0), 0.5, 0.63)

    assert check_retrieval_thresholds(passing, thresholds) == []
    assert check_retrieval_thresholds(failing, thresholds) == [
        "root_cause_recall_at_5: 0.0 < 1.0"
    ]


def test_versioned_offline_regression_suite_checks_real_and_hand_fixed_inputs() -> None:
    from issuelens.retrieval import run_regression_suite

    manifest = Path(__file__).resolve().parents[2] / "data/retrieval/regression/manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["thresholds"] == {
        "root_cause_recall_at_5": 1.0,
        "root_cause_recall_at_10": 1.0,
        "mrr": 0.5,
        "ndcg_at_10": 0.63,
    }
    result = run_regression_suite(manifest)

    assert result["status"] == "passed"
    assert [entry["kind"] for entry in result["entries"]] == [
        "real_snapshot",
        "hand_fixed_corpus",
    ]
    assert result["entries"][1]["pipeline"] == "vector+bm25+rrf+reranker"
    assert all(entry["failures"] == [] for entry in result["entries"])


def test_regression_cli_returns_failure_when_a_fixed_input_falls_below_threshold(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    original = root / "data/retrieval/regression/manifest.json"
    manifest = json.loads(original.read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        entry["source"] = str((original.parent / entry["source"]).resolve())
        entry["gold"] = str((original.parent / entry["gold"]).resolve())
    frozen = json.loads(Path(manifest["entries"][0]["source"]).read_text(encoding="utf-8"))
    frozen["groups"]["g2_hybrid"]["candidates"] = [
        candidate for candidate in frozen["groups"]["g2_hybrid"]["candidates"]
        if candidate["path"] != "starlette/routing.py"
    ]
    degraded = tmp_path / "degraded-g2.json"
    degraded.write_text(json.dumps(frozen), encoding="utf-8")
    manifest["entries"][0]["source"] = str(degraded)
    failing_manifest = tmp_path / "failing-manifest.json"
    failing_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_retrieval_regression.py"),
         "--manifest", str(failing_manifest)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(root / "backend/src"), "HF_HUB_OFFLINE": "1"},
        check=False,
    )

    assert result.returncode == 1
    assert "root_cause_recall_at_5" in result.stdout


def test_ci_runs_the_frozen_regression_gate_offline() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")

    assert "uv run --frozen --offline python scripts/check_retrieval_regression.py" in workflow
    assert "PYTHONPATH: backend/src" in workflow


def test_three_way_result_exposes_metrics_and_every_candidate_stage(
    tmp_path: Path,
) -> None:
    from issuelens.retrieval import (
        RerankerConfig,
        add_reranker_comparison,
        write_reranker_comparison,
    )

    common: dict[str, Any] = dict(
        commit="e086fc2", path="root.py", start_line=1, end_line=1,
        source_type="python_source", index_version="v1", content="route name",
        reference="https://example.test/root.py#L1-L1",
    )
    g1 = RetrievalRun(
        query="route name",
        candidates=(RetrievalCandidate(rank=1, score="0.900000", **common),),
        metadata=RetrievalMetadata("cases-v1", "e086fc2", "v1", "fixed-v1", 2, 1, "v1", 1),
    )
    g2 = HybridRun(
        query="route name",
        candidates=(HybridCandidate(
            rank=1, score="0.03200000", vector_rank=1, vector_score="0.900000",
            vector_contribution="0.01600000", bm25_rank=1, bm25_score="2.000000",
            bm25_contribution="0.01600000", **common,
        ),),
        metadata={"strategy": "rrf-v1"},
    )
    baseline = RetrievalComparison(
        shared_input={"query": "route name", "top_k": 1},
        g1_vector=g1,
        g2_hybrid=g2,
        metadata={"data_version": "cases-v1"},
    )

    class FixedReranker:
        identifier = "fixed-test-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            return [0.75]

    comparison = add_reranker_comparison(baseline, FixedReranker(), RerankerConfig(True, 1, 6))
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({
        "root_cause_files": [{"path": "root.py", "relevance": 2}],
        "changed_files": [{"path": "root.py"}], "test_files": [],
    }), encoding="utf-8")
    output = tmp_path / "three-way.json"

    write_reranker_comparison(output, "fixture", comparison, gold)
    result = json.loads(output.read_text(encoding="utf-8"))

    assert list(result["groups"]) == ["g1_vector", "g2_hybrid", "g3_reranked"]
    assert result["groups"]["g3_reranked"]["metrics"]["mrr"] == 1.0
    assert result["groups"]["g3_reranked"]["candidates"][0] == {
        **{**common, "rank": 1, "score": "0.750000"},
        "vector_rank": 1, "vector_score": "0.900000", "vector_contribution": "0.01600000",
        "bm25_rank": 1, "bm25_score": "2.000000", "bm25_contribution": "0.01600000",
        "g2_rank": 1, "rrf_score": "0.03200000", "reranker_score": "0.750000",
    }
    assert result["metadata"]["reranker"]["identifier"] == "fixed-test-v1"


def test_cached_reranker_files_must_match_pinned_hashes(tmp_path: Path) -> None:
    from issuelens.retrieval import verify_model_artifacts

    model = tmp_path / "model"
    model.mkdir()
    (model / "onnx").mkdir()
    (model / "onnx/model.onnx").write_bytes(b"fixed model")
    expected = hashlib.sha256(b"fixed model").hexdigest()

    assert verify_model_artifacts(model, {"onnx/model.onnx": expected}) == 11
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_model_artifacts(model, {"onnx/model.onnx": "0" * 64})
    with pytest.raises(ValueError, match="missing"):
        verify_model_artifacts(model, {"tokenizer.json": expected})


def test_real_experiment_refuses_unpinned_model_without_loading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issuelens.retrieval import load_local_reranker

    monkeypatch.setattr(
        "issuelens.retrieval.import_module",
        lambda _name: pytest.fail("production model load"),
    )
    config = {"enabled": True, "model": {"revision": None, "artifact_files": {}}}

    with pytest.raises(ValueError, match="pinned local Reranker model"):
        load_local_reranker(config, tmp_path)


def test_disabled_model_configuration_skips_cache_and_model_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issuelens.retrieval import load_local_reranker

    monkeypatch.setattr(
        "issuelens.retrieval.import_module",
        lambda _name: pytest.fail("production model load"),
    )
    config = {"enabled": False, "model": {"revision": None, "artifact_files": {}}}

    assert load_local_reranker(config, tmp_path) == (None, 0)


def test_three_way_experiment_records_measured_reranker_latency_and_shared_baseline() -> None:
    from issuelens.retrieval import BenchmarkConfig, RerankerConfig, run_three_way_experiment

    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            return [(1.0, 0.0) for _ in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    class FixedReranker:
        identifier = "fixed-reranker-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            return [-float(candidate.rank) for candidate in candidates]

    corpus = Corpus(
        chunks=(Chunk("python_source", "Kludex/starlette", "e086fc2", "root.py", 1, 1,
                      "route name", "v1"),),
        chunking_config=ChunkingConfig("v1", 120, 20),
    )
    ticks = iter(range(0, 1000, 10))

    result = run_three_way_experiment(
        corpus, "route name", FixedEmbedder(),
        VectorIndexConfig(1, 6, "v1", "cases-v1"),
        BM25Config(1, 6, "code-text-v1", 1.2, 0.75),
        FusionConfig(1, 8, 60, 1.0, 1.0),
        FixedReranker(), RerankerConfig(True, 1, 6),
        benchmark=BenchmarkConfig(warmup_runs=1, repetitions=2),
        clock=lambda: next(ticks),
    )

    assert result.shared_input["query"] == "route name"
    assert result.g3_reranked.candidates[0].reference == result.g2_hybrid.candidates[0].reference
    assert result.metadata["latency_ns"]["g3_reranked"]["reranker"] == {
        "samples": [10, 10], "median": 10, "min": 10, "max": 10,
    }


def test_repair_evidence_marker_never_reaches_g3_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issuelens.retrieval import BenchmarkConfig, RerankerConfig, run_three_way_experiment

    marker = "REPAIR_ONLY_SENTINEL_06"
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps({
        "case_id": "fixture", "schema_version": "cases-v1",
        "issue": {"repository": "fixture/demo", "title": "route name", "source_url": "https://example.test/issue"},
        "repository_snapshot": {"commit": "fixture-v1"},
        "repair_evidence": {"changed_files": [{"path": marker}], "pull_request": {"title": marker}},
    }), encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    (snapshot / "starlette").mkdir(parents=True)
    (snapshot / "starlette/routing.py").write_text(
        "def route_name():\n    pass\n", encoding="utf-8"
    )
    (snapshot / ".issuelens-snapshot.json").write_text(
        json.dumps({"repository": "fixture/demo", "commit": "fixture-v1"}), encoding="utf-8"
    )
    observed: list[str] = []
    monkeypatch.setattr(socket.socket, "connect", lambda *_args, **_kwargs: pytest.fail("network"))

    class CapturingEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            observed.extend(texts)
            return [(1.0, 0.0) for _ in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            observed.append(text)
            return (1.0, 0.0)

    class CapturingReranker:
        identifier = "fixed-reranker-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            observed.append(query)
            observed.extend(candidate.content for candidate in candidates)
            return [1.0 for _ in candidates]

    retrieval_input = load_retrieval_input(case_path)
    corpus = build_corpus(snapshot, retrieval_input, ChunkingConfig("fixture-v1", 120, 20))
    result = run_three_way_experiment(
        corpus, retrieval_input.query, CapturingEmbedder(),
        VectorIndexConfig(1, 6, "fixture-v1", "cases-v1"),
        BM25Config(1, 6, "code-text-v1", 1.2, 0.75),
        FusionConfig(1, 8, 60, 1.0, 1.0),
        CapturingReranker(), RerankerConfig(True, 1, 6),
        benchmark=BenchmarkConfig(),
    )

    assert marker not in json.dumps({"observed": observed, "result": str(result)})


def test_g3_candidate_references_resolve_to_the_snapshot(tmp_path: Path) -> None:
    from issuelens.retrieval import validate_candidate_references

    source = tmp_path / "starlette/routing.py"
    source.parent.mkdir()
    source.write_text("first\nroute name\nthird\n", encoding="utf-8")
    good = HybridCandidate(
        1, "0.03200000", "e086fc2", "starlette/routing.py", 2, 2,
        "python_source", "v1", "route name",
        "https://github.com/Kludex/starlette/blob/e086fc2/starlette/routing.py#L2-L2",
        1, "0.900000", "0.01600000", 1, "2.000000", "0.01600000",
    )
    bad = HybridCandidate(
        2, "0.03100000", "e086fc2", "starlette/routing.py", 2, 2,
        "python_source", "v1", "future repair text", good.reference,
        2, "0.800000", "0.01500000", 2, "1.000000", "0.01600000",
    )

    assert validate_candidate_references((good,), tmp_path, "Kludex/starlette", "e086fc2") == []
    assert validate_candidate_references((bad,), tmp_path, "Kludex/starlette", "e086fc2") == [
        "starlette/routing.py:2-2: content does not match snapshot"
    ]


def test_equal_rounded_reranker_scores_keep_g2_order_and_repeat_exactly() -> None:
    from issuelens.retrieval import RerankerConfig, rerank_hybrid_run

    candidates = tuple(
        HybridCandidate(
            rank, "0.01600000", "e086fc2", "routing.py", rank, rank,
            "python_source", "v1", f"chunk {rank}",
            f"https://example.test/routing.py#L{rank}-L{rank}",
            rank, "0.500000", "0.01600000", None, None, "0.00000000",
        )
        for rank in (1, 2, 3)
    )
    g2 = HybridRun("route name", candidates, {"strategy": "rrf-v1"})

    class TiedReranker:
        identifier = "fixed-ties-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            return [0.50000001, 0.50000002, 0.50000003]

    config = RerankerConfig(True, 3, 6)
    first = rerank_hybrid_run(g2, TiedReranker(), config)
    second = rerank_hybrid_run(g2, TiedReranker(), config)

    assert isinstance(first, RerankedRun)
    assert first == second
    assert [(item.rank, item.g2_rank, item.score) for item in first.candidates] == [
        (1, 1, "0.500000"), (2, 2, "0.500000"), (3, 3, "0.500000")
    ]


def test_ci_regression_suite_never_loads_models_or_connects_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from issuelens.retrieval import run_regression_suite

    monkeypatch.setattr(socket.socket, "connect", lambda *_args, **_kwargs: pytest.fail("network"))
    monkeypatch.setattr(
        "issuelens.retrieval.import_module",
        lambda _name: pytest.fail("production model load"),
    )
    manifest = Path(__file__).resolve().parents[2] / "data/retrieval/regression/manifest.json"

    assert run_regression_suite(manifest)["status"] == "passed"


def test_ci_regression_result_is_versioned_and_reproducible() -> None:
    from issuelens.retrieval import run_regression_suite

    directory = Path(__file__).resolve().parents[2] / "data/retrieval/regression"
    expected = json.loads((directory / "result.json").read_text(encoding="utf-8"))

    assert run_regression_suite(directory / "manifest.json") == expected


def test_regression_result_records_fixed_thresholds_and_manifest_hash() -> None:
    from issuelens.retrieval import run_regression_suite

    manifest = Path(__file__).resolve().parents[2] / "data/retrieval/regression/manifest.json"
    result = run_regression_suite(manifest)

    assert result["thresholds"]["root_cause_recall_at_10"] == 1.0
    assert result["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_real_experiment_runner_writes_auditable_offline_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts/run_reranker_comparison.py"
    spec = importlib.util.spec_from_file_location("reranker_script", script)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    root = tmp_path
    data = root / "data/retrieval/starlette-issue-1552"
    data.mkdir(parents=True)
    case_path = root / "data/cases/starlette-issue-1552.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(json.dumps({
        "case_id": "starlette-issue-1552", "schema_version": "cases-v1",
        "issue": {"repository": "Kludex/starlette", "title": "route name",
                  "source_url": "https://example.test/issue"},
        "repository_snapshot": {"commit": "fixture-v1"},
    }), encoding="utf-8")
    snapshot = root / ".retrieval-cache/starlette-fixture-v1/starlette-fixture-v1"
    (snapshot / "starlette").mkdir(parents=True)
    (snapshot / "starlette/routing.py").write_text(
        "def route_name():\n    pass\n", encoding="utf-8"
    )
    (snapshot / ".issuelens-snapshot.json").write_text(json.dumps({
        "repository": "Kludex/starlette", "commit": "fixture-v1",
    }), encoding="utf-8")
    baseline_config: dict[str, Any] = {
        "snapshot": {"commit": "fixture-v1", "repository": "Kludex/starlette"},
        "chunking": {"index_version": "fixture-v1", "max_lines": 120, "overlap_lines": 20},
        "embedding": {"model": "fixed", "revision": "rev", "dimension": 2},
        "index": {"top_k": 1, "score_precision": 6},
        "bm25": {"score_precision": 6, "tokenizer": {"version": "code-text-v1"},
                   "k1": 1.2, "b": 0.75},
        "fusion": {"candidate_top_k": 1, "top_k": 1, "score_precision": 8,
                   "rank_constant": 60, "vector_weight": 1.0, "bm25_weight": 1.0},
        "generation_version": "fixture-v1", "data_version": "cases-v1",
        "query_source": "issue.title",
    }
    (data / "config.json").write_text(json.dumps(baseline_config), encoding="utf-8")
    reranker_config = {
        "enabled": True,
        "model": {"name": "fixed", "revision": "rev", "artifact_files": {"model.onnx": "hash"}},
        "ranking": {"top_k": 1, "score_precision": 6},
        "benchmark": {"warmup_runs": 0, "repetitions": 1},
    }
    (data / "reranker-config.json").write_text(json.dumps(reranker_config), encoding="utf-8")
    (data / "gold.json").write_text(json.dumps({
        "root_cause_files": [{"path": "starlette/routing.py", "relevance": 2}],
        "changed_files": [], "test_files": [],
    }), encoding="utf-8")

    class FixedEmbedder:
        identifier = "fixed:rev"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            return [(1.0, 0.0) for _ in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    class FixedReranker:
        identifier = "fixed-reranker:rev"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            return [0.75 for _ in candidates]

    corpus = build_corpus(snapshot, load_retrieval_input(case_path),
                          ChunkingConfig(**baseline_config["chunking"]))
    frozen = run_retrieval_comparison(
        corpus, "route name", FixedEmbedder(),
        VectorIndexConfig(1, 6, "fixture-v1", "cases-v1"),
        BM25Config(1, 6, "code-text-v1", 1.2, 0.75),
        FusionConfig(1, 8, 60, 1.0, 1.0),
    )
    (data / "comparison.json").write_text(json.dumps({
        "shared_input": frozen.shared_input,
        "groups": {
            "g1_vector": {"candidates": [asdict(item) for item in frozen.g1_vector.candidates]},
            "g2_hybrid": {"candidates": [asdict(item) for item in frozen.g2_hybrid.candidates]},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "DATA", data)
    monkeypatch.setattr(runner, "FastEmbedAdapter", lambda **_kwargs: FixedEmbedder())
    monkeypatch.setattr(runner, "load_local_reranker", lambda *_args: (FixedReranker(), 10))
    monkeypatch.setattr(socket.socket, "connect", lambda *_args, **_kwargs: pytest.fail("network"))

    assert runner.main() == 0
    result = json.loads((data / "three-way.json").read_text(encoding="utf-8"))
    assert result["groups"]["g3_reranked"]["metrics"]["mrr"] == 1.0
    assert result["metadata"]["reranker"]["model"]["revision"] == "rev"
    assert result["metadata"]["evaluation"]["metrics_version"] == "root-file-v1"
