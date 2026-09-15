from fastapi.testclient import TestClient
from issuelens.api import app


def test_health_reports_service_status() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"service": "issuelens-api", "status": "ok"}
