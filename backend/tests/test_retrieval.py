import json
import socket
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from issuelens.retrieval import (
    BenchmarkConfig,
    BM25Config,
    BM25Run,
    Chunk,
    ChunkingConfig,
    Corpus,
    FastEmbedAdapter,
    FusionConfig,
    RecallMetric,
    RecallReport,
    RetrievalCandidate,
    RetrievalInput,
    RetrievalMetadata,
    RetrievalRun,
    VectorIndexConfig,
    build_corpus,
    evaluate_recall,
    evaluate_retrieval_metrics,
    fuse_retrieval_runs,
    load_retrieval_input,
    run_bm25_retrieval,
    run_retrieval_comparison,
    run_vector_retrieval,
    write_retrieval_comparison,
    write_retrieval_result,
)


def write_snapshot_manifest(snapshot_root: Path, commit: str = "e086fc2") -> None:
    snapshot_root.mkdir(parents=True, exist_ok=True)
    (snapshot_root / ".issuelens-snapshot.json").write_text(
        json.dumps({"repository": "Kludex/starlette", "commit": commit}),
        encoding="utf-8",
    )


def test_bm25_matches_hand_calculated_scores_and_order() -> None:
    corpus = Corpus(
        chunks=tuple(
            Chunk(
                source_type="markdown",
                repository="Kludex/starlette",
                commit="e086fc2",
                path=path,
                start_line=1,
                end_line=1,
                content=content,
                index_version="hybrid-v1",
            )
            for path, content in (
                ("docs/a.md", "route route method"),
                ("docs/b.md", "route name"),
                ("docs/c.md", "unrelated"),
            )
        ),
        chunking_config=ChunkingConfig(
            index_version="hybrid-v1", max_lines=120, overlap_lines=20
        ),
    )

    result = run_bm25_retrieval(
        corpus,
        "route method",
        BM25Config(
            top_k=3,
            score_precision=6,
            tokenizer_version="code-text-v1",
            k1=1.2,
            b=0.75,
        ),
    )

    assert [(candidate.path, candidate.score) for candidate in result.candidates] == [
        ("docs/a.md", "1.518488"),
        ("docs/b.md", "0.470004"),
    ]
    assert result.metadata == {
        "document_count": 3,
        "average_document_length": "5.000000",
        "tokenizer_version": "code-text-v1",
        "k1": "1.200000",
        "b": "0.750000",
        "score_precision": 6,
        "tie_break": "path,start_line,end_line,source_type",
    }


def test_bm25_retrieves_code_symbols_error_text_and_markdown_terms() -> None:
    corpus = Corpus(
        chunks=tuple(
            Chunk(
                source_type=source_type,
                repository="Kludex/starlette",
                commit="e086fc2",
                path=path,
                start_line=1,
                end_line=1,
                content=content,
                index_version="hybrid-v1",
            )
            for source_type, path, content in (
                ("python_source", "starlette/symbol.py", "def get_name(endpoint): ..."),
                (
                    "python_source",
                    "starlette/error.py",
                    "RuntimeError: Routed paths must start with '/'",
                ),
                ("markdown", "docs/routing.md", "## Route Naming\nIntrospection"),
            )
        ),
        chunking_config=ChunkingConfig(
            index_version="hybrid-v1", max_lines=120, overlap_lines=20
        ),
    )
    config = BM25Config(
        top_k=1,
        score_precision=6,
        tokenizer_version="code-text-v1",
        k1=1.2,
        b=0.75,
    )

    assert [
        run_bm25_retrieval(corpus, query, config).candidates[0].path
        for query in ("get name", "Routed paths must start", "route naming")
    ] == [
        "starlette/symbol.py",
        "starlette/error.py",
        "docs/routing.md",
    ]


def test_bm25_uses_fixed_precision_and_stable_tie_break() -> None:
    corpus = Corpus(
        chunks=tuple(
            Chunk(
                source_type=source_type,
                repository="Kludex/starlette",
                commit="e086fc2",
                path=path,
                start_line=start_line,
                end_line=start_line,
                content="shared",
                index_version="hybrid-v1",
            )
            for source_type, path, start_line in (
                ("python_source", "z.py", 1),
                ("python_source", "a.py", 2),
                ("python_test", "a.py", 1),
                ("markdown", "a.py", 1),
            )
        ),
        chunking_config=ChunkingConfig(
            index_version="hybrid-v1", max_lines=120, overlap_lines=20
        ),
    )

    result = run_bm25_retrieval(
        corpus,
        "shared",
        BM25Config(
            top_k=4,
            score_precision=6,
            tokenizer_version="code-text-v1",
            k1=1.2,
            b=0.75,
        ),
    )

    assert [
        (
            candidate.path,
            candidate.start_line,
            candidate.end_line,
            candidate.source_type,
            candidate.score,
        )
        for candidate in result.candidates
    ] == [
        ("a.py", 1, 1, "markdown", "0.105361"),
        ("a.py", 1, 1, "python_test", "0.105361"),
        ("a.py", 2, 2, "python_source", "0.105361"),
        ("z.py", 1, 1, "python_source", "0.105361"),
    ]


def test_rrf_fuses_fixed_vector_and_bm25_rankings() -> None:
    def candidate(rank: int, path: str, score: str) -> RetrievalCandidate:
        return RetrievalCandidate(
            rank=rank,
            score=score,
            commit="e086fc2",
            path=path,
            start_line=1,
            end_line=2,
            source_type="python_source",
            index_version="hybrid-v1",
            content=f"content for {path}",
            reference=f"https://example.test/{path}#L1-L2",
        )

    vector = RetrievalRun(
        query="route name",
        candidates=(
            candidate(1, "a.py", "0.900000"),
            candidate(2, "b.py", "0.800000"),
        ),
        metadata=RetrievalMetadata(
            data_version="cases-v1",
            snapshot_commit="e086fc2",
            index_version="hybrid-v1",
            embedding_identifier="fixed-test-v1",
            embedding_dimension=2,
            top_k=2,
            generation_version="retrieval-v1",
            chunk_count=3,
        ),
    )
    bm25 = BM25Run(
        query="route name",
        candidates=(
            candidate(1, "b.py", "2.000000"),
            candidate(2, "c.py", "1.000000"),
        ),
        metadata={},
    )

    result = fuse_retrieval_runs(
        vector,
        bm25,
        FusionConfig(
            top_k=3,
            score_precision=8,
            rank_constant=60,
            vector_weight=1.0,
            bm25_weight=1.0,
        ),
    )

    assert [(item.path, item.score) for item in result.candidates] == [
        ("b.py", "0.03252247"),
        ("a.py", "0.01639344"),
        ("c.py", "0.01612903"),
    ]


def test_hybrid_candidate_preserves_channel_scores_contributions_and_reference() -> None:
    def candidate(rank: int, score: str) -> RetrievalCandidate:
        return RetrievalCandidate(
            rank=rank,
            score=score,
            commit="e086fc2",
            path="starlette/routing.py",
            start_line=86,
            end_line=89,
            source_type="python_source",
            index_version="hybrid-v1",
            content="def get_name(endpoint): ...",
            reference="https://example.test/starlette/routing.py#L86-L89",
        )

    vector = RetrievalRun(
        query="route name",
        candidates=(candidate(2, "0.724352"),),
        metadata=RetrievalMetadata(
            data_version="cases-v1",
            snapshot_commit="e086fc2",
            index_version="hybrid-v1",
            embedding_identifier="fixed-test-v1",
            embedding_dimension=2,
            top_k=10,
            generation_version="retrieval-v1",
            chunk_count=1,
        ),
    )
    bm25 = BM25Run(
        query="route name",
        candidates=(candidate(1, "3.250000"),),
        metadata={},
    )

    result = fuse_retrieval_runs(
        vector,
        bm25,
        FusionConfig(
            top_k=10,
            score_precision=8,
            rank_constant=60,
            vector_weight=1.0,
            bm25_weight=1.0,
        ),
    )
    item = result.candidates[0]

    assert (
        item.vector_rank,
        item.vector_score,
        item.vector_contribution,
        item.bm25_rank,
        item.bm25_score,
        item.bm25_contribution,
        item.rank,
        item.score,
        item.commit,
        item.reference,
    ) == (
        2,
        "0.724352",
        "0.01612903",
        1,
        "3.250000",
        "0.01639344",
        1,
        "0.03252247",
        "e086fc2",
        "https://example.test/starlette/routing.py#L86-L89",
    )


def test_g1_and_g2_share_corpus_query_embedding_and_top_k() -> None:
    document_calls: list[list[str]] = []

    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            document_calls.append(list(texts))
            return [(1.0, 0.0), (0.0, 1.0)]

        def embed_query(self, text: str) -> tuple[float, float]:
            assert text == "route name"
            return (1.0, 0.0)

    corpus = Corpus(
        chunks=tuple(
            Chunk(
                source_type="python_source",
                repository="Kludex/starlette",
                commit="e086fc2",
                path=path,
                start_line=1,
                end_line=2,
                content=content,
                index_version="hybrid-v1",
            )
            for path, content in (
                ("starlette/routing.py", "def get_name(endpoint): ..."),
                ("starlette/other.py", "unrelated"),
            )
        ),
        chunking_config=ChunkingConfig(
            index_version="hybrid-v1", max_lines=120, overlap_lines=20
        ),
    )

    comparison = run_retrieval_comparison(
        corpus,
        "route name",
        FixedEmbedder(),
        VectorIndexConfig(
            top_k=2,
            score_precision=6,
            generation_version="retrieval-v1",
            data_version="cases-v1",
        ),
        BM25Config(
            top_k=2,
            score_precision=6,
            tokenizer_version="code-text-v1",
            k1=1.2,
            b=0.75,
        ),
        FusionConfig(
            top_k=2,
            score_precision=8,
            rank_constant=60,
            vector_weight=1.0,
            bm25_weight=1.0,
        ),
    )

    assert len(document_calls) == 1
    assert comparison.shared_input == {
        "query": "route name",
        "snapshot_commit": "e086fc2",
        "index_version": "hybrid-v1",
        "chunk_count": 2,
        "embedding_identifier": "fixed-test-v1",
        "embedding_dimension": 2,
        "top_k": 2,
    }
    assert comparison.g2_hybrid.candidates[0].vector_rank == (
        comparison.g1_vector.candidates[0].rank
    )
    assert comparison.g2_hybrid.candidates[0].vector_score == (
        comparison.g1_vector.candidates[0].score
    )


def test_retrieval_metrics_match_hand_calculated_recall_mrr_and_ndcg(
    tmp_path: Path,
) -> None:
    def candidate(rank: int, path: str, start_line: int = 1) -> RetrievalCandidate:
        return RetrievalCandidate(
            rank=rank,
            score=f"{1 / rank:.6f}",
            commit="e086fc2",
            path=path,
            start_line=start_line,
            end_line=start_line,
            source_type="python_source",
            index_version="hybrid-v1",
            content="content",
            reference=f"https://example.test/{path}#L{start_line}-L{start_line}",
        )

    run = RetrievalRun(
        query="route name",
        candidates=(
            candidate(1, "irrelevant.py"),
            candidate(2, "root-a.py"),
            candidate(3, "root-a.py", 10),
            candidate(4, "root-b.py"),
            candidate(5, "changed.py"),
            candidate(6, "test.py"),
        ),
        metadata=RetrievalMetadata(
            data_version="cases-v1",
            snapshot_commit="e086fc2",
            index_version="hybrid-v1",
            embedding_identifier="fixed-test-v1",
            embedding_dimension=2,
            top_k=10,
            generation_version="retrieval-v1",
            chunk_count=6,
        ),
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "root_cause_files": [
                    {"path": "root-a.py", "relevance": 2},
                    {"path": "root-b.py", "relevance": 1},
                ],
                "changed_files": [{"path": "changed.py"}],
                "test_files": [{"path": "test.py"}],
            }
        ),
        encoding="utf-8",
    )

    metrics = evaluate_retrieval_metrics(run, gold_path)

    assert asdict(metrics) == {
        "recall_at_5": {
            "top_k": 5,
            "root_cause_files": {"hits": 2, "total": 2, "recall": 1.0},
            "changed_files": {"hits": 1, "total": 1, "recall": 1.0},
            "test_files": {"hits": 0, "total": 1, "recall": 0.0},
        },
        "recall_at_10": {
            "top_k": 10,
            "root_cause_files": {"hits": 2, "total": 2, "recall": 1.0},
            "changed_files": {"hits": 1, "total": 1, "recall": 1.0},
            "test_files": {"hits": 1, "total": 1, "recall": 1.0},
        },
        "mrr": 0.5,
        "ndcg_at_10": 0.639909,
    }


def test_comparison_metadata_records_inputs_latency_and_reproducible_index_size() -> None:
    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            assert list(texts) == ["routing.py\nroute name"]
            return [(1.0, 0.0)]

        def embed_query(self, text: str) -> tuple[float, float]:
            assert text == "route name"
            return (1.0, 0.0)

    corpus = Corpus(
        chunks=(
            Chunk(
                source_type="python_source",
                repository="Kludex/starlette",
                commit="e086fc2",
                path="routing.py",
                start_line=1,
                end_line=1,
                content="route name",
                index_version="hybrid-v1",
            ),
        ),
        chunking_config=ChunkingConfig(
            index_version="hybrid-v1", max_lines=120, overlap_lines=20
        ),
    )
    ticks = iter(range(0, 1000, 10))

    comparison = run_retrieval_comparison(
        corpus,
        "route name",
        FixedEmbedder(),
        VectorIndexConfig(
            top_k=1,
            score_precision=6,
            generation_version="retrieval-v1",
            data_version="cases-v1",
        ),
        BM25Config(
            top_k=1,
            score_precision=6,
            tokenizer_version="code-text-v1",
            k1=1.2,
            b=0.75,
        ),
        FusionConfig(
            top_k=1,
            score_precision=8,
            rank_constant=60,
            vector_weight=1.0,
            bm25_weight=1.0,
        ),
        benchmark=BenchmarkConfig(warmup_runs=1, repetitions=2),
        clock=lambda: next(ticks),
    )

    assert comparison.metadata["data_version"] == "cases-v1"
    assert comparison.metadata["snapshot_commit"] == "e086fc2"
    assert comparison.metadata["chunking"] == {
        "index_version": "hybrid-v1",
        "max_lines": 120,
        "overlap_lines": 20,
        "python_parser": "ast-v1",
        "markdown_parser": "headings-v1",
    }
    assert comparison.metadata["embedding"] == {
        "identifier": "fixed-test-v1",
        "dimension": 2,
    }
    assert comparison.metadata["bm25"] == {
        "tokenizer_version": "code-text-v1",
        "k1": "1.200000",
        "b": "0.750000",
        "score_precision": 6,
    }
    assert comparison.metadata["fusion"] == {
        "strategy": "rrf-v1",
        "rank_constant": 60,
        "vector_weight": "1.00000000",
        "bm25_weight": "1.00000000",
        "score_precision": 8,
    }
    assert comparison.metadata["top_k"] == 1
    assert comparison.metadata["benchmark"] == {
        "clock": "perf_counter_ns",
        "warmup_runs": 1,
        "repetitions": 2,
    }
    assert comparison.metadata["latency_ns"] == {
        "g1_vector": {
            "build": {"samples": [10, 10], "median": 10, "min": 10, "max": 10},
            "query": {"samples": [10, 10], "median": 10, "min": 10, "max": 10},
        },
        "g2_hybrid": {
            "build": {"samples": [20, 20], "median": 20, "min": 20, "max": 20},
            "query": {"samples": [30, 30], "median": 30, "min": 30, "max": 30},
        },
    }
    index_size = comparison.metadata["index_size"]
    assert index_size["vector"] == {
        "document_count": 1,
        "dimension": 2,
        "value_count": 2,
        "payload_bytes": 8,
        "element_encoding": "float32",
    }
    assert index_size["bm25"]["document_count"] == 1
    assert index_size["bm25"]["term_count"] == 4
    assert index_size["bm25"]["posting_count"] == 4
    assert index_size["bm25"]["token_count"] == 4
    assert index_size["bm25"]["serialized_bytes"] > 0
    assert index_size["g1_total_bytes"] == 8
    assert index_size["g2_total_bytes"] == (
        8 + index_size["bm25"]["serialized_bytes"]
    )


def test_comparison_result_is_deterministic_except_wall_clock_latency(
    tmp_path: Path,
) -> None:
    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            return [(1.0, 0.0) for _text in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    corpus = Corpus(
        chunks=(
            Chunk(
                source_type="python_source",
                repository="Kludex/starlette",
                commit="e086fc2",
                path="routing.py",
                start_line=1,
                end_line=2,
                content="def get_name(endpoint): ...",
                index_version="hybrid-v1",
            ),
        ),
        chunking_config=ChunkingConfig(
            index_version="hybrid-v1", max_lines=120, overlap_lines=20
        ),
    )
    vector_config = VectorIndexConfig(
        top_k=1,
        score_precision=6,
        generation_version="retrieval-v1",
        data_version="cases-v1",
    )
    bm25_config = BM25Config(
        top_k=1,
        score_precision=6,
        tokenizer_version="code-text-v1",
        k1=1.2,
        b=0.75,
    )
    fusion_config = FusionConfig(
        top_k=1,
        score_precision=8,
        rank_constant=60,
        vector_weight=1.0,
        bm25_weight=1.0,
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "root_cause_files": [{"path": "routing.py", "relevance": 2}],
                "changed_files": [{"path": "routing.py"}],
                "test_files": [],
            }
        ),
        encoding="utf-8",
    )

    def run_with_tick(step: int) -> tuple[dict[str, Any], Path]:
        ticks = iter(range(0, 1000, step))
        comparison = run_retrieval_comparison(
            corpus,
            "route name",
            FixedEmbedder(),
            vector_config,
            bm25_config,
            fusion_config,
            clock=lambda: next(ticks),
        )
        output = tmp_path / f"comparison-{step}.json"
        write_retrieval_comparison(
            output,
            "starlette-issue-1552",
            comparison,
            evaluate_retrieval_metrics(comparison.g1_vector, gold_path),
            evaluate_retrieval_metrics(comparison.g2_hybrid, gold_path),
        )
        return json.loads(output.read_text(encoding="utf-8")), output

    first, first_path = run_with_tick(10)
    second, second_path = run_with_tick(20)
    first["metadata"].pop("latency_ns")
    second["metadata"].pop("latency_ns")

    assert first == second
    assert first_path.read_bytes() != second_path.read_bytes()
    assert first["groups"]["g2_hybrid"]["candidates"][0]["vector_rank"] == 1
    assert first["groups"]["g2_hybrid"]["candidates"][0]["bm25_rank"] == 1


def test_python_source_corpus_preserves_structure_commit_path_and_lines(
    tmp_path: Path,
) -> None:
    source = """import os

ROUTE_PREFIX = "/api"

def endpoint(request):
    return request

class Handler:
    def __call__(self, request):
        return request
"""
    source_path = tmp_path / "starlette" / "routing.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    write_snapshot_manifest(tmp_path)

    corpus = build_corpus(
        tmp_path,
        RetrievalInput(
            case_id="starlette-issue-1552",
            data_version="1.0",
            repository="Kludex/starlette",
            snapshot_commit="e086fc2",
            query='Route naming introspection always return "method"',
            issue_source_url="https://github.com/Kludex/starlette/issues/1552",
        ),
        ChunkingConfig(index_version="vector-v1", max_lines=120, overlap_lines=20),
    )

    assert [
        (
            chunk.source_type,
            chunk.repository,
            chunk.commit,
            chunk.path,
            chunk.start_line,
            chunk.end_line,
            chunk.content,
            chunk.index_version,
        )
        for chunk in corpus.chunks
    ] == [
        (
            "python_source",
            "Kludex/starlette",
            "e086fc2",
            "starlette/routing.py",
            1,
            3,
            'import os\n\nROUTE_PREFIX = "/api"',
            "vector-v1",
        ),
        (
            "python_source",
            "Kludex/starlette",
            "e086fc2",
            "starlette/routing.py",
            5,
            6,
            "def endpoint(request):\n    return request",
            "vector-v1",
        ),
        (
            "python_source",
            "Kludex/starlette",
            "e086fc2",
            "starlette/routing.py",
            8,
            10,
            "class Handler:\n    def __call__(self, request):\n        return request",
            "vector-v1",
        ),
    ]


def test_python_test_corpus_uses_test_structure_and_includes_decorators(
    tmp_path: Path,
) -> None:
    source = """@pytest.mark.anyio
async def test_route_name():
    assert True
"""
    source_path = tmp_path / "tests" / "test_routing.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    write_snapshot_manifest(tmp_path)

    corpus = build_corpus(
        tmp_path,
        RetrievalInput(
            case_id="starlette-issue-1552",
            data_version="1.0",
            repository="Kludex/starlette",
            snapshot_commit="e086fc2",
            query="route name",
            issue_source_url="https://github.com/Kludex/starlette/issues/1552",
        ),
        ChunkingConfig(index_version="vector-v1", max_lines=120, overlap_lines=20),
    )

    assert len(corpus.chunks) == 1
    chunk = corpus.chunks[0]
    assert (
        chunk.source_type,
        chunk.path,
        chunk.start_line,
        chunk.end_line,
        chunk.content,
    ) == (
        "python_test",
        "tests/test_routing.py",
        1,
        3,
        source.rstrip(),
    )


def test_markdown_corpus_splits_on_headings_but_not_fenced_code(
    tmp_path: Path,
) -> None:
    source = """# Routing

Routing overview.

```python
# This is code, not a heading
```

## Route names

Names identify routes.
"""
    source_path = tmp_path / "docs" / "routing.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    write_snapshot_manifest(tmp_path)

    corpus = build_corpus(
        tmp_path,
        RetrievalInput(
            case_id="starlette-issue-1552",
            data_version="1.0",
            repository="Kludex/starlette",
            snapshot_commit="e086fc2",
            query="route name",
            issue_source_url="https://github.com/Kludex/starlette/issues/1552",
        ),
        ChunkingConfig(index_version="vector-v1", max_lines=120, overlap_lines=20),
    )

    assert [
        (chunk.source_type, chunk.path, chunk.start_line, chunk.end_line, chunk.content)
        for chunk in corpus.chunks
    ] == [
        (
            "markdown",
            "docs/routing.md",
            1,
            8,
            "# Routing\n\nRouting overview.\n\n```python\n# This is code, not a heading\n```\n",
        ),
        (
            "markdown",
            "docs/routing.md",
            9,
            11,
            "## Route names\n\nNames identify routes.",
        ),
    ]


def test_retrieval_input_and_corpus_cannot_observe_repair_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repair_only_marker = "REPAIR_ONLY_SENTINEL_04"
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "case_id": "starlette-issue-1552",
                "issue": {
                    "repository": "Kludex/starlette",
                    "title": '  Route naming introspection always return "method"  ',
                    "source_url": "https://github.com/Kludex/starlette/issues/1552",
                },
                "repository_snapshot": {"commit": "e086fc2"},
                "repair_evidence": {
                    "read_access": "evaluation_only",
                    "changed_files": [{"path": repair_only_marker}],
                },
            }
        ),
        encoding="utf-8",
    )
    source_path = tmp_path / "snapshot" / "starlette" / "routing.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("def route_name():\n    return 'allowed'\n", encoding="utf-8")
    write_snapshot_manifest(tmp_path / "snapshot")

    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("retrieval attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    retrieval_input = load_retrieval_input(case_path)
    corpus = build_corpus(
        tmp_path / "snapshot",
        retrieval_input,
        ChunkingConfig(index_version="vector-v1", max_lines=120, overlap_lines=20),
    )
    serialized_index_input = json.dumps(
        {"retrieval_input": asdict(retrieval_input), "corpus": asdict(corpus)}
    )

    observed_embedding_inputs: list[str] = []

    class CapturingEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            observed_embedding_inputs.extend(texts)
            return [(1.0, 0.0) for _text in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            observed_embedding_inputs.append(text)
            return (1.0, 0.0)

    run_vector_retrieval(
        corpus,
        retrieval_input.query,
        CapturingEmbedder(),
        VectorIndexConfig(
            top_k=1,
            score_precision=6,
            generation_version="retrieval-v1",
            data_version="cases-v1",
        ),
    )
    comparison = run_retrieval_comparison(
        corpus,
        retrieval_input.query,
        CapturingEmbedder(),
        VectorIndexConfig(
            top_k=1,
            score_precision=6,
            generation_version="retrieval-v1",
            data_version="cases-v1",
        ),
        BM25Config(
            top_k=1,
            score_precision=6,
            tokenizer_version="code-text-v1",
            k1=1.2,
            b=0.75,
        ),
        FusionConfig(
            top_k=1,
            score_precision=8,
            rank_constant=60,
            vector_weight=1.0,
            bm25_weight=1.0,
        ),
    )

    assert retrieval_input.query == 'Route naming introspection always return "method"'
    assert repair_only_marker not in serialized_index_input
    assert repair_only_marker not in "\n".join(observed_embedding_inputs)
    assert repair_only_marker not in json.dumps(asdict(comparison))


def test_corpus_rejects_snapshot_from_a_different_commit(tmp_path: Path) -> None:
    source_path = tmp_path / "starlette" / "routing.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("def route_name():\n    return 'wrong tree'\n", encoding="utf-8")
    write_snapshot_manifest(tmp_path, commit="current-default-branch")

    try:
        build_corpus(
            tmp_path,
            RetrievalInput(
                case_id="starlette-issue-1552",
                data_version="1.0",
                repository="Kludex/starlette",
                snapshot_commit="e086fc2",
                query="route name",
                issue_source_url="https://github.com/Kludex/starlette/issues/1552",
            ),
            ChunkingConfig(index_version="vector-v1", max_lines=120, overlap_lines=20),
        )
    except ValueError as error:
        assert str(error) == "Snapshot manifest does not match retrieval input"
    else:
        raise AssertionError("Mismatched snapshot was accepted")


def test_oversized_structure_uses_configured_line_overlap(tmp_path: Path) -> None:
    source = """def route_name():
    first = 1
    second = 2
    third = 3
    fourth = 4
    fifth = 5
    return first
"""
    source_path = tmp_path / "starlette" / "routing.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    write_snapshot_manifest(tmp_path)

    corpus = build_corpus(
        tmp_path,
        RetrievalInput(
            case_id="starlette-issue-1552",
            data_version="1.0",
            repository="Kludex/starlette",
            snapshot_commit="e086fc2",
            query="route name",
            issue_source_url="https://github.com/Kludex/starlette/issues/1552",
        ),
        ChunkingConfig(index_version="vector-v1", max_lines=3, overlap_lines=1),
    )

    assert [
        (chunk.start_line, chunk.end_line, chunk.content) for chunk in corpus.chunks
    ] == [
        (1, 3, "def route_name():\n    first = 1\n    second = 2"),
        (3, 5, "    second = 2\n    third = 3\n    fourth = 4"),
        (5, 7, "    fourth = 4\n    fifth = 5\n    return first"),
    ]


def test_fixed_embeddings_return_stable_cosine_top_k() -> None:
    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            vectors = {
                "starlette/a.py\nexact": (1.0, 0.0),
                "starlette/z.py\nalso exact": (1.0, 0.0),
                "starlette/b.py\nrelated": (0.8, 0.6),
                "starlette/c.py\nunrelated": (0.0, 1.0),
            }
            return [vectors[text] for text in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            assert text == "route name"
            return (1.0, 0.0)

    corpus = Corpus(
        chunks=tuple(
            Chunk(
                source_type="python_source",
                repository="Kludex/starlette",
                commit="e086fc2",
                path=path,
                start_line=1,
                end_line=1,
                content=content,
                index_version="vector-v1",
            )
            for path, content in (
                ("starlette/z.py", "also exact"),
                ("starlette/b.py", "related"),
                ("starlette/a.py", "exact"),
                ("starlette/c.py", "unrelated"),
            )
        ),
        chunking_config=ChunkingConfig(
            index_version="vector-v1", max_lines=120, overlap_lines=20
        ),
    )
    config = VectorIndexConfig(
        top_k=3,
        score_precision=6,
        generation_version="retrieval-v1",
        data_version="1.0",
    )

    first = run_vector_retrieval(corpus, "route name", FixedEmbedder(), config)
    second = run_vector_retrieval(corpus, "route name", FixedEmbedder(), config)

    assert first == second
    assert [
        (candidate.rank, candidate.score, candidate.path)
        for candidate in first.candidates
    ] == [
        (1, "1.000000", "starlette/a.py"),
        (2, "1.000000", "starlette/z.py"),
        (3, "0.800000", "starlette/b.py"),
    ]
    assert (
        first.metadata.embedding_identifier,
        first.metadata.embedding_dimension,
        first.metadata.top_k,
        first.metadata.generation_version,
    ) == ("fixed-test-v1", 2, 3, "retrieval-v1")


def test_fastembed_adapter_is_fixed_and_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialized: dict[str, object] = {}

    class FakeTextEmbedding:
        def __init__(self, **kwargs: object) -> None:
            initialized.update(kwargs)

        def passage_embed(self, texts: Sequence[str]) -> Iterable[tuple[float, float]]:
            assert list(texts) == ["document"]
            return iter([(1.0, 0.0)])

        def query_embed(self, text: str) -> Iterable[tuple[float, float]]:
            assert text == "query"
            return iter([(0.0, 1.0)])

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding)
    )

    adapter = FastEmbedAdapter(
        model_name="BAAI/bge-small-en-v1.5",
        model_revision="fixed-revision",
        dimension=384,
        cache_dir=tmp_path,
    )

    assert initialized == {
        "model_name": "BAAI/bge-small-en-v1.5",
        "cache_dir": str(tmp_path),
        "threads": 1,
        "local_files_only": True,
        "revision": "fixed-revision",
    }
    assert adapter.identifier == "fastembed:BAAI/bge-small-en-v1.5@fixed-revision"
    assert adapter.dimension == 384
    assert adapter.embed_documents(["document"]) == [(1.0, 0.0)]
    assert adapter.embed_query("query") == (0.0, 1.0)


def test_recall_keeps_root_cause_changed_and_test_files_separate(tmp_path: Path) -> None:
    def candidate(rank: int, path: str) -> RetrievalCandidate:
        return RetrievalCandidate(
            rank=rank,
            score="1.000000",
            commit="e086fc2",
            path=path,
            start_line=1,
            end_line=2,
            source_type="python_source",
            index_version="vector-v1",
            content="content",
            reference=f"https://example.test/{path}#L1-L2",
        )

    run = RetrievalRun(
        query="route name",
        candidates=(
            candidate(1, "starlette/routing.py"),
            candidate(2, "tests/test_routing.py"),
            candidate(3, "docs/routing.md"),
        ),
        metadata=RetrievalMetadata(
            data_version="1.0",
            snapshot_commit="e086fc2",
            index_version="vector-v1",
            embedding_identifier="fixed-test-v1",
            embedding_dimension=2,
            top_k=3,
            generation_version="retrieval-v1",
            chunk_count=3,
        ),
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "root_cause_files": [
                    {"path": "starlette/routing.py"},
                    {"path": "starlette/endpoints.py"},
                ],
                "changed_files": [
                    {"path": "starlette/routing.py"},
                    {"path": "docs/routing.md"},
                ],
                "test_files": [{"path": "tests/test_routing.py"}],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_recall(run, gold_path, top_k=2)

    assert asdict(report) == {
        "top_k": 2,
        "root_cause_files": {"hits": 1, "total": 2, "recall": 0.5},
        "changed_files": {"hits": 1, "total": 2, "recall": 0.5},
        "test_files": {"hits": 1, "total": 1, "recall": 1.0},
    }


def test_retrieval_metadata_records_chunking_and_index_inputs() -> None:
    class FixedEmbedder:
        identifier = "fixed-test-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            return [(1.0, 0.0) for _text in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    chunking = ChunkingConfig(
        index_version="vector-v1",
        max_lines=120,
        overlap_lines=20,
        python_parser="ast-v1",
        markdown_parser="headings-v1",
    )
    corpus = Corpus(
        chunks=(
            Chunk(
                source_type="python_source",
                repository="Kludex/starlette",
                commit="e086fc2",
                path="starlette/routing.py",
                start_line=1,
                end_line=2,
                content="class Route:\n    pass",
                index_version="vector-v1",
            ),
        ),
        chunking_config=chunking,
    )

    run = run_vector_retrieval(
        corpus,
        "route name",
        FixedEmbedder(),
        VectorIndexConfig(
            top_k=10,
            score_precision=6,
            generation_version="retrieval-v1",
            data_version="cases-v1",
        ),
    )

    assert asdict(run.metadata)["chunking"] == {
        "max_lines": 120,
        "overlap_lines": 20,
        "python_parser": "ast-v1",
        "markdown_parser": "headings-v1",
    }
    metadata = asdict(run.metadata)
    assert (
        metadata["similarity"],
        metadata["score_precision"],
        metadata["tie_break"],
    ) == ("cosine", 6, "path,start_line,end_line,source_type")


def test_retrieval_result_is_stable_reviewable_json(tmp_path: Path) -> None:
    run = RetrievalRun(
        query="route name",
        candidates=(
            RetrievalCandidate(
                rank=1,
                score="0.750000",
                commit="e086fc2",
                path="starlette/routing.py",
                start_line=86,
                end_line=89,
                source_type="python_source",
                index_version="vector-v1",
                content="def get_name(endpoint): ...",
                reference="https://github.com/Kludex/starlette/blob/e086fc2/starlette/routing.py#L86-L89",
            ),
        ),
        metadata=RetrievalMetadata(
            data_version="cases-v1",
            snapshot_commit="e086fc2",
            index_version="vector-v1",
            embedding_identifier="fixed-test-v1",
            embedding_dimension=2,
            top_k=1,
            generation_version="retrieval-v1",
            chunk_count=1,
        ),
    )
    metric = RecallMetric(hits=1, total=1, recall=1.0)
    recall = RecallReport(
        top_k=1,
        root_cause_files=metric,
        changed_files=metric,
        test_files=RecallMetric(hits=0, total=1, recall=0.0),
    )
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    write_retrieval_result(first_path, "starlette-issue-1552", run, recall)
    write_retrieval_result(second_path, "starlette-issue-1552", run, recall)

    result = json.loads(first_path.read_text(encoding="utf-8"))
    assert first_path.read_bytes() == second_path.read_bytes()
    assert result["case_id"] == "starlette-issue-1552"
    assert result["query"] == "route name"
    assert result["candidates"][0]["reference"].endswith(
        "starlette/routing.py#L86-L89"
    )
    assert result["recall"]["root_cause_files"]["recall"] == 1.0
