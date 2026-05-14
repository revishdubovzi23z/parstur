"""Smoke tests for the host allow-list used by `/api/subtitle_proxy`.

Driven via FastAPI's TestClient so the routing wiring + 403 behaviour
stay observable. PR 5 of the kino.pub integration added kino.pub CDN
hosts to the allow-list; these tests pin that behaviour without
hitting the network — `requests.get` is monkey-patched to a stub.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)

    # `_auth_enabled` is captured at routes.auth import-time. Other
    # tests may have left it True, so we must reload `settings` +
    # `routes.auth` BEFORE reloading `main`, otherwise the middleware
    # still gates `/api/subtitle_proxy` behind a 401.
    import settings as settings_mod

    settings_mod.reload_settings()
    import routes.auth as auth_mod

    importlib.reload(auth_mod)

    import main

    importlib.reload(main)

    from db import Database

    main.db = Database(str(tmp_path / "test.db"))
    main.db.init_schema()
    return TestClient(main.app)


class _FakeResp:
    def __init__(self, body: bytes = b"WEBVTT\n\n", status: int = 200) -> None:
        self.content = body
        self.status_code = status
        self.headers = {"Content-Type": "text/vtt"}


def test_subtitle_proxy_rejects_disallowed_host(client: TestClient) -> None:
    r = client.get("/api/subtitle_proxy", params={"url": "https://evil.com/subs.vtt"})
    assert r.status_code == 403
    assert "not allowed" in r.json()["error"]


@pytest.mark.parametrize(
    "host",
    [
        "kino.pub",
        "cdn.kino.pub",
        "service-kp.com",
        "cdn.service-kp.com",
        "s1.kp.cdn.consoto.sbs",
    ],
)
def test_subtitle_proxy_allows_kinopub_hosts(client: TestClient, monkeypatch, host: str) -> None:
    """PR 5 — kino.pub subtitles must round-trip through subtitle_proxy
    so `<track>` elements can render captions cross-origin."""
    captured: dict[str, Any] = {}

    def _fake_get(url: str, *a: Any, **k: Any) -> _FakeResp:
        captured["url"] = url
        return _FakeResp()

    import requests as _req

    monkeypatch.setattr(_req, "get", _fake_get, raising=True)
    target = f"https://{host}/path/to/subs.vtt"
    r = client.get("/api/subtitle_proxy", params={"url": target})
    assert r.status_code == 200, r.text
    assert captured["url"] == target
    assert r.headers["content-type"].startswith("text/vtt")


def test_subtitle_proxy_still_allows_rezka_hosts(client: TestClient, monkeypatch) -> None:
    """Regression guard — extending the allow-list must not remove
    the original Rezka hosts."""

    def _fake_get(url: str, *a: Any, **k: Any) -> _FakeResp:
        return _FakeResp()

    import requests as _req

    monkeypatch.setattr(_req, "get", _fake_get, raising=True)
    r = client.get(
        "/api/subtitle_proxy",
        params={"url": "https://prx.rezka.ag/sub.vtt"},
    )
    assert r.status_code == 200
