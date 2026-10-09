import json
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from statistics import fmean
from typing import Any, cast

from issuelens.investigation import InvestigationTools, run_investigation
from issuelens.report import ReportConfig, ReportModel, generate_report
from issuelens.retrieval import (
    BenchmarkConfig,
    BM25Config,
    ChunkingConfig,
    Embedder,
    FusionConfig,
    RerankedRun,
    Reranker,
    RerankerConfig,
    RetrievalCandidate,
    RetrievalMetadata,
    RetrievalRun,
    VectorIndexConfig,
    build_corpus,
    evaluate_retrieval_metrics,
    load_retrieval_input,
    run_three_way_experiment,
)


def _write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


class _FrozenRetrievalTools(InvestigationTools):
    def __init__(self, candidates: tuple[RetrievalCandidate, ...]) -> None:
        self.candidates = candidates

    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        return list(self.candidates) if name in {"search_code", "semantic_search"} else []


def run_comparable_experiment(
    data_root: Path,
    snapshot_cache: Path,
    config_path: Path,
    output_dir: Path,
    model: ReportModel,
    embedder: Embedder,
    reranker: Reranker,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    if output_dir.exists() and not resume:
        raise FileExistsError(f"experiment output already exists: {output_dir}")
    if resume and not output_dir.is_dir():
        raise FileNotFoundError(f"experiment output does not exist: {output_dir}")
    if resume and (output_dir / "result.json").exists():
        raise FileExistsError(f"completed experiment cannot be resumed: {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest_path = Path(config["dataset_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if config.get("formal") and (
        manifest.get("frozen") is not True
        or len(manifest["splits"]["primary"]) != 30
        or len(manifest["splits"]["stability"]) != 10
        or not set(manifest["splits"]["stability"]).issubset(manifest["splits"]["primary"])
    ):
        raise ValueError("formal experiment requires frozen 30 primary and 10 stability cases")
    if config.get("formal") and sha256(manifest_path.read_bytes()).hexdigest() != config.get(
        "dataset_manifest_sha256"
    ):
        raise ValueError("dataset manifest hash mismatch")
    retrieval = config["retrieval"]
    report = config["report"]
    schedule = [
        *((case_id, 1) for case_id in manifest["splits"]["primary"]),
        *(
            (case_id, replicate)
            for replicate in (2, 3)
            for case_id in manifest["splits"]["stability"]
        ),
    ]
    expected_keys = [
        (case_id, group, replicate)
        for case_id, replicate in schedule
        for group in ("g1", "g2", "g3", "g4")
    ]
    runs: dict[tuple[str, str, int], dict[str, Any]] = {}
    if resume:
        for path in (output_dir / "raw").rglob("*.json"):
            run = json.loads(path.read_text(encoding="utf-8"))
            key = (run.get("case_id"), run.get("group"), run.get("replicate"))
            expected_path = output_dir / "raw" / str(key[0]) / str(key[1]) / f"{key[2]}.json"
            if key not in expected_keys or path.resolve() != expected_path.resolve() or key in runs:
                raise ValueError(f"invalid resumable raw result: {path}")
            runs[key] = run
    else:
        output_dir.mkdir(parents=True)

    def record(run: dict[str, Any]) -> None:
        key = (run["case_id"], run["group"], run["replicate"])
        if key in runs:
            raise FileExistsError(f"raw result already exists: {key}")
        _write_json_exclusive(
            output_dir / "raw" / run["case_id"] / run["group"] / f"{run['replicate']}.json",
            run,
        )
        runs[key] = run

    for case_id, replicate in schedule:
        missing_groups = [
            group
            for group in ("g1", "g2", "g3", "g4")
            if (case_id, group, replicate) not in runs
        ]
        if not missing_groups:
            continue
        retrieval_input = load_retrieval_input(data_root / "cases" / f"{case_id}.json")
        snapshot = (
            snapshot_cache
            / f"starlette-{retrieval_input.snapshot_commit}"
            / f"starlette-{retrieval_input.snapshot_commit}"
        )
        try:
            comparison = run_three_way_experiment(
                build_corpus(snapshot, retrieval_input, ChunkingConfig(**retrieval["chunking"])),
                retrieval_input.query,
                embedder,
                VectorIndexConfig(**retrieval["vector"], data_version=manifest["dataset_version"]),
                BM25Config(**retrieval["bm25"]),
                FusionConfig(**retrieval["fusion"]),
                reranker,
                RerankerConfig(**retrieval["reranker"]),
                benchmark=BenchmarkConfig(),
            )
            if not isinstance(comparison.g3_reranked, RerankedRun):
                raise ValueError("G3 requires an enabled reranker")
        except Exception as error:
            for group in missing_groups:
                record(
                    {
                        "case_id": case_id,
                        "group": group,
                        "replicate": replicate,
                        "status": "failed",
                        "failure": {
                            "error_type": type(error).__name__,
                            "message": str(error),
                        },
                    }
                )
            continue
        latency = comparison.metadata["latency_ns"]
        retrieval_latency = {
            "g1": latency["g1_vector"]["build"]["median"] + latency["g1_vector"]["query"]["median"],
            "g2": latency["g2_hybrid"]["build"]["median"] + latency["g2_hybrid"]["query"]["median"],
            "g3": latency["g2_hybrid"]["build"]["median"]
            + latency["g2_hybrid"]["query"]["median"]
            + latency["g3_reranked"]["reranker"]["median"],
        }
        one_shot_config = ReportConfig(
            report["model"],
            "one-shot-v1",
            report["input_price_per_million"],
            report["output_price_per_million"],
            report["currency"],
            report["cached_input_price_per_million"],
        )
        for group, retrieval_run in (
            ("g1", comparison.g1_vector),
            ("g2", comparison.g2_hybrid),
            ("g3", comparison.g3_reranked),
        ):
            if group not in missing_groups:
                continue
            base = {
                "case_id": case_id,
                "group": group,
                "replicate": replicate,
                "retrieval": {"candidates": [asdict(item) for item in retrieval_run.candidates]},
            }
            try:
                generated = generate_report(
                    retrieval_input,
                    retrieval_run,
                    snapshot,
                    model,
                    one_shot_config,
                )
            except Exception as error:
                record(
                    {
                        **base,
                        "status": "failed",
                        "failure": {
                            "error_type": type(error).__name__,
                            "message": str(error),
                        },
                    }
                )
            else:
                generated["metadata"]["retrieval_latency_ns"] = retrieval_latency[group]
                generated["metadata"]["total_latency_ns"] = (
                    retrieval_latency[group] + generated["metadata"]["latency_ns"]
                )
                record(
                    {
                        **base,
                        "status": "completed",
                        **generated,
                    }
                )
        if "g4" not in missing_groups:
            continue
        try:
            investigation = run_investigation(
                retrieval_input,
                comparison.g3_reranked,
                snapshot,
                model,
                ReportConfig(
                    report["model"],
                    "agent-v1",
                    report["input_price_per_million"],
                    report["output_price_per_million"],
                    report["currency"],
                    report["cached_input_price_per_million"],
                ),
                _FrozenRetrievalTools(comparison.g3_reranked.candidates),
            )
        except Exception as error:
            record(
                {
                    "case_id": case_id,
                    "group": "g4",
                    "replicate": replicate,
                    "retrieval": {
                        "candidates": [asdict(item) for item in comparison.g3_reranked.candidates]
                    },
                    "status": "failed",
                    "failure": {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                }
            )
        else:
            investigation["statistics"]["retrieval_latency_ns"] = retrieval_latency["g3"]
            investigation["statistics"]["total_latency_ns"] = (
                retrieval_latency["g3"] + investigation["statistics"]["run_latency_ns"]
            )
            record(
                {
                    "case_id": case_id,
                    "group": "g4",
                    "replicate": replicate,
                    "retrieval": {
                        "candidates": [asdict(item) for item in comparison.g3_reranked.candidates]
                    },
                    "status": "completed",
                    "run_id": investigation["run_id"],
                    "trace_id": investigation["trace_id"],
                    "report": investigation["report"],
                    "metadata": investigation["statistics"],
                    "outcome": investigation["outcome"],
                    "events": investigation["events"],
                }
            )

    result = {
        "schema_version": "1.0",
        "experiment_version": config["experiment_version"],
        "dataset_version": manifest["dataset_version"],
        "evaluator_version": config["evaluator_version"],
        "reproducibility": {
            "application_commit": config.get("application_commit"),
            "dataset_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
            "model": report["model"],
            "model_version": report.get("model_version"),
            "prompt_scope": {
                "g1": "one-shot-v1",
                "g2": "one-shot-v1",
                "g3": "one-shot-v1",
                "g4": "agent-v1",
            },
            "report_schema_version": config.get("report_schema_version", "1.0"),
            "retrieval": retrieval,
        },
        "runs": [runs[key] for key in expected_keys],
    }
    _write_json_exclusive(output_dir / "result.json", result)
    return result


def prepare_blind_review(
    results_path: Path, manifest_path: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"blind review output already exists: {output_dir}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    experiment_version = results["experiment_version"]
    primary_runs = {
        (run["case_id"], run["group"]): run for run in results["runs"] if run["replicate"] == 1
    }
    key: dict[str, str] = {}

    def material(case_ids: list[str], reviewer_role: str) -> dict[str, Any]:
        cases: list[dict[str, Any]] = []
        for case_id in case_ids:
            submissions: list[dict[str, Any]] = []
            for group in ("g1", "g2", "g3", "g4"):
                run = primary_runs[(case_id, group)]
                submission_id = sha256(
                    f"{experiment_version}:{case_id}:{group}".encode()
                ).hexdigest()[:12]
                key[submission_id] = group
                report = run.get("report")
                citation_count = sum(
                    len(claim["evidence"])
                    for section in ("facts", "root_cause_hypotheses")
                    for claim in (report or {}).get(section, [])
                )
                submissions.append(
                    {
                        "submission_id": submission_id,
                        "report": report,
                        "failure": run.get("failure"),
                        "score": {
                            "root_cause_verdict": None,
                            "citation_validity": [None] * citation_count,
                            "notes": "",
                        },
                    }
                )
            cases.append(
                {
                    "case_id": case_id,
                    "submissions": sorted(submissions, key=lambda item: item["submission_id"]),
                }
            )
        return {
            "schema_version": "1.0",
            "experiment_version": experiment_version,
            "dataset_version": manifest["dataset_version"],
            "reviewer_role": reviewer_role,
            "cases": cases,
        }

    author = material(manifest["splits"]["primary"], "project_author")
    second = material(manifest["splits"]["stability"], "second_reviewer")
    review_key = {
        "schema_version": "1.0",
        "experiment_version": experiment_version,
        "submissions": key,
    }
    output_dir.mkdir(parents=True)
    _write_json_exclusive(output_dir / "author-blind.json", author)
    _write_json_exclusive(output_dir / "second-reviewer-blind.json", second)
    _write_json_exclusive(output_dir / "review-key.json", review_key)
    return {"author": author, "second_reviewer": second, "review_key": review_key}


def score_experiment(
    results_path: Path, review_paths: list[Path], manifest_path: Path
) -> dict[str, Any]:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def review_scores(review: dict[str, Any]) -> list[dict[str, Any]]:
        if "scores" in review:
            return cast(list[dict[str, Any]], review["scores"])
        return [
            {
                "case_id": case["case_id"],
                "submission_id": submission["submission_id"],
                **submission["score"],
            }
            for case in review.get("cases", [])
            for submission in case["submissions"]
            if submission["score"].get("root_cause_verdict") is not None
        ]

    reviews = {
        review["reviewer_role"]: review_scores(review)
        for path in review_paths
        for review in [json.loads(path.read_text(encoding="utf-8"))]
    }
    expected_author: dict[str, tuple[str, int]] = {}
    expected_second: dict[str, tuple[str, int]] = {}
    stability_cases = set(manifest["splits"]["stability"])
    for run in results["runs"]:
        if run["replicate"] != 1 or run.get("status") == "failed":
            continue
        submission_id = sha256(
            f"{results['experiment_version']}:{run['case_id']}:{run['group']}".encode()
        ).hexdigest()[:12]
        citation_count = sum(
            len(claim["evidence"])
            for section in ("facts", "root_cause_hypotheses")
            for claim in run["report"].get(section, [])
        )
        expected_author[submission_id] = (run["case_id"], citation_count)
        if run["case_id"] in stability_cases:
            expected_second[submission_id] = (run["case_id"], citation_count)

    def validate_scores(role: str, expected: dict[str, tuple[str, int]]) -> None:
        scores = reviews.get(role, [])
        identifiers = [item.get("submission_id") for item in scores]
        if len(set(identifiers)) != len(identifiers) or set(identifiers) != set(expected):
            raise ValueError(f"{role} review coverage does not match frozen cases")
        for item in scores:
            case_id, citation_count = expected[item["submission_id"]]
            verdict = item.get("root_cause_verdict")
            citations = item.get("citation_validity")
            if (
                item.get("case_id") != case_id
                or isinstance(verdict, bool)
                or verdict not in (0, 1, 2)
                or not isinstance(citations, list)
                or len(citations) != citation_count
                or any(type(value) is not bool for value in citations)
            ):
                raise ValueError(f"{role} score is invalid: {item.get('submission_id')}")

    validate_scores("project_author", expected_author)
    validate_scores("second_reviewer", expected_second)
    author = {item["submission_id"]: item for item in reviews["project_author"]}
    second = {item["submission_id"]: item for item in reviews["second_reviewer"]}
    agreement_ids = sorted(author.keys() & second.keys())
    root_agreements = [
        author[item]["root_cause_verdict"] == second[item]["root_cause_verdict"]
        for item in agreement_ids
    ]
    citation_agreements = [
        author[item]["citation_validity"] == second[item]["citation_validity"]
        for item in agreement_ids
    ]
    disagreement_ids = [
        item
        for item in agreement_ids
        if author[item]["root_cause_verdict"] != second[item]["root_cause_verdict"]
        or author[item]["citation_validity"] != second[item]["citation_validity"]
    ]
    arbitration = {item["submission_id"]: item for item in reviews.get("arbitration", [])}
    unresolved = [item for item in disagreement_ids if item not in arbitration]
    if unresolved:
        raise ValueError(f"unresolved review disagreement: {unresolved[0]}")
    for item in disagreement_ids:
        score = arbitration[item]
        case_id, citation_count = expected_author[item]
        verdict = score.get("root_cause_verdict")
        citations = score.get("citation_validity")
        if (
            score.get("case_id") != case_id
            or isinstance(verdict, bool)
            or verdict not in (0, 1, 2)
            or not isinstance(citations, list)
            or len(citations) != citation_count
            or any(type(value) is not bool for value in citations)
        ):
            raise ValueError(f"arbitration score is invalid: {item}")
        if not str(score.get("notes", "")).strip():
            raise ValueError(f"arbitration reason is required: {item}")
    final_scores = {**author, **arbitration}

    per_case: list[dict[str, Any]] = []
    group_rows: dict[str, list[dict[str, Any]]] = {group: [] for group in ("g1", "g2", "g3", "g4")}
    metric_names = (
        "root_cause_files_recall_at_5",
        "root_cause_files_recall_at_10",
        "changed_files_recall_at_5",
        "changed_files_recall_at_10",
        "test_files_recall_at_5",
        "test_files_recall_at_10",
        "mrr",
        "ndcg_at_10",
        "root_cause_accuracy",
        "citation_validity",
    )
    experiment_version = results["experiment_version"]
    for run in results["runs"]:
        if run["replicate"] != 1:
            continue
        if run.get("status") == "failed":
            row = {
                "case_id": run["case_id"],
                "group": run["group"],
                "status": "failed",
                "failure": run["failure"],
                "llm_judge": run.get("llm_judge"),
                **{name: 0.0 for name in metric_names},
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost": "0",
                    "latency_ns": 0,
                },
            }
            per_case.append(row)
            group_rows[run["group"]].append(row)
            continue
        candidates = tuple(
            RetrievalCandidate(
                rank=item["rank"],
                score=item["score"],
                commit=item["commit"],
                path=item["path"],
                start_line=item["start_line"],
                end_line=item["end_line"],
                source_type=item["source_type"],
                index_version=item["index_version"],
                content=item["content"],
                reference=item["reference"],
            )
            for item in run["retrieval"]["candidates"]
        )
        metrics = evaluate_retrieval_metrics(
            RetrievalRun(
                "",
                candidates,
                RetrievalMetadata("", "", "", "", 0, len(candidates), "", len(candidates)),
            ),
            manifest_path.parent / "gold" / f"{run['case_id']}.json",
        )
        submission_id = sha256(
            f"{experiment_version}:{run['case_id']}:{run['group']}".encode()
        ).hexdigest()[:12]
        human = final_scores[submission_id]
        citations = human["citation_validity"]
        row = {
            "case_id": run["case_id"],
            "group": run["group"],
            "status": "completed",
            "llm_judge": run.get("llm_judge"),
            "root_cause_files_recall_at_5": metrics.recall_at_5.root_cause_files.recall or 0.0,
            "root_cause_files_recall_at_10": metrics.recall_at_10.root_cause_files.recall or 0.0,
            "changed_files_recall_at_5": metrics.recall_at_5.changed_files.recall or 0.0,
            "changed_files_recall_at_10": metrics.recall_at_10.changed_files.recall or 0.0,
            "test_files_recall_at_5": metrics.recall_at_5.test_files.recall or 0.0,
            "test_files_recall_at_10": metrics.recall_at_10.test_files.recall or 0.0,
            "mrr": metrics.mrr,
            "ndcg_at_10": metrics.ndcg_at_10,
            "root_cause_accuracy": float(human["root_cause_verdict"] == 2),
            "citation_validity": fmean(citations) if citations else 0.0,
            "usage": run["metadata"],
        }
        per_case.append(row)
        group_rows[run["group"]].append(row)

    groups = {}
    for group, rows in group_rows.items():
        groups[group] = {
            "failures": sum(row["status"] == "failed" for row in rows),
            "metrics": {name: fmean(row[name] for row in rows) for name in metric_names},
            "usage": {
                "input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
                "output_tokens": sum(row["usage"]["output_tokens"] for row in rows),
                "total_tokens": sum(row["usage"]["total_tokens"] for row in rows),
                "cost": str(sum((Decimal(row["usage"]["cost"]) for row in rows), Decimal())),
                "latency_ns": sum(
                    row["usage"].get("total_latency_ns", row["usage"]["latency_ns"]) for row in rows
                ),
            },
        }

    thresholds = manifest["preregistered_metrics"]
    g1 = groups["g1"]["metrics"]
    g4 = groups["g4"]["metrics"]
    root_gain = g4["root_cause_accuracy"] - g1["root_cause_accuracy"]
    recall_gain = g4["root_cause_files_recall_at_10"] - g1["root_cause_files_recall_at_10"]
    citation_gain = g4["citation_validity"] - g1["citation_validity"]
    threshold_metrics = (
        (
            "root_cause_recall_at_10",
            "root_cause_files_recall_at_10",
            "root_cause_recall_at_10_minimum",
        ),
        ("citation_validity", "citation_validity", "citation_validity_minimum"),
        ("root_cause_accuracy", "root_cause_accuracy", "root_cause_score_2_minimum"),
    )
    group_assessments = {
        group: {
            "values": {
                label: groups[group]["metrics"][metric]
                for label, metric, threshold in threshold_metrics
                if threshold in thresholds
            },
            "unmet": [
                label
                for label, metric, threshold in threshold_metrics
                if threshold in thresholds
                and groups[group]["metrics"][metric] < thresholds[threshold]
            ],
        }
        for group in ("g1", "g2", "g3", "g4")
    }
    stability_groups: dict[str, dict[str, Any]] = {}
    stability_cases = set(manifest["splits"]["stability"])
    for group in ("g1", "g2", "g3", "g4"):
        group_runs = [
            run
            for run in results["runs"]
            if run["group"] == group and run["case_id"] in stability_cases
        ]
        if not group_runs:
            continue
        outcome_consistency = []
        conclusion_consistency = []
        for case_id in manifest["splits"]["stability"]:
            case_runs = [run for run in group_runs if run["case_id"] == case_id]
            if len(case_runs) != 3:
                raise ValueError(f"stability case requires three runs: {case_id}/{group}")
            outcomes = [
                run.get("outcome", {}).get("status", run.get("status", "failed"))
                for run in case_runs
            ]
            conclusions = [
                json.dumps(
                    run.get("report", {}).get("root_cause_hypotheses"),
                    sort_keys=True,
                )
                for run in case_runs
            ]
            outcome_consistency.append(max(outcomes.count(item) for item in outcomes) / 3)
            conclusion_consistency.append(max(conclusions.count(item) for item in conclusions) / 3)
        costs = [float(run.get("metadata", {}).get("cost", 0)) for run in group_runs]
        latencies = [
            int(
                run.get("metadata", {}).get(
                    "total_latency_ns",
                    run.get("metadata", {}).get(
                        "run_latency_ns", run.get("metadata", {}).get("latency_ns", 0)
                    ),
                )
            )
            for run in group_runs
        ]
        stability_groups[group] = {
            "runs": len(group_runs),
            "outcome_consistency": fmean(outcome_consistency),
            "root_cause_conclusion_consistency": fmean(conclusion_consistency),
            "cost": {"min": min(costs), "max": max(costs), "mean": fmean(costs)},
            "latency_ns": {
                "min": min(latencies),
                "max": max(latencies),
                "mean": fmean(latencies),
            },
        }
    return {
        "schema_version": "1.0",
        "experiment_version": experiment_version,
        "dataset_version": results["dataset_version"],
        "evaluator_version": results["evaluator_version"],
        "per_case": per_case,
        "groups": groups,
        "review_agreement": {
            "root_cause_exact": fmean(root_agreements) if root_agreements else None,
            "citation_exact": fmean(citation_agreements) if citation_agreements else None,
            "disagreements": [
                {
                    "submission_id": item,
                    "project_author": {
                        "root_cause_verdict": author[item]["root_cause_verdict"],
                        "citation_validity": author[item]["citation_validity"],
                    },
                    "second_reviewer": {
                        "root_cause_verdict": second[item]["root_cause_verdict"],
                        "citation_validity": second[item]["citation_validity"],
                    },
                    "arbitration": {
                        "root_cause_verdict": arbitration[item]["root_cause_verdict"],
                        "citation_validity": arbitration[item]["citation_validity"],
                        "notes": arbitration[item]["notes"],
                    },
                }
                for item in disagreement_ids
            ],
        },
        "stability": {"groups": stability_groups},
        "preregistered_assessment": {
            "groups": group_assessments,
            "root_cause_accuracy_gain": root_gain,
            "root_cause_recall_at_10_gain": recall_gain,
            "citation_validity_gain": citation_gain,
            "g4_vs_g1_clear_gain": (
                root_gain >= thresholds["g4_vs_g1_root_cause_accuracy_gain_minimum"]
                and max(recall_gain, citation_gain)
                >= thresholds["g4_vs_g1_recall_or_citation_gain_minimum"]
            ),
        },
    }
