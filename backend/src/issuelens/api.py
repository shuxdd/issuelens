import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException

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

    case: dict[str, object] = json.loads(case_path.read_text(encoding="utf-8"))
    case["validation_status"] = (
        "valid" if case.pop("schema_version", None) == "1.0" else "invalid"
    )
    return case
