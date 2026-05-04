"""Smoke tests for the centralised settings module (5.5).

The settings module is the new single source of truth for env-driven
configuration. These tests pin the contract that everything that
calling code expects to find is reachable on the singleton, and that
reload_settings() actually picks up env changes (it's the test-only
escape hatch for the otherwise-cached singleton).
"""

from __future__ import annotations

import pytest

from settings import Settings, reload_settings


def test_defaults_present() -> None:
    s = Settings()
    # Auth defaults to off.
    assert s.auth_user == ""
    assert s.auth_pass == ""
    assert s.auth_pass_hash == ""
    # Rezka concurrency intentionally unset so the existing
    # config.json fallback remains the source of truth until the
    # operator opts in.
    assert s.rezka_concurrency is None
    # Sync ranges match the historical defaults so a fresh container
    # with no env behaves the same as a hand-pinned `os.getenv`.
    assert s.sync_min_year == 1900
    assert s.sync_max_year == 2099
    assert s.status_key == "sync_video"
    assert s.rutor_mirror == "https://rutor.info"
    assert s.debug is False
    assert s.log_level == "INFO"


def test_env_vars_are_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_USER", "alice")
    monkeypatch.setenv("AUTH_PASS_HASH", "pbkdf2_sha256$600000$...$abc")
    monkeypatch.setenv("REZKA_CONCURRENCY", "12")
    monkeypatch.setenv("SYNC_MIN_YEAR", "1950")
    s = reload_settings()
    assert s.auth_user == "alice"
    assert s.auth_pass_hash.startswith("pbkdf2_sha256$")
    assert s.rezka_concurrency == 12
    assert s.sync_min_year == 1950


def test_case_insensitive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # `case_sensitive=False` is essential because users sometimes
    # write `auth_user=` in their .env (lowercase).
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.setenv("auth_user", "bob")
    s = reload_settings()
    assert s.auth_user == "bob"


def test_invalid_concurrency_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Field has ge=1; pydantic should refuse to load.
    monkeypatch.setenv("REZKA_CONCURRENCY", "0")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        reload_settings()


def test_unknown_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # `extra=ignore` means PATH / HOME / random vars don't blow up.
    monkeypatch.setenv("PAR2_TOTALLY_UNKNOWN", "yes")
    s = reload_settings()  # must not raise
    assert s.auth_user is not None  # any field; just confirms model loaded


@pytest.fixture(autouse=True)
def _restore_settings_after_test() -> None:
    """Reset the global `settings` after each test that messed with
    env vars, so subsequent imports see a clean baseline.
    """
    yield
    reload_settings()
