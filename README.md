# IssueLens

IssueLens is an evidence-driven investigation system for open-source issues.

## Current capabilities

- Fifty-one versioned, time-sliced Starlette cases with leak checks and source
  traceability, including a frozen 30-case primary set and 10-case stability
  subset.
- A reproducible vector, BM25 hybrid, and reranked retrieval comparison for the
  first fixed case, including offline regression thresholds.
- An English structured investigation report and a controlled investigation
  path with `supported`, `insufficient_evidence`, `budget_exhausted`, and
  `unsafe_request` outcomes.
- Explicit budgets for tool calls, retrieval rewrites, root-cause hypotheses,
  patch attempts, and total runtime. A constrained Docker patch sandbox validates
  the fixed ten-case campaign; it does not promise automatic repair.
- Ordered local investigation events with stable run/trace identifiers, explicit
  parent links, model usage/cost, latency, and terminal reasons. Langfuse is an
  optional sanitized export; local events remain the offline source of record.
- A read-only Web Demo of five committed investigation trajectories and their
  patch-validation context.

See [the architecture, state machine, trace, and threat model](docs/reproducible-release.md).

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
- Cached responses in `data/github-cache/` contain the public Issue body needed
  for investigation plus minimal metadata. Comments, PR bodies/discussions,
  diff patches, and repair file contents are not stored or redistributed.
- Every data object carries a `source_url` pointing back to GitHub, and
  `data/screening-report.json` records the filtering funnel with exclusion
  reasons.

**License**

- Starlette is distributed under the BSD-3-Clause license; referenced code,
  commits, and issues belong to the upstream authors.
- Case files, reviews, and the screening report are project-authored files that
  reference upstream sources rather than redistributing upstream content.
- This repository does not declare a separate license for its project-authored
  files; obtain permission before redistributing them.

**Rebuild steps**

- `data/` is committed to Git, so rebuilding is optional and only needs the
  versioned inputs (`data/github-cache/`, `data/repository-snapshots/`,
  `data/reviews.json`).
- The formal v2 review queue records 80 project-author decisions. Two candidates
  fail the final time-slice gate; the remaining decisions produce 51 accepted
  cases and the frozen split in `data/evaluation/dataset-manifest.json`.
- Dataset file hashes use LF-normalized UTF-8 content, so Windows and Linux
  checkout line endings produce the same validation result.
- Validate the committed dataset (schema, source traceability, and leak checks):

  ```shell
  uv run --frozen pytest backend/tests/test_dataset.py backend/tests/test_case_validation.py
  ```

- Rebuild the 51-case dataset in a temporary directory from the versioned
  inputs, then verify that a second build is byte-identical:

  ```shell
  uv run --frozen pytest backend/tests/test_dataset.py::test_versioned_inputs_rebuild_the_dataset_idempotently
  ```

## Run locally

Start the web app, API, and PostgreSQL with one command:

```shell
docker compose up --build --detach --wait
```

Open <http://localhost:3000>. Confirm the health endpoint returns
`{"service":"issuelens-api","status":"ok"}`:

```shell
curl --fail http://localhost:3000/api/health
```

In PowerShell, use `Invoke-RestMethod http://localhost:3000/api/health`.

Stop the services with `docker compose down` when finished; this retains the
PostgreSQL data volume.

The public Demo exposes five fixed cases through these read-only endpoints:

- `/api/demo/cases`
- `/api/demo/cases/starlette-issue-1552`
- `/api/demo/cases/starlette-issue-1552/events`

Install the locked Python dependencies once with `uv sync --frozen`. Then
rebuild the dataset, check its schema and time-slice leakage rules, run two
investigations with fixed offline tools, and rebuild the ten-case patch report:

```shell
uv run --frozen pytest backend/tests/test_dataset.py backend/tests/test_case_validation.py
uv run --frozen pytest backend/tests/test_release_reproduction.py backend/tests/test_patch_evaluation.py::test_report_command_uses_the_preselected_ten_frozen_cases
```

The checks use temporary output directories and compare new investigation
records and report metrics with the committed files. They need no production
API key, Langfuse account, or live GitHub API. The published ten-case metrics
are reproduction rate `1.0` and patch application, target-test, regression-free,
pass-at-1, and pass-at-2 rates `0.8`. These are results for a fixed candidate
campaign, not autonomous repair rates.

## Model and safety configuration

The offline commands use fixed responses or stop before model generation.
For an optional live one-shot report, `scripts/run_one_shot_report.py` reads
`DEEPSEEK_API_KEY` from the environment or ignored `.env`; its model and pricing
are specified in `data/reports/starlette-issue-1552/config.json`. It requires
the matching local Starlette source snapshot and network access to the model.
It is outside the offline release checks.

Langfuse export is disabled by default. A live investigation can set
`LANGFUSE_ENABLED=true` with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and
optionally `LANGFUSE_BASE_URL`. Export failures preserve the local outcome.
Only allowlisted summaries are exported; credentials, prompts, full tool
results, and container output stay local.

The Web Demo accepts only the five listed case IDs and read-only requests.
Its safety boundary and residual risks are recorded in the
[threat model](docs/reproducible-release.md#threat-model).

## Known limits

- The Demo serves recorded runs. Only its two safety trajectories are freshly
  regenerated by the offline check; supported and insufficient-evidence runs
  remain versioned historical artifacts.
- The dataset covers Starlette issues and frozen cutoffs, not arbitrary GitHub
  repositories. Full source retrieval and fresh supported investigations need
  a matching local source snapshot and cached retrieval models.
- First-time package and Docker image installation may contact package
  registries. The data, investigation replay, and metric checks themselves do
  not call the live GitHub API or a production model.

## Checks

Run all backend tests, lint rules, and type checks:

```shell
uv run --frozen python scripts/check_backend.py
```

Run all frontend tests, lint rules, type checks, and the production build:

```shell
cd frontend
pnpm install --frozen-lockfile
pnpm check
```

CI additionally checks the dataset schema and leak rules, offline replay and
metric reconstruction, retrieval regression, and a Docker Compose smoke test.
No API keys are required for local checks or CI.
