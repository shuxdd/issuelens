import hashlib
import json
import platform
from dataclasses import asdict
from pathlib import Path

from issuelens.retrieval import (
    BenchmarkConfig,
    BM25Config,
    ChunkingConfig,
    FastEmbedAdapter,
    FusionConfig,
    VectorIndexConfig,
    build_corpus,
    evaluate_retrieval_metrics,
    load_retrieval_input,
    run_retrieval_comparison,
    write_retrieval_comparison,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "retrieval" / "starlette-issue-1552"


def main() -> None:
    config = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
    case = load_retrieval_input(
        ROOT / "data" / "cases" / "starlette-issue-1552.json"
    )
    snapshot_commit = config["snapshot"]["commit"]
    snapshot_root = (
        ROOT
        / ".retrieval-cache"
        / f"starlette-{snapshot_commit}"
        / f"starlette-{snapshot_commit}"
    )
    embedding = config["embedding"]
    bm25 = config["bm25"]
    fusion = config["fusion"]
    benchmark = config["benchmark"]
    corpus = build_corpus(snapshot_root, case, ChunkingConfig(**config["chunking"]))
    embedder = FastEmbedAdapter(
        model_name=embedding["model"],
        model_revision=embedding["revision"],
        dimension=embedding["dimension"],
        cache_dir=ROOT / ".retrieval-cache" / "models",
    )
    comparison = run_retrieval_comparison(
        corpus,
        case.query,
        embedder,
        VectorIndexConfig(
            top_k=config["index"]["top_k"],
            score_precision=config["index"]["score_precision"],
            generation_version=config["generation_version"],
            data_version=config["data_version"],
        ),
        BM25Config(
            top_k=fusion["candidate_top_k"],
            score_precision=bm25["score_precision"],
            tokenizer_version=bm25["tokenizer"]["version"],
            k1=bm25["k1"],
            b=bm25["b"],
        ),
        FusionConfig(
            top_k=fusion["top_k"],
            score_precision=fusion["score_precision"],
            rank_constant=fusion["rank_constant"],
            vector_weight=fusion["vector_weight"],
            bm25_weight=fusion["bm25_weight"],
        ),
        benchmark=BenchmarkConfig(
            warmup_runs=benchmark["warmup_runs"],
            repetitions=benchmark["repetitions"],
        ),
    )
    gold_path = DATA / "gold.json"
    comparison.metadata.update(
        {
            "generation_version": "hybrid-retrieval-v1",
            "query_source": config["query_source"],
            "snapshot": config["snapshot"],
            "embedding": {
                **embedding,
                "identifier": embedder.identifier,
            },
            "bm25": bm25,
            "fusion": fusion,
            "gold": {
                "sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
                "root_cause_relevance_field": "root_cause_files[].relevance",
                "mrr_ndcg_categories": ["root_cause_files"],
            },
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "processor": platform.processor(),
            },
        }
    )
    g1_metrics = evaluate_retrieval_metrics(comparison.g1_vector, gold_path)
    g2_metrics = evaluate_retrieval_metrics(comparison.g2_hybrid, gold_path)
    write_retrieval_comparison(
        DATA / "comparison.json",
        case.case_id,
        comparison,
        g1_metrics,
        g2_metrics,
    )
    print(
        json.dumps(
            {
                "g1_metrics": asdict(g1_metrics),
                "g2_metrics": asdict(g2_metrics),
                "g1_paths": [item.path for item in comparison.g1_vector.candidates],
                "g2_paths": [item.path for item in comparison.g2_hybrid.candidates],
                "latency_ns": comparison.metadata["latency_ns"],
                "index_size": comparison.metadata["index_size"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
