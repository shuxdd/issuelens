import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from issuelens.cases import validate_case

app = FastAPI(title="IssueLens API")
CASES_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "cases"


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "issuelens-api", "status": "ok"}


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
