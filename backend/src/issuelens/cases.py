import json
from pathlib import Path


def build_case(github_cache: Path, repository_snapshot: Path, output_directory: Path) -> Path:
    github = json.loads(github_cache.read_text(encoding="utf-8"))
    snapshot = json.loads((repository_snapshot / "snapshot.json").read_text(encoding="utf-8"))
    issue = github["issue"]
    pull_request = github["closing_pull_request"]
    case_id = f"starlette-issue-{issue['number']}"
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "issue": {
            "repository": github["repository"],
            "number": issue["number"],
            "title": issue["title"],
            "source_url": issue["url"],
        },
        "investigation_cutoff": pull_request["created_at"],
        "repository_snapshot": {
            "commit": snapshot["commit"],
            "source_url": snapshot["source_url"],
        },
        "allowed_evidence": {
            "read_access": "investigation",
            "references": [issue["url"]],
        },
        "repair_evidence": {
            "read_access": "evaluation_only",
            "references": [pull_request["url"]],
        },
    }
    case_path = output_directory / "cases" / f"{case_id}.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = {
        "schema_version": "1.0",
        "accepted_case_ids": [case_id],
        "excluded": [],
    }
    (output_directory / "screening-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return case_path
