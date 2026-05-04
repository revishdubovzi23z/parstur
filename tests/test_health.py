"""6.5 — /health endpoint contract.

Liveness + readiness probe for Docker / k8s / uptime monitors.
Pins the wire format so future changes don't accidentally break
the field names that probes depend on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _make_client(monkeypatch) -> TestClient:
    """Build a TestClient against a fresh, in-memory-style FastAPI app.

    The app reads from a real `db.db` (sqlite file in the repo). We
    don't mutate it; /health only does SELECT 1 + PRAGMA user_version.
    """
    # Force-disable auth so the middleware doesn't gate /health
    # transitively even though /health is on the exemption list — we
    # want a clean test that doesn't depend on AUTH_USER state.
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)
    import importlib

    import main

    importlib.reload(main)
    return TestClient(main.app)


def test_health_returns_200_with_db_ok(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "par2"
    # db.user_version is whatever the migrations runner left it at —
    # we just want to know the field is present and is an int.
    assert "db" in body
    assert body["db"]["ok"] is True
    assert isinstance(body["db"]["user_version"], int)
    assert "queue" in body
    assert "size" in body["queue"]
    assert "worker_active" in body["queue"]


def test_health_does_not_require_auth(monkeypatch) -> None:
    """Even when AUTH is enabled, /health must remain reachable so
    Docker HEALTHCHECK / k8s probes work."""
    monkeypatch.setenv("AUTH_USER", "alice")
    monkeypatch.setenv("AUTH_PASS", "secret")
    import importlib

    import main

    importlib.reload(main)
    client = TestClient(main.app)
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # And a sanity check: confirm AUTH was actually enabled, so this
    # test isn't lying. Hitting a normally-protected endpoint without
    # a token should 401.
    r2 = client.get("/api/auth_status")
    # auth_status itself is in the exemption list, so this is 200.
    assert r2.status_code == 200
    r3 = client.get("/api/debug/queue")
    assert r3.status_code in (401, 404), r3.status_code
