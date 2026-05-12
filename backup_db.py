"""6.6 — standalone DB backup CLI.

Run as a cron / scheduled task to keep periodic snapshots of
`app_data.db` (or whichever `DB_PATH` is configured). Uses the
SQLite online backup API via `Database.backup_to`, so it's safe to
run while the FastAPI app is up and writing.

Default destination is `backups/app_data-YYYYMMDD-HHMMSS.db` under
the current working directory; override with `--out PATH` for a
fixed filename or a different folder.

Examples:

    # daily snapshot from a cron entry
    python backup_db.py

    # explicit destination
    python backup_db.py --out /var/backups/par2/today.db

    # keep at most 7 backups in `backups/`, rotate by mtime
    python backup_db.py --rotate 7
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
import logging

from db import db

logger = logging.getLogger("parsclode.backup")


def _default_dest(out_dir: str = "backups") -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return os.path.join(out_dir, f"app_data-{stamp}.db")


def _rotate(out_dir: str, keep: int) -> list[str]:
    """Drop oldest backups so at most `keep` remain. Returns list of
    paths removed."""
    if keep <= 0:
        return []
    if not os.path.isdir(out_dir):
        return []
    candidates = []
    for name in os.listdir(out_dir):
        if not name.startswith("app_data-") or not name.endswith(".db"):
            continue
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        candidates.append((os.path.getmtime(path), path))
    candidates.sort()  # oldest first
    overflow = len(candidates) - keep
    removed: list[str] = []
    if overflow > 0:
        for _mtime, path in candidates[:overflow]:
            try:
                os.unlink(path)
                removed.append(path)
            except OSError as e:
                logger.error(f"[backup] couldn't delete {path}: {e}")
    return removed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Online backup of the par2 SQLite database.")
    p.add_argument(
        "--out",
        help="Destination path. Defaults to backups/app_data-<UTC-timestamp>.db.",
    )
    p.add_argument(
        "--out-dir",
        default="backups",
        help="Directory for the default-named backup. Ignored if --out is given.",
    )
    p.add_argument(
        "--pages",
        type=int,
        default=100,
        help="SQLite backup-step page count (smaller = less write blocking).",
    )
    p.add_argument(
        "--rotate",
        type=int,
        default=0,
        help="Keep at most N backups in --out-dir; 0 disables rotation.",
    )
    args = p.parse_args(argv)

    dest = args.out or _default_dest(args.out_dir)
    started = time.monotonic()
    try:
        size = db.backup_to(dest, pages=args.pages)
    except Exception as e:
        logger.error(f"[backup] FAILED: {type(e).__name__}: {e}", exc_info=True)
        return 2

    elapsed = time.monotonic() - started
    logger.info(f"[backup] wrote {dest} ({size} bytes) in {elapsed:.2f}s")

    removed = _rotate(args.out_dir if not args.out else os.path.dirname(dest), args.rotate)
    for r in removed:
        logger.info(f"[backup] rotated out {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
