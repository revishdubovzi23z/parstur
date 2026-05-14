"""6.7 — Rezka session re-login on detected logout.

Pins the contract of `_rezka_session_dead` (the sniffer) and
`_rezka_request` (the requests wrapper that re-logs in on dead
session). Done with monkeypatching rather than a network mock
because the helpers are pure dispatch — no real HTTP needed.
"""

from __future__ import annotations

from types import SimpleNamespace

from runtime import rezka


def _resp(*, status=200, headers=None, body=b"", url="https://rezka.ag/foo"):
    return SimpleNamespace(
        status_code=status,
        headers=headers or {},
        content=body,
        url=url,
    )


# ── _rezka_session_dead ─────────────────────────────────────────────


def test_dead_on_401() -> None:
    assert rezka._rezka_session_dead(_resp(status=401)) is True


def test_dead_on_403() -> None:
    assert rezka._rezka_session_dead(_resp(status=403)) is True


def test_dead_on_redirect_to_login() -> None:
    r = _resp(status=302, headers={"Location": "https://rezka.ag/login.html"})
    assert rezka._rezka_session_dead(r) is True


def test_dead_on_final_url_login() -> None:
    r = _resp(status=200, url="https://rezka.ag/login.html")
    assert rezka._rezka_session_dead(r) is True


def test_dead_on_login_popup_html() -> None:
    body = b"<html>" + b" " * 300 + b'<form><input name="login_name" /></form>'
    assert rezka._rezka_session_dead(_resp(status=200, body=body)) is True


def test_alive_on_200_with_normal_html() -> None:
    body = b"<html>" + b"<div class='b-favorites_content'></div>" + b" " * 500
    assert rezka._rezka_session_dead(_resp(status=200, body=body)) is False


def test_alive_on_short_body_without_login_markers() -> None:
    # Empty / very short bodies must NOT be flagged dead — that
    # would re-login on every 204 / empty AJAX response.
    assert rezka._rezka_session_dead(_resp(status=200, body=b"")) is False
    assert rezka._rezka_session_dead(_resp(status=200, body=b"{}")) is False


def test_none_response_treated_as_alive() -> None:
    assert rezka._rezka_session_dead(None) is False


# ── _rezka_request retry loop ───────────────────────────────────────


def test_no_session_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(rezka, "rezka_session", None)
    out = rezka._rezka_request("GET", "https://rezka.ag/", cookies={})
    assert out is None


def test_alive_response_passes_through(monkeypatch) -> None:
    fake_session = SimpleNamespace(cookies={"x": "1"})
    monkeypatch.setattr(rezka, "rezka_session", fake_session)

    calls = []
    happy = _resp(status=200, body=b"<div>ok</div>" + b" " * 400)

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return happy

    import requests

    monkeypatch.setattr(requests, "request", fake_request)
    out = rezka._rezka_request("GET", "https://rezka.ag/foo", cookies={})
    assert out is happy
    # No re-login means exactly one request.
    assert len(calls) == 1


def test_dead_response_triggers_relogin_and_retry(monkeypatch) -> None:
    fake_session = SimpleNamespace(cookies={"old": "1"})
    monkeypatch.setattr(rezka, "rezka_session", fake_session)

    calls = []
    dead = _resp(status=401)
    fresh = _resp(status=200, body=b"<div>ok</div>" + b" " * 400)

    def fake_request(method, url, **kwargs):
        calls.append((method, url, dict(kwargs.get("cookies") or {})))
        return dead if len(calls) == 1 else fresh

    relogin_calls = []

    def fake_init():
        relogin_calls.append(1)
        # Pretend the new session has fresh cookies.
        rezka.rezka_session = SimpleNamespace(cookies={"new": "2"})

    import requests

    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(rezka, "_init_rezka_session", fake_init)

    out = rezka._rezka_request("POST", "https://rezka.ag/ajax/favorites/", cookies={"old": "1"})
    assert out is fresh
    # Re-login was triggered exactly once.
    assert len(relogin_calls) == 1
    # Request was retried with fresh cookies.
    assert len(calls) == 2
    assert calls[1][2] == {"new": "2"}


def test_relogin_failure_returns_dead_response(monkeypatch) -> None:
    """If re-login itself fails, we must still return the original
    dead response rather than raising — callers handle dead
    responses themselves."""
    fake_session = SimpleNamespace(cookies={"old": "1"})
    monkeypatch.setattr(rezka, "rezka_session", fake_session)

    dead = _resp(status=401)

    def fake_request(method, url, **kwargs):
        return dead

    def fake_init():
        raise RuntimeError("re-login failed")

    import requests

    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(rezka, "_init_rezka_session", fake_init)

    out = rezka._rezka_request("GET", "https://rezka.ag/", cookies={})
    assert out is dead
