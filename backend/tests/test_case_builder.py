import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest
from issuelens.cases import (
    build_case,
    build_cases,
    build_review_queue,
    collect_evaluation_candidates,
    collect_github_data,
    collect_repository_snapshots,
    discover_github_issues,
    enrich_github_issue_content,
    freeze_evaluation_dataset,
    open_with_gh,
    validate_case,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_gh_api_boundary_retries_a_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def run(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise subprocess.CalledProcessError(1, ["gh", "api"])
        return subprocess.CompletedProcess(["gh", "api"], 0, stdout=b'{"ok": true}')

    monkeypatch.setattr("issuelens.cases.subprocess.run", run)

    with open_with_gh(Request("https://api.github.com/test")) as response:
        assert json.loads(response.read()) == {"ok": True}
    assert attempts == 2


def test_graphql_enrichment_caches_issue_body_and_last_edit_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "Kludex-starlette-1552.json"
    cache_path.write_text(
        json.dumps(
            {
                "repository": "Kludex/starlette",
                "issue": {"number": 1552, "title": "Route naming"},
            }
        ),
        encoding="utf-8",
    )

    def run(*args: object, **kwargs: object) -> object:
        return subprocess.CompletedProcess(
            ["gh", "api", "graphql"],
            0,
            stdout=json.dumps(
                {
                    "data": {
                        "repository": {
                            "i1552": {
                                "number": 1552,
                                "body": "Observed behavior",
                                "lastEditedAt": "2022-03-20T00:00:00Z",
                            }
                        }
                    }
                }
            ).encode(),
        )

    monkeypatch.setattr("issuelens.cases.subprocess.run", run)

    enrich_github_issue_content([cache_path])

    issue = json.loads(cache_path.read_text(encoding="utf-8"))["issue"]
    assert issue["body"] == "Observed behavior"
    assert issue["body_last_edited_at"] == "2022-03-20T00:00:00Z"


def test_graphql_enrichment_uses_complete_cache_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "Kludex-starlette-1552.json"
    cache_path.write_text(
        json.dumps(
            {
                "repository": "Kludex/starlette",
                "issue": {
                    "number": 1552,
                    "body": "Observed behavior",
                    "body_last_edited_at": None,
                },
            }
        ),
        encoding="utf-8",
    )

    def fail_on_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("GraphQL is forbidden on a complete cache hit")

    monkeypatch.setattr("issuelens.cases.subprocess.run", fail_on_network)

    enrich_github_issue_content([cache_path])


def test_issue_discovery_writes_a_sorted_fixed_high_water_manifest(
    tmp_path: Path,
) -> None:
    api = "https://api.github.com/repos/Kludex/starlette/issues"
    responses = {
        f"{api}?state=closed&sort=created&direction=asc&per_page=100&page=1": [
            {"number": 3, "html_url": "https://github.com/Kludex/starlette/issues/3"},
            {
                "number": 4,
                "html_url": "https://github.com/Kludex/starlette/pull/4",
                "pull_request": {"url": "https://api.github.com/pulls/4"},
            },
            {"number": 6, "html_url": "https://github.com/Kludex/starlette/issues/6"},
        ],
        f"{api}?state=closed&sort=created&direction=asc&per_page=100&page=2": [],
    }

    def open_fixture(request: Request) -> io.BytesIO:
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    manifest_path = discover_github_issues(
        "Kludex/starlette",
        tmp_path / "candidate-manifest.json",
        high_water_issue_number=5,
        opener=open_fixture,
    )

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "repository": "Kludex/starlette",
        "high_water_issue_number": 5,
        "issue_numbers": [3],
    }


def test_candidate_collection_stops_after_the_requested_cleaned_count(
    tmp_path: Path,
) -> None:
    api = "https://api.github.com/repos/Kludex/starlette"
    manifest_path = tmp_path / "candidate-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "repository": "Kludex/starlette",
                "high_water_issue_number": 2,
                "issue_numbers": [1, 2],
            }
        ),
        encoding="utf-8",
    )
    timeline = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 20,
                    "repository": {"full_name": "Kludex/starlette"},
                    "pull_request": {
                        "url": f"{api}/pulls/20",
                        "merged_at": "2026-01-03T00:00:00Z",
                    },
                }
            },
        }
    ]
    non_test_timeline = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 10,
                    "repository": {"full_name": "Kludex/starlette"},
                    "pull_request": {
                        "url": f"{api}/pulls/10",
                        "merged_at": "2026-01-03T00:00:00Z",
                    },
                }
            },
        }
    ]
    responses = {
        f"{api}/issues/1": {"number": 1, "state": "closed", "user": {"login": "user"}},
        f"{api}/issues/1/timeline?per_page=100": non_test_timeline,
        f"{api}/pulls/10": {"number": 10, "created_at": "2026-01-02T00:00:00Z"},
        f"{api}/pulls/10/commits?per_page=100": [{"sha": "repair"}],
        f"{api}/pulls/10/files?per_page=100": [
            {"filename": "starlette/example.py", "status": "modified"},
            {"filename": "tests/example/__init__.py", "status": "modified"},
        ],
        f"{api}/issues/2": {"number": 2, "state": "closed", "user": {"login": "user"}},
        f"{api}/issues/2/timeline?per_page=100": timeline,
        f"{api}/pulls/20": {"number": 20, "created_at": "2026-01-02T00:00:00Z"},
        f"{api}/pulls/20/commits?per_page=100": [{"sha": "repair"}],
        f"{api}/pulls/20/files?per_page=100": [
            {"filename": "starlette/example.py", "status": "modified"},
            {"filename": "tests/test_example.py", "status": "modified"},
        ],
    }

    def open_fixture(request: Request) -> io.BytesIO:
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    collected = collect_evaluation_candidates(
        manifest_path,
        tmp_path / "cache",
        cleaned_limit=1,
        opener=open_fixture,
    )

    assert [path.stem for path in collected] == ["Kludex-starlette-2"]
    assert {path.stem for path in (tmp_path / "cache").glob("*.json")} == {
        "Kludex-starlette-1",
        "Kludex-starlette-2",
    }


def test_snapshot_collection_records_latest_main_commit_before_each_cutoff(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "Kludex-starlette-2.json"
    cache_path.write_text(
        json.dumps(
            {
                "repository": "Kludex/starlette",
                "issue": {"number": 2},
                "pull_request": {"created_at": "2026-01-02T00:00:00Z"},
            }
        ),
        encoding="utf-8",
    )
    commit = {
        "sha": "abc123",
        "html_url": "https://github.com/Kludex/starlette/commit/abc123",
        "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}},
    }

    def open_fixture(request: Request) -> io.BytesIO:
        return io.BytesIO(json.dumps([commit]).encode())

    snapshot_path = collect_repository_snapshots(
        [cache_path], tmp_path / "snapshot.json", opener=open_fixture
    )

    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "repository": "Kludex/starlette",
        "default_branch": "main",
        "default_branch_commits": [
            {
                "commit": "abc123",
                "committed_at": "2026-01-01T00:00:00Z",
                "source_url": "https://github.com/Kludex/starlette/commit/abc123",
            }
        ],
    }


def test_snapshot_collection_can_use_a_local_bare_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "Kludex-starlette-2.json"
    cache_path.write_text(
        json.dumps(
            {
                "repository": "Kludex/starlette",
                "issue": {"number": 2},
                "pull_request": {"created_at": "2026-01-02T00:00:00Z"},
            }
        ),
        encoding="utf-8",
    )

    def run(*args: object, **kwargs: object) -> object:
        return subprocess.CompletedProcess(
            ["git", "log"], 0, stdout=b"abc123\x001767225600\n"
        )

    monkeypatch.setattr("issuelens.cases.subprocess.run", run)

    snapshot_path = collect_repository_snapshots(
        [cache_path],
        tmp_path / "snapshot.json",
        git_directory=tmp_path / "starlette.git",
    )

    assert json.loads(snapshot_path.read_text(encoding="utf-8"))[
        "default_branch_commits"
    ] == [
        {
            "commit": "abc123",
            "committed_at": "2026-01-01T00:00:00Z",
            "source_url": "https://github.com/Kludex/starlette/commit/abc123",
        }
    ]


def test_review_queue_contains_static_evidence_but_stays_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_calls: list[list[str]] = []
    cache_path = tmp_path / "Kludex-starlette-2.json"
    cache_path.write_text(
        json.dumps(
            {
                "repository": "Kludex/starlette",
                "issue": {
                    "number": 2,
                    "html_url": "https://github.com/Kludex/starlette/issues/2",
                    "body_last_edited_at": None,
                },
                "pull_request": {
                    "number": 20,
                    "html_url": "https://github.com/Kludex/starlette/pull/20",
                    "created_at": "2026-01-02T00:00:00Z",
                    "merge_commit_sha": "merged-repair",
                },
                "commits": [{"sha": "repair"}],
                "files": [
                    {"filename": "starlette/example.py"},
                    {"filename": "tests/test_example.py"},
                ],
                "repository_snapshot": {
                    "commit": "snapshot",
                    "committed_at": "2026-01-01T00:00:00Z",
                    "source_url": "https://github.com/Kludex/starlette/commit/snapshot",
                },
            }
        ),
        encoding="utf-8",
    )

    def run(arguments: list[str], **kwargs: object) -> object:
        git_calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout=b"value = 1\n")

    monkeypatch.setattr("issuelens.cases.subprocess.run", run)
    screening_path = tmp_path / "screening-report.json"
    screening_path.write_text(
        json.dumps({"automatic_funnel": {}, "funnel": {}, "excluded": []}),
        encoding="utf-8",
    )

    reviews_path = build_review_queue(
        [cache_path],
        tmp_path / "reviews.json",
        git_directory=tmp_path / "starlette.git",
        limit=1,
        screening_report_path=screening_path,
    )

    review = json.loads(reviews_path.read_text(encoding="utf-8"))["reviews"]["2"]
    assert review["review_status"] == "pending"
    assert review["reason"] is None
    assert review["reviewer"] is None
    assert review["reviewed_at"] is None
    assert review["manual_review"]["repair_files"] == ["starlette/example.py"]
    assert review["manual_review"]["test_files"] == ["tests/test_example.py"]
    assert review["static_analysis"]["root_cause_candidates"] == [
        "starlette/example.py"
    ]
    assert git_calls and all("--no-lazy-fetch" in call for call in git_calls)
    assert any("merged-repair:tests/test_example.py" in call for call in git_calls)
    report = json.loads(screening_path.read_text(encoding="utf-8"))
    assert report["automatic_funnel"] == {
        "snapshot_available": 1,
        "buildable": 1,
        "statically_analyzable": 1,
        "cleaned_candidates": 1,
    }


def test_review_queue_keeps_a_repair_series_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_paths = []
    for issue_number, pull_number in ((2, 20), (3, 30)):
        cache_path = tmp_path / f"Kludex-starlette-{issue_number}.json"
        cache_path.write_text(
            json.dumps(
                {
                    "repository": "Kludex/starlette",
                    "issue": {
                        "number": issue_number,
                        "html_url": (
                            f"https://github.com/Kludex/starlette/issues/{issue_number}"
                        ),
                        "body_last_edited_at": None,
                    },
                    "cross_references": [],
                    "pull_request": {
                        "number": pull_number,
                        "html_url": (
                            f"https://github.com/Kludex/starlette/pull/{pull_number}"
                        ),
                        "created_at": f"2026-01-0{issue_number}T00:00:00Z",
                        "merge_commit_sha": "shared-merge",
                    },
                    "commits": [{"sha": "repair"}],
                    "files": [
                        {"filename": "starlette/example.py"},
                        {"filename": "tests/test_example.py"},
                    ],
                    "repository_snapshot": {
                        "commit": "snapshot",
                        "committed_at": "2026-01-01T00:00:00Z",
                        "source_url": (
                            "https://github.com/Kludex/starlette/commit/snapshot"
                        ),
                    },
                }
            ),
            encoding="utf-8",
        )
        cache_paths.append(cache_path)
    monkeypatch.setattr(
        "issuelens.cases.subprocess.run",
        lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments, 0, stdout=b"value = 1\n"
        ),
    )

    reviews_path = build_review_queue(
        cache_paths,
        tmp_path / "reviews.json",
        git_directory=tmp_path / "starlette.git",
        limit=1,
    )

    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))["reviews"]
    assert set(reviews) == {"2", "3"}
    assert {review["repair_series"] for review in reviews.values()} == {
        "starlette-repair-series-20"
    }


def test_freeze_builds_series_safe_thirty_and_ten_splits_with_gold(
    tmp_path: Path,
) -> None:
    cases_directory = tmp_path / "cases"
    cases_directory.mkdir()
    reviews: dict[str, Any] = {"schema_version": "2.0", "reviews": {}}
    for number in range(32):
        case_id = f"starlette-issue-{number}"
        case = {
            "schema_version": "1.0",
            "case_id": case_id,
            "issue": {"number": number, "title": f"Bug {number}"},
            "investigation_cutoff": f"2026-01-{number + 1:02d}T00:00:00Z",
            "repository_snapshot": {"commit": f"snapshot-{number}"},
            "repair_evidence": {
                "pull_request": {"number": 100 + number},
                "changed_files": [
                    {"path": f"starlette/file_{number}.py", "source_url": "source"}
                ],
                "test_files": [
                    {"path": f"tests/test_{number}.py", "source_url": "test"}
                ],
            },
        }
        (cases_directory / f"{case_id}.json").write_text(
            json.dumps(case), encoding="utf-8"
        )
        reviews["reviews"][str(number)] = {
            "review_status": "accepted",
            "repair_series": f"starlette-repair-series-{100 + number}",
            "agent_recommendation": {
                "suggested_root_cause_files": [f"starlette/file_{number}.py"]
            },
        }
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(json.dumps(reviews), encoding="utf-8")
    (tmp_path / "screening-report.json").write_text("{}", encoding="utf-8")
    manifest_source = tmp_path / "candidate-manifest.json"
    manifest_source.write_text(
        json.dumps({"high_water_issue_number": 3572}), encoding="utf-8"
    )

    manifest_path = freeze_evaluation_dataset(
        tmp_path,
        reviews_path,
        manifest_source,
        dataset_version="starlette-eval-v1",
        application_commit="abc123",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["splits"]["development"]) == 2
    assert len(manifest["splits"]["primary"]) == 30
    assert manifest["splits"]["stability"] == manifest["splits"]["primary"][::3]
    assert len(list((tmp_path / "evaluation" / "gold").glob("*.json"))) == 32
    assert manifest["leak_check"]["status"] == "passed"


def test_repeated_collection_uses_complete_cache_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    cached_path = cache_directory / "Kludex-starlette-3497.json"
    shutil.copyfile(FIXTURES / "github" / "complete-case.json", cached_path)

    def fail_on_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("GitHub network access is forbidden on a cache hit")

    monkeypatch.setattr("urllib.request.urlopen", fail_on_network)

    first_path = collect_github_data("Kludex/starlette", 3497, cache_directory)
    first_content = first_path.read_bytes()
    second_path = collect_github_data("Kludex/starlette", 3497, cache_directory)

    assert second_path == first_path == cached_path
    assert second_path.read_bytes() == first_content
    assert set(json.loads(first_content)) >= {
        "issue",
        "timeline",
        "cross_references",
        "pull_request",
        "commits",
        "files",
    }


def test_cache_miss_collects_complete_github_metadata_from_timeline(tmp_path: Path) -> None:
    api = "https://api.github.com/repos/Kludex/starlette"
    responses = {
        f"{api}/issues/3497": {
            "number": 3497,
            "title": "BlockingPortal alias",
            "html_url": "https://github.com/Kludex/starlette/issues/3497",
        },
        f"{api}/issues/3497/timeline?per_page=100": [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "number": 3498,
                        "html_url": "https://github.com/Kludex/starlette/pull/3498",
                        "repository": {"full_name": "Kludex/starlette"},
                        "pull_request": {
                            "url": f"{api}/pulls/3498",
                            "merged_at": "2026-09-05T07:03:31Z",
                        },
                    }
                },
            }
        ],
        f"{api}/pulls/3498": {
            "number": 3498,
            "created_at": "2026-09-03T06:58:28Z",
            "html_url": "https://github.com/Kludex/starlette/pull/3498",
        },
        f"{api}/pulls/3498/commits?per_page=100": [{"sha": "repair-commit"}],
        f"{api}/pulls/3498/files?per_page=100": [
            {"filename": "tests/test_testclient.py", "status": "modified"}
        ],
    }
    requested_urls: list[str] = []

    def open_fixture(request: Request) -> io.BytesIO:
        requested_urls.append(request.full_url)
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    cache_path = collect_github_data(
        "Kludex/starlette", 3497, tmp_path, opener=open_fixture
    )
    cached = json.loads(cache_path.read_text(encoding="utf-8"))

    assert set(requested_urls) == set(responses)
    assert cached == {
        "repository": "Kludex/starlette",
        "issue": responses[f"{api}/issues/3497"],
        "timeline": responses[f"{api}/issues/3497/timeline?per_page=100"],
        "cross_references": responses[f"{api}/issues/3497/timeline?per_page=100"],
        "pull_request": responses[f"{api}/pulls/3498"],
        "commits": responses[f"{api}/pulls/3498/commits?per_page=100"],
        "files": responses[f"{api}/pulls/3498/files?per_page=100"],
    }


def test_collection_ignores_cross_repository_references_when_recovering_closing_pr(
    tmp_path: Path,
) -> None:
    api = "https://api.github.com/repos/Kludex/starlette"
    timeline = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 99,
                    "repository": {"full_name": "example/consumer"},
                    "pull_request": {
                        "url": "https://api.github.com/repos/example/consumer/pulls/99",
                        "merged_at": "2026-09-03T04:25:15Z",
                    },
                }
            },
        },
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 3498,
                    "repository": {"full_name": "Kludex/starlette"},
                    "pull_request": {
                        "url": f"{api}/pulls/3498",
                        "merged_at": "2026-09-05T07:03:31Z",
                    },
                }
            },
        },
    ]
    responses = {
        f"{api}/issues/3497": {"number": 3497},
        f"{api}/issues/3497/timeline?per_page=100": timeline,
        f"{api}/pulls/3498": {"number": 3498},
        f"{api}/pulls/3498/commits?per_page=100": [],
        f"{api}/pulls/3498/files?per_page=100": [],
    }

    def open_fixture(request: Request) -> io.BytesIO:
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    cache_path = collect_github_data(
        "Kludex/starlette", 3497, tmp_path, opener=open_fixture
    )

    assert json.loads(cache_path.read_text(encoding="utf-8"))["pull_request"]["number"] == 3498


def test_collection_caches_issue_without_a_closing_pr_for_screening(
    tmp_path: Path,
) -> None:
    api = "https://api.github.com/repos/Kludex/starlette"
    responses = {
        f"{api}/issues/3497": {
            "number": 3497,
            "title": "Unrelated closed issue",
            "html_url": "https://github.com/Kludex/starlette/issues/3497",
            "state": "closed",
        },
        f"{api}/issues/3497/timeline?per_page=100": [],
    }

    def open_fixture(request: Request) -> io.BytesIO:
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    cache_path = collect_github_data(
        "Kludex/starlette", 3497, tmp_path, opener=open_fixture
    )
    cached = json.loads(cache_path.read_text(encoding="utf-8"))

    assert cached["issue"]["number"] == 3497
    assert cached["cross_references"] == []
    assert "pull_request" not in cached
    assert "commits" not in cached
    assert "files" not in cached


def test_collection_keeps_required_issue_body_without_repair_bodies_or_diff_patches(
    tmp_path: Path,
) -> None:
    api = "https://api.github.com/repos/Kludex/starlette"
    timeline = [
        {
            "event": "cross-referenced",
            "created_at": "2026-09-03T07:01:00Z",
            "source": {
                "issue": {
                    "number": 3498,
                    "body": "UNRELATED_TIMELINE_BODY",
                    "repository": {"full_name": "Kludex/starlette"},
                    "pull_request": {
                        "url": f"{api}/pulls/3498",
                        "merged_at": "2026-09-05T07:03:31Z",
                    },
                }
            },
        }
    ]
    responses = {
        f"{api}/issues/3497": {
            "number": 3497,
            "title": "BlockingPortal alias",
            "body": "ISSUE_BODY",
            "html_url": "https://github.com/Kludex/starlette/issues/3497",
            "created_at": "2026-09-03T04:02:00Z",
        },
        f"{api}/issues/3497/timeline?per_page=100": timeline,
        f"{api}/pulls/3498": {
            "number": 3498,
            "body": "PR_BODY",
            "created_at": "2026-09-03T06:58:28Z",
        },
        f"{api}/pulls/3498/commits?per_page=100": [
            {"sha": "repair", "html_url": "https://example.test/repair"}
        ],
        f"{api}/pulls/3498/files?per_page=100": [
            {
                "filename": "tests/test_testclient.py",
                "status": "modified",
                "patch": "DIFF_PATCH",
            }
        ],
    }

    def open_fixture(request: Request) -> io.BytesIO:
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    cache_path = collect_github_data(
        "Kludex/starlette", 3497, tmp_path, opener=open_fixture
    )
    serialized = cache_path.read_text(encoding="utf-8")

    assert json.loads(serialized)["issue"]["body"] == "ISSUE_BODY"
    assert "UNRELATED_TIMELINE_BODY" not in serialized
    assert "PR_BODY" not in serialized
    assert "DIFF_PATCH" not in serialized


def test_collection_uses_token_without_persisting_it(tmp_path: Path) -> None:
    api = "https://api.github.com/repos/Kludex/starlette"
    timeline = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 3498,
                    "repository": {"full_name": "Kludex/starlette"},
                    "pull_request": {
                        "url": f"{api}/pulls/3498",
                        "merged_at": "2026-09-05T07:03:31Z",
                    },
                }
            },
        }
    ]
    responses = {
        f"{api}/issues/3497": {"number": 3497},
        f"{api}/issues/3497/timeline?per_page=100": timeline,
        f"{api}/pulls/3498": {"number": 3498},
        f"{api}/pulls/3498/commits?per_page=100": [],
        f"{api}/pulls/3498/files?per_page=100": [],
    }
    authorization_headers: list[str | None] = []

    def open_fixture(request: Request) -> io.BytesIO:
        authorization_headers.append(request.get_header("Authorization"))
        return io.BytesIO(json.dumps(responses[request.full_url]).encode())

    cache_path = collect_github_data(
        "Kludex/starlette",
        3497,
        tmp_path,
        opener=open_fixture,
        token="secret-token-marker",
    )

    assert authorization_headers == ["Bearer secret-token-marker"] * 5
    assert "secret-token-marker" not in cache_path.read_text(encoding="utf-8")


def test_complete_sources_build_a_reviewable_time_sliced_case(tmp_path: Path) -> None:
    case_path = build_case(
        FIXTURES / "github" / "complete-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )
    case = json.loads(case_path.read_text(encoding="utf-8"))

    assert case["investigation_cutoff"] == "2026-09-03T06:58:28Z"
    assert case["repository_snapshot"]["commit"] == (
        "39fd0ffac25593fce39466320c9a666957ce8b8c"
    )
    assert case["allowed_evidence"]["items"] == [
        {
            "kind": "issue",
            "observed_at": "2026-09-03T04:02:00Z",
            "source_url": "https://github.com/Kludex/starlette/issues/3497",
        }
    ]
    assert [item["path"] for item in case["repair_evidence"]["changed_files"]] == [
        "starlette/testclient.py"
    ]
    assert [item["path"] for item in case["repair_evidence"]["test_files"]] == [
        "tests/test_testclient.py"
    ]
    assert case["manual_review"]["relationship_basis"].startswith("GitHub timeline")
    assert case["manual_review"]["verdict"] == "accepted"
    assert validate_case(case) == []


def test_case_keeps_issue_body_on_the_investigation_side(tmp_path: Path) -> None:
    cache = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    cache["issue"]["body"] = "Observed failure and reproduction details"
    cache_path = tmp_path / "complete-case.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    case_path = build_case(
        cache_path,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
    )
    case = json.loads(case_path.read_text(encoding="utf-8"))

    assert case["issue"]["body"] == "Observed failure and reproduction details"
    assert "body" not in case["repair_evidence"]["pull_request"]


def test_batch_build_outputs_ten_cases_and_an_explainable_screening_funnel(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    template = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    exclusions = [
        ("category_eligible", "security", "Security-sensitive issue"),
        ("category_eligible", "documentation_only", "Only documentation files changed"),
        ("category_eligible", "dependency_bot", "Opened by a dependency bot"),
        ("analyzable", "cross_repository_root_cause", "Root cause is outside Starlette"),
        ("analyzable", "unanalyzable", "Historical issue body cannot be recovered"),
    ]
    for offset in range(15):
        candidate = json.loads(json.dumps(template))
        issue_number = 3400 + offset
        candidate["issue"]["number"] = issue_number
        candidate["issue"]["html_url"] = (
            f"https://github.com/Kludex/starlette/issues/{issue_number}"
        )
        if offset >= 10:
            stage, reason_code, reason = exclusions[offset - 10]
            candidate["screening"] = {
                "exclusion_stage": stage,
                "reason_code": reason_code,
                "reason": reason,
            }
        (cache_directory / f"case-{issue_number}.json").write_text(
            json.dumps(candidate), encoding="utf-8"
        )

    case_paths = build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
    )
    report = json.loads(
        (tmp_path / "output" / "screening-report.json").read_text(encoding="utf-8")
    )

    assert len(case_paths) == 10
    assert report["funnel"] == {
        "collected": 15,
        "relationship_recovered": 15,
        "category_eligible": 12,
        "analyzable": 10,
        "time_slice_valid": 10,
        "manual_reviewed": 10,
        "accepted": 10,
    }
    assert {item["reason_code"] for item in report["excluded"]} == {
        "security",
        "documentation_only",
        "dependency_bot",
        "cross_repository_root_cause",
        "unanalyzable",
    }


def test_batch_cleaning_reports_stable_automatic_funnel_and_exclusion_codes(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    template = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )

    def write_candidate(issue_number: int, change: str) -> None:
        candidate = json.loads(json.dumps(template))
        candidate["issue"]["number"] = issue_number
        candidate["issue"]["html_url"] = (
            f"https://github.com/Kludex/starlette/issues/{issue_number}"
        )
        if change == "missing_relationship":
            candidate["cross_references"] = []
        elif change == "incomplete_repair_metadata":
            del candidate["pull_request"]
        elif change == "missing_test_change":
            candidate["files"] = [
                file
                for file in candidate["files"]
                if not file["filename"].startswith("tests/")
            ]
        elif change == "dependency_bot":
            candidate["issue"]["user"] = "dependabot[bot]"
        (cache_directory / f"case-{issue_number}.json").write_text(
            json.dumps(candidate), encoding="utf-8"
        )

    for issue_number, change in enumerate(
        (
            "accepted",
            "missing_relationship",
            "incomplete_repair_metadata",
            "missing_test_change",
            "dependency_bot",
        ),
        start=3500,
    ):
        write_candidate(issue_number, change)

    build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
    )
    report = json.loads(
        (tmp_path / "output" / "screening-report.json").read_text(encoding="utf-8")
    )

    assert report["automatic_funnel"] == {
        "discovered": 5,
        "relationship_recovered": 4,
        "repair_metadata_complete": 3,
        "behavior_test_present": 2,
        "category_eligible": 1,
    }
    assert [item["reason_code"] for item in report["excluded"]] == [
        "missing_closing_pr_relationship",
        "incomplete_repair_metadata",
        "missing_test_change",
        "dependency_bot",
    ]


def test_batch_excludes_issue_edited_after_the_investigation_cutoff(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    candidate = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    candidate["issue"]["body_last_edited_at"] = "2026-09-04T00:00:00Z"
    (cache_directory / "edited-after-cutoff.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )

    case_paths = build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
    )
    report = json.loads(
        (tmp_path / "output" / "screening-report.json").read_text(encoding="utf-8")
    )

    assert case_paths == []
    assert report["funnel"]["time_slice_valid"] == 0
    assert report["excluded"] == [
        {
            "exclusion_stage": "time_slice_valid",
            "issue_number": 3497,
            "reason": "Issue body was edited after the investigation cutoff",
            "reason_code": "issue_body_edited_after_cutoff",
        }
    ]


def test_batch_excludes_issue_created_after_the_investigation_cutoff(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    candidate = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    candidate["issue"]["created_at"] = "2026-09-04T00:00:00Z"
    (cache_directory / "created-after-cutoff.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )

    case_paths = build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
    )
    report = json.loads(
        (tmp_path / "output" / "screening-report.json").read_text(encoding="utf-8")
    )

    assert case_paths == []
    assert report["excluded"] == [
        {
            "exclusion_stage": "time_slice_valid",
            "issue_number": 3497,
            "reason": "Issue was created after the investigation cutoff",
            "reason_code": "issue_created_after_cutoff",
        }
    ]


def test_batch_build_applies_versioned_manual_reviews_without_mutating_cache(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    candidate = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    review = candidate.pop("manual_review")
    candidate["issue"].pop("body_last_edited_at")
    cache_path = cache_directory / "case-3497.json"
    cache_path.write_text(json.dumps(candidate), encoding="utf-8")
    cached_bytes = cache_path.read_bytes()
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "reviews": {
                    "3497": {
                        "body_last_edited_at": None,
                        "manual_review": review,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    case_paths = build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
        reviews_path=reviews_path,
    )

    assert len(case_paths) == 1
    assert json.loads(case_paths[0].read_text(encoding="utf-8"))["manual_review"] == review
    assert cache_path.read_bytes() == cached_bytes


def test_pending_author_review_cannot_enter_the_formal_dataset(tmp_path: Path) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    candidate = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    review = candidate.pop("manual_review")
    (cache_directory / "case-3497.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "reviews": {
                    "3497": {
                        "review_status": "pending",
                        "reason": None,
                        "reviewer": None,
                        "reviewed_at": None,
                        "manual_review": review,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    paths = build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
        reviews_path=reviews_path,
    )
    report = json.loads(
        (tmp_path / "output" / "screening-report.json").read_text(encoding="utf-8")
    )

    assert paths == []
    assert report["funnel"]["manual_reviewed"] == 0
    assert report["funnel"]["accepted"] == 0


def test_formal_case_keeps_author_review_identity_reason_and_time(tmp_path: Path) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    candidate = json.loads(
        (FIXTURES / "github" / "complete-case.json").read_text(encoding="utf-8")
    )
    review = candidate.pop("manual_review")
    (cache_directory / "case-3497.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "reviews": {
                    "3497": {
                        "review_status": "accepted",
                        "reason": "Confirmed against the linked issue and snapshot",
                        "reviewer": "project-author",
                        "reviewed_at": "2026-09-23T08:00:00Z",
                        "manual_review": review,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    [case_path] = build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
        reviews_path=reviews_path,
    )
    case = json.loads(case_path.read_text(encoding="utf-8"))

    assert case["dataset_review"] == {
        "review_status": "accepted",
        "reason": "Confirmed against the linked issue and snapshot",
        "reviewer": "project-author",
        "reviewed_at": "2026-09-23T08:00:00Z",
    }


def test_screening_report_includes_candidates_excluded_before_relation_recovery(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    shutil.copyfile(
        FIXTURES / "github" / "complete-case.json",
        cache_directory / "case-3497.json",
    )
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "reviews": {},
                "standalone_exclusions": [
                    {
                        "candidate_id": "issue-3096",
                        "source_url": "https://github.com/Kludex/starlette/issues/3096",
                        "exclusion_stage": "category_eligible",
                        "reason_code": "security",
                        "reason": "Security-sensitive candidate",
                    },
                    {
                        "candidate_id": "pr-3515",
                        "source_url": "https://github.com/Kludex/starlette/pull/3515",
                        "exclusion_stage": "category_eligible",
                        "reason_code": "documentation_only",
                        "reason": "Only documentation files changed",
                    },
                    {
                        "candidate_id": "pr-2894",
                        "source_url": "https://github.com/Kludex/starlette/pull/2894",
                        "exclusion_stage": "category_eligible",
                        "reason_code": "dependency_bot",
                        "reason": "Opened by dependabot[bot]",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        tmp_path / "output",
        reviews_path=reviews_path,
    )
    report = json.loads(
        (tmp_path / "output" / "screening-report.json").read_text(encoding="utf-8")
    )

    assert report["funnel"]["collected"] == 4
    assert report["funnel"]["relationship_recovered"] == 1
    assert {item["reason_code"] for item in report["excluded"]} == {
        "security",
        "documentation_only",
        "dependency_bot",
    }


def test_rebuild_removes_stale_logical_cases_from_the_controlled_output(
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "github"
    cache_directory.mkdir()
    shutil.copyfile(
        FIXTURES / "github" / "complete-case.json",
        cache_directory / "case-3497.json",
    )
    output_directory = tmp_path / "output"
    cases_directory = output_directory / "cases"
    cases_directory.mkdir(parents=True)
    (cases_directory / "starlette-issue-999.json").write_text("{}", encoding="utf-8")

    build_cases(
        cache_directory,
        FIXTURES / "repositories" / "starlette",
        output_directory,
    )

    assert [path.name for path in cases_directory.glob("*.json")] == [
        "starlette-issue-3497.json"
    ]


def test_fixed_sources_build_a_versioned_investigation_case(tmp_path: Path) -> None:
    case_path = build_case(
        FIXTURES / "github" / "single-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )

    assert json.loads(case_path.read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "case_id": "starlette-issue-3497",
        "issue": {
            "repository": "Kludex/starlette",
            "number": 3497,
            "title": "`testclient` imports the deprecated `anyio.abc.BlockingPortal` alias",
            "source_url": "https://github.com/Kludex/starlette/issues/3497",
        },
        "investigation_cutoff": "2026-09-03T06:58:28Z",
        "repository_snapshot": {
            "commit": "39fd0ffac25593fce39466320c9a666957ce8b8c",
            "source_url": "https://github.com/Kludex/starlette/commit/39fd0ffac25593fce39466320c9a666957ce8b8c",
        },
        "allowed_evidence": {
            "read_access": "investigation",
            "references": ["https://github.com/Kludex/starlette/issues/3497"],
        },
        "repair_evidence": {
            "read_access": "evaluation_only",
            "references": ["https://github.com/Kludex/starlette/pull/3498"],
        },
    }


def test_repeated_build_keeps_one_equivalent_logical_case(tmp_path: Path) -> None:
    first_path = build_case(
        FIXTURES / "github" / "single-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )
    first_content = first_path.read_bytes()

    second_path = build_case(
        FIXTURES / "github" / "single-case.json",
        FIXTURES / "repositories" / "starlette",
        tmp_path,
    )

    assert second_path == first_path
    assert second_path.read_bytes() == first_content
    assert [path.name for path in (tmp_path / "cases").iterdir()] == [
        "starlette-issue-3497.json"
    ]
    assert json.loads((tmp_path / "screening-report.json").read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "accepted_case_ids": ["starlette-issue-3497"],
        "excluded": [],
    }
