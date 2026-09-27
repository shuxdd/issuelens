import json
import shutil
from pathlib import Path

from issuelens.cases import build_cases, check_cases

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def test_versioned_dataset_has_fifty_one_traceable_leak_free_cases() -> None:
    manifest = json.loads(
        (DATA / "evaluation" / "dataset-manifest.json").read_text(encoding="utf-8")
    )
    expected = set(manifest["splits"]["development"] + manifest["splits"]["primary"])
    assert {path.stem for path in (DATA / "cases").glob("*.json")} == expected
    assert len(expected) == 51
    assert check_cases(DATA) == []


def test_versioned_screening_report_explains_the_candidate_funnel() -> None:
    report = json.loads((DATA / "screening-report.json").read_text(encoding="utf-8"))

    assert report["funnel"] == {
        "collected": 778,
        "relationship_recovered": 416,
        "category_eligible": 109,
        "analyzable": 108,
        "time_slice_valid": 99,
        "snapshot_available": 99,
        "buildable": 99,
        "statically_analyzable": 93,
        "cleaned_candidates": 93,
        "queued_for_review": 80,
        "manual_reviewed": 78,
        "accepted": 51,
    }
    assert {item["reason_code"] for item in report["excluded"]} >= {
        "security",
        "documentation_only",
        "dependency_bot",
        "cross_repository_root_cause",
        "static_analysis_failed",
    }


def test_versioned_inputs_rebuild_the_dataset_idempotently(tmp_path: Path) -> None:
    cache = shutil.copytree(DATA / "github-cache", tmp_path / "github-cache")
    snapshot = shutil.copytree(
        DATA / "repository-snapshots" / "starlette", tmp_path / "snapshot"
    )
    reviews = Path(shutil.copy2(DATA / "reviews.json", tmp_path / "reviews.json"))
    output = tmp_path / "output"

    build_cases(cache, snapshot, output, reviews_path=reviews)
    first = {
        path.name: path.read_bytes()
        for path in sorted((output / "cases").glob("*.json"))
    }
    first_report = (output / "screening-report.json").read_bytes()

    paths = build_cases(cache, snapshot, output, reviews_path=reviews)

    assert len(paths) == 51
    assert {
        path.name: path.read_bytes()
        for path in sorted((output / "cases").glob("*.json"))
    } == first
    assert (output / "screening-report.json").read_bytes() == first_report


def test_first_case_retrieval_config_and_gold_are_versioned_and_separate() -> None:
    retrieval = DATA / "retrieval" / "starlette-issue-1552"
    config = json.loads((retrieval / "config.json").read_text(encoding="utf-8"))
    gold = json.loads((retrieval / "gold.json").read_text(encoding="utf-8"))

    assert config["snapshot"]["commit"] == (
        "e086fc2da361767b532cf690e5203619bbae98aa"
    )
    assert config["query_source"] == "issue.title"
    assert config["chunking"] == {
        "index_version": "starlette-1552-vector-v1",
        "markdown_parser": "headings-v1",
        "max_lines": 120,
        "overlap_lines": 20,
        "python_parser": "ast-v1",
    }
    assert config["embedding"]["model"] == "BAAI/bge-small-en-v1.5"
    assert config["embedding"]["dimension"] == 384
    assert config["embedding"]["local_files_only"] is True
    assert config["index"]["similarity"] == "cosine"
    assert config["index"]["top_k"] == 10

    assert {item["path"] for item in gold["root_cause_files"]} == {
        "starlette/routing.py"
    }
    assert {item["path"] for item in gold["changed_files"]} == {
        "starlette/routing.py"
    }
    assert {item["path"] for item in gold["test_files"]} == {
        "tests/test_routing.py"
    }
    assert gold["root_cause_files"] != gold["changed_files"]


def test_first_case_hybrid_config_and_root_cause_relevance_are_versioned() -> None:
    retrieval = DATA / "retrieval" / "starlette-issue-1552"
    config = json.loads((retrieval / "config.json").read_text(encoding="utf-8"))
    gold = json.loads((retrieval / "gold.json").read_text(encoding="utf-8"))

    assert config["bm25"] == {
        "implementation": "python_stdlib",
        "tokenizer": {
            "version": "code-text-v1",
            "pattern": "[a-z0-9]+(?:_[a-z0-9]+)*",
            "casefold": True,
            "snake_case": "whole_and_parts",
            "fields": ["path", "content"],
            "stemming": False,
            "stop_words": False,
        },
        "idf": "log(1 + (N - df + 0.5) / (df + 0.5))",
        "k1": 1.2,
        "b": 0.75,
        "score_precision": 6,
        "tie_break": [
            "score_desc",
            "path_asc",
            "start_line_asc",
            "end_line_asc",
            "source_type_asc",
        ],
        "storage": "in_memory_inverted_index",
        "capacity_scope": "single_case",
        "migration_condition": (
            "Use persistent search storage when multi-case scale or measured latency "
            "exceeds the project budget"
        ),
    }
    assert config["fusion"] == {
        "strategy": "rrf-v1",
        "rank_constant": 60,
        "vector_weight": 1.0,
        "bm25_weight": 1.0,
        "candidate_top_k": 10,
        "top_k": 10,
        "score_precision": 8,
        "tie_break": [
            "score_desc",
            "path_asc",
            "start_line_asc",
            "end_line_asc",
            "source_type_asc",
        ],
    }
    assert config["benchmark"] == {
        "clock": "time.perf_counter_ns",
        "warmup_runs": 1,
        "repetitions": 5,
        "summary": ["samples", "median", "min", "max"],
    }
    assert [item["relevance"] for item in gold["root_cause_files"]] == [2]
    assert all("relevance" not in item for item in gold["changed_files"])
    assert all("relevance" not in item for item in gold["test_files"])


def test_first_case_retrieval_result_has_parseable_versioned_citations() -> None:
    retrieval = DATA / "retrieval" / "starlette-issue-1552"
    result = json.loads((retrieval / "result.json").read_text(encoding="utf-8"))
    commit = "e086fc2da361767b532cf690e5203619bbae98aa"

    assert result["query"] == (
        'Route naming introspection always return "method" for method endpoints'
    )
    assert [candidate["rank"] for candidate in result["candidates"]] == list(
        range(1, 11)
    )
    for candidate in result["candidates"]:
        assert candidate["commit"] == commit
        assert candidate["start_line"] <= candidate["end_line"]
        assert candidate["reference"] == (
            f"https://github.com/Kludex/starlette/blob/{commit}/"
            f"{candidate['path']}#L{candidate['start_line']}-L{candidate['end_line']}"
        )
        assert len(candidate["score"].partition(".")[2]) == 6

    assert result["metadata"] == {
        "chunk_count": 850,
        "chunking": {
            "markdown_parser": "headings-v1",
            "max_lines": 120,
            "overlap_lines": 20,
            "python_parser": "ast-v1",
        },
        "data_version": "cases-v1",
        "embedding_dimension": 384,
        "embedding_identifier": (
            "fastembed:BAAI/bge-small-en-v1.5@"
            "52398278842ec682c6f32300af41344b1c0b0bb2"
        ),
        "generation_version": "retrieval-v1",
        "index_version": "starlette-1552-vector-v1",
        "score_precision": 6,
        "similarity": "cosine",
        "snapshot_commit": commit,
        "tie_break": "path,start_line,end_line,source_type",
        "top_k": 10,
    }
    assert result["recall"] == {
        "changed_files": {"hits": 1, "recall": 1.0, "total": 1},
        "root_cause_files": {"hits": 1, "recall": 1.0, "total": 1},
        "test_files": {"hits": 0, "recall": 0.0, "total": 1},
        "top_k": 10,
    }


def test_first_case_hybrid_comparison_is_versioned_traceable_and_measured() -> None:
    retrieval = DATA / "retrieval" / "starlette-issue-1552"
    config = json.loads((retrieval / "config.json").read_text(encoding="utf-8"))
    result = json.loads((retrieval / "comparison.json").read_text(encoding="utf-8"))
    commit = config["snapshot"]["commit"]

    assert result["case_id"] == "starlette-issue-1552"
    assert result["shared_input"] == {
        "query": 'Route naming introspection always return "method" for method endpoints',
        "snapshot_commit": commit,
        "index_version": config["chunking"]["index_version"],
        "chunk_count": 850,
        "embedding_identifier": (
            f"fastembed:{config['embedding']['model']}@{config['embedding']['revision']}"
        ),
        "embedding_dimension": config["embedding"]["dimension"],
        "top_k": 10,
    }
    assert set(result["groups"]) == {"g1_vector", "g2_hybrid"}
    for group in result["groups"].values():
        assert [item["rank"] for item in group["candidates"]] == list(range(1, 11))
        assert set(group["metrics"]) == {
            "recall_at_5",
            "recall_at_10",
            "mrr",
            "ndcg_at_10",
        }
        for recall in ("recall_at_5", "recall_at_10"):
            assert set(group["metrics"][recall]) == {
                "top_k",
                "root_cause_files",
                "changed_files",
                "test_files",
            }
        for candidate in group["candidates"]:
            assert candidate["commit"] == commit
            assert candidate["start_line"] <= candidate["end_line"]
            assert candidate["reference"] == (
                f"https://github.com/Kludex/starlette/blob/{commit}/"
                f"{candidate['path']}#L{candidate['start_line']}-L{candidate['end_line']}"
            )

    for candidate in result["groups"]["g2_hybrid"]["candidates"]:
        assert set(candidate) >= {
            "vector_rank",
            "vector_score",
            "vector_contribution",
            "bm25_rank",
            "bm25_score",
            "bm25_contribution",
            "rank",
            "score",
            "commit",
            "path",
            "start_line",
            "end_line",
            "reference",
        }
        assert len(candidate["score"].partition(".")[2]) == 8

    metadata = result["metadata"]
    assert metadata["data_version"] == config["data_version"]
    assert metadata["snapshot_commit"] == commit
    assert metadata["top_k"] == config["fusion"]["top_k"]
    assert metadata["benchmark"] == {
        "clock": "perf_counter_ns",
        "warmup_runs": 1,
        "repetitions": 5,
    }
    for group in ("g1_vector", "g2_hybrid"):
        for stage in ("build", "query"):
            measurement = metadata["latency_ns"][group][stage]
            assert len(measurement["samples"]) == 5
            assert all(value >= 0 for value in measurement["samples"])
    assert metadata["index_size"]["vector"]["document_count"] == 850
    assert metadata["index_size"]["bm25"]["document_count"] == 850
    assert metadata["index_size"]["g2_total_bytes"] > (
        metadata["index_size"]["g1_total_bytes"]
    )
