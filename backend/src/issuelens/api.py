from fastapi import FastAPI

app = FastAPI(title="IssueLens API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "issuelens-api", "status": "ok"}
