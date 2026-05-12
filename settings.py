"""Centralised application configuration (item 5.5).

Before this module existed, every script/file pulled its own values
out of `os.getenv(...)` with ad-hoc defaults — tedious to audit and
impossible to type-check. Now everything goes through a single
pydantic-settings model. Calling code does:

    from settings import settings

    if settings.auth_user:
        ...
    delay = settings.sync_request_delay

Values come from (in order of precedence):

  1. Environment variables (and `.env` via python-dotenv, loaded
     elsewhere — pydantic-settings will also read `.env` directly
     thanks to `model_config.env_file`).
  2. Defaults defined here.

This is a *staged migration*: brand new code should import
`settings` directly. Existing modules still call `os.getenv(...)`
in places — those will be replaced incrementally so the migration
is reviewable in chunks (one file per commit) instead of a single
sweep.

Adding a new setting:
  1. Add a field below with a default and a docstring.
  2. Use `settings.<field>` from your module.
  3. Document the env var in `.env.example`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _AuthSettings(BaseSettings):
    """Authentication settings (read at app startup)."""

    auth_user: str = Field(default="", description="Username for /api/login.")
    auth_pass: str = Field(
        default="",
        description=(
            "Plain-text password (deprecated — set AUTH_PASS_HASH instead). "
            "If both AUTH_PASS and AUTH_PASS_HASH are set, the hash wins."
        ),
    )
    auth_pass_hash: str = Field(
        default="",
        description=(
            "pbkdf2_sha256 hash of the password. Generate with the helper snippet in SECURITY.md."
        ),
    )


class _RezkaSettings(BaseSettings):
    """HDRezka session + concurrency settings."""

    rezka_email: str = Field(default="", description="HDRezka login (optional).")
    rezka_password: str = Field(default="", description="HDRezka password (optional).")
    rezka_concurrency: int | None = Field(
        default=None,
        description=(
            "Override for the rezka_sync semaphore. Falls back to config.json -> 6 when unset."
        ),
        ge=1,
    )


class _SyncSettings(BaseSettings):
    """Per-script sync defaults (read by sync_job.py / fix_posters.py / ...)."""

    sync_min_year: int = Field(default=1900, ge=1800, le=2100)
    sync_max_year: int = Field(default=2099, ge=1800, le=2100)
    status_key: str = Field(
        default="sync_video",
        description=(
            "Process key used for stop_<key>.flag, checkpoint_<key>.json, "
            "progress_<key>.json. Set per-script by the launcher."
        ),
    )


class _ApiKeysSettings(BaseSettings):
    """External API credentials."""

    kinopoisk_api_key: str | None = Field(default=None)
    tmdb_api_key: str | None = Field(default=None)
    poiskkino_api_key: str | None = Field(default=None)


class _StorageSettings(BaseSettings):
    """Filesystem layout. Container deployments (5.8) override these
    so the SQLite DB and request-cache live on a mounted volume.

    `app_data_dir` is a hint; modules that already hard-code
    `app_data.db` / `api_cache.db` in the working directory should
    migrate to read these instead. The default keeps the historical
    "everything in CWD" layout so existing installs don't move."""

    app_data_dir: str = Field(
        default=".",
        description="Base directory for runtime state (DB, caches, progress files).",
    )
    db_path: str = Field(
        default="app_data.db",
        description="Path to the main SQLite database.",
    )
    api_cache_path: str = Field(
        default="api_cache.db",
        description="Path to the requests-cache SQLite backing store.",
    )
    rutor_mirror: str = Field(
        default="https://rutor.info",
        description="Rutor mirror URL (with scheme, no trailing slash).",
    )


class Settings(
    _AuthSettings,
    _RezkaSettings,
    _SyncSettings,
    _ApiKeysSettings,
    _StorageSettings,
):
    """Top-level merged settings.

    The class inherits from every per-domain mixin so that calling
    code only ever imports the one `settings` instance. Domain
    splitting is purely organisational.
    """

    # FastAPI-side runtime knobs.
    app_name: str = Field(
        default="Antigravity Tracker",
        description="The display name of the application.",
    )
    debug: bool = Field(
        default=False,
        description="Enables debug endpoints (e.g. /api/debug/queue).",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `extra=ignore` — let unrelated env vars (PATH, HOME, ...)
        # pass through without raising. Critical: pydantic-settings
        # would otherwise complain that the env contains values it
        # doesn't know about.
        extra="ignore",
        # Keep the API friendly: AUTH_USER / auth_user / Auth_User
        # all map to the same field.
        case_sensitive=False,
    )


# Singleton. Cheap to import — pydantic-settings does a single env
# scan when the model is first instantiated, then caches.
settings = Settings()


def reload_settings() -> Settings:
    """Force a re-read of the environment.

    Tests and CLI flag handlers use this to pick up monkey-patched
    env vars after the singleton was already created. In production
    code there's no reason to call this — just import `settings`.
    """
    global settings
    settings = Settings()
    return settings
