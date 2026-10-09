import argparse
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from issuelens.deepseek import DeepSeekChatModel
from issuelens.evaluation import (
    prepare_blind_review,
    run_comparable_experiment,
    score_experiment,
)
from issuelens.retrieval import FastEmbedAdapter, load_local_reranker

ROOT = Path(__file__).resolve().parents[1]


def _materialize_snapshots(
    data_root: Path, manifest_path: Path, git_directory: Path, cache: Path
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    for case_id in manifest["splits"]["primary"]:
        case = json.loads((data_root / "cases" / f"{case_id}.json").read_text(encoding="utf-8"))
        commit = case["repository_snapshot"]["commit"]
        target = cache / f"starlette-{commit}" / f"starlette-{commit}"
        snapshot_manifest = target / ".issuelens-snapshot.json"
        if snapshot_manifest.is_file():
            existing = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
            if existing == {"repository": "Kludex/starlette", "commit": commit}:
                continue
            raise ValueError(f"snapshot cache mismatch: {target}")
        if target.exists():
            raise ValueError(f"incomplete snapshot cache exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary_name:
            temporary = Path(temporary_name)
            archive = temporary / "snapshot.tar"
            extracted = temporary / "snapshot"
            extracted.mkdir()
            with archive.open("wb") as output:
                subprocess.run(
                    [
                        "git",
                        "--no-lazy-fetch",
                        f"--git-dir={git_directory}",
                        "archive",
                        "--format=tar",
                        commit,
                    ],
                    check=True,
                    stdout=output,
                )
            with tarfile.open(archive) as source:
                source.extractall(extracted, filter="data")
            (extracted / ".issuelens-snapshot.json").write_text(
                json.dumps(
                    {"repository": "Kludex/starlette", "commit": commit},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(extracted, target)


def _load_config(path: Path) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if any(
        str(value).startswith("SELECT_")
        for value in (
            config.get("application_commit"),
            config["report"].get("model"),
            config["report"].get("model_version"),
            config["report"].get("input_price_per_million"),
            config["report"].get("output_price_per_million"),
        )
    ):
        raise ValueError("formal model, pricing, and application commit require author selection")
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--snapshot-cache", type=Path, default=ROOT / ".evaluation-cache/snapshots")
    run.add_argument(
        "--git-directory", type=Path, default=ROOT / ".uv-cache/issuelens-starlette.git"
    )
    blind = commands.add_parser("blind")
    blind.add_argument("--results", type=Path, required=True)
    blind.add_argument("--manifest", type=Path, required=True)
    blind.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("score")
    score.add_argument("--results", type=Path, required=True)
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--review", type=Path, action="append", required=True)
    score.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    if arguments.command == "blind":
        prepare_blind_review(arguments.results, arguments.manifest, arguments.output)
        return 0
    if arguments.command == "score":
        if arguments.output.exists():
            raise FileExistsError(f"score output already exists: {arguments.output}")
        result = score_experiment(arguments.results, arguments.review, arguments.manifest)
        arguments.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0

    config = _load_config(arguments.config)
    manifest_path = Path(config["dataset_manifest"])
    _materialize_snapshots(
        ROOT / "data", manifest_path, arguments.git_directory, arguments.snapshot_cache
    )
    embedding = config["retrieval"]["embedding"]
    embedder = FastEmbedAdapter(
        model_name=embedding["model"],
        model_revision=embedding["revision"],
        dimension=embedding["dimension"],
        cache_dir=ROOT / ".retrieval-cache/models",
    )
    reranker, _ = load_local_reranker(
        config["retrieval"]["reranker_model"], ROOT / ".retrieval-cache/models"
    )
    if reranker is None:
        raise ValueError("formal G3 requires an enabled reranker")
    run_comparable_experiment(
        ROOT / "data",
        arguments.snapshot_cache,
        arguments.config,
        arguments.output,
        DeepSeekChatModel(os.environ.get("DEEPSEEK_API_KEY", "")),
        embedder,
        reranker,
        resume=arguments.resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
