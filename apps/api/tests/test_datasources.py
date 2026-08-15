"""Data source endpoint tests (hermetic — no live database required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nomadata.main import app

client = TestClient(app)


def test_list_data_sources_empty_by_default() -> None:
    # No NOMADATA_DS_* configured in tests → registry is empty.
    resp = client.get("/api/v1/datasources")
    assert resp.status_code == 200
    assert resp.json() == []


def test_unknown_data_source_returns_404() -> None:
    assert client.get("/api/v1/datasources/nope/schema").status_code == 404
    assert client.post("/api/v1/datasources/nope/test").status_code == 404
    assert client.get("/api/v1/datasources/nope/tables").status_code == 404
    assert client.get("/api/v1/datasources/nope/tables/t").status_code == 404


def test_list_tables_rejects_bad_paging() -> None:
    # limit above the cap / negative offset — 422 before the source lookup.
    resp = client.get("/api/v1/datasources/nope/tables", params={"limit": 999})
    assert resp.status_code == 422
    resp = client.get("/api/v1/datasources/nope/tables", params={"offset": -1})
    assert resp.status_code == 422
