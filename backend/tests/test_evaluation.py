import json
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import cast

from issuelens.report import ModelResponse, ReportConfig
from issuelens.retrieval import Embedder, HybridCandidate, Reranker


class _FixedEmbedder:
    identifier = "fixed"
    dimension = 1

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float]]:
        return [(1.0,) for _ in texts]

    def embed_query(self, text: str) -> tuple[float]:
        return (1.0,)


class _FixedReranker:
    identifier = "fixed"

    def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
        return [1.0 for _ in candidates]


def _write_minimal_experiment_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    (data / "cases").mkdir(parents=True)
    (data / "cases" / "fixture.json").write_text(
        json.dumps(
            {
                "case_id": "fixture",
                "schema_version": "1.0",
                "issue": {
                    "repository": "Kludex/starlette",
                    "title": "Route fails",
                    "source_url": "https://example.test/issues/1",
                },
                "repository_snapshot": {"commit": "abc123"},
            }
        ),
        encoding="utf-8",
    )
    manifest = data / "evaluation" / "dataset-manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "frozen": True,
                "splits": {"primary": ["fixture"], "stability": []},
            }
        ),
        encoding="utf-8",
    )
    snapshot_cache = tmp_path / "snapshots"
    snapshot = snapshot_cache / "starlette-abc123" / "starlette-abc123"
    (snapshot / "starlette").mkdir(parents=True)
    (snapshot / ".issuelens-snapshot.json").write_text(
        json.dumps({"repository": "Kludex/starlette", "commit": "abc123"}),
        encoding="utf-8",
    )
    (snapshot / "starlette" / "routing.py").write_text("route = True\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "experiment_version": "fixture-v1",
                "formal": False,
                "dataset_manifest": str(manifest),
                "evaluator_version": "evaluation-v1",
                "report": {
                    "model": "fixed",
                    "input_price_per_million": "0",
                    "cached_input_price_per_million": "0",
                    "output_price_per_million": "0",
                    "currency": "USD",
                },
                "retrieval": {
                    "chunking": {
                        "index_version": "v1",
                        "max_lines": 120,
                        "overlap_lines": 20,
                    },
                    "vector": {
                        "top_k": 1,
                        "score_precision": 6,
                        "generation_version": "v1",
                    },
                    "bm25": {
                        "top_k": 1,
                        "score_precision": 6,
                        "tokenizer_version": "v1",
                        "k1": 1.2,
                        "b": 0.75,
                    },
                    "fusion": {
                        "top_k": 1,
                        "score_precision": 8,
                        "rank_constant": 60,
                        "vector_weight": 1.0,
                        "bm25_weight": 1.0,
                    },
                    "reranker": {"enabled": True, "top_k": 1, "score_precision": 6},
                },
            }
        ),
        encoding="utf-8",
    )
    return data, snapshot_cache, config


def test_comparable_experiment_runs_four_groups_through_shared_public_seams(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data = tmp_path / "data"
    case_id = "fixture"
    commit = "abc123"
    case_path = data / "cases" / f"{case_id}.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(
        json.dumps(
            {
                "case_id": case_id,
                "schema_version": "1.0",
                "issue": {
                    "repository": "Kludex/starlette",
                    "title": "Route fails",
                    "source_url": "https://example.test/issues/1",
                },
                "repository_snapshot": {"commit": commit},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = data / "evaluation" / "dataset-manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "frozen": True,
                "splits": {"primary": [case_id], "stability": []},
                "preregistered_metrics": {
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshots" / f"starlette-{commit}" / f"starlette-{commit}"
    (snapshot / "starlette").mkdir(parents=True)
    (snapshot / ".issuelens-snapshot.json").write_text(
        json.dumps({"repository": "Kludex/starlette", "commit": commit}),
        encoding="utf-8",
    )
    (snapshot / "starlette" / "routing.py").write_text(
        "def route():\n    return 'broken'\n", encoding="utf-8"
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "experiment_version": "fixture-experiment-v1",
                "formal": False,
                "dataset_manifest": str(manifest_path),
                "report": {
                    "model": "fixed-model",
                    "input_price_per_million": "0",
                    "cached_input_price_per_million": "0",
                    "output_price_per_million": "0",
                    "currency": "USD",
                    "schema_version": "1.0",
                },
                "retrieval": {
                    "chunking": {
                        "index_version": "fixture-index-v1",
                        "max_lines": 120,
                        "overlap_lines": 20,
                    },
                    "vector": {
                        "top_k": 1,
                        "score_precision": 6,
                        "generation_version": "retrieval-v1",
                    },
                    "bm25": {
                        "top_k": 1,
                        "score_precision": 6,
                        "tokenizer_version": "code-text-v1",
                        "k1": 1.2,
                        "b": 0.75,
                    },
                    "fusion": {
                        "top_k": 1,
                        "score_precision": 8,
                        "rank_constant": 60,
                        "vector_weight": 1.0,
                        "bm25_weight": 1.0,
                    },
                    "reranker": {"enabled": True, "top_k": 1, "score_precision": 6},
                },
                "evaluator_version": "evaluation-v1",
            }
        ),
        encoding="utf-8",
    )

    class FixedEmbedder:
        identifier = "fixed-embedder-v1"
        dimension = 2

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, float]]:
            return [(1.0, 0.0) for _ in texts]

        def embed_query(self, text: str) -> tuple[float, float]:
            return (1.0, 0.0)

    class FixedReranker:
        identifier = "fixed-reranker-v1"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            return [1.0 for _ in candidates]

    observed_prompts: list[str] = []

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            observed_prompts.append(config.prompt_version)
            return ModelResponse(
                json.dumps(
                    {
                        "issue_classification": "routing",
                        "facts": [],
                        "root_cause_hypotheses": [],
                        "confidence": "low",
                        "unresolved_questions": [],
                    }
                ),
                1,
                1,
                2,
            )

    result = run_comparable_experiment(
        data,
        tmp_path / "snapshots",
        config_path,
        tmp_path / "results",
        FixedModel(),
        FixedEmbedder(),
        FixedReranker(),
    )

    assert [run["group"] for run in result["runs"]] == ["g1", "g2", "g3", "g4"]
    assert observed_prompts == ["one-shot-v1"] * 3 + ["agent-v1"]
    assert (
        result["runs"][3]["retrieval"]["candidates"] == result["runs"][2]["retrieval"]["candidates"]
    )
    assert {run["report"]["schema_version"] for run in result["runs"]} == {"1.0"}


def test_blind_review_material_hides_groups_and_uses_frozen_reviewer_subset(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import prepare_blind_review

    manifest_path = tmp_path / "dataset-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "splits": {"primary": ["case-a", "case-b"], "stability": ["case-b"]},
            }
        ),
        encoding="utf-8",
    )
    results_path = tmp_path / "result.json"
    results_path.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "runs": [
                    {
                        "case_id": case_id,
                        "group": group,
                        "replicate": 1,
                        "report": {"schema_version": "1.0", "root_cause_hypotheses": []},
                    }
                    for case_id in ("case-a", "case-b")
                    for group in ("g1", "g2", "g3", "g4")
                ],
            }
        ),
        encoding="utf-8",
    )

    materials = prepare_blind_review(results_path, manifest_path, tmp_path / "reviews")

    assert [item["case_id"] for item in materials["author"]["cases"]] == [
        "case-a",
        "case-b",
    ]
    assert [item["case_id"] for item in materials["second_reviewer"]["cases"]] == ["case-b"]
    assert all(
        len(case["submissions"]) == 4
        for review in (materials["author"], materials["second_reviewer"])
        for case in review["cases"]
    )
    assert "group" not in json.dumps(materials["author"])
    assert set(materials["review_key"]["submissions"].values()) == {"g1", "g2", "g3", "g4"}


def test_experiment_scoring_combines_deterministic_and_human_metrics(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import score_experiment

    case_id = "case-a"
    experiment_version = "experiment-v1"
    evaluation = tmp_path / "evaluation"
    (evaluation / "gold").mkdir(parents=True)
    manifest_path = evaluation / "dataset-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "splits": {"primary": [case_id], "stability": [case_id]},
                "preregistered_metrics": {
                    "root_cause_recall_at_10_minimum": 0.7,
                    "citation_validity_minimum": 0.9,
                    "root_cause_score_2_minimum": 0.65,
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    (evaluation / "gold" / f"{case_id}.json").write_text(
        json.dumps(
            {
                "root_cause_files": [{"path": "starlette/routing.py", "relevance": 2}],
                "changed_files": [{"path": "starlette/routing.py"}],
                "test_files": [{"path": "tests/test_routing.py"}],
            }
        ),
        encoding="utf-8",
    )

    def candidate(path: str, rank: int) -> dict[str, object]:
        return {
            "rank": rank,
            "score": "1.0",
            "commit": "abc123",
            "path": path,
            "start_line": 1,
            "end_line": 1,
            "source_type": "python_source",
            "index_version": "v1",
            "content": "line",
            "reference": f"https://example.test/{path}#L1-L1",
        }

    runs = []
    for group in ("g1", "g2", "g3", "g4"):
        runs.append(
            {
                "case_id": case_id,
                "group": group,
                "replicate": 1,
                "retrieval": {
                    "candidates": [
                        candidate("starlette/routing.py", 1),
                        candidate("tests/test_routing.py", 2),
                    ]
                },
                "report": {
                    "schema_version": "1.0",
                    "facts": [{"evidence": [{"reference": "https://example.test/a#L1-L1"}]}],
                    "root_cause_hypotheses": [],
                },
                "metadata": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "cost": "0.01",
                    "latency_ns": 100,
                },
            }
        )
    primary_runs = list(runs)
    runs.extend({**run, "replicate": replicate} for replicate in (2, 3) for run in primary_runs)
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "experiment_version": experiment_version,
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": runs,
            }
        ),
        encoding="utf-8",
    )

    scores = [
        {
            "case_id": case_id,
            "submission_id": sha256(f"{experiment_version}:{case_id}:{group}".encode()).hexdigest()[
                :12
            ],
            "root_cause_verdict": 2,
            "citation_validity": [True],
            "notes": "",
        }
        for group in ("g1", "g2", "g3", "g4")
    ]
    review_paths = []
    for role in ("project_author", "second_reviewer"):
        path = tmp_path / f"{role}.json"
        path.write_text(
            json.dumps(
                {
                    "reviewer_role": role,
                    "cases": [
                        {
                            "case_id": case_id,
                            "submissions": [
                                {
                                    "submission_id": item["submission_id"],
                                    "score": {
                                        key: value
                                        for key, value in item.items()
                                        if key not in {"case_id", "submission_id"}
                                    },
                                }
                                for item in scores
                            ]
                            + [
                                {
                                    "submission_id": "failed-submission",
                                    "score": {
                                        "root_cause_verdict": None,
                                        "citation_validity": [],
                                        "notes": "",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        review_paths.append(path)

    report = score_experiment(results_path, review_paths, manifest_path)

    assert report["groups"]["g1"]["metrics"] == {
        "root_cause_files_recall_at_5": 1.0,
        "root_cause_files_recall_at_10": 1.0,
        "changed_files_recall_at_5": 1.0,
        "changed_files_recall_at_10": 1.0,
        "test_files_recall_at_5": 1.0,
        "test_files_recall_at_10": 1.0,
        "mrr": 1.0,
        "ndcg_at_10": 1.0,
        "root_cause_accuracy": 1.0,
        "citation_validity": 1.0,
    }
    assert report["groups"]["g1"]["usage"]["total_tokens"] == 15
    assert report["preregistered_assessment"]["g4_vs_g1_clear_gain"] is False
    assert report["review_agreement"]["root_cause_exact"] == 1.0


def test_formal_experiment_refuses_to_overwrite_existing_raw_results(
    tmp_path: Path,
) -> None:
    import pytest
    from issuelens.evaluation import run_comparable_experiment
    from issuelens.report import ReportModel

    output = tmp_path / "formal-run"
    output.mkdir()
    config = tmp_path / "config.json"
    config.write_text('{"formal":true}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="experiment output already exists"):
        run_comparable_experiment(
            tmp_path,
            tmp_path,
            config,
            output,
            cast(ReportModel, object()),
            cast(Embedder, object()),
            cast(Reranker, object()),
        )


def test_stability_case_keeps_three_independent_runs_for_every_group(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data = tmp_path / "data"
    (data / "cases").mkdir(parents=True)
    (data / "cases" / "fixture.json").write_text(
        json.dumps(
            {
                "case_id": "fixture",
                "schema_version": "1.0",
                "issue": {
                    "repository": "Kludex/starlette",
                    "title": "Route fails",
                    "source_url": "https://example.test/issues/1",
                },
                "repository_snapshot": {"commit": "abc123"},
            }
        ),
        encoding="utf-8",
    )
    manifest = data / "evaluation" / "dataset-manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "frozen": True,
                "splits": {"primary": ["fixture"], "stability": ["fixture"]},
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshots" / "starlette-abc123" / "starlette-abc123"
    (snapshot / "starlette").mkdir(parents=True)
    (snapshot / ".issuelens-snapshot.json").write_text(
        json.dumps({"repository": "Kludex/starlette", "commit": "abc123"}),
        encoding="utf-8",
    )
    (snapshot / "starlette" / "routing.py").write_text("route = True\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "experiment_version": "fixture-v1",
                "formal": False,
                "dataset_manifest": str(manifest),
                "evaluator_version": "evaluation-v1",
                "report": {
                    "model": "fixed",
                    "input_price_per_million": "0",
                    "cached_input_price_per_million": "0",
                    "output_price_per_million": "0",
                    "currency": "USD",
                },
                "retrieval": {
                    "chunking": {"index_version": "v1", "max_lines": 120, "overlap_lines": 20},
                    "vector": {"top_k": 1, "score_precision": 6, "generation_version": "v1"},
                    "bm25": {
                        "top_k": 1,
                        "score_precision": 6,
                        "tokenizer_version": "v1",
                        "k1": 1.2,
                        "b": 0.75,
                    },
                    "fusion": {
                        "top_k": 1,
                        "score_precision": 8,
                        "rank_constant": 60,
                        "vector_weight": 1.0,
                        "bm25_weight": 1.0,
                    },
                    "reranker": {"enabled": True, "top_k": 1, "score_precision": 6},
                },
            }
        ),
        encoding="utf-8",
    )

    class FixedEmbedder:
        identifier = "fixed"
        dimension = 1

        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float]]:
            return [(1.0,) for _ in texts]

        def embed_query(self, text: str) -> tuple[float]:
            return (1.0,)

    class FixedReranker:
        identifier = "fixed"

        def score(self, query: str, candidates: Sequence[HybridCandidate]) -> list[float]:
            return [1.0 for _ in candidates]

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                0,
                0,
                0,
            )

    result = run_comparable_experiment(
        data,
        tmp_path / "snapshots",
        config,
        tmp_path / "results",
        FixedModel(),
        FixedEmbedder(),
        FixedReranker(),
    )

    assert [(run["replicate"], run["group"]) for run in result["runs"]] == [
        (replicate, group) for replicate in (1, 2, 3) for group in ("g1", "g2", "g3", "g4")
    ]


def test_scoring_requires_arbitration_for_every_human_review_disagreement(
    tmp_path: Path,
) -> None:
    import pytest
    from issuelens.evaluation import score_experiment

    submission_id = sha256(b"experiment-v1:case-a:g1").hexdigest()[:12]
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": "g1",
                        "replicate": 1,
                        "status": "completed",
                        "report": {
                            "facts": [{"evidence": [{"reference": "citation"}]}],
                            "root_cause_hypotheses": [],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": ["case-a"], "stability": ["case-a"]},
                "preregistered_metrics": {},
            }
        ),
        encoding="utf-8",
    )
    paths = []
    for role, verdict in (("project_author", 2), ("second_reviewer", 1)):
        path = tmp_path / f"{role}.json"
        path.write_text(
            json.dumps(
                {
                    "reviewer_role": role,
                    "scores": [
                        {
                            "case_id": "case-a",
                            "submission_id": submission_id,
                            "root_cause_verdict": verdict,
                            "citation_validity": [True],
                            "notes": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)

    with pytest.raises(ValueError, match=f"unresolved review disagreement: {submission_id}"):
        score_experiment(results, paths, manifest)


def test_formal_experiment_requires_frozen_thirty_case_and_ten_case_splits(
    tmp_path: Path,
) -> None:
    import pytest
    from issuelens.evaluation import run_comparable_experiment
    from issuelens.report import ReportModel

    manifest = tmp_path / "dataset-manifest.json"
    primary = [f"case-{number}" for number in range(29)]
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "frozen": True,
                "splits": {"primary": primary, "stability": primary[:10]},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"formal": True, "dataset_manifest": str(manifest)}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="30 primary and 10 stability cases"):
        run_comparable_experiment(
            tmp_path,
            tmp_path,
            config,
            tmp_path / "results",
            cast(ReportModel, object()),
            cast(Embedder, object()),
            cast(Reranker, object()),
        )


def test_formal_experiment_rejects_frozen_manifest_hash_drift(tmp_path: Path) -> None:
    import pytest
    from issuelens.evaluation import run_comparable_experiment
    from issuelens.report import ReportModel

    manifest = tmp_path / "dataset-manifest.json"
    primary = [f"case-{number}" for number in range(30)]
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "frozen": True,
                "splits": {"primary": primary, "stability": primary[:10]},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "formal": True,
                "dataset_manifest": str(manifest),
                "dataset_manifest_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dataset manifest hash mismatch"):
        run_comparable_experiment(
            tmp_path,
            tmp_path,
            config,
            tmp_path / "results",
            cast(ReportModel, object()),
            cast(Embedder, object()),
            cast(Reranker, object()),
        )


def test_failed_frozen_runs_remain_in_metric_denominators(tmp_path: Path) -> None:
    from issuelens.evaluation import score_experiment

    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": group,
                        "replicate": 1,
                        "status": "failed",
                        "failure": {"error_type": "RuntimeError", "message": "offline failure"},
                    }
                    for group in ("g1", "g2", "g3", "g4")
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": ["case-a"], "stability": []},
                "preregistered_metrics": {
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    review_paths = []
    for role in ("project_author", "second_reviewer"):
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps({"reviewer_role": role, "scores": []}), encoding="utf-8")
        review_paths.append(path)

    report = score_experiment(results, review_paths, manifest)

    assert report["groups"]["g4"]["failures"] == 1
    assert set(report["groups"]["g4"]["metrics"].values()) == {0.0}
    assert len(report["per_case"]) == 4


def test_stability_report_links_three_runs_and_reports_consistency_and_variation(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import score_experiment

    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": group,
                        "replicate": replicate,
                        "status": "failed",
                        "failure": {
                            "error_type": "RuntimeError",
                            "message": "offline failure",
                        },
                    }
                    for group in ("g1", "g2", "g3", "g4")
                    for replicate in (1, 2, 3)
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": ["case-a"], "stability": ["case-a"]},
                "preregistered_metrics": {
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    review_paths = []
    for role in ("project_author", "second_reviewer"):
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps({"reviewer_role": role, "scores": []}), encoding="utf-8")
        review_paths.append(path)

    report = score_experiment(results, review_paths, manifest)

    stability = report["stability"]["groups"]["g1"]
    assert stability["runs"] == 3
    assert stability["outcome_consistency"] == 1.0
    assert stability["root_cause_conclusion_consistency"] == 1.0
    assert stability["cost"] == {"min": 0.0, "max": 0.0, "mean": 0.0}
    assert stability["latency_ns"] == {"min": 0, "max": 0, "mean": 0.0}


def test_model_failures_are_written_as_independent_auditable_raw_runs(tmp_path: Path) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data, snapshot_cache, config = _write_minimal_experiment_fixture(tmp_path)

    class FailingModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            raise RuntimeError("provider unavailable")

    output = tmp_path / "results"
    result = run_comparable_experiment(
        data,
        snapshot_cache,
        config,
        output,
        FailingModel(),
        _FixedEmbedder(),
        _FixedReranker(),
    )

    assert [(run["group"], run["status"]) for run in result["runs"]] == [
        (group, "failed") for group in ("g1", "g2", "g3", "g4")
    ]
    assert all(run["failure"]["error_type"] == "RuntimeError" for run in result["runs"])
    assert sorted(path.name for path in (output / "raw" / "fixture").rglob("*.json")) == [
        "1.json",
        "1.json",
        "1.json",
        "1.json",
    ]


def test_shared_retrieval_failure_is_recorded_for_all_four_groups(tmp_path: Path) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data, snapshot_cache, config = _write_minimal_experiment_fixture(tmp_path)

    class FailingEmbedder(_FixedEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> list[tuple[float]]:
            raise RuntimeError("embedding unavailable")

    class UnusedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            raise AssertionError("model must not run when retrieval failed")

    result = run_comparable_experiment(
        data,
        snapshot_cache,
        config,
        tmp_path / "results",
        UnusedModel(),
        FailingEmbedder(),
        _FixedReranker(),
    )

    assert [(run["group"], run["status"]) for run in result["runs"]] == [
        (group, "failed") for group in ("g1", "g2", "g3", "g4")
    ]
    assert all(run["failure"]["message"] == "embedding unavailable" for run in result["runs"])


def test_llm_judge_is_preserved_only_as_an_auxiliary_column(tmp_path: Path) -> None:
    from issuelens.evaluation import score_experiment

    judge = {"root_cause_verdict": 2, "evaluator": "judge-v1"}
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": group,
                        "replicate": 1,
                        "status": "failed",
                        "failure": {"error_type": "RuntimeError", "message": "failed"},
                        "llm_judge": judge,
                    }
                    for group in ("g1", "g2", "g3", "g4")
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": ["case-a"], "stability": []},
                "preregistered_metrics": {
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    reviews = []
    for role in ("project_author", "second_reviewer"):
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps({"reviewer_role": role, "scores": []}), encoding="utf-8")
        reviews.append(path)

    report = score_experiment(results, reviews, manifest)

    assert report["per_case"][0]["llm_judge"] == judge
    assert report["groups"]["g1"]["metrics"]["root_cause_accuracy"] == 0.0


def test_blind_review_preallocates_one_validity_score_per_report_citation(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import prepare_blind_review

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "fixture-v1",
                "splits": {"primary": ["case-a"], "stability": []},
            }
        ),
        encoding="utf-8",
    )
    report = {
        "schema_version": "1.0",
        "facts": [{"evidence": [{"reference": "https://example.test/a#L1-L1"}]}],
        "root_cause_hypotheses": [],
    }
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": group,
                        "replicate": 1,
                        "report": report,
                    }
                    for group in ("g1", "g2", "g3", "g4")
                ],
            }
        ),
        encoding="utf-8",
    )

    materials = prepare_blind_review(results, manifest, tmp_path / "reviews")

    assert all(
        submission["score"]["citation_validity"] == [None]
        for submission in materials["author"]["cases"][0]["submissions"]
    )


def test_scoring_rejects_incomplete_project_author_review_coverage(tmp_path: Path) -> None:
    import pytest
    from issuelens.evaluation import score_experiment

    experiment = "experiment-v1"
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": experiment,
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": group,
                        "replicate": 1,
                        "status": "completed",
                        "report": {"facts": [], "root_cause_hypotheses": []},
                    }
                    for group in ("g1", "g2", "g3", "g4")
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": ["case-a"], "stability": []},
                "preregistered_metrics": {},
            }
        ),
        encoding="utf-8",
    )
    scores = [
        {
            "case_id": "case-a",
            "submission_id": sha256(f"{experiment}:case-a:{group}".encode()).hexdigest()[:12],
            "root_cause_verdict": 2,
            "citation_validity": [],
            "notes": "",
        }
        for group in ("g1", "g2", "g3")
    ]
    author = tmp_path / "author.json"
    author.write_text(
        json.dumps({"reviewer_role": "project_author", "scores": scores}), encoding="utf-8"
    )
    second = tmp_path / "second.json"
    second.write_text(
        json.dumps({"reviewer_role": "second_reviewer", "scores": []}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="project_author review coverage"):
        score_experiment(results, [author, second], manifest)


def test_experiment_result_records_all_shared_reproducibility_versions(tmp_path: Path) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data, snapshot_cache, config_path = _write_minimal_experiment_fixture(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "application_commit": "1ee1521",
            "report_schema_version": "1.0",
            "report": {**config["report"], "model_version": "fixed-model-v1"},
        }
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                0,
                0,
                0,
            )

    result = run_comparable_experiment(
        data,
        snapshot_cache,
        config_path,
        tmp_path / "results",
        FixedModel(),
        _FixedEmbedder(),
        _FixedReranker(),
    )

    assert result["reproducibility"] == {
        "application_commit": "1ee1521",
        "dataset_manifest_sha256": sha256(
            (data / "evaluation" / "dataset-manifest.json").read_bytes()
        ).hexdigest(),
        "model": "fixed",
        "model_version": "fixed-model-v1",
        "prompt_scope": {
            "g1": "one-shot-v1",
            "g2": "one-shot-v1",
            "g3": "one-shot-v1",
            "g4": "agent-v1",
        },
        "report_schema_version": "1.0",
        "retrieval": config["retrieval"],
    }


def test_preregistered_assessment_publicly_lists_every_unmet_group_metric(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import score_experiment

    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": "experiment-v1",
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    {
                        "case_id": "case-a",
                        "group": group,
                        "replicate": 1,
                        "status": "failed",
                        "failure": {"error_type": "RuntimeError", "message": "failed"},
                    }
                    for group in ("g1", "g2", "g3", "g4")
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": ["case-a"], "stability": []},
                "preregistered_metrics": {
                    "root_cause_recall_at_10_minimum": 0.7,
                    "citation_validity_minimum": 0.9,
                    "root_cause_score_2_minimum": 0.65,
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    reviews = []
    for role in ("project_author", "second_reviewer"):
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps({"reviewer_role": role, "scores": []}), encoding="utf-8")
        reviews.append(path)

    report = score_experiment(results, reviews, manifest)

    assert report["preregistered_assessment"]["groups"]["g1"]["unmet"] == [
        "root_cause_recall_at_10",
        "citation_validity",
        "root_cause_accuracy",
    ]


def test_run_records_retrieval_and_end_to_end_latency_for_every_group(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data, snapshot_cache, config = _write_minimal_experiment_fixture(tmp_path)

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                0,
                0,
                0,
            )

    result = run_comparable_experiment(
        data,
        snapshot_cache,
        config,
        tmp_path / "results",
        FixedModel(),
        _FixedEmbedder(),
        _FixedReranker(),
    )

    assert all(run["metadata"]["retrieval_latency_ns"] >= 0 for run in result["runs"])
    assert all(
        run["metadata"]["total_latency_ns"] >= run["metadata"]["retrieval_latency_ns"]
        for run in result["runs"]
    )


def test_g4_preserves_trace_identifiers_for_optional_score_export(tmp_path: Path) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data, snapshot_cache, config = _write_minimal_experiment_fixture(tmp_path)

    class FixedModel:
        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                0,
                0,
                0,
            )

    result = run_comparable_experiment(
        data,
        snapshot_cache,
        config,
        tmp_path / "results",
        FixedModel(),
        _FixedEmbedder(),
        _FixedReranker(),
    )

    g4 = result["runs"][3]
    assert g4["run_id"]
    assert g4["trace_id"]
    assert all(event["run_id"] == g4["run_id"] for event in g4["events"])


def test_blind_review_refuses_to_overwrite_existing_materials(tmp_path: Path) -> None:
    import pytest
    from issuelens.evaluation import prepare_blind_review

    output = tmp_path / "reviews"
    output.mkdir()

    with pytest.raises(FileExistsError, match="blind review output already exists"):
        prepare_blind_review(
            tmp_path / "missing-results.json", tmp_path / "missing-manifest.json", output
        )


def test_arbitration_preserves_specific_disagreement_and_controls_final_score(
    tmp_path: Path,
) -> None:
    import pytest
    from issuelens.evaluation import score_experiment

    experiment = "experiment-v1"
    case_id = "case-a"
    evaluation = tmp_path / "evaluation"
    (evaluation / "gold").mkdir(parents=True)
    manifest = evaluation / "dataset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "splits": {"primary": [case_id], "stability": [case_id]},
                "preregistered_metrics": {
                    "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.1,
                    "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    (evaluation / "gold" / f"{case_id}.json").write_text(
        json.dumps(
            {
                "root_cause_files": [{"path": "starlette/routing.py", "relevance": 2}],
                "changed_files": [{"path": "starlette/routing.py"}],
                "test_files": [],
            }
        ),
        encoding="utf-8",
    )
    candidate = {
        "rank": 1,
        "score": "1",
        "commit": "abc",
        "path": "starlette/routing.py",
        "start_line": 1,
        "end_line": 1,
        "source_type": "python_source",
        "index_version": "v1",
        "content": "line",
        "reference": "https://example.test/routing.py#L1-L1",
    }
    completed = {
        "case_id": case_id,
        "group": "g1",
        "status": "completed",
        "retrieval": {"candidates": [candidate]},
        "report": {"facts": [], "root_cause_hypotheses": []},
        "metadata": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost": "0",
            "latency_ns": 0,
        },
    }
    failed = {
        "case_id": case_id,
        "status": "failed",
        "failure": {"error_type": "RuntimeError", "message": "failed"},
    }
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "experiment_version": experiment,
                "dataset_version": "fixture-v1",
                "evaluator_version": "evaluation-v1",
                "runs": [
                    *({**completed, "replicate": replicate} for replicate in (1, 2, 3)),
                    *(
                        {**failed, "group": group, "replicate": replicate}
                        for group in ("g2", "g3", "g4")
                        for replicate in (1, 2, 3)
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    submission_id = sha256(f"{experiment}:{case_id}:g1".encode()).hexdigest()[:12]
    paths = []
    for role, verdict, notes in (
        ("project_author", 2, "author evidence assessment"),
        ("second_reviewer", 1, "mechanism is incomplete"),
        ("arbitration", 2, "code region and mechanism are both supported"),
    ):
        path = tmp_path / f"{role}.json"
        path.write_text(
            json.dumps(
                {
                    "reviewer_role": role,
                    "scores": [
                        {
                            "case_id": case_id,
                            "submission_id": submission_id,
                            "root_cause_verdict": verdict,
                            "citation_validity": [],
                            "notes": notes,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)

    report = score_experiment(results, paths, manifest)

    assert report["review_agreement"]["root_cause_exact"] == 0.0
    assert report["review_agreement"]["disagreements"] == [
        {
            "submission_id": submission_id,
            "project_author": {"root_cause_verdict": 2, "citation_validity": []},
            "second_reviewer": {"root_cause_verdict": 1, "citation_validity": []},
            "arbitration": {
                "root_cause_verdict": 2,
                "citation_validity": [],
                "notes": "code region and mechanism are both supported",
            },
        }
    ]
    assert report["groups"]["g1"]["metrics"]["root_cause_accuracy"] == 1.0

    paths[-1].write_text(
        json.dumps(
            {
                "reviewer_role": "arbitration",
                "scores": [
                    {
                        "case_id": case_id,
                        "submission_id": submission_id,
                        "root_cause_verdict": 3,
                        "citation_validity": [],
                        "notes": "invalid verdict",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=f"arbitration score is invalid: {submission_id}"):
        score_experiment(results, paths, manifest)


def test_interrupted_experiment_resumes_without_overwriting_existing_raw_runs(
    tmp_path: Path,
) -> None:
    from issuelens.evaluation import run_comparable_experiment

    data, snapshot_cache, config = _write_minimal_experiment_fixture(tmp_path)
    output = tmp_path / "results"

    class FixedModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                '{"issue_classification":"routing","facts":[],"root_cause_hypotheses":[],"confidence":"low","unresolved_questions":[]}',
                0,
                0,
                0,
            )

    run_comparable_experiment(
        data,
        snapshot_cache,
        config,
        output,
        FixedModel(),
        _FixedEmbedder(),
        _FixedReranker(),
    )
    preserved = (output / "raw/fixture/g1/1.json").read_bytes()
    (output / "raw/fixture/g4/1.json").unlink()
    (output / "result.json").unlink()
    resumed_model = FixedModel()

    result = run_comparable_experiment(
        data,
        snapshot_cache,
        config,
        output,
        resumed_model,
        _FixedEmbedder(),
        _FixedReranker(),
        resume=True,
    )

    assert resumed_model.calls == 1
    assert len(result["runs"]) == 4
    assert (output / "raw/fixture/g1/1.json").read_bytes() == preserved
