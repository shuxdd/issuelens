import json
import re
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


def _metadata(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys if source.get(key) is not None}


def _timeline_metadata(event: dict[str, Any]) -> dict[str, Any]:
    result = _metadata(event, "event", "created_at", "commit_id", "commit_url")
    source_issue = event.get("source", {}).get("issue")
    if source_issue:
        result["source"] = {
            "issue": {
                **_metadata(source_issue, "number", "title", "html_url"),
                "repository": _metadata(source_issue.get("repository", {}), "full_name"),
                "pull_request": _metadata(
                    source_issue.get("pull_request", {}), "url", "merged_at"
                ),
            }
        }
    return result


def collect_github_data(
    repository: str,
    issue_number: int,
    cache_directory: Path,
    *,
    opener: Callable[[urllib.request.Request], Any] | None = None,
) -> Path:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None or issue_number < 1:
        raise ValueError("Invalid GitHub issue")

    cache_path = cache_directory / f"{repository.replace('/', '-')}-{issue_number}.json"
    if cache_path.is_file():
        return cache_path

    open_request = opener or urllib.request.urlopen
    api = f"https://api.github.com/repos/{repository}"

    def get_json(url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "IssueLens"},
        )
        with open_request(request) as response:
            return json.loads(response.read())

    issue = get_json(f"{api}/issues/{issue_number}")
    timeline = get_json(f"{api}/issues/{issue_number}/timeline?per_page=100")
    cross_references = [event for event in timeline if event.get("event") == "cross-referenced"]
    closing_references = [
        event
        for event in cross_references
        if event.get("source", {}).get("issue", {}).get("repository", {}).get("full_name")
        == repository
        and event["source"]["issue"].get("pull_request", {}).get("merged_at")
        and (
            not issue.get("closed_at")
            or event["source"]["issue"]["pull_request"]["merged_at"] <= issue["closed_at"]
        )
    ]
    closing_reference = max(
        closing_references,
        key=lambda event: event["source"]["issue"]["pull_request"]["merged_at"],
    )
    pull_request_url = closing_reference["source"]["issue"]["pull_request"]["url"]
    pull_request = get_json(pull_request_url)
    pull_number = pull_request["number"]
    commits = get_json(f"{api}/pulls/{pull_number}/commits?per_page=100")
    files = get_json(f"{api}/pulls/{pull_number}/files?per_page=100")
    timeline_metadata = [_timeline_metadata(event) for event in timeline]
    cached = {
        "repository": repository,
        "issue": {
            **_metadata(
                issue,
                "number",
                "title",
                "html_url",
                "created_at",
                "closed_at",
                "state",
                "body_last_edited_at",
            ),
            **(
                {"user": issue["user"]["login"]}
                if issue.get("user", {}).get("login")
                else {}
            ),
            **(
                {"labels": [label["name"] for label in issue["labels"]]}
                if issue.get("labels")
                else {}
            ),
        },
        "timeline": timeline_metadata,
        "cross_references": [
            event for event in timeline_metadata if event.get("event") == "cross-referenced"
        ],
        "pull_request": {
            **_metadata(
                pull_request,
                "number",
                "title",
                "html_url",
                "created_at",
                "closed_at",
                "merged_at",
                "merge_commit_sha",
            ),
            **(
                {"user": pull_request["user"]["login"]}
                if pull_request.get("user", {}).get("login")
                else {}
            ),
        },
        "commits": [
            {
                **_metadata(commit, "sha", "html_url"),
                **(
                    {"committed_at": commit["commit"]["committer"]["date"]}
                    if commit.get("commit", {}).get("committer", {}).get("date")
                    else {}
                ),
            }
            for commit in commits
        ],
        "files": [
            _metadata(
                file,
                "filename",
                "status",
                "sha",
                "blob_url",
                "additions",
                "deletions",
                "changes",
            )
            for file in files
        ],
    }
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cached, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cache_path


def validate_case(case: dict[str, Any]) -> list[str]:
    cutoff = datetime.fromisoformat(case["investigation_cutoff"].replace("Z", "+00:00"))
    allowed_evidence = case["allowed_evidence"]
    repair_evidence = case["repair_evidence"]
    errors = []
    if not case["investigation_cutoff"].endswith("Z"):
        errors.append("investigation_cutoff must use UTC ISO 8601")
    snapshot_time = case.get("repository_snapshot", {}).get("committed_at")
    if snapshot_time and not snapshot_time.endswith("Z"):
        errors.append("repository_snapshot.committed_at must use UTC ISO 8601")
    errors.extend(
        f"allowed_evidence[{index}].observed_at must use UTC ISO 8601"
        for index, evidence in enumerate(allowed_evidence["items"])
        if not evidence["observed_at"].endswith("Z")
    )
    pull_request_time = repair_evidence.get("pull_request", {}).get("created_at")
    if pull_request_time and not pull_request_time.endswith("Z"):
        errors.append("repair_evidence.pull_request.created_at must use UTC ISO 8601")
    if case.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not case.get("issue", {}).get("source_url"):
        errors.append("issue.source_url is required")
    if not case.get("repository_snapshot", {}).get("commit"):
        errors.append("repository_snapshot.commit is required")
    if not case.get("repository_snapshot", {}).get("source_url"):
        errors.append("repository_snapshot.source_url is required")
    errors.extend(
        f"allowed_evidence[{index}].source_url is required"
        for index, evidence in enumerate(allowed_evidence["items"])
        if not evidence.get("source_url")
    )
    if not repair_evidence.get("pull_request", {}).get("source_url"):
        errors.append("repair_evidence.pull_request.source_url is required")
    for collection in ("commits", "changed_files", "test_files"):
        errors.extend(
            f"repair_evidence.{collection}[{index}].source_url is required"
            for index, evidence in enumerate(repair_evidence.get(collection, []))
            if not evidence.get("source_url")
        )
    if not repair_evidence.get("test_files"):
        errors.append("repair_evidence.test_files must not be empty")
    if not case.get("manual_review", {}).get("relationship_basis"):
        errors.append("manual_review.relationship_basis is required")
    manual_review = case.get("manual_review", {})
    errors.extend(
        f"manual_review.{field} is required"
        for field in (
            "investigation_cutoff_basis",
            "snapshot_basis",
            "exclusion_assessment",
            "repair_files",
            "test_files",
            "future_information_risk",
            "verdict",
            "notes",
        )
        if not manual_review.get(field)
    )
    if manual_review.get("repair_files") and set(manual_review["repair_files"]) != {
        file["path"] for file in repair_evidence.get("changed_files", [])
    }:
        errors.append(
            "manual_review.repair_files must match repair_evidence.changed_files"
        )
    if (
        manual_review.get("test_files")
        and repair_evidence.get("test_files")
        and set(manual_review["test_files"])
        != {
            file["path"] for file in repair_evidence.get("test_files", [])
        }
    ):
        errors.append("manual_review.test_files must match repair_evidence.test_files")
    if allowed_evidence["read_access"] != "investigation":
        errors.append("allowed_evidence.read_access must be investigation")
    if repair_evidence["read_access"] != "evaluation_only":
        errors.append("repair_evidence.read_access must be evaluation_only")
    errors.extend(
        f"allowed_evidence[{index}] is later than investigation_cutoff"
        for index, evidence in enumerate(allowed_evidence["items"])
        if datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00")) >= cutoff
    )
    return errors


def check_cases(data_directory: Path) -> list[str]:
    errors: list[str] = []
    for case_path in sorted((data_directory / "cases").glob("*.json")):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        errors.extend(f"{case_path.stem}: {error}" for error in validate_case(case))
    return errors


def build_case(
    github_cache: Path,
    repository_snapshot: Path,
    output_directory: Path,
    review: dict[str, Any] | None = None,
) -> Path:
    github = json.loads(github_cache.read_text(encoding="utf-8"))
    if review:
        github["issue"]["body_last_edited_at"] = review.get("body_last_edited_at")
        github["manual_review"] = review["manual_review"]
    snapshot = json.loads((repository_snapshot / "snapshot.json").read_text(encoding="utf-8"))
    issue = github["issue"]
    complete_cache = "pull_request" in github
    pull_request = github["pull_request"] if complete_cache else github["closing_pull_request"]
    cutoff = pull_request["created_at"]
    selected_snapshot = snapshot
    if complete_cache:
        selected_snapshot = max(
            (
                commit
                for commit in snapshot["default_branch_commits"]
                if commit["committed_at"] < cutoff
            ),
            key=lambda commit: commit["committed_at"],
        )
    case_id = f"starlette-issue-{issue['number']}"
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "issue": {
            "repository": github["repository"],
            "number": issue["number"],
            "title": issue["title"],
            "source_url": issue.get("html_url", issue.get("url")),
        },
        "investigation_cutoff": cutoff,
        "repository_snapshot": {
            "commit": selected_snapshot["commit"],
            "source_url": selected_snapshot["source_url"],
        },
        "allowed_evidence": {
            "read_access": "investigation",
            "references": [issue.get("html_url", issue.get("url"))],
        },
        "repair_evidence": {
            "read_access": "evaluation_only",
            "references": [pull_request.get("html_url", pull_request.get("url"))],
        },
    }
    if complete_cache:
        files = [
            {
                "path": file["filename"],
                "source_url": file["blob_url"],
                "status": file["status"],
            }
            for file in github["files"]
        ]
        test_files = [file for file in files if file["path"].startswith("tests/")]
        case["repository_snapshot"]["committed_at"] = selected_snapshot["committed_at"]
        case["allowed_evidence"]["items"] = [
            {
                "kind": "issue",
                "source_url": issue["html_url"],
                "observed_at": issue.get("body_last_edited_at") or issue["created_at"],
            }
        ]
        case["repair_evidence"].update(
            {
                "pull_request": {
                    "number": pull_request["number"],
                    "source_url": pull_request["html_url"],
                    "created_at": cutoff,
                },
                "commits": [
                    {"sha": commit["sha"], "source_url": commit["html_url"]}
                    for commit in github["commits"]
                ],
                "changed_files": [file for file in files if file not in test_files],
                "test_files": test_files,
            }
        )
        case["manual_review"] = github["manual_review"]
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


def build_cases(
    github_cache_directory: Path,
    repository_snapshot: Path,
    output_directory: Path,
    *,
    reviews_path: Path | None = None,
) -> list[Path]:
    cases_directory = output_directory / "cases"
    if cases_directory.is_dir():
        for stale_case in cases_directory.glob("starlette-issue-*.json"):
            stale_case.unlink()
    candidates = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(github_cache_directory.glob("*.json"))
    ]
    review_data = (
        json.loads(reviews_path.read_text(encoding="utf-8")) if reviews_path else {}
    )
    reviews = review_data.get("reviews", {})
    standalone_exclusions = review_data.get("standalone_exclusions", [])
    for _path, candidate in candidates:
        review = reviews.get(str(candidate["issue"]["number"]))
        if review:
            candidate["issue"]["body_last_edited_at"] = review.get("body_last_edited_at")
            candidate["manual_review"] = review["manual_review"]
            if review.get("screening"):
                candidate["screening"] = review["screening"]
    related = [candidate for candidate in candidates if candidate[1].get("cross_references")]
    category_eligible = [
        candidate
        for candidate in related
        if candidate[1].get("screening", {}).get("exclusion_stage") != "category_eligible"
    ]
    analyzable = [
        candidate
        for candidate in category_eligible
        if candidate[1].get("screening", {}).get("exclusion_stage") != "analyzable"
    ]
    time_slice_excluded = [
        candidate
        for candidate in analyzable
        if candidate[1]["issue"].get("body_last_edited_at")
        and candidate[1]["issue"]["body_last_edited_at"]
        >= candidate[1]["pull_request"]["created_at"]
    ]
    time_slice_valid = [
        candidate for candidate in analyzable if candidate not in time_slice_excluded
    ]
    reviewed = [candidate for candidate in time_slice_valid if candidate[1].get("manual_review")]
    accepted = [
        candidate
        for candidate in reviewed
        if candidate[1]["manual_review"].get("verdict") == "accepted"
    ]
    case_paths = [
        build_case(
            path,
            repository_snapshot,
            output_directory,
            reviews.get(str(candidate["issue"]["number"])),
        )
        for path, candidate in accepted
    ]
    report = {
        "schema_version": "1.0",
        "funnel": {
            "collected": len(candidates) + len(standalone_exclusions),
            "relationship_recovered": len(related),
            "category_eligible": len(category_eligible),
            "analyzable": len(analyzable),
            "time_slice_valid": len(time_slice_valid),
            "manual_reviewed": len(reviewed),
            "accepted": len(accepted),
        },
        "accepted_case_ids": [path.stem for path in case_paths],
        "excluded": [
            {
                "issue_number": candidate["issue"]["number"],
                **candidate["screening"],
            }
            for _path, candidate in candidates
            if candidate.get("screening")
        ]
        + [
            {
                "issue_number": candidate["issue"]["number"],
                "exclusion_stage": "time_slice_valid",
                "reason_code": "issue_body_edited_after_cutoff",
                "reason": "Issue body was edited after the investigation cutoff",
            }
            for _path, candidate in time_slice_excluded
        ]
        + standalone_exclusions,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "screening-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return case_paths
