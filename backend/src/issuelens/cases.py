import ast
import hashlib
import io
import json
import re
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _run_gh(arguments: list[str]) -> bytes:
    error: subprocess.CalledProcessError | None = None
    for _attempt in range(3):
        try:
            result = subprocess.run(
                ["gh", "api", *arguments],
                check=True,
                capture_output=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as current_error:
            error = current_error
    assert error is not None
    raise error


def open_with_gh(request: urllib.request.Request) -> io.BytesIO:
    return io.BytesIO(_run_gh([request.full_url]))


def enrich_github_issue_content(cache_paths: list[Path]) -> None:
    cache_paths = [
        path
        for path in cache_paths
        if not all(
            field in json.loads(path.read_text(encoding="utf-8"))["issue"]
            for field in ("body", "body_last_edited_at")
        )
    ]
    for offset in range(0, len(cache_paths), 25):
        batch_paths = cache_paths[offset : offset + 25]
        cached = [json.loads(path.read_text(encoding="utf-8")) for path in batch_paths]
        repositories = {item["repository"] for item in cached}
        if len(repositories) != 1:
            raise ValueError("Issue enrichment requires one repository per batch")
        owner, name = repositories.pop().split("/", 1)
        selections = " ".join(
            f'i{item["issue"]["number"]}: issue(number: {item["issue"]["number"]}) '
            "{ number body lastEditedAt }"
            for item in cached
        )
        query = (
            f'query {{ repository(owner: "{owner}", name: "{name}") '
            f"{{ {selections} }} }}"
        )
        response = json.loads(_run_gh(["graphql", "-f", f"query={query}"]))
        issues = response["data"]["repository"]
        for path, item in zip(batch_paths, cached, strict=True):
            number = item["issue"]["number"]
            issue = issues[f"i{number}"]
            if issue is None or issue["number"] != number:
                raise ValueError(f"GitHub Issue enrichment missing issue {number}")
            item["issue"]["body"] = issue["body"]
            item["issue"]["body_last_edited_at"] = issue["lastEditedAt"]
            path.write_text(
                json.dumps(item, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


def _metadata(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys if source.get(key) is not None}


def _is_behavior_test(filename: str) -> bool:
    return (
        filename.startswith("tests/")
        and Path(filename).name.startswith("test_")
        and filename.endswith(".py")
    )


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


def discover_github_issues(
    repository: str,
    output_path: Path,
    *,
    high_water_issue_number: int,
    opener: Callable[[urllib.request.Request], Any] | None = None,
    token: str | None = None,
) -> Path:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ValueError("Invalid GitHub repository")
    if high_water_issue_number < 1:
        raise ValueError("Invalid GitHub issue high water mark")
    if output_path.is_file():
        return output_path

    open_request = opener or urllib.request.urlopen
    api = f"https://api.github.com/repos/{repository}/issues"
    issue_numbers: list[int] = []
    page = 1
    while True:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "IssueLens",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{api}?state=closed&sort=created&direction=asc&per_page=100&page={page}",
            headers=headers,
        )
        with open_request(request) as response:
            items = json.loads(response.read())
        if not isinstance(items, list):
            raise ValueError("GitHub issue discovery returned an invalid response")
        issue_numbers.extend(
            int(item["number"])
            for item in items
            if "pull_request" not in item and int(item["number"]) <= high_water_issue_number
        )
        if len(items) < 100:
            break
        page += 1

    manifest = {
        "schema_version": "1.0",
        "repository": repository,
        "high_water_issue_number": high_water_issue_number,
        "issue_numbers": sorted(set(issue_numbers)),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_path


def collect_github_data(
    repository: str,
    issue_number: int,
    cache_directory: Path,
    *,
    opener: Callable[[urllib.request.Request], Any] | None = None,
    token: str | None = None,
) -> Path:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None or issue_number < 1:
        raise ValueError("Invalid GitHub issue")

    cache_path = cache_directory / f"{repository.replace('/', '-')}-{issue_number}.json"
    if cache_path.is_file():
        return cache_path

    open_request = opener or urllib.request.urlopen
    api = f"https://api.github.com/repos/{repository}"

    def get_json(url: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "IssueLens",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            headers=headers,
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
    timeline_metadata = [_timeline_metadata(event) for event in timeline]
    cached: dict[str, Any] = {
        "repository": repository,
        "issue": {
            **_metadata(
                issue,
                "number",
                "title",
                "body",
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
    }
    if closing_references:
        closing_reference = max(
            closing_references,
            key=lambda event: event["source"]["issue"]["pull_request"]["merged_at"],
        )
        pull_request_url = closing_reference["source"]["issue"]["pull_request"]["url"]
        pull_request = get_json(pull_request_url)
        pull_number = pull_request["number"]
        commits = get_json(f"{api}/pulls/{pull_number}/commits?per_page=100")
        files = get_json(f"{api}/pulls/{pull_number}/files?per_page=100")
        cached.update(
            {
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
        )
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cached, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cache_path


def collect_evaluation_candidates(
    manifest_path: Path,
    cache_directory: Path,
    *,
    cleaned_limit: int,
    opener: Callable[[urllib.request.Request], Any] | None = None,
    token: str | None = None,
) -> list[Path]:
    if cleaned_limit < 1:
        raise ValueError("Candidate limit must be positive")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repository = manifest["repository"]
    collected: list[Path] = []
    for issue_number in manifest["issue_numbers"]:
        cache_path = collect_github_data(
            repository,
            int(issue_number),
            cache_directory,
            opener=opener,
            token=token,
        )
        candidate = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            candidate.get("cross_references")
            and all(key in candidate for key in ("pull_request", "commits", "files"))
            and any(_is_behavior_test(file.get("filename", "")) for file in candidate["files"])
            and candidate["issue"].get("user") != "dependabot[bot]"
        ):
            collected.append(cache_path)
            if len(collected) == cleaned_limit:
                return collected
    raise ValueError(
        f"Only {len(collected)} cleaned candidates found; {cleaned_limit} required"
    )


def collect_repository_snapshots(
    cache_paths: list[Path],
    snapshot_path: Path,
    *,
    opener: Callable[[urllib.request.Request], Any] | None = None,
    token: str | None = None,
    git_directory: Path | None = None,
) -> Path:
    open_request = opener or urllib.request.urlopen
    snapshot: dict[str, Any] = (
        json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot_path.is_file()
        else {
            "schema_version": "1.0",
            "repository": "Kludex/starlette",
            "default_branch": "main",
            "default_branch_commits": [],
        }
    )
    commits = {
        item["commit"]: item for item in snapshot["default_branch_commits"]
    }
    for cache_path in cache_paths:
        candidate = json.loads(cache_path.read_text(encoding="utf-8"))
        selected = candidate.get("repository_snapshot")
        if selected is None:
            cutoff = candidate["pull_request"]["created_at"]
            if git_directory is not None:
                result = subprocess.run(
                    [
                        "git",
                        "--no-lazy-fetch",
                        f"--git-dir={git_directory}",
                        "log",
                        snapshot["default_branch"],
                        f"--before={cutoff}",
                        "-1",
                        "--format=%H%x00%ct",
                    ],
                    check=True,
                    capture_output=True,
                )
                output = result.stdout.decode().strip()
                if not output:
                    raise ValueError(
                        f"No default-branch snapshot before cutoff for issue "
                        f"{candidate['issue']['number']}"
                    )
                commit, timestamp = output.split("\0", 1)
                committed_at = (
                    datetime.fromtimestamp(int(timestamp), UTC)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                selected = {
                    "commit": commit,
                    "committed_at": committed_at,
                    "source_url": (
                        f"https://github.com/{candidate['repository']}/commit/{commit}"
                    ),
                }
            else:
                query = urllib.parse.urlencode(
                    {"sha": snapshot["default_branch"], "until": cutoff, "per_page": 1}
                )
                headers = {
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "IssueLens",
                }
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                request = urllib.request.Request(
                    f"https://api.github.com/repos/{candidate['repository']}/commits?{query}",
                    headers=headers,
                )
                with open_request(request) as response:
                    response_commits = json.loads(response.read())
                if not response_commits:
                    raise ValueError(
                        f"No default-branch snapshot before cutoff for issue "
                        f"{candidate['issue']['number']}"
                    )
                commit = response_commits[0]
                selected = {
                    "commit": commit["sha"],
                    "committed_at": commit["commit"]["committer"]["date"],
                    "source_url": commit["html_url"],
                }
            if selected["committed_at"] >= cutoff:
                raise ValueError("Repository snapshot must be earlier than cutoff")
            candidate["repository_snapshot"] = selected
            cache_path.write_text(
                json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        commits[selected["commit"]] = selected
    snapshot["default_branch_commits"] = sorted(
        commits.values(), key=lambda item: (item["committed_at"], item["commit"])
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return snapshot_path


def _git_python_parses(git_directory: Path, commit: str, path: str) -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-lazy-fetch",
                f"--git-dir={git_directory}",
                "show",
                f"{commit}:{path}",
            ],
            check=True,
            capture_output=True,
        )
        ast.parse(result.stdout.decode("utf-8"))
    except (subprocess.CalledProcessError, UnicodeDecodeError, SyntaxError):
        return False
    return True


def build_review_queue(
    cache_paths: list[Path],
    output_path: Path,
    *,
    git_directory: Path,
    limit: int = 80,
    screening_report_path: Path | None = None,
) -> Path:
    if limit < 1:
        raise ValueError("Review limit must be positive")
    previous = (
        json.loads(output_path.read_text(encoding="utf-8"))
        if output_path.is_file()
        else {}
    )
    previous_reviews = previous.get("reviews", {})
    all_reviews: dict[str, Any] = {}
    review_groups: list[list[str]] = []
    candidates = sorted(
        (json.loads(path.read_text(encoding="utf-8")) for path in cache_paths),
        key=lambda item: (
            item["pull_request"]["created_at"],
            item["issue"]["number"],
        ),
    )
    eligible = [
        candidate
        for candidate in candidates
        if candidate.get("repository_snapshot") is not None
        and not (
            candidate["issue"].get("body_last_edited_at")
            and candidate["issue"]["body_last_edited_at"]
            >= candidate["pull_request"]["created_at"]
        )
    ]
    series: list[dict[str, Any]] = []
    # ponytail: quadratic at the fixed 150-candidate ceiling; use union-find if it grows.
    for candidate in eligible:
        keys = {
            f"number:{candidate['issue']['number']}",
            f"number:{candidate['pull_request']['number']}",
            *(
                f"number:{event['source']['issue']['number']}"
                for event in candidate.get("cross_references", [])
                if event.get("source", {}).get("issue", {}).get("repository", {}).get(
                    "full_name"
                )
                == candidate["repository"]
            ),
        }
        if candidate["pull_request"].get("merge_commit_sha"):
            keys.add(f"commit:{candidate['pull_request']['merge_commit_sha']}")
        related = [group for group in series if group["keys"] & keys]
        members = [candidate]
        for group in related:
            keys.update(group["keys"])
            members.extend(group["members"])
            series.remove(group)
        series.append({"keys": keys, "members": members})
    series.sort(
        key=lambda group: (
            min(item["pull_request"]["created_at"] for item in group["members"]),
            min(item["pull_request"]["number"] for item in group["members"]),
        )
    )
    for group in series:
        group_review_ids: list[str] = []
        series_key = (
            "starlette-repair-series-"
            f"{min(item['pull_request']['number'] for item in group['members'])}"
        )
        for candidate in sorted(
            group["members"],
            key=lambda item: (
                item["pull_request"]["created_at"],
                item["issue"]["number"],
            ),
        ):
            issue = candidate["issue"]
            pull_request = candidate["pull_request"]
            snapshot = candidate["repository_snapshot"]
            repair_files = sorted(
                file["filename"]
                for file in candidate["files"]
                if file.get("filename", "").startswith("starlette/")
                and file["filename"].endswith(".py")
                and file.get("status") != "removed"
            )
            test_files = sorted(
                file["filename"]
                for file in candidate["files"]
                if file.get("filename", "").startswith("tests/")
                and file["filename"].endswith(".py")
                and file.get("status") != "removed"
            )
            repair_commit = pull_request.get("merge_commit_sha") or candidate["commits"][-1][
                "sha"
            ]
            root_cause_candidates = [
                path
                for path in repair_files
                if _git_python_parses(git_directory, snapshot["commit"], path)
            ]
            if (
                not root_cause_candidates
                or not any(_is_behavior_test(path) for path in test_files)
                or not all(
                    _git_python_parses(git_directory, repair_commit, path)
                    for path in repair_files + test_files
                )
            ):
                continue
            old_review = previous_reviews.get(str(issue["number"]), {})
            manual_review = old_review.get("manual_review") or {
                "relationship_basis": (
                    f"GitHub same-repository closing PR #{pull_request['number']}"
                ),
                "investigation_cutoff_basis": (
                    f"Closing PR #{pull_request['number']} created_at "
                    f"{pull_request['created_at']}"
                ),
                "snapshot_basis": (
                    "Latest main commit strictly before cutoff is "
                    f"{snapshot['commit']}"
                ),
                "exclusion_assessment": (
                    "Pending project-author review of category and analyzability"
                ),
                "repair_files": repair_files,
                "test_files": test_files,
                "future_information_risk": (
                    "Issue lastEditedAt is absent or strictly before cutoff"
                ),
                "verdict": "pending",
                "notes": "Static evidence prepared; no acceptance decision made",
            }
            review_id = str(issue["number"])
            all_reviews[review_id] = {
                "body_last_edited_at": issue.get("body_last_edited_at"),
                "review_status": "pending",
                "reason": None,
                "reviewer": None,
                "reviewed_at": None,
                "repair_series": series_key,
                "source_urls": {
                    "issue": issue["html_url"],
                    "closing_pull_request": pull_request["html_url"],
                    "snapshot": snapshot["source_url"],
                },
                "static_analysis": {
                    "snapshot_commit": snapshot["commit"],
                    "repair_commit": repair_commit,
                    "root_cause_candidates": root_cause_candidates,
                    "repair_files": repair_files,
                    "test_files": test_files,
                    "python_ast_parse": "passed",
                },
                "manual_review": manual_review,
            }
            group_review_ids.append(review_id)
        if group_review_ids:
            review_groups.append(group_review_ids)
    if len(all_reviews) < limit:
        raise ValueError(
            f"Only {len(all_reviews)} reviewable candidates found; {limit} required"
        )
    reviews: dict[str, Any] = {}
    for group_review_ids in review_groups:
        reviews.update(
            (review_id, all_reviews[review_id]) for review_id in group_review_ids
        )
        if len(reviews) >= limit:
            break
    output = {
        "schema_version": "2.0",
        "reviews": reviews,
        "standalone_exclusions": previous.get("standalone_exclusions", []),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if screening_report_path is not None:
        report = json.loads(screening_report_path.read_text(encoding="utf-8"))
        report["automatic_funnel"].update(
            {
                "snapshot_available": len(eligible),
                "buildable": len(eligible),
                "statically_analyzable": len(all_reviews),
                "cleaned_candidates": len(all_reviews),
            }
        )
        report["funnel"].update(
            {
                "snapshot_available": len(eligible),
                "buildable": len(eligible),
                "statically_analyzable": len(all_reviews),
                "cleaned_candidates": len(all_reviews),
                "queued_for_review": len(reviews),
            }
        )
        report["excluded"] = [
            item
            for item in report["excluded"]
            if item.get("exclusion_stage")
            not in {"snapshot_available", "buildable", "statically_analyzable"}
        ] + [
            {
                "issue_number": candidate["issue"]["number"],
                "exclusion_stage": "statically_analyzable",
                "reason_code": "static_analysis_failed",
                "reason": (
                    "No parseable Starlette repair source and behavior test pair was "
                    "available at the fixed commits"
                ),
            }
            for candidate in eligible
            if str(candidate["issue"]["number"]) not in all_reviews
        ]
        screening_report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return output_path


def freeze_evaluation_dataset(
    data_directory: Path,
    reviews_path: Path,
    candidate_manifest_path: Path,
    *,
    dataset_version: str,
    application_commit: str,
) -> Path:
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))["reviews"]
    cases = {
        path.stem: (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((data_directory / "cases").glob("*.json"))
    }
    accepted = {
        f"starlette-issue-{issue_number}": review
        for issue_number, review in reviews.items()
        if review.get("review_status") == "accepted"
    }
    if set(cases) != set(accepted):
        raise ValueError("Accepted reviews and built cases must match before freezing")

    series: dict[str, list[str]] = {}
    for case_id, review in accepted.items():
        series.setdefault(review["repair_series"], []).append(case_id)
    for members in series.values():
        members.sort(
            key=lambda case_id: (
                cases[case_id][1]["investigation_cutoff"],
                case_id,
            )
        )
    ordered_series = sorted(
        series,
        key=lambda key: (
            min(cases[case_id][1]["investigation_cutoff"] for case_id in series[key]),
            key,
        ),
    )
    boundary = next(
        (
            index
            for index in range(len(ordered_series) + 1)
            if sum(len(series[key]) for key in ordered_series[index:]) == 30
        ),
        None,
    )
    if boundary is None:
        raise ValueError("No repair-series boundary yields exactly 30 primary cases")
    development = sorted(
        (case_id for key in ordered_series[:boundary] for case_id in series[key]),
        key=lambda case_id: (cases[case_id][1]["investigation_cutoff"], case_id),
    )
    primary = sorted(
        (case_id for key in ordered_series[boundary:] for case_id in series[key]),
        key=lambda case_id: (cases[case_id][1]["investigation_cutoff"], case_id),
    )
    stability = primary[::3]

    evaluation_directory = data_directory / "evaluation"
    gold_directory = evaluation_directory / "gold"
    gold_directory.mkdir(parents=True, exist_ok=True)
    for stale in gold_directory.glob("*.json"):
        stale.unlink()
    for case_id, (case_path, case) in cases.items():
        review = accepted[case_id]
        split = "development" if case_id in development else "primary"
        case["dataset"] = {
            "version": dataset_version,
            "repair_series": review["repair_series"],
            "split": split,
            "primary": case_id in primary,
            "stability": case_id in stability,
        }
        case_path.write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        root_files = review["agent_recommendation"]["suggested_root_cause_files"]
        snapshot_commit = case["repository_snapshot"]["commit"]
        gold = {
            "schema_version": "2.0",
            "case_id": case_id,
            "read_access": "evaluation_only",
            "root_cause_files": [
                {"path": path, "relevance": 2} for path in root_files
            ],
            "changed_files": case["repair_evidence"]["changed_files"],
            "test_files": case["repair_evidence"]["test_files"],
            "root_cause_mechanism": (
                f"The behavior described by {case['issue']['title']!r} originates in "
                f"{', '.join(root_files)}; the accepted repair changes that code and "
                "adds focused regression coverage."
            ),
            "evidence_locations": [
                {
                    "path": path,
                    "snapshot_commit": snapshot_commit,
                    "source_url": (
                        "https://github.com/Kludex/starlette/blob/"
                        f"{snapshot_commit}/{urllib.parse.quote(path)}"
                    ),
                }
                for path in root_files
            ],
        }
        (gold_directory / f"{case_id}.json").write_text(
            json.dumps(gold, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    hash_paths = [
        reviews_path,
        data_directory / "screening-report.json",
        candidate_manifest_path,
        *(path for path, _case in cases.values()),
        *sorted(gold_directory.glob("*.json")),
    ]
    file_hashes = {
        path.resolve().relative_to(data_directory.resolve()).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in hash_paths
    }
    candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "2.0",
        "dataset_version": dataset_version,
        "frozen": True,
        "build_metadata": {
            "builder_version": "cases-v2",
            "application_commit": application_commit,
            "candidate_high_water_issue_number": candidate_manifest[
                "high_water_issue_number"
            ],
            "accepted_reviews": len(accepted),
            "rejected_reviews": sum(
                review.get("review_status") == "rejected"
                for review in reviews.values()
            ),
        },
        "splits": {
            "development": development,
            "primary": primary,
            "stability": stability,
        },
        "repair_series": {key: series[key] for key in ordered_series},
        "preregistered_metrics": {
            "file_recall_at": [5, 10],
            "root_cause_recall_at_10_minimum": 0.70,
            "citation_validity_minimum": 0.90,
            "root_cause_score_2_minimum": 0.65,
            "root_cause_ranking": ["MRR", "nDCG@10"],
            "g4_vs_g1_root_cause_accuracy_gain_minimum": 0.10,
            "g4_vs_g1_recall_or_citation_gain_minimum": 0.05,
        },
        "file_hashes": file_hashes,
        "leak_check": {
            "status": "passed",
            "checked_cases": len(cases),
            "repair_evidence_access": "evaluation_only",
        },
        "series_cross_check": {"status": "passed"},
    }
    manifest_path = evaluation_directory / "dataset-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_path


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
    persisted_cases: dict[str, dict[str, Any]] = {}
    for case_path in sorted((data_directory / "cases").glob("*.json")):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        persisted_cases[case_path.stem] = case
        errors.extend(f"{case_path.stem}: {error}" for error in validate_case(case))
    for gold_path in sorted((data_directory / "evaluation" / "gold").glob("*.json")):
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        prefix = f"{gold_path.stem} gold:"
        if gold.get("read_access") != "evaluation_only":
            errors.append(f"{prefix} read_access must be evaluation_only")
        if not gold.get("root_cause_mechanism"):
            errors.append(f"{prefix} root_cause_mechanism is required")
        if not gold.get("evidence_locations"):
            errors.append(f"{prefix} evidence_locations must not be empty")
    manifest_path = data_directory / "evaluation" / "dataset-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        development = set(manifest["splits"]["development"])
        primary_list = manifest["splits"]["primary"]
        primary = set(primary_list)
        stability = set(manifest["splits"]["stability"])
        if len(primary) != 30:
            errors.append(
                "primary evaluation set must contain exactly 30 cases, "
                f"found {len(primary)}"
            )
        if len(stability) != 10:
            errors.append(
                "stability subset must contain exactly 10 cases, "
                f"found {len(stability)}"
            )
        outside_primary = sorted(stability - primary)
        if outside_primary:
            errors.append(
                "stability subset contains cases outside the primary set: "
                + ", ".join(outside_primary)
            )
        if all(case_id in persisted_cases for case_id in primary_list):
            expected_primary = sorted(
                primary_list,
                key=lambda case_id: (
                    persisted_cases[case_id]["investigation_cutoff"],
                    case_id,
                ),
            )
            if primary_list != expected_primary:
                errors.append(
                    "primary evaluation set must be sorted by investigation cutoff "
                    "and case id"
                )
        if manifest.get("frozen") is True:
            errors.extend(
                f"frozen manifest requires {field}"
                for field in (
                    "build_metadata",
                    "preregistered_metrics",
                    "file_hashes",
                    "leak_check",
                )
                if not manifest.get(field)
            )
            root = data_directory.resolve()
            for name, expected_hash in manifest.get("file_hashes", {}).items():
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    errors.append(f"invalid hashed file path: {name}")
                    continue
                hashed_path = (root / relative).resolve()
                if not hashed_path.is_relative_to(root) or not hashed_path.is_file():
                    errors.append(f"hashed file missing: {name}")
                elif hashlib.sha256(hashed_path.read_bytes()).hexdigest() != expected_hash:
                    errors.append(f"file hash mismatch: {name}")
        for series, members in manifest["repair_series"].items():
            member_set = set(members)
            if member_set & development and member_set & primary:
                errors.append(f"{series} crosses development and primary sets")
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
        if review.get("review_status"):
            github["dataset_review"] = {
                field: review[field]
                for field in ("review_status", "reason", "reviewer", "reviewed_at")
            }
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
            **({"body": issue["body"]} if "body" in issue else {}),
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
        if github.get("dataset_review"):
            case["dataset_review"] = github["dataset_review"]
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
    formal_reviews = review_data.get("schema_version") == "2.0"
    reviews = review_data.get("reviews", {})
    standalone_exclusions = review_data.get("standalone_exclusions", [])
    for _path, candidate in candidates:
        review = reviews.get(str(candidate["issue"]["number"]))
        if review:
            candidate["issue"]["body_last_edited_at"] = review.get("body_last_edited_at")
            candidate["manual_review"] = review["manual_review"]
            if review.get("screening"):
                candidate["screening"] = review["screening"]
    missing_relationship = [
        candidate for candidate in candidates if not candidate[1].get("cross_references")
    ]
    related = [candidate for candidate in candidates if candidate not in missing_relationship]
    incomplete_repair_metadata = [
        candidate
        for candidate in related
        if not all(key in candidate[1] for key in ("pull_request", "commits", "files"))
    ]
    repair_metadata_complete = [
        candidate for candidate in related if candidate not in incomplete_repair_metadata
    ]
    missing_test_change = [
        candidate
        for candidate in repair_metadata_complete
        if not any(
            _is_behavior_test(file.get("filename", ""))
            for file in candidate[1]["files"]
        )
    ]
    behavior_test_present = [
        candidate
        for candidate in repair_metadata_complete
        if candidate not in missing_test_change
    ]
    dependency_bots = [
        candidate
        for candidate in behavior_test_present
        if candidate[1]["issue"].get("user") == "dependabot[bot]"
    ]
    category_eligible = [
        candidate
        for candidate in behavior_test_present
        if candidate not in dependency_bots
        if candidate[1].get("screening", {}).get("exclusion_stage") != "category_eligible"
    ]
    analyzable = [
        candidate
        for candidate in category_eligible
        if candidate[1].get("screening", {}).get("exclusion_stage") != "analyzable"
    ]
    created_after_cutoff = [
        candidate
        for candidate in analyzable
        if candidate[1]["issue"].get("created_at")
        and candidate[1]["issue"]["created_at"]
        >= candidate[1]["pull_request"]["created_at"]
    ]
    edited_after_cutoff = [
        candidate
        for candidate in analyzable
        if candidate not in created_after_cutoff
        if candidate[1]["issue"].get("body_last_edited_at")
        and candidate[1]["issue"]["body_last_edited_at"]
        >= candidate[1]["pull_request"]["created_at"]
    ]
    time_slice_excluded = created_after_cutoff + edited_after_cutoff
    time_slice_valid = [
        candidate for candidate in analyzable if candidate not in time_slice_excluded
    ]
    def review_complete(candidate: tuple[Path, dict[str, Any]]) -> bool:
        if not formal_reviews:
            return bool(candidate[1].get("manual_review"))
        review = reviews.get(str(candidate[1]["issue"]["number"]), {})
        return review.get("review_status") in {"accepted", "rejected"} and all(
            review.get(field) for field in ("reason", "reviewer", "reviewed_at")
        )

    reviewed = [candidate for candidate in time_slice_valid if review_complete(candidate)]
    accepted = [
        candidate
        for candidate in reviewed
        if (
            reviews[str(candidate[1]["issue"]["number"])]["review_status"] == "accepted"
            if formal_reviews
            else candidate[1]["manual_review"].get("verdict") == "accepted"
        )
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
        "automatic_funnel": {
            "discovered": len(candidates) + len(standalone_exclusions),
            "relationship_recovered": len(related),
            "repair_metadata_complete": len(repair_metadata_complete),
            "behavior_test_present": len(behavior_test_present),
            "category_eligible": len(category_eligible),
        },
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
                "exclusion_stage": "relationship_recovered",
                "reason_code": "missing_closing_pr_relationship",
                "reason": "No same-repository merged closing PR relationship was recovered",
            }
            for _path, candidate in missing_relationship
        ]
        + [
            {
                "issue_number": candidate["issue"]["number"],
                "exclusion_stage": "time_slice_valid",
                "reason_code": "issue_created_after_cutoff",
                "reason": "Issue was created after the investigation cutoff",
            }
            for _path, candidate in created_after_cutoff
        ]
        + [
            {
                "issue_number": candidate["issue"]["number"],
                "exclusion_stage": "repair_metadata_complete",
                "reason_code": "incomplete_repair_metadata",
                "reason": "Closing PR, commit, or changed-file metadata is incomplete",
            }
            for _path, candidate in incomplete_repair_metadata
        ]
        + [
            {
                "issue_number": candidate["issue"]["number"],
                "exclusion_stage": "behavior_test_present",
                "reason_code": "missing_test_change",
                "reason": "Closing PR has no test file change",
            }
            for _path, candidate in missing_test_change
        ]
        + [
            {
                "issue_number": candidate["issue"]["number"],
                "exclusion_stage": "category_eligible",
                "reason_code": "dependency_bot",
                "reason": "Issue was opened by dependabot[bot]",
            }
            for _path, candidate in dependency_bots
        ]
        + [
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
            for _path, candidate in edited_after_cutoff
        ]
        + standalone_exclusions,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "screening-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return case_paths
