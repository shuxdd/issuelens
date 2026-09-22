import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path
from statistics import median

from issuelens.retrieval import (
    BenchmarkConfig,
    BM25Config,
    CachedDocumentEmbedder,
    ChunkingConfig,
    FastEmbedAdapter,
    FusionConfig,
    RerankerConfig,
    VectorIndexConfig,
    build_corpus,
    load_local_reranker,
    load_retrieval_input,
    run_three_way_experiment,
    validate_candidate_references,
    write_reranker_comparison,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/retrieval/starlette-issue-1552"


def main() -> int:
    config = json.loads((DATA / "reranker-config.json").read_text(encoding="utf-8"))
    model = config["model"]
    os.environ["HF_HUB_OFFLINE"] = "1"
    started = time.perf_counter_ns()
    try:
        reranker, model_bytes = load_local_reranker(
            config, ROOT / ".retrieval-cache/models"
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    cache_verify_and_model_load_ns = time.perf_counter_ns() - started

    baseline_config = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
    case = load_retrieval_input(ROOT / "data/cases/starlette-issue-1552.json")
    snapshot_commit = baseline_config["snapshot"]["commit"]
    snapshot_root = (
        ROOT / ".retrieval-cache" / f"starlette-{snapshot_commit}"
        / f"starlette-{snapshot_commit}"
    )
    corpus = build_corpus(
        snapshot_root, case, ChunkingConfig(**baseline_config["chunking"])
    )
    embedding = baseline_config["embedding"]
    embedder = CachedDocumentEmbedder(FastEmbedAdapter(
        model_name=embedding["model"], model_revision=embedding["revision"],
        dimension=embedding["dimension"],
        cache_dir=ROOT / ".retrieval-cache/models",
    ), ROOT / ".retrieval-cache/indexes")
    fusion = baseline_config["fusion"]
    bm25 = baseline_config["bm25"]
    benchmark = config["benchmark"]
    comparison = run_three_way_experiment(
        corpus, case.query, embedder,
        VectorIndexConfig(
            top_k=baseline_config["index"]["top_k"],
            score_precision=baseline_config["index"]["score_precision"],
            generation_version=baseline_config["generation_version"],
            data_version=baseline_config["data_version"],
        ),
        BM25Config(
            top_k=fusion["candidate_top_k"],
            score_precision=bm25["score_precision"],
            tokenizer_version=bm25["tokenizer"]["version"],
            k1=bm25["k1"], b=bm25["b"],
        ),
        FusionConfig(
            top_k=fusion["top_k"], score_precision=fusion["score_precision"],
            rank_constant=fusion["rank_constant"],
            vector_weight=fusion["vector_weight"],
            bm25_weight=fusion["bm25_weight"],
        ),
        reranker,
        RerankerConfig(
            enabled=config["enabled"], top_k=config["ranking"]["top_k"],
            score_precision=config["ranking"]["score_precision"],
        ),
        benchmark=BenchmarkConfig(
            warmup_runs=benchmark["warmup_runs"],
            repetitions=benchmark["repetitions"],
        ),
    )
    frozen = json.loads((DATA / "comparison.json").read_text(encoding="utf-8"))
    if comparison.shared_input != frozen["shared_input"] or any(
        [asdict(candidate) for candidate in getattr(comparison, field).candidates]
        != frozen["groups"][group]["candidates"]
        for field, group in (("g1_vector", "g1_vector"), ("g2_hybrid", "g2_hybrid"))
    ):
        raise ValueError("G1/G2 no longer match the versioned baseline")
    for group in (
        comparison.g1_vector, comparison.g2_hybrid, comparison.g3_reranked
    ):
        errors = validate_candidate_references(
            group.candidates, snapshot_root, case.repository, snapshot_commit
        )
        if errors:
            raise ValueError(f"Candidate references are invalid: {errors}")

    reranker_samples = comparison.metadata["latency_ns"]["g3_reranked"]["reranker"]["samples"]
    g2_samples = comparison.metadata["latency_ns"]["g2_hybrid"]["query"]["samples"]
    query_samples = [a + b for a, b in zip(g2_samples, reranker_samples, strict=True)]
    comparison.metadata["latency_ns"]["g3_reranked"].update({
        "cache_verify_and_model_load_ns": cache_verify_and_model_load_ns,
        "query": {
            "samples": query_samples, "median": int(median(query_samples)),
            "min": min(query_samples), "max": max(query_samples),
        },
    })
    comparison.metadata["index_size"].update({
        "reranker_model_bytes": model_bytes,
        "g3_total_bytes": comparison.metadata["index_size"]["g2_total_bytes"] + model_bytes,
    })
    gold_path = DATA / "gold.json"
    regression_manifest = ROOT / "data/retrieval/regression/manifest.json"
    gold_sha256 = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    comparison.metadata.update({
        "generation_version": "reranker-retrieval-v1",
        "case_id": case.case_id,
        "query_source": baseline_config["query_source"],
        "snapshot": baseline_config["snapshot"],
        "embedding": {**embedding, "identifier": embedder.identifier},
        "document_vector_cache": {
            "directory": ".retrieval-cache/indexes",
            "hits": embedder.cache_hits,
            "misses": embedder.cache_misses,
        },
        "bm25": bm25,
        "fusion": fusion,
        "reranker": {**config, "identifier": reranker.identifier if reranker else None,
                     "model": {**model, "artifact_size_bytes": model_bytes}},
        "gold": {"sha256": gold_sha256,
                 "mrr_ndcg_categories": ["root_cause_files"]},
        "evaluation": {
            "metrics_version": "root-file-v1",
            "recall_cutoffs": [5, 10],
            "ndcg_cutoff": 10,
            "gold_sha256": gold_sha256,
            "regression_manifest_sha256": (
                hashlib.sha256(regression_manifest.read_bytes()).hexdigest()
                if regression_manifest.is_file() else None
            ),
        },
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "reference_validation": {"checked_candidates": sum(
            len(group.candidates) for group in (
                comparison.g1_vector, comparison.g2_hybrid, comparison.g3_reranked
            )
        ), "errors": []},
    })
    write_reranker_comparison(
        DATA / "three-way.json", case.case_id, comparison, gold_path
    )
    written = json.loads((DATA / "three-way.json").read_text(encoding="utf-8"))
    print(json.dumps({
        "case_id": case.case_id,
        "metrics": {
            name: group["metrics"] for name, group in written["groups"].items()
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
