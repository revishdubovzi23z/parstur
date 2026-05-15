"""Background matcher for kino.pub catalog ids (PR 4).

Mirrors the structure of `rezka_sync.py` but talks to the kino.pub
JSON API via the runtime helpers from `runtime/kinopub.py`. For every
par2 item that does not yet have a `kinopub_id` we issue a search by
title (+ year + type hint where available), score the candidates the
same way `rezka_sync` does for HDRezka rows, and write back the best
match via `db.items.kinopub_bind`. Items that fail to match are
flagged with `checked_kinopub = 1` so the next run skips them — the
operator can later force a re-check via `db.set_ids` (or by clicking
"Отвязать" in the modal, which resets the flag).

Run modes:

* ``python sync_kinopub.py`` — process every un-checked item, with a
  checkpoint persisted every 100 rows so a kill/restart can resume.
* ``python sync_kinopub.py --recheck`` — clear `checked_kinopub` for
  every row first, useful after a catalog reshuffle on kino.pub.

The script is intentionally synchronous — kino.pub's rate limit is
generous but the API still benefits from a small ``time.sleep`` between
requests, and a sequential loop keeps the checkpoint logic trivial.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any

from db import Database
from kinopub_client import KinopubAPIError, KinopubAuthError, KinopubClient
from logging_config import setup_logging
from runtime.kinopub import (
    KinopubAuthError as RuntimeKinopubAuthError,
)
from runtime.kinopub import (
    _authenticated_client,
    _build_item_url,
)
from script_utils import (
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
    should_stop,
)
from settings import settings

STATUS_KEY = "kinopub"
CHECKPOINT_EVERY = 100
# kino.pub's `q=` search returns ~10 candidates per request; 25 is well
# under the documented 50/req cap and gives us enough headroom for the
# year/type filter to land a strong match.
SEARCH_LIMIT = 25
# Sleep between requests to stay polite to the API even though the
# documented rate limit is much higher. Operators on a paid account
# can shorten this via $KINOPUB_SYNC_DELAY_MS.
DEFAULT_DELAY_MS = int(os.environ.get("KINOPUB_SYNC_DELAY_MS", "150"))

# Match the rezka scoring philosophy: title overlap is the floor,
# year is the strongest signal, type comes next. The thresholds below
# were chosen so a tight title match without any auxiliary signals
# still passes, but a wildly different year forces the matcher to
# skip the row instead of binding the wrong release.
SCORE_TITLE_MATCH = 50
SCORE_TITLE_PARTIAL = 20
SCORE_YEAR_MATCH = 60
SCORE_YEAR_OFF_BY_ONE = 30
SCORE_YEAR_MISMATCH = -80
SCORE_TYPE_MATCH = 40
SCORE_TYPE_MISMATCH = -40
SCORE_MIN_ACCEPT = 60

# Rough mapping from par2 `category_id` (see `app_data.db.categories`
# bootstrap rows) to the `type` filter kino.pub understands. Mirrors
# the values listed in 0006_kinopub.sql and lets us send `type=movie`
# / `type=serial` so the API doesn't return concerts when we want
# feature films.
CATEGORY_TYPE_HINT: dict[int, str] = {
    1: "movie",  # Зарубежные фильмы
    4: "serial",  # Зарубежные сериалы
    5: "movie",  # Наши фильмы
    7: "serial",  # Наши сериалы
    16: "movie",  # Anime / мультфильмы — kino.pub returns movies + serials
}

# Categories whose rows the matcher should consider at all. Games,
# software, books etc. have no kino.pub counterpart.
ELIGIBLE_CATEGORY_IDS: tuple[int, ...] = (1, 4, 5, 7, 16)

logger = setup_logging("parsclode.kinopub", settings.log_file_path)


# ---------------------------------------------------------------------------
# Title/year scoring helpers (no I/O — easy to unit-test).


_NON_ALNUM = re.compile(r"[^\w\s]+", flags=re.UNICODE)


def _normalise_title(title: str | None) -> str:
    """Lower-case, strip punctuation, collapse whitespace. The matcher
    doesn't need linguistic accuracy — it just wants two strings to
    compare for "looks like the same release"."""
    if not title:
        return ""
    cleaned = _NON_ALNUM.sub(" ", title.lower())
    return " ".join(cleaned.split())


def _candidate_titles(item: dict) -> list[str]:
    """The DB stores Russian + English titles concatenated with " / ".
    We want to score against each piece independently because kino.pub
    might only return the Russian or only the English variant."""
    raw = str(item.get("title") or "")
    parts = [p.strip() for p in raw.split("/")]
    return [p for p in parts if p]


def score_candidate(
    *,
    item: dict,
    candidate: dict,
    type_hint: str | None,
) -> int:
    """Return the matcher score for one kino.pub search result against
    one par2 item. Pure function — every parameter is data."""

    # ID matching is the strongest signal: guaranteed match if they agree.
    cand_kp = str(candidate.get("kinopoisk") or "")
    cand_imdb = str(candidate.get("imdb") or "")
    item_kp = str(item.get("kp_id") or "")
    item_imdb = str(item.get("imdb_id") or "")

    if item_imdb and item_imdb.startswith("tt"):
        item_imdb = item_imdb[2:]
    if cand_imdb and cand_imdb.startswith("tt"):
        cand_imdb = cand_imdb[2:]

    if item_kp and cand_kp and item_kp == cand_kp:
        return 1000
    if item_imdb and cand_imdb and item_imdb == cand_imdb:
        return 1000

    score = 0

    # Title overlap is the floor: every other signal piles on top.
    cand_title_norm = _normalise_title(candidate.get("title"))
    title_matched = False
    if cand_title_norm:
        for piece in _candidate_titles(item):
            piece_norm = _normalise_title(piece)
            if not piece_norm:
                continue
            if piece_norm == cand_title_norm:
                score += SCORE_TITLE_MATCH
                title_matched = True
                break
            if piece_norm in cand_title_norm or cand_title_norm in piece_norm:
                score += SCORE_TITLE_PARTIAL
                title_matched = True
                break

    # If the title didn't match at all, we reject the candidate immediately
    # (unless it was an exact ID match, which is handled above).
    if not title_matched:
        return 0

    cand_year = candidate.get("year")
    item_year = item.get("year")
    if isinstance(item_year, int) and item_year > 0 and isinstance(cand_year, int):
        if cand_year == item_year:
            score += SCORE_YEAR_MATCH
        elif abs(cand_year - item_year) == 1:
            score += SCORE_YEAR_OFF_BY_ONE
        else:
            score += SCORE_YEAR_MISMATCH

    cand_type = candidate.get("type")
    if type_hint and cand_type:
        if cand_type == type_hint:
            score += SCORE_TYPE_MATCH
        elif cand_type in {"movie", "serial"}:
            # Strong negative penalty: the API knows the type, ours
            # disagrees — almost certainly a different release.
            score += SCORE_TYPE_MISMATCH

    return score


def best_candidate(
    *,
    item: dict,
    raw_results: list[dict],
    type_hint: str | None,
) -> tuple[dict, int] | None:
    """Pick the highest-scoring kino.pub search result for the given
    par2 item. Returns ``None`` when no candidate clears
    ``SCORE_MIN_ACCEPT``."""
    best: tuple[dict, int] | None = None
    for cand in raw_results:
        if not isinstance(cand, dict) or cand.get("id") is None:
            continue
        score = score_candidate(item=item, candidate=cand, type_hint=type_hint)
        if best is None or score > best[1]:
            best = (cand, score)
    if best is None:
        return None
    if best[1] < SCORE_MIN_ACCEPT:
        return None
    return best


# ---------------------------------------------------------------------------
# Progress reporting — same shape as rezka_sync, so the existing UI
# poller in `stores/sync.ts` picks it up automatically.


def report_progress(current: int, total: int) -> None:
    try:
        p_file = os.path.join(settings.app_data_dir, f"progress_{STATUS_KEY}.json")
        with open(p_file, "w", encoding="utf-8") as f:
            json.dump({"current": current, "total": total}, f)
    except Exception:
        # Progress is best-effort — never crash the matcher because
        # the JSON dump fails.
        pass


# ---------------------------------------------------------------------------
# Public entry point.


def _load_eligible_items(db: Database, *, resume_from_id: int | None) -> list[dict]:
    """Read every un-checked item the matcher should consider. Filters
    out games/software and rows that are already bound or hard-ignored."""
    placeholders = ",".join("?" for _ in ELIGIBLE_CATEGORY_IDS)
    sql = (
        "SELECT id, title, year, category_id, kp_id, imdb_id "
        "FROM items "
        f"WHERE category_id IN ({placeholders}) "
        "  AND is_ignored = 0 "
        "  AND checked_kinopub = 0 "
        "  AND kinopub_id IS NULL"
    )
    params: list[Any] = list(ELIGIBLE_CATEGORY_IDS)
    if resume_from_id is not None:
        sql += " AND id < ?"
        params.append(resume_from_id)
    sql += " ORDER BY id DESC"
    conn = db.get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _make_client_factory(
    factory: Any | None,
) -> KinopubClient:
    """Return a `KinopubClient` either from a factory (for tests) or
    via the lazy-refresh path used in production."""
    if factory is not None:
        client = factory()
        if not isinstance(client, KinopubClient):
            raise TypeError("factory() must return a KinopubClient")
        return client
    return _authenticated_client()


def run(
    *,
    db: Database | None = None,
    client_factory: Any | None = None,
    delay_ms: int = DEFAULT_DELAY_MS,
    recheck: bool = False,
) -> dict[str, int]:
    """Iterate through un-checked items and try to bind each one to
    kino.pub. Returns ``{processed, bound, skipped}`` for the test
    suite and the script's CLI summary."""
    db = db or Database()

    if recheck:
        conn = db.get_connection()
        try:
            conn.execute(
                "UPDATE items SET checked_kinopub = 0 "
                "WHERE kinopub_id IS NULL "
                f"  AND category_id IN ({','.join('?' for _ in ELIGIBLE_CATEGORY_IDS)})",
                ELIGIBLE_CATEGORY_IDS,
            )
            conn.commit()
        finally:
            conn.close()
        clear_checkpoint(STATUS_KEY)

    resume_from_id: int | None = None
    checkpoint = load_checkpoint(STATUS_KEY)
    if isinstance(checkpoint, dict):
        last_id = checkpoint.get("last_id")
        if isinstance(last_id, int):
            resume_from_id = last_id

    items = _load_eligible_items(db, resume_from_id=resume_from_id)
    total = len(items)
    logger.info(f"=== KINOPUB SYNC (Total: {total}) ===")
    report_progress(0, total)

    if total == 0:
        return {"processed": 0, "bound": 0, "skipped": 0}

    client: KinopubClient | None = None
    try:
        client = _make_client_factory(client_factory)
    except (KinopubAuthError, RuntimeKinopubAuthError) as e:
        logger.error(f"[kinopub] cannot start sync — not authenticated: {e}")
        return {"processed": 0, "bound": 0, "skipped": total}

    bound = 0
    skipped = 0

    for idx, item in enumerate(items):
        if should_stop(STATUS_KEY):
            logger.info("[kinopub] stop flag detected — saving checkpoint and exiting")
            break

        item_id = int(item["id"])
        try:
            queries = _candidate_titles(item) or [str(item.get("title") or "").strip()]
            queries = [q for q in queries if q]
            if not queries:
                db.mark_checked(item_id, STATUS_KEY)
                skipped += 1
                continue

            type_hint = CATEGORY_TYPE_HINT.get(int(item.get("category_id") or 0))
            year_hint = item.get("year") if isinstance(item.get("year"), int) else None
            raw: list[dict] = []
            seen_ids: set[int] = set()
            for query in queries:
                results = client.search(
                    query,
                    type_=type_hint,
                    year=year_hint,
                    limit=SEARCH_LIMIT,
                )
                for entry in results:
                    if not isinstance(entry, dict) or entry.get("id") is None:
                        raw.append(entry)
                        continue
                    try:
                        cand_id = int(entry["id"])
                    except (TypeError, ValueError):
                        raw.append(entry)
                        continue
                    if cand_id in seen_ids:
                        continue
                    seen_ids.add(cand_id)
                    raw.append(entry)
            pick = best_candidate(item=item, raw_results=raw, type_hint=type_hint)
            if pick is None:
                db.mark_checked(item_id, STATUS_KEY)
                skipped += 1
            else:
                cand, score = pick
                kp_id = int(cand["id"])
                db.kinopub_bind(
                    item_id,
                    kinopub_id=kp_id,
                    kinopub_type=cand.get("type") or None,
                    kinopub_url=_build_item_url(kp_id),
                )
                logger.info(
                    f"[kinopub] bound item {item_id} -> #{kp_id} "
                    f"(title={cand.get('title')!r}, score={score})"
                )
                bound += 1
        except (KinopubAuthError, RuntimeKinopubAuthError) as e:
            logger.error(f"[kinopub] auth failure mid-sweep, aborting: {e}")
            break
        except KinopubAPIError as e:
            logger.warning(
                f"[kinopub] API error for item {item_id} ({type(e).__name__}: {e}); "
                "marking checked and continuing"
            )
            db.mark_checked(item_id, STATUS_KEY)
            skipped += 1
        except Exception as e:
            logger.warning(
                f"[kinopub] unexpected error for item {item_id} "
                f"({type(e).__name__}: {e}); marking checked and continuing"
            )
            db.mark_checked(item_id, STATUS_KEY)
            skipped += 1

        report_progress(idx + 1, total)
        if (idx + 1) % CHECKPOINT_EVERY == 0:
            save_checkpoint(STATUS_KEY, {"last_id": item_id})
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    processed = bound + skipped
    logger.info(f"[kinopub] done: processed={processed}, bound={bound}, skipped={skipped}")
    clear_checkpoint(STATUS_KEY)
    return {"processed": processed, "bound": bound, "skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Match par2 items to kino.pub catalog ids")
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Clear checked_kinopub for un-bound rows before sweeping.",
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=DEFAULT_DELAY_MS,
        help="Sleep between kino.pub requests, in milliseconds.",
    )
    args = parser.parse_args()
    run(recheck=args.recheck, delay_ms=args.delay_ms)
