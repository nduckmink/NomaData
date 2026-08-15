"""Semantic endpoint tests (hermetic — no live database or AI required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nomadata.main import app

client = TestClient(app)


def test_suggest_unknown_source_returns_404() -> None:
    # No data source registered in tests → suggest can't introspect a schema.
    assert client.post("/api/v1/datasources/nope/semantic/suggest").status_code == 404


def test_get_semantic_unavailable_without_app_db() -> None:
    # App DB is disabled in tests → semantic model service is unconfigured (503).
    assert client.get("/api/v1/datasources/nope/semantic").status_code == 503


def test_delete_semantic_unavailable_without_app_db() -> None:
    assert client.delete("/api/v1/datasources/nope/semantic").status_code == 503


def test_semantic_overview_unavailable_without_app_db() -> None:
    assert client.get("/api/v1/semantic").status_code == 503
