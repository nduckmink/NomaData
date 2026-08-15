"""M0 acceptance: the health endpoint proves the skeleton boots end-to-end."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nomadata.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.0.1"
    assert body["checks"]["api"] == "ok"


def test_health_registries_wired_but_empty() -> None:
    # M0: registries exist and are wired, but nothing is registered yet.
    body = client.get("/api/v1/health").json()
    assert body["providers"] == []
    assert body["data_sources"] == []
