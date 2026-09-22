import argparse
import json
from pathlib import Path

from issuelens.retrieval import run_regression_suite

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data/retrieval/regression/manifest.json",
    )
    args = parser.parse_args()
    result = run_regression_suite(args.manifest)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
