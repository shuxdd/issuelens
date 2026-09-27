import argparse
import json
from pathlib import Path

from issuelens.cases import (
    collect_evaluation_candidates,
    discover_github_issues,
    enrich_github_issue_content,
    open_with_gh,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--high-water", type=int, default=3572)
    parser.add_argument("--cleaned-limit", type=int, default=109)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data" / "candidate-manifest.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "data" / "github-cache",
    )
    args = parser.parse_args()

    discover_github_issues(
        "Kludex/starlette",
        args.manifest,
        high_water_issue_number=args.high_water,
        opener=open_with_gh,
    )
    paths = collect_evaluation_candidates(
        args.manifest,
        args.cache,
        cleaned_limit=args.cleaned_limit,
        opener=open_with_gh,
    )
    enrich_github_issue_content(paths)
    print(
        json.dumps(
            {
                "candidate_manifest": str(args.manifest),
                "cleaned_count": len(paths),
                "first_case": paths[0].stem,
                "last_case": paths[-1].stem,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
