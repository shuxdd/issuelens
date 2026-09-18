import json
from pathlib import Path

from issuelens.cases import build_case

FIXTURES = Path(__file__).parent / "fixtures"


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
