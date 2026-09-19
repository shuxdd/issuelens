import json
import shutil
from pathlib import Path

from issuelens.cases import build_cases, check_cases

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def test_versioned_dataset_has_ten_traceable_leak_free_cases() -> None:
    assert {path.stem for path in (DATA / "cases").glob("*.json")} == {
        "starlette-issue-1552",
        "starlette-issue-2298",
        "starlette-issue-2306",
        "starlette-issue-2516",
        "starlette-issue-2625",
        "starlette-issue-2646",
        "starlette-issue-2692",
        "starlette-issue-2785",
        "starlette-issue-3357",
        "starlette-issue-3388",
    }
    assert check_cases(DATA) == []


def test_versioned_screening_report_explains_the_candidate_funnel() -> None:
    report = json.loads((DATA / "screening-report.json").read_text(encoding="utf-8"))

    assert report["funnel"] == {
        "collected": 17,
        "relationship_recovered": 14,
        "category_eligible": 14,
        "analyzable": 10,
        "time_slice_valid": 10,
        "manual_reviewed": 10,
        "accepted": 10,
    }
    assert {item["reason_code"] for item in report["excluded"]} == {
        "security",
        "documentation_only",
        "dependency_bot",
        "cross_repository_root_cause",
        "unanalyzable",
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

    assert len(paths) == 10
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
