import json
from pathlib import Path

from scripts.build_demo_replays import build

ROOT = Path(__file__).resolve().parents[2]


def test_offline_safety_replays_match_versioned_investigations(tmp_path: Path) -> None:
    build(tmp_path)

    for case_id in ("starlette-issue-1552-budget", "safe-dangerous-01"):
        actual = json.loads((tmp_path / f"{case_id}.json").read_text(encoding="utf-8"))
        expected = json.loads(
            (ROOT / "data/demo/runs" / f"{case_id}.json").read_text(encoding="utf-8")
        )
        assert actual == expected
