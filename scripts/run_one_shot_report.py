"""Run the frozen three-way investigation through one shared report generator."""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from issuelens.deepseek import DeepSeekChatModel
from issuelens.report import (
    ReportConfig,
    ReportModel,
    generate_three_way_reports,
    load_versioned_three_way,
    write_three_way_reports,
)
from issuelens.retrieval import load_retrieval_input

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "starlette-issue-1552"


def run(root: Path, model: ReportModel, config: ReportConfig, output: Path) -> None:
    case = load_retrieval_input(root / "data/cases" / f"{CASE_ID}.json")
    frozen = root / "data/retrieval" / CASE_ID / "three-way.json"
    comparison = load_versioned_three_way(root / "data/cases" / f"{CASE_ID}.json", frozen)
    snapshot_root = (
        root
        / ".retrieval-cache"
        / f"starlette-{case.snapshot_commit}"
        / f"starlette-{case.snapshot_commit}"
    )
    groups = generate_three_way_reports(case, comparison, snapshot_root, model, config)
    write_three_way_reports(output, case.case_id, groups)


def main() -> None:
    config_path = ROOT / "data/reports" / CASE_ID / "config.json"
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    peak = now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)
    tier = "peak" if peak else "off_peak"
    rates = settings["pricing"][tier]
    config = ReportConfig(
        settings["model"],
        settings["prompt_version"],
        rates["input_cache_miss"],
        rates["output"],
        "USD",
        rates["input_cache_hit"],
    )
    frozen = ROOT / "data/retrieval" / CASE_ID / "three-way.json"
    peak_rates = settings["pricing"]["peak"]
    worst_usd = (
        Decimal(frozen.stat().st_size + 6000) * Decimal(peak_rates["input_cache_miss"])
        + Decimal(3 * 2048) * Decimal(peak_rates["output"])
    ) / 1_000_000
    if worst_usd >= Decimal("0.50"):
        raise ValueError("three-call budget preflight exceeds conservative ¥5 limit")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.partition("=")[2].strip()
                break
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is required in ignored .env")
    output = ROOT / "data/reports" / CASE_ID / "report.json"
    run(ROOT, DeepSeekChatModel(key), config, output)
    report = json.loads(output.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "case_id": CASE_ID,
                "pricing_tier": tier,
                "max_preflight_usd": str(worst_usd),
                "groups": {name: result["metadata"] for name, result in report["groups"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
