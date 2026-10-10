import os

os.environ["CGS_AI_SKIP_STARTUP"] = "1"

from fastapi.testclient import TestClient

from app.core.config import settings
from main import app

client = TestClient(app)


def test_public_health_is_minimal():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_diagnostics_require_the_internal_key():
    response = client.get("/internal/diagnostics")
    assert response.status_code == 401


def test_diagnostics_do_not_return_secrets():
    response = client.get(
        "/internal/diagnostics",
        headers={"X-Internal-Service-Key": settings.CGS_INTERNAL_SERVICE_KEY},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "base_url" not in body
    assert "api_key" not in body
    assert "openrouter_configured" not in body
    assert isinstance(body["llm_configured"], bool)
