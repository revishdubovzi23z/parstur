from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

import main


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)
    monkeypatch.delenv("REZKA_EMAIL", raising=False)
    monkeypatch.delenv("REZKA_PASSWORD", raising=False)
    monkeypatch.delenv("TMDB_API_TOKEN", raising=False)
    import settings as settings_module
    from routes import admin

    importlib.reload(settings_module)
    importlib.reload(admin)
    monkeypatch.setattr(admin, "_env_path", lambda: tmp_path / ".env")
    importlib.reload(main)
    return TestClient(main.app)


def test_credentials_status_masks_sensitive_values(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "REZKA_EMAIL=user@example.com\nREZKA_PASSWORD=secret\nTMDB_API_TOKEN=token\n",
        encoding="utf-8",
    )
    client = _client(monkeypatch, tmp_path)

    r = client.get("/api/settings/credentials")

    assert r.status_code == 200
    body = r.json()["credentials"]
    assert body["REZKA_EMAIL"] == {
        "configured": True,
        "value": "user@example.com",
    }
    assert body["REZKA_PASSWORD"] == {"configured": True, "value": ""}
    assert body["TMDB_API_TOKEN"] == {"configured": True, "value": ""}


def test_credentials_update_writes_env_and_reloads_settings(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    r = client.put(
        "/api/settings/credentials",
        json={
            "values": {
                "REZKA_EMAIL": "new@example.com",
                "REZKA_PASSWORD": "new-secret",
            }
        },
    )

    assert r.status_code == 200
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "REZKA_EMAIL='new@example.com'" in env_text
    assert "REZKA_PASSWORD='new-secret'" in env_text
    body = r.json()["credentials"]
    assert body["REZKA_EMAIL"]["value"] == "new@example.com"
    assert body["REZKA_PASSWORD"] == {"configured": True, "value": ""}
