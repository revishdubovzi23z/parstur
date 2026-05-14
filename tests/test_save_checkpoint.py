"""Tests for the atomic checkpoint writer (item 3.12).

The previous implementation truncated the file with 'w' and then
streamed JSON in. A kill mid-write left the file half-written and
load_checkpoint silently returned None — wiping the resume point.
The new implementation uses tempfile.mkstemp + os.fsync + os.replace
for atomicity. These tests:

  * confirm a normal save round-trips,
  * confirm an in-flight write of a NEW value does not leave a
    half-written FILE on disk (the old failure mode),
  * confirm the temp sidecar gets cleaned up on failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from script_utils import (
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test in its own working directory.

    save_checkpoint writes `checkpoint_<key>.json` relative to CWD,
    so tests need an isolated tmp dir to avoid clobbering each other
    or the project's real checkpoint files.
    """
    monkeypatch.chdir(tmp_path)


def test_round_trip() -> None:
    save_checkpoint("test", {"page": 5, "items": [1, 2, 3]})
    loaded = load_checkpoint("test")
    assert loaded == {"page": 5, "items": [1, 2, 3]}


def test_overwrites_previous_value() -> None:
    save_checkpoint("test", {"page": 1})
    save_checkpoint("test", {"page": 2})
    assert load_checkpoint("test") == {"page": 2}


def test_clear_removes_file() -> None:
    save_checkpoint("test", {"x": 1})
    assert os.path.exists("checkpoint_test.json")
    clear_checkpoint("test")
    assert not os.path.exists("checkpoint_test.json")


def test_atomic_write_does_not_corrupt_existing_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force os.replace to fail and verify the original file
    survives intact. This is the regression-test for item 3.12 —
    the bug used to be that the target file was truncated FIRST
    (with `open(..., 'w')`) and any failure between truncate and
    final flush left the user with garbage.
    """
    save_checkpoint("test", {"page": 7})
    original = load_checkpoint("test")

    real_replace = os.replace

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        save_checkpoint("test", {"page": 999})

    # Restore replace so cleanup_logic in tmp_path teardown works.
    monkeypatch.setattr(os, "replace", real_replace)

    # Original content must still be readable — the failed write
    # didn't touch it because the new content was staged in a
    # separate temp file that os.replace would have swapped in
    # atomically.
    surviving = load_checkpoint("test")
    assert surviving == original

    # And no orphan temp sidecar should be left lying around.
    sidecars = [f for f in os.listdir(".") if f.startswith(".checkpoint_test.")]
    assert sidecars == []


def test_unicode_round_trip() -> None:
    # ensure_ascii=False is set in save_checkpoint; the file stores
    # cyrillic text directly. load_checkpoint reads UTF-8.
    payload = {"title": "Матрица перезагрузка", "page": 1}
    save_checkpoint("ru", payload)
    assert load_checkpoint("ru") == payload
    # Spot-check that the file really is UTF-8 (not \u-escaped).
    with open("checkpoint_ru.json", encoding="utf-8") as f:
        raw = f.read()
    assert "Матрица" in raw
    # And it's still valid JSON.
    assert json.loads(raw) == payload
