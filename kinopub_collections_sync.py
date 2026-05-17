"""Bi-directional collection-folder sync against kino.pub.

Mirrors `rezka_collections_sync.py` but talks to kino.pub's Bookmarks
API (закладки / folders) instead of HDRezka's favorites pages. For
every folder on the operator's kino.pub account we:

1. Match it to a par2 collection by normalised name (create the local
   collection when missing — same "plus" half of the diff as the
   rezka script).
2. Pull the items currently inside the folder and add any that are
   not yet in the local collection (kinopub -> project).
3. For items that exist only in the local collection, look up their
   `kinopub_id` (running an on-demand `search()` for unbound rows)
   and push them into the folder via `POST /v1/bookmarks/add`
   (project -> kinopub).

Authentication uses the same OAuth token as `sync_kinopub.py` —
the script bails out early with a clear log line when the operator
has not finished the device-code flow.
"""

from __future__ import annotations

from typing import Any

from app_core import normalize_title
from db import Database
from kinopub_client import KinopubAPIError, KinopubAuthError, KinopubClient
from logging_config import setup_logging
from runtime.kinopub import (
    KinopubAuthError as RuntimeKinopubAuthError,
)
from runtime.kinopub import (
    _authenticated_client,
    _build_item_url,
    add_item_to_folder,
    list_bookmark_folders,
    list_folder_items,
)
from settings import settings
from sync_kinopub import (
    CATEGORY_TYPE_HINT,
    SEARCH_LIMIT,
    _candidate_titles,
    _clean_query_for_search,
    best_candidate,
)

logger = setup_logging("parsclode.kinopub_collections", settings.log_file_path)


# ---------------------------------------------------------------------------
# Pure helpers — easy to unit-test without hitting the API.


def _match_folder_to_collection(folder_name: str, collections: list[dict]) -> dict | None:
    """Find a par2 collection whose normalised name matches the given
    kino.pub folder. Falls back to a >=70% character-overlap fuzzy
    match, identical to the rezka collections sync."""
    folder_norm = normalize_title(folder_name)
    if not folder_norm:
        return None
    for coll in collections:
        if normalize_title(coll["name"]) == folder_norm:
            return coll
    best: dict | None = None
    best_ratio = 0.0
    for coll in collections:
        coll_norm = normalize_title(coll["name"])
        shorter = min(len(folder_norm), len(coll_norm))
        if shorter < 3:
            continue
        overlap = sum(1 for a, b in zip(folder_norm, coll_norm, strict=False) if a == b)
        ratio = overlap / max(len(folder_norm), len(coll_norm))
        if ratio > best_ratio and ratio >= 0.7:
            best_ratio = ratio
            best = coll
    return best


def _folder_title(folder: dict) -> str:
    """kino.pub returns the human name under `title`, but be defensive:
    some endpoints use `name`."""
    return str(folder.get("title") or folder.get("name") or "").strip()


def _folder_id(folder: dict) -> int | None:
    try:
        return int(folder["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _search_kinopub_id(
    item_info: dict,
    *,
    client: KinopubClient,
) -> tuple[int, str | None] | None:
    """Run the kinopub matcher against one local item. Returns
    ``(kinopub_id, kinopub_type)`` on success, ``None`` otherwise.
    Reuses `sync_kinopub.best_candidate` so the score thresholds
    stay consistent with the background matcher."""
    queries = _candidate_titles(item_info) or [str(item_info.get("title") or "").strip()]
    queries = [q for q in queries if q]
    if not queries:
        return None

    type_hint = CATEGORY_TYPE_HINT.get(int(item_info.get("category_id") or 0))
    year = item_info.get("year") if isinstance(item_info.get("year"), int) else None
    api_year = year if type_hint != "serial" else None

    raw: list[dict] = []
    seen: set[int] = set()
    for query in queries:
        clean_q = _clean_query_for_search(query)
        if not clean_q:
            continue
        try:
            results = client.search(
                clean_q,
                type_=None,
                year=api_year,
                limit=SEARCH_LIMIT,
            )
        except KinopubAPIError as e:
            logger.warning(f"      [!] kinopub search error for '{clean_q}': {e}")
            continue
        for entry in results:
            if not isinstance(entry, dict):
                continue
            ent_id = entry.get("id")
            try:
                cand_id = int(ent_id) if ent_id is not None else None
            except (TypeError, ValueError):
                cand_id = None
            if cand_id is None or cand_id in seen:
                continue
            seen.add(cand_id)
            raw.append(entry)

    pick = best_candidate(item=item_info, raw_results=raw, type_hint=type_hint)
    if not pick:
        return None
    cand, _score = pick
    try:
        kp_id = int(cand["id"])
    except (KeyError, TypeError, ValueError):
        return None
    kp_type = cand.get("type") or None
    return kp_id, (str(kp_type) if kp_type else None)


# ---------------------------------------------------------------------------
# Main entry point.


def sync_kinopub_collections(
    *,
    db: Database | None = None,
    client: KinopubClient | None = None,
) -> dict[str, int]:
    """Run the bi-directional sync. Returns a summary dict for tests."""
    logger.info("=== KINOPUB COLLECTIONS BIDIRECTIONAL SYNC ===")

    try:
        api = client or _authenticated_client()
    except (KinopubAuthError, RuntimeKinopubAuthError) as e:
        logger.error(f"[-] Failed to authenticate to kino.pub: {e}")
        logger.error("=== SYNC ABORTED: NOT AUTHENTICATED ===")
        return {
            "kinopub_to_project": 0,
            "project_to_kinopub": 0,
            "new_kinopub_ids": 0,
        }

    try:
        folders = list_bookmark_folders(client=api)
    except (KinopubAuthError, KinopubAPIError) as e:
        logger.error(f"[-] Failed to list bookmark folders: {e}")
        return {
            "kinopub_to_project": 0,
            "project_to_kinopub": 0,
            "new_kinopub_ids": 0,
        }

    if not folders:
        logger.warning("[-] No bookmark folders found on kino.pub")
        return {
            "kinopub_to_project": 0,
            "project_to_kinopub": 0,
            "new_kinopub_ids": 0,
        }

    logger.info(f"[*] Found {len(folders)} kino.pub folders:")
    for f in folders:
        logger.info(f"    {_folder_title(f)} ({f.get('count', '?')}) [id={f.get('id')}]")

    db = db or Database()
    conn = db.get_connection()
    c = conn.cursor()
    try:
        collections = db.get_collections()

        c.execute(
            "SELECT id, kinopub_id FROM items WHERE kinopub_id IS NOT NULL AND kinopub_id != ''"
        )
        kp_to_item: dict[int, int] = {}
        for row in c.fetchall():
            try:
                kp_to_item[int(row["kinopub_id"])] = int(row["id"])
            except (TypeError, ValueError):
                continue

        all_items: dict[int, dict[str, Any]] = {}
        c.execute("SELECT id, title, year, category_id, kp_id, imdb_id, kinopub_id FROM items")
        for row in c.fetchall():
            all_items[int(row["id"])] = {
                "title": row["title"],
                "year": row["year"],
                "category_id": row["category_id"],
                "kp_id": row["kp_id"],
                "imdb_id": row["imdb_id"],
                "kinopub_id": row["kinopub_id"],
            }

        total_kinopub_to_project = 0
        total_project_to_kinopub = 0
        total_new_kinopub_ids = 0

        for folder in folders:
            folder_name = _folder_title(folder)
            folder_id = _folder_id(folder)
            if not folder_name or folder_id is None:
                continue

            coll = _match_folder_to_collection(folder_name, collections)
            if not coll:
                logger.info(f"\n  [+] Creating new collection '{folder_name}'")
                db.create_collection(folder_name)
                conn.commit()
                collections = db.get_collections()
                coll = _match_folder_to_collection(folder_name, collections)

            if not coll:
                continue

            coll_id = int(coll["id"])
            coll_name = coll["name"]
            label = folder_name if folder_name == coll_name else f"{folder_name} -> {coll_name}"
            logger.info(f"\n  [sync] '{label}' (folder_id={folder_id}, coll_id={coll_id})")

            try:
                kp_items = list_folder_items(folder_id, client=api)
            except (KinopubAuthError, KinopubAPIError) as e:
                logger.error(f"    [!] Failed to fetch folder items: {e}")
                continue

            folder_kp_ids: set[int] = set()
            for entry in kp_items:
                if not isinstance(entry, dict):
                    continue
                ent_id = entry.get("id")
                try:
                    if ent_id is not None:
                        folder_kp_ids.add(int(ent_id))
                except (TypeError, ValueError):
                    continue

            folder_local_item_ids: set[int] = set()
            for kp_id in folder_kp_ids:
                item_id = kp_to_item.get(kp_id)
                if item_id:
                    folder_local_item_ids.add(item_id)

            c.execute("SELECT item_id FROM collection_items WHERE collection_id = ?", (coll_id,))
            project_item_ids = {int(r[0]) for r in c.fetchall()}

            # kinopub -> project: add bound items present on kinopub but
            # missing from the local collection.
            only_on_kinopub = folder_local_item_ids - project_item_ids
            for item_id in only_on_kinopub:
                c.execute(
                    "INSERT OR IGNORE INTO collection_items (collection_id, item_id) VALUES (?, ?)",
                    (coll_id, item_id),
                )
            if only_on_kinopub:
                conn.commit()
                total_kinopub_to_project += len(only_on_kinopub)
            logger.info(f"    kino.pub -> Project: +{len(only_on_kinopub)} items")

            # project -> kinopub: push local-only items into the folder,
            # searching kinopub on demand when the row isn't bound yet.
            only_on_project = project_item_ids - folder_local_item_ids
            pushed = 0
            searched = 0
            for item_id in only_on_project:
                info = all_items.get(item_id)
                if not info:
                    continue

                kp_id_raw = info.get("kinopub_id")
                try:
                    kp_id = int(kp_id_raw) if kp_id_raw else None
                except (TypeError, ValueError):
                    kp_id = None

                if not kp_id:
                    logger.info(
                        f"    [search] Looking for kino.pub id: {info['title']} ({info['year']})"
                    )
                    try:
                        found = _search_kinopub_id(info, client=api)
                    except (KinopubAuthError, RuntimeKinopubAuthError) as e:
                        logger.error(f"      [!] kino.pub auth failure mid-sweep: {e}")
                        found = None
                    if not found:
                        logger.info("      [-] Not found on kino.pub")
                        continue
                    kp_id, kp_type = found
                    db.kinopub_bind(
                        item_id,
                        kinopub_id=kp_id,
                        kinopub_type=kp_type,
                        kinopub_url=_build_item_url(kp_id),
                    )
                    kp_to_item[kp_id] = item_id
                    all_items[item_id]["kinopub_id"] = kp_id
                    searched += 1
                    total_new_kinopub_ids += 1
                    logger.info(f"      [+] Bound -> kinopub_id={kp_id}")

                try:
                    add_item_to_folder(item=kp_id, folder=folder_id, client=api)
                    pushed += 1
                    folder_kp_ids.add(kp_id)
                except (KinopubAuthError, RuntimeKinopubAuthError) as e:
                    logger.error(f"      [!] kino.pub auth failure: {e}")
                    break
                except KinopubAPIError as e:
                    logger.error(f"      [!] Failed to push kinopub_id={kp_id} into folder: {e}")

            total_project_to_kinopub += pushed
            logger.info(
                f"    Project -> kino.pub: +{pushed} items pushed "
                f"({searched} kino.pub ids found by search)"
            )

            final_count = len(folder_local_item_ids | only_on_kinopub) + pushed
            logger.info(f"    Collection '{coll_name}' total: {final_count} items")

        logger.info("\n=== SYNC COMPLETE ===")
        logger.info(
            f"  kino.pub -> Project: +{total_kinopub_to_project} items added to collections"
        )
        logger.info(f"  Project -> kino.pub: +{total_project_to_kinopub} items pushed to bookmarks")
        logger.info(f"  New kino.pub ids bound by search: {total_new_kinopub_ids}")

        return {
            "kinopub_to_project": total_kinopub_to_project,
            "project_to_kinopub": total_project_to_kinopub,
            "new_kinopub_ids": total_new_kinopub_ids,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    sync_kinopub_collections()
