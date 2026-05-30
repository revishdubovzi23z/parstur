"""Stage 13 — unit tests for cloud-sync safety logic.

Network- and libsql-free: these cover the parts of ``CloudSync`` that
must behave correctly even when the optional ``libsql`` dependency and a
real Turso remote are absent — the enabled/gating contract, the
disabled-noop responses, and the ``_local_has_data`` guard that protects
a populated local DB from being overwritten by an empty remote.

Settings are built with ``_env_file=None`` so neither the developer's
shell env nor a committed ``.env`` leaks cloud credentials into these
assertions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import cloud_sync as cs_mod
from cloud_sync import CloudSync
from settings import Settings, reload_settings


@pytest.fixture(autouse=True)
def _restore_settings_after_test():
    """Rebuild the cached settings singleton after each test."""
    yield
    reload_settings()


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides):
    """Point cloud_sync's module-level ``settings`` at a fresh, env-free
    Settings instance with the given field overrides."""
    s = Settings(_env_file=None, **overrides)
    monkeypatch.setattr(cs_mod, "settings", s)
    return s


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    assert CloudSync().enabled is False


def test_enabled_requires_provider_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Provider alone is not enough.
    _settings(monkeypatch, cloud_provider="turso")
    assert CloudSync().enabled is False
    # URL + token complete the contract.
    _settings(
        monkeypatch,
        cloud_provider="turso",
        cloud_turso_url="libsql://example.turso.io",
        cloud_turso_token="tok-123",
    )
    assert CloudSync().enabled is True


def test_push_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    assert CloudSync().push()["status"] == "disabled"


def test_pull_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    assert CloudSync().pull()["status"] == "disabled"


def _make_db(path: Path, *, with_item: bool) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)")
        if with_item:
            conn.execute("INSERT INTO items (title) VALUES ('x')")
        conn.commit()
    finally:
        conn.close()


def test_local_has_data_true_when_items_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _settings(monkeypatch)
    db = tmp_path / "app.db"
    _make_db(db, with_item=True)
    assert CloudSync()._local_has_data(str(db)) is True


def test_local_has_data_false_when_no_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _settings(monkeypatch)
    db = tmp_path / "app.db"
    _make_db(db, with_item=False)
    assert CloudSync()._local_has_data(str(db)) is False


def test_local_has_data_false_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _settings(monkeypatch)
    assert CloudSync()._local_has_data(str(tmp_path / "does-not-exist.db")) is False
