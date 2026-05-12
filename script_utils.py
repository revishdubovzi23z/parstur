from __future__ import annotations

import json
import os
import tempfile
from typing import Any

# 4.1: Path resolution for Docker persistence.
# In a containerized environment, persistent files (checkpoints, flags)
# must reside in /data. We use APP_DATA_DIR from env to locate them.
# We don't import settings.py here to avoid circular dependencies
# (settings.py imports script_utils).
_APP_DATA_DIR = os.getenv("APP_DATA_DIR", ".")


def _get_data_path(filename: str) -> str:
    """Join filename with the configured APP_DATA_DIR."""
    return os.path.join(_APP_DATA_DIR, filename)


_config: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    global _config
    if _config is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                _config = json.load(f)
        else:
            _config = {}
    assert _config is not None
    return _config


def should_stop(status_key: str) -> bool:
    return os.path.exists(_get_data_path(f"stop_{status_key}.flag"))


def save_checkpoint(status_key: str, data: Any) -> None:
    """Atomically write checkpoint JSON.

    The previous implementation truncated the file with 'w' and then
    streamed json.dump into it. If the process was killed (or hit OOM)
    between truncate and the final flush, the on-disk file was left
    half-written and load_checkpoint silently returned None — wiping
    the resume point that was supposed to make 'cleanup' / 'sync'
    restartable. Use a temp file in the same directory and os.replace
    so the swap is atomic on every supported platform.
    """
    target = _get_data_path(f"checkpoint_{status_key}.json")
    target_dir = os.path.dirname(os.path.abspath(target)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".checkpoint_{status_key}.", suffix=".tmp", dir=target_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Best-effort cleanup of the orphan temp file on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_checkpoint(status_key: str) -> Any | None:
    path = _get_data_path(f"checkpoint_{status_key}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def clear_checkpoint(status_key: str) -> None:
    path = _get_data_path(f"checkpoint_{status_key}.json")
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def clear_stop_flag(status_key: str) -> None:
    path = _get_data_path(f"stop_{status_key}.flag")
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
