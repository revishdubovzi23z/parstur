"""ROADMAP Stage 10.7z — the Vite/Vue 3/TS SPA is now THE frontend.

`main.py` serves `frontend/dist/index.html` from an explicit `GET /`
handler and mounts `frontend/dist/assets` at `/assets`. The legacy
CDN-driven `index.html` at the repo root has been deleted. These
tests pin:

  * `GET /` serves the SPA shell with a Vite-emitted `<script
    type=\"module\" src=\"/assets/...\">`,
  * the legacy CDN markers (Vue 3 from unpkg, Tailwind Play,
    SortableJS) are gone,
  * a fresh checkout without `npm run build` returns 503 + an inline
    instructions page (rather than a bare 404 / a crashed import),
  * `/api/*`, `/health`, `/sw.js` are still routed normally even with
    the asset mount registered.
"""

from __future__ import annotations

import builtins
import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient


def _reload_app(monkeypatch):
    # Clearing AUTH_* keeps the test independent of whatever was set in
    # the host environment. `/`, `/manifest.json`, `/favicon.png`, `/sw.js`
    # are all explicitly allow-listed by `auth_middleware`.
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)
    import main

    importlib.reload(main)
    return main


def test_root_serves_spa_shell(monkeypatch) -> None:
    """`GET /` must return the Vite-built SPA shell.

    The legacy index.html shipped a Vue 3 CDN <script> and Tailwind
    Play — neither must appear in the response any more. The SPA
    bundles them inside `/assets/index-*.js` instead.
    """
    main = _reload_app(monkeypatch)
    dist = Path(main._FRONTEND_DIST)
    if not (dist / "index.html").exists():
        # Fresh checkout / no `npm run build` yet — skip rather than
        # fail. CI always builds before pytest runs (see ci.yml).
        import pytest

        pytest.skip(f"{dist}/index.html not built; run `npm run build` in frontend/")

    client = TestClient(main.app)
    r = client.get("/")
    assert r.status_code == 200, r.text
    body = r.text
    # SPA shell markers.
    assert '<div id="app"' in body
    assert 'src="/assets/' in body, "vite base should rewrite asset URLs to /assets/"
    # Legacy CDN markers must be gone (10.7z).
    assert "cdn.tailwindcss.com" not in body
    assert "unpkg.com/vue" not in body
    assert "sortablejs" not in body.lower()


def test_root_returns_build_instructions_when_dist_missing(monkeypatch) -> None:
    """The SPA shell lives in `frontend/dist/index.html`. When that
    file isn't there (fresh checkout / partial build), `/` must return
    a 503 with a clear "please run `npm run build`" page — a bare 404
    confused new contributors who'd just cloned the repo and started
    `uvicorn` without realising the SPA needs a build step.
    """
    main = _reload_app(monkeypatch)

    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        # Pretend the SPA shell isn't built yet. Everything else
        # (sw.js, favicon, the test logfile, etc.) keeps working.
        spa_html = os.path.join(main._FRONTEND_DIST, "index.html")
        if isinstance(file, str | bytes | os.PathLike) and os.fspath(file) == spa_html:
            raise FileNotFoundError(spa_html)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    client = TestClient(main.app)
    r = client.get("/")
    assert r.status_code == 503
    body = r.text
    assert "npm run build" in body
    assert "frontend" in body
    # The fallback must not pretend to be the real SPA shell — e.g.
    # it shouldn't contain a #app mount-point that Vue would try to
    # bind to and then immediately crash on missing JS.
    assert '<div id="app"' not in body


def test_api_routes_take_precedence_over_static_mount(monkeypatch) -> None:
    """The static mount lives at `/` so it'd be tempting to think it
    shadows `/api/*` etc. — pin that it doesn't. `/health` is the
    cheapest unauthenticated API to probe (returns JSON, not HTML).
    """
    main = _reload_app(monkeypatch)
    client = TestClient(main.app)
    r = client.get("/health")
    assert r.status_code == 200, r.text
    # /health returns JSON, not HTML. If the mount were shadowing it,
    # we'd get the SPA index.html instead (or a 404 if dist is missing).
    assert r.headers.get("content-type", "").startswith("application/json")
    body = r.json()
    # See main.py:health() — the payload exposes a `db` block with
    # liveness state. Matching on a known key proves the JSON handler
    # ran rather than the static mount intercepting.
    assert "db" in body


def test_sw_js_substitutes_precache_list(monkeypatch) -> None:
    """`/sw.js` must substitute `__SW_PRECACHE__` with the actual list
    of assets so the SW can precache the SPA shell on install.
    """
    main = _reload_app(monkeypatch)
    dist = Path(main._FRONTEND_DIST)
    if not (dist / "index.html").exists():
        import pytest

        pytest.skip(f"{dist}/index.html not built; run `npm run build` in frontend/")

    client = TestClient(main.app)
    r = client.get("/sw.js")
    assert r.status_code == 200, r.text
    body = r.text
    # Placeholders must be substituted.
    assert "__SW_VERSION__" not in body
    assert "__SW_PRECACHE__" not in body
    # The precache list always includes the SPA shell.
    assert '"/"' in body
    assert "/manifest.json" in body
    # And at least one /assets/ entry from the build.
    assert "/assets/" in body


def test_assets_path_is_not_auth_gated(monkeypatch) -> None:
    """ROADMAP 10.7z — the SPA's hashed `/assets/*` bundle must load
    BEFORE login, otherwise the user can't render the login form. The
    auth middleware previously gated `/assets/`; this test pins it
    open.

    `_auth_enabled` is captured at routes.auth import-time, so we have
    to reload that module first (with the env in place) and only THEN
    reload main, otherwise the gate is silently disabled and the test
    becomes meaningless.
    """
    monkeypatch.setenv("AUTH_USER", "tester")
    monkeypatch.setenv("AUTH_PASS", "secret")
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)

    import routes.auth as auth_mod
    import settings as settings_mod

    importlib.reload(settings_mod)
    importlib.reload(auth_mod)
    import main

    importlib.reload(main)
    assert auth_mod._auth_enabled, "test precondition: auth must be enabled"

    dist = Path(main._FRONTEND_DIST)
    if not (dist / "index.html").exists():
        import pytest

        pytest.skip(f"{dist}/index.html not built; run `npm run build` in frontend/")

    # Pick whatever asset Vite emitted. We don't care about the name —
    # only that it isn't 401'd by the middleware.
    assets = dist / "assets"
    candidate = next(assets.iterdir())
    client = TestClient(main.app)
    r = client.get(f"/assets/{candidate.name}")
    assert (
        r.status_code == 200
    ), f"/assets/{candidate.name} must be public; got {r.status_code} {r.text!r}"
    # And a sibling /api/* without a Bearer token must 401 so we know
    # the gate actually ran (rather than auth being silently off).
    r = client.get("/api/collections")
    assert r.status_code == 401
