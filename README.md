# IssueLens

IssueLens is an evidence-driven investigation system for open-source issues.

## Run locally

Start the web app, API, and PostgreSQL with one command:

```shell
docker compose up --build
```

Open <http://localhost:3000>. The page reports the API health status through the
frontend proxy. The machine-readable endpoint is available at
<http://localhost:3000/api/health>.

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
