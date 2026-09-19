import io
import json
import shutil
from pathlib import Path
from urllib.request import Request

import pytest
from issuelens.cases import build_case, build_cases, collect_github_data, validate_case

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_collection_keeps_metadata_without_bodies_or_diff_patches(tmp_path: Path) -> None:
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

    assert "ISSUE_BODY" not in serialized
    assert "UNRELATED_TIMELINE_BODY" not in serialized
    assert "PR_BODY" not in serialized
    assert "DIFF_PATCH" not in serialized


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
