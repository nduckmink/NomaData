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


def test_dev_cors_allows_any_localhost_port() -> None:
    # Next.js may fall back to :3001 when :3000 is taken — dev CORS must allow it.
    resp = client.get("/api/v1/health", headers={"Origin": "http://localhost:3001"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3001"
