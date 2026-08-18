"""Semantic endpoint tests (hermetic — no live database or AI required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nomadata.main import app

client = TestClient(app)


def test_get_semantic_unavailable_without_app_db() -> None:
    # App DB is disabled in tests → semantic model service is unconfigured (503).
    assert client.get("/api/v1/datasources/nope/semantic").status_code == 503


def test_delete_semantic_unavailable_without_app_db() -> None:
    assert client.delete("/api/v1/datasources/nope/semantic").status_code == 503


def test_semantic_overview_unavailable_without_app_db() -> None:
    assert client.get("/api/v1/semantic").status_code == 503


def test_generate_job_unavailable_without_app_db() -> None:
    assert client.post("/api/v1/datasources/nope/semantic/generate").status_code == 503


def test_get_job_unavailable_without_app_db() -> None:
    assert client.get("/api/v1/datasources/nope/semantic/jobs/abc").status_code == 503


def test_context_unavailable_without_app_db() -> None:
    assert client.get("/api/v1/datasources/nope/semantic/context").status_code == 503


def test_entity_draft_requires_an_ai_provider() -> None:
    response = client.post(
        "/api/v1/datasources/nope/semantic/entities/draft",
        json={"prompt": "a lookup table", "entity_key": "public.x"},
    )
    assert response.status_code == 409


def test_metric_draft_requires_an_ai_provider() -> None:
    # No provider configured in tests → a clear 409, not a 500.
    response = client.post(
        "/api/v1/datasources/nope/semantic/metrics/draft",
        json={"prompt": "total revenue"},
    )
    assert response.status_code == 409
    assert "AI provider" in response.json()["detail"]
