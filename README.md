# IssueLens

IssueLens is an evidence-driven investigation system for open-source issues.

## Current capabilities

- Ten versioned, time-sliced Starlette cases with leak checks and source
  traceability.
- A reproducible vector, BM25 hybrid, and reranked retrieval comparison for the
  first fixed case, including offline regression thresholds.
- An English structured investigation report and a controlled investigation
  path with `supported`, `insufficient_evidence`, `budget_exhausted`, and
  `unsafe_request` outcomes.
- Explicit budgets for tool calls, retrieval rewrites, root-cause hypotheses,
  patch attempts, and total runtime. Tests use fixed model and tool substitutes;
  no real patch sandbox is implemented yet.

## Dataset

The versioned dataset under `data/` is the authoritative source for cases and
evaluation inputs (see `docs/adr/0002-versioned-files-as-evaluation-source-of-truth.md`).

**Data boundaries**

- Cases are built from public GitHub metadata for
  [Kludex/starlette](https://github.com/Kludex/starlette): issue metadata,
  timeline/cross-references, the closing PR, commits, and changed/test file lists.
- Each case in `data/cases/` fixes an investigation cutoff (UTC, ISO 8601) and a
  repository snapshot commit. Allowed evidence is restricted to what existed
  before the cutoff (`docs/adr/0001-time-sliced-evaluation.md`).
- Repair evidence (closing PR files, commits, tests) is marked `evaluation_only`
  and never enters the investigation context.
- Cached responses in `data/github-cache/` contain metadata only; issue bodies,
  PR discussions, and diff patches are not stored or redistributed.
- Every data object carries a `source_url` pointing back to GitHub, and
  `data/screening-report.json` records the filtering funnel with exclusion
  reasons.

**License**

- Starlette is distributed under the BSD-3-Clause license; referenced code,
  commits, and issues belong to the upstream authors.
- Case files, reviews, and the screening report are project-authored files that
  reference upstream sources rather than redistributing upstream content.

**Rebuild steps**

- `data/` is committed to Git, so rebuilding is optional and only needs the
  versioned inputs (`data/github-cache/`, `data/repository-snapshots/`,
  `data/reviews.json`).
- Validate the committed dataset (schema, source traceability, and leak checks):

  ```shell
  uv run pytest backend/tests/test_dataset.py backend/tests/test_case_validation.py
  ```

- Rebuild from the versioned inputs and confirm byte-identical idempotent
  output (covered by `backend/tests/test_dataset.py`):

  ```python
  from issuelens.cases import build_cases

  build_cases(cache, snapshot, output, reviews_path=reviews)
  ```

## Run locally

Start the web app, API, and PostgreSQL with one command:

```shell
docker compose up --build
```

Open <http://localhost:3000>. The page reports the API health status through the
frontend proxy. The machine-readable endpoint is available at
<http://localhost:3000/api/health>.

The fixed case can also be inspected through these read-only endpoints:

- `/api/cases/starlette-issue-1552`
- `/api/retrieval/three-way/starlette-issue-1552`
- `/api/reports/three-way/starlette-issue-1552`
- `/api/investigations/starlette-issue-1552`

Replay the fixed supported investigation without network or model API calls:

```shell
PYTHONPATH=backend/src uv run --frozen --offline python scripts/run_supported_investigation.py
```

The replay writes the deterministic result to
`data/investigations/starlette-issue-1552/run.json`.

## Checks

Run all backend tests, lint rules, and type checks:

```shell
uv run python scripts/check_backend.py
```

Run all frontend tests, lint rules, type checks, and the production build:

```shell
cd frontend
pnpm check
```

No API keys are required for local checks or CI.
