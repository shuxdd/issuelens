import json
import os
import re
import time
from collections import deque
from math import ceil
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from issuelens.cases import validate_case

app = FastAPI(title="IssueLens API")
CASES_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "cases"
RETRIEVAL_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "retrieval"
REPORT_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "reports"
INVESTIGATION_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "investigations"
DATA_DIRECTORY = Path(__file__).resolve().parents[3] / "data"
DEMO_MANIFEST = DATA_DIRECTORY / "demo" / "manifest.json"
DEMO_REQUESTS: dict[str, deque[float]] = {}
DEMO_RATE_LOCK = Lock()
DEMO_STREAM_LOCK = Lock()
DEMO_ACTIVE_STREAMS = 0
DEMO_CONCURRENT_LOCK = Lock()
DEMO_ACTIVE_REQUESTS = 0


@app.middleware("http")
async def reject_demo_inputs(request: Request, call_next: Any) -> Any:
    global DEMO_ACTIVE_REQUESTS
    if os.getenv("ISSUELENS_PUBLIC_DEMO") == "true":
        if request.method != "GET":
            return JSONResponse(status_code=405, content={"detail": "This demo is read only."})
        if request.url.path != "/health" and not request.url.path.startswith("/demo/"):
            return JSONResponse(status_code=404, content={"detail": "Demo route not found."})
    if request.url.path.startswith("/demo/") and request.query_params:
        return JSONResponse(
            status_code=400,
            content={"detail": "Demo input is limited to the five listed case IDs."},
        )
    if request.url.path.startswith("/demo/"):
        client_ip = request.client.host if request.client else "unknown"
        if os.getenv("ISSUELENS_PUBLIC_DEMO") == "true":
            client_ip = request.headers.get("x-real-ip", client_ip)
        now = time.monotonic()
        limit = int(os.getenv("ISSUELENS_DEMO_RATE_LIMIT", "60"))
        with DEMO_RATE_LOCK:
            times = DEMO_REQUESTS.setdefault(client_ip, deque())
            while times and now - times[0] >= 60:
                times.popleft()
            if len(times) >= limit:
                retry_after = max(1, ceil(60 - (now - times[0])))
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    content={"detail": "Too many demo requests. Wait and try again."},
                )
            times.append(now)
        with DEMO_CONCURRENT_LOCK:
            if int(os.getenv("ISSUELENS_DEMO_MAX_CONCURRENT", "8")) <= DEMO_ACTIVE_REQUESTS:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": "1"},
                    content={"detail": "Demo is busy. Wait and try again."},
                )
            DEMO_ACTIVE_REQUESTS += 1
        try:
            response = await call_next(request)
        except BaseException:
            with DEMO_CONCURRENT_LOCK:
                DEMO_ACTIVE_REQUESTS -= 1
            raise
        original_body = response.body_iterator

        async def limited_body() -> Any:
            global DEMO_ACTIVE_REQUESTS
            try:
                async for chunk in original_body:
                    yield chunk
            finally:
                with DEMO_CONCURRENT_LOCK:
                    DEMO_ACTIVE_REQUESTS -= 1

        response.body_iterator = limited_body()
        return response
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "issuelens-api", "status": "ok"}


@app.get("/demo/cases")
def demo_cases() -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    return {"cases": [{key: case[key] for key in ("case_id", "title", "outcome", "scenario")}
                      for case in manifest["cases"]]}


@app.get("/demo/cases/{case_id}")
def demo_case(case_id: str) -> dict[str, Any]:
    manifest = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    case = next((item for item in manifest["cases"] if item["case_id"] == case_id), None)
    if case is None:
        raise HTTPException(
            status_code=404, detail="Demo case not found. Choose one of the five listed cases."
        )
    try:
        run = json.loads((DATA_DIRECTORY / case["run"]).read_text(encoding="utf-8"))
        patches = json.loads(
            (DATA_DIRECTORY / "patch-evaluation" / "report.json").read_text(encoding="utf-8")
        )["cases"]
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503, detail="Replay unavailable. Please try again later."
        ) from exc
    patch = next((item for item in patches if item["case_id"] == case_id), None)
    return {"case": {key: case[key] for key in ("case_id", "title", "outcome", "scenario")},
            "run": run, "patch_validation": patch}


@app.get("/demo/cases/{case_id}/progress/{after}")
def demo_progress(case_id: str, after: int) -> dict[str, Any]:
    run = demo_case(case_id)["run"]
    return {"events": [event for event in run["events"] if event["sequence"] > after],
            "complete": True}


@app.get("/demo/cases/{case_id}/events")
def demo_events(case_id: str) -> StreamingResponse:
    global DEMO_ACTIVE_STREAMS
    run = demo_case(case_id)["run"]
    with DEMO_STREAM_LOCK:
        if int(os.getenv("ISSUELENS_DEMO_MAX_STREAMS", "4")) <= DEMO_ACTIVE_STREAMS:
            raise HTTPException(
                status_code=429,
                detail="All demo streams are busy. Wait and try again.",
                headers={"Retry-After": "1"},
            )
        DEMO_ACTIVE_STREAMS += 1

    def stream() -> Any:
        global DEMO_ACTIVE_STREAMS
        try:
            for event in run["events"]:
                yield f"event: trace\ndata: {json.dumps(event)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            with DEMO_STREAM_LOCK:
                DEMO_ACTIVE_STREAMS -= 1

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/demo/cases/{case_id:path}")
def invalid_demo_case(case_id: str) -> None:
    raise HTTPException(
        status_code=404, detail="Demo case not found. Choose one of the five listed cases."
    )


@app.get("/cases/{case_id}")
def review_case(case_id: str) -> dict[str, object]:
    if re.fullmatch(r"[a-z0-9-]+", case_id) is None:
        raise HTTPException(status_code=404, detail="Investigation case not found")

    case_path = CASES_DIRECTORY / f"{case_id}.json"
    if not case_path.is_file():
        raise HTTPException(status_code=404, detail="Investigation case not found")

    case: dict[str, Any] = json.loads(case_path.read_text(encoding="utf-8"))
    schema_version = case.pop("schema_version", None)
    errors = (
        validate_case({"schema_version": schema_version, **case})
        if "items" in case["allowed_evidence"]
        else []
    )
    case["validation_status"] = "valid" if schema_version == "1.0" and not errors else "invalid"
    if errors:
        case["validation_errors"] = errors
    return case


@app.get("/retrieval/cases/{case_id}")
def review_retrieval(case_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[a-z0-9-]+", case_id) is None:
        raise HTTPException(status_code=404, detail="Retrieval result not found")

    result_path = RETRIEVAL_DIRECTORY / case_id / "result.json"
    if not result_path.is_file():
        raise HTTPException(status_code=404, detail="Retrieval result not found")
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    return result


@app.get("/retrieval/comparisons/{case_id}")
def review_retrieval_comparison(case_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[a-z0-9-]+", case_id) is None:
        raise HTTPException(status_code=404, detail="Retrieval comparison not found")

    result_path = RETRIEVAL_DIRECTORY / case_id / "comparison.json"
    if not result_path.is_file():
        raise HTTPException(status_code=404, detail="Retrieval comparison not found")
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    return result


@app.get("/retrieval/three-way/{case_id}")
def review_reranker_comparison(case_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[a-z0-9-]+", case_id) is None:
        raise HTTPException(status_code=404, detail="Reranker comparison not found")

    result_path = RETRIEVAL_DIRECTORY / case_id / "three-way.json"
    if not result_path.is_file():
        raise HTTPException(status_code=404, detail="Reranker comparison not found")
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    return result


@app.get("/reports/three-way/{case_id}")
def review_three_way_report(case_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[a-z0-9-]+", case_id) is None:
        raise HTTPException(status_code=404, detail="Investigation report not found")
    path = REPORT_DIRECTORY / case_id / "report.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Investigation report not found")
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result


@app.get("/investigations/{case_id}")
def review_investigation(case_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[a-z0-9-]+", case_id) is None:
        raise HTTPException(status_code=404, detail="Investigation result not found")
    path = INVESTIGATION_DIRECTORY / case_id / "run.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Investigation result not found")
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result
