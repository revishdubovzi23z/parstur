"""JacRed-compatible search endpoint that exposes parstur's release
catalog to Lampac.

Lampac aggregates torrent results from many trackers via its JacRed
module (`Modules/JacRed/Controllers/*Controller.cs`). Each tracker is
just a thin HTTP client wrapped around the upstream site. This file
makes parstur look like one more such tracker: Lampac calls
`GET /api/jacred/search?query=...`, gets back releases (already
indexed and curated by parstur), and renders them alongside Rutor,
RuTracker, Kinozal etc.

We deliberately keep the response shape minimal and parstur-specific
(not Torznab) — Lampac's ParsturController owns the mapping to
`TorrentDetails`, so we don't have to mimic somebody else's wire
format. Fields that JacRed needs and that we have in the DB:

    title     – release torrent_title (the actual torrent name)
    magnet    – magnet:?xt=urn:btih:... URI; releases without one are
                filtered out (Lampac can't stream them)
    size_name – human-readable size string ("2.5 GB"), since that's
                what parstur stores; Lampac falls back to parsing it
    url       – upstream tracker page URL (rutor.info for now)
    sid/pir   – seeders/leechers — NOT stored in parstur yet; left at
                0 for now. Lampac sorts results across trackers and
                will simply rank parstur entries lower than ones with
                seeder data. This is acceptable and avoids an invasive
                schema change for the first integration cut.
    item      – owning item metadata (id, title, year, kp_id, imdb_id,
                category_id) so Lampac can apply title/year matching
                if it wants.

Matching strategy mirrors `db.get_feed`'s `search` branch: prefer FTS5
(`items_fts`), fall back to LIKE on items.title / items.title_norm /
item_search_names. We never invent metadata — if a release isn't
linked to an item the user has indexed, it doesn't show up.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from db import db

logger = logging.getLogger("parsclode.routes.jacred")

router = APIRouter(prefix="/api/jacred", tags=["jacred"])


# Hard cap on results returned per query. JacRed merges across many
# trackers and trims to 2000 server-side anyway; 200 from parstur is
# more than enough for any sensible title-based query.
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


@router.get("/search")
def jacred_search(
    query: str = Query("", description="Title text to search for."),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> dict:
    """Return parstur releases whose owning item matches `query`.

    The response is a single JSON object so Lampac's ParsturController
    can deserialise it with a fixed-shape model. We always include the
    `results` key (possibly empty) — never null — so the C# side can
    iterate unconditionally.
    """

    q = (query or "").strip()
    if not q:
        return {"results": []}

    with db._conn() as c:
        # Prefer FTS5 when the virtual table exists (it's set up by
        # `db.ensure_fts_indexed` at startup). Fall back to LIKE for
        # fresh / partially-migrated DBs and for old SQLite builds
        # without the fts5 extension. This mirrors db.get_feed.
        fts_available = False
        try:
            c.execute("SELECT count(*) FROM items_fts LIMIT 1")
            fts_available = True
        except Exception:
            pass

        if fts_available:
            # OR-join the tokens so partial-title queries still match,
            # e.g. "inception 2010" matches items containing either.
            fts_query = " OR ".join(q.split()) or q
            sql = """
                SELECT
                    r.id          AS release_id,
                    r.torrent_title,
                    r.magnet,
                    r.link,
                    r.size,
                    r.date_added,
                    r.quality,
                    r.rutor_id,
                    i.id          AS item_id,
                    i.title       AS item_title,
                    i.original_title,
                    i.year,
                    i.kp_id,
                    i.imdb_id,
                    i.category_id
                FROM releases r
                JOIN items i ON r.item_id = i.id
                WHERE r.magnet IS NOT NULL AND r.magnet != ''
                  AND i.id IN (
                      SELECT rowid FROM items_fts WHERE items_fts MATCH ?
                  )
                ORDER BY r.date_added DESC
                LIMIT ?
            """
            rows = c.execute(sql, (fts_query, limit)).fetchall()
        else:
            like = f"%{q.lower()}%"
            sql = """
                SELECT
                    r.id          AS release_id,
                    r.torrent_title,
                    r.magnet,
                    r.link,
                    r.size,
                    r.date_added,
                    r.quality,
                    r.rutor_id,
                    i.id          AS item_id,
                    i.title       AS item_title,
                    i.original_title,
                    i.year,
                    i.kp_id,
                    i.imdb_id,
                    i.category_id
                FROM releases r
                JOIN items i ON r.item_id = i.id
                WHERE r.magnet IS NOT NULL AND r.magnet != ''
                  AND (
                      LOWER(i.title) LIKE ?
                      OR LOWER(i.original_title) LIKE ?
                      OR i.title_norm LIKE ?
                      OR EXISTS (
                          SELECT 1 FROM item_search_names sn
                          WHERE sn.item_id = i.id AND sn.name_norm LIKE ?
                      )
                  )
                ORDER BY r.date_added DESC
                LIMIT ?
            """
            rows = c.execute(sql, (like, like, like, like, limit)).fetchall()

    results: list[dict] = []
    for row in rows:
        results.append(
            {
                # Tracker identifier — parstur owns this name in JacRed's
                # aggregated output.
                "tracker": "parstur",
                "title": row["torrent_title"] or "",
                "magnet": row["magnet"] or "",
                # Upstream tracker page (currently rutor.info); kept so
                # users can click through from the Lampa client.
                "url": row["link"] or "",
                "size_name": row["size"] or "",
                "date_added": row["date_added"] or "",
                "quality": row["quality"] or "",
                # Owning item metadata for downstream year/title matching.
                "item": {
                    "id": row["item_id"],
                    "title": row["item_title"] or "",
                    "original_title": row["original_title"] or "",
                    "year": row["year"] or 0,
                    "kp_id": row["kp_id"] or "",
                    "imdb_id": row["imdb_id"] or "",
                    "category_id": row["category_id"] or 0,
                },
            }
        )

    return {"results": results}
