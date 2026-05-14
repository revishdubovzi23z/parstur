"""Tests for `runtime.kinopub` content helpers (PR 3 of the kino.pub stack).

Covers `search()` and `get_stream_info()` plus the shared
`_authenticated_client()` gate. All HTTP is faked through a stand-in
that mirrors `KinopubClient.search` / `KinopubClient.get_item`; no
network is required.
"""

from __future__ import annotations

from typing import Optional

import pytest

import db as db_module
import runtime.kinopub as rk
from kinopub_client import KinopubAuthError


@pytest.fixture(autouse=True)
def _wire_tmp_db(tmp_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "db", tmp_db, raising=True)
    monkeypatch.setattr(rk, "db", tmp_db, raising=True)
    rk._pending.clear()
    yield


@pytest.fixture(autouse=True)
def _enable_kinopub(monkeypatch: pytest.MonkeyPatch):
    """Same shape as test_runtime_kinopub.py — flip the master switch
    on and re-bind the module-level `settings` to the live instance
    so other tests' `reload_settings()` doesn't poison us."""
    import settings as settings_mod

    current_settings = settings_mod.settings
    monkeypatch.setattr(rk, "settings", current_settings, raising=True)
    monkeypatch.setattr(current_settings, "kinopub_enabled", True, raising=True)


class _FakeClient:
    """Just enough of `KinopubClient` for the content helpers."""

    def __init__(
        self,
        *,
        search_response: Optional[list[dict]] = None,
        item_response: Optional[dict] = None,
    ) -> None:
        self.search_response = search_response or []
        self.item_response = item_response or {}
        self.search_calls: list[dict] = []
        self.get_item_calls: list[int] = []

    def search(
        self,
        query: str,
        *,
        type_: Optional[str] = None,
        year: Optional[int] = None,
        limit: int = 25,
    ) -> list[dict]:
        self.search_calls.append({"query": query, "type_": type_, "year": year, "limit": limit})
        return self.search_response

    def get_item(self, item_id: int) -> dict:
        self.get_item_calls.append(item_id)
        return self.item_response


# ── _authenticated_client gate ──────────────────────────────────────────


def test_search_requires_enabled(monkeypatch) -> None:
    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    with pytest.raises(KinopubAuthError):
        rk.search("Inception")


def test_search_requires_token(monkeypatch) -> None:
    # No row in kinopub_auth → current_token() returns None.
    with pytest.raises(KinopubAuthError):
        rk.search("Inception")


def test_get_stream_info_requires_enabled(monkeypatch) -> None:
    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    with pytest.raises(KinopubAuthError):
        rk.get_stream_info(42)


def test_get_stream_info_requires_token() -> None:
    with pytest.raises(KinopubAuthError):
        rk.get_stream_info(42)


# ── search() shape ──────────────────────────────────────────────────────


def test_search_passes_filters_to_client() -> None:
    fc = _FakeClient(search_response=[])
    rk.search("Inception", year=2010, type_="movie", limit=5, client=fc)
    assert fc.search_calls == [{"query": "Inception", "type_": "movie", "year": 2010, "limit": 5}]


def test_search_maps_minimal_entry() -> None:
    fc = _FakeClient(
        search_response=[{"id": 12345, "title": "Inception", "year": 2010, "type": "movie"}]
    )
    out = rk.search("Inception", client=fc)
    assert out == [
        {
            "id": 12345,
            "title": "Inception",
            "year": 2010,
            "type": "movie",
            "url": "https://kino.pub/item/12345",
            "poster": None,
        }
    ]


def test_search_extracts_poster_with_fallback_order() -> None:
    fc = _FakeClient(
        search_response=[
            {
                "id": 1,
                "title": "A",
                "year": 2020,
                "type": "movie",
                "posters": {
                    "small": "https://cdn/small.jpg",
                    "medium": "https://cdn/medium.jpg",
                    "big": "https://cdn/big.jpg",
                },
            }
        ]
    )
    out = rk.search("A", client=fc)
    # medium preferred over small / big.
    assert out[0]["poster"] == "https://cdn/medium.jpg"


def test_search_filters_unusable_entries() -> None:
    fc = _FakeClient(
        search_response=[
            {"id": 1, "title": "ok"},
            "not a dict",
            {"title": "no id"},  # ← dropped
            {"id": 2, "title": "also ok"},
        ]
    )
    out = rk.search("x", client=fc)
    assert [e["id"] for e in out] == [1, 2]


# ── get_stream_info() shape ─────────────────────────────────────────────


_MOVIE_BODY = {
    "id": 555,
    "title": "Inception",
    "year": 2010,
    "type": "movie",
    "videos": [
        {
            "number": 1,
            "duration": 8880,
            "files": [
                {
                    "url": "https://cdn.kino.pub/inception-720.mp4",
                    "quality": "720p",
                    "codec": "h264",
                },
                {
                    "url": "https://cdn.kino.pub/inception-1080.mp4",
                    "quality": "1080p",
                },
            ],
            "audios": [
                {"lang": "ru", "type": "AVO", "author": "Гоблин"},
                {"lang": {"code": "en", "title": "English"}},
            ],
            "subtitles": [
                {
                    "url": "https://cdn.kino.pub/inception-ru.vtt",
                    "lang": "ru",
                    "shift": 0,
                    "embed": False,
                },
                # Missing URL → skipped.
                {"lang": "fr"},
            ],
        }
    ],
}


def test_get_stream_info_maps_movie_body() -> None:
    fc = _FakeClient(item_response=_MOVIE_BODY)
    out = rk.get_stream_info(555, client=fc)
    assert fc.get_item_calls == [555]
    assert out["id"] == 555
    assert out["title"] == "Inception"
    assert out["year"] == 2010
    assert out["type"] == "movie"
    assert out["url"] == "https://kino.pub/item/555"
    assert out["seasons"] == []
    assert len(out["videos"]) == 1
    video = out["videos"][0]
    assert video["number"] == 1
    assert video["duration"] == 8880
    qualities = [f["quality"] for f in video["files"]]
    assert qualities == ["720p", "1080p"]
    assert video["audios"][0]["lang"] == "ru"
    assert video["audios"][0]["type"] == "AVO"
    assert video["audios"][1]["lang"] == "en"
    # The bad subtitle entry (no url) was dropped.
    assert len(video["subtitles"]) == 1
    assert video["subtitles"][0]["url"].endswith("ru.vtt")


_SERIAL_BODY = {
    "id": 100,
    "title": "Stranger Things",
    "year": 2016,
    "type": "serial",
    "videos": [],
    "seasons": [
        {
            "number": 1,
            "episodes": [
                {
                    "number": 1,
                    "title": "Chapter One",
                    "files": [{"url": "https://cdn/s01e01.mp4", "quality": "720p"}],
                    "audios": [],
                    "subtitles": [],
                },
                {
                    "number": 2,
                    "title": "Chapter Two",
                    "files": [{"url": "https://cdn/s01e02.mp4", "quality": "720p"}],
                },
            ],
        },
        {
            "number": 2,
            "episodes": [
                {
                    "number": 1,
                    "title": "MADMAX",
                    "files": [{"url": "https://cdn/s02e01.mp4", "quality": "1080p"}],
                }
            ],
        },
    ],
}


def test_get_stream_info_maps_serial_seasons() -> None:
    fc = _FakeClient(item_response=_SERIAL_BODY)
    out = rk.get_stream_info(100, client=fc)
    assert out["videos"] == []
    assert [s["number"] for s in out["seasons"]] == [1, 2]
    assert [len(s["episodes"]) for s in out["seasons"]] == [2, 1]
    s1_e1 = out["seasons"][0]["episodes"][0]
    assert s1_e1["number"] == 1
    assert s1_e1["title"] == "Chapter One"
    assert s1_e1["files"][0]["url"] == "https://cdn/s01e01.mp4"


def test_get_stream_info_ignores_non_dict_entries() -> None:
    fc = _FakeClient(
        item_response={
            "id": 1,
            "title": "x",
            "type": "movie",
            "videos": ["not a dict", {"number": 1, "files": []}],
            "seasons": ["not a dict", {"number": 1, "episodes": ["also not"]}],
        }
    )
    out = rk.get_stream_info(1, client=fc)
    assert len(out["videos"]) == 1
    assert len(out["seasons"]) == 1
    assert out["seasons"][0]["episodes"] == []


def test_build_item_url_is_stable() -> None:
    assert rk._build_item_url(42) == "https://kino.pub/item/42"
