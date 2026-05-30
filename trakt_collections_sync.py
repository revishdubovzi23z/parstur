from app_core import normalize_title
from db import Database
from logging_config import setup_logging
from tmdb_client import TMDBClient
from trakt_client import TraktClient

logger = setup_logging("parsclode.trakt_collections", "trakt_collections_log.txt")


def _match_collection_to_trakt_list(col_name, trakt_lists):
    col_norm = normalize_title(col_name)
    for t_list in trakt_lists:
        if normalize_title(t_list["name"]) == col_norm:
            return t_list
    return None


def sync_trakt_collections():
    logger.info("=== TRAKT.TV COLLECTIONS BIDIRECTIONAL SYNC ===")

    client = TraktClient()
    if not client.access_token:
        logger.error(
            "[-] Trakt.tv is not authorized. Run python trakt_import.py first to authorize!"
        )
        return

    tmdb = TMDBClient()
    db = Database()
    conn = db.get_connection()
    c = conn.cursor()

    # 1. Fetch Trakt custom lists
    trakt_lists = client.get_custom_lists()
    logger.info(f"[*] Found {len(trakt_lists)} custom lists on Trakt.tv")

    # 2. Fetch local collections
    collections = db.get_collections()
    logger.info(f"[*] Found {len(collections)} local collections in project")

    # Map project collections to Trakt lists, creating missing ones on Trakt
    for col in collections:
        col_name = col["name"]
        col_id = col["id"]

        # Match or create Trakt list
        t_list = _match_collection_to_trakt_list(col_name, trakt_lists)
        if not t_list:
            logger.info(f"  [+] Creating new custom list on Trakt: '{col_name}'")
            t_list = client.create_custom_list(
                col_name, description="Синхронизировано из проекта Antigravity Tracker"
            )
            if t_list:
                trakt_lists.append(t_list)
            else:
                logger.error(f"  [-] Failed to create Trakt list for '{col_name}'")
                continue

        list_id = t_list["ids"]["trakt"]
        list_slug = t_list["ids"]["slug"]
        logger.info(f"\n[Sync] '{col_name}' <--> Trakt list '{t_list['name']}' (slug: {list_slug})")

        # 3. Get items in this Trakt custom list
        trakt_raw_items = client.get_custom_list_items(list_id)

        # Build mappings of Trakt items (by IMDb ID and TMDB ID)
        trakt_imdb_map = {}
        trakt_tmdb_map = {}

        for item in trakt_raw_items:
            m_type = item.get("type")
            if m_type not in ("movie", "show"):
                continue
            media_data = item.get(m_type, {})
            ids = media_data.get("ids", {})

            trakt_item_info = {
                "type": m_type,
                "title": media_data.get("title"),
                "year": media_data.get("year"),
                "imdb_id": ids.get("imdb"),
                "tmdb_id": ids.get("tmdb"),
            }
            if ids.get("imdb"):
                trakt_imdb_map[ids["imdb"]] = trakt_item_info
            if ids.get("tmdb"):
                trakt_tmdb_map[int(ids["tmdb"])] = trakt_item_info

        # 4. Get items in this local collection
        c.execute(
            """
            SELECT i.id, i.title, i.original_title, i.year, i.imdb_id, i.kp_id, i.category_id
            FROM collection_items ci
            JOIN items i ON ci.item_id = i.id
            WHERE ci.collection_id = ?
            """,
            (col_id,),
        )
        local_items = c.fetchall()

        local_item_ids = {r[0] for r in local_items}
        local_imdb_set = {r[4] for r in local_items if r[4]}

        # Find which Trakt items are missing locally
        new_items_from_trakt = []
        for imdb_id, t_info in trakt_imdb_map.items():
            if imdb_id not in local_imdb_set:
                new_items_from_trakt.append(t_info)

        for tmdb_id, t_info in trakt_tmdb_map.items():
            if t_info["imdb_id"] not in local_imdb_set:  # avoid duplicate addition if already added
                # Double check by title + year in local items
                matched_locally = False
                for r in local_items:
                    if (
                        normalize_title(r[1]) == normalize_title(t_info["title"])
                        and r[3] == t_info["year"]
                    ):
                        matched_locally = True
                        break
                if not matched_locally:
                    new_items_from_trakt.append(t_info)

        # Remove duplicate tasks from Trakt new items list
        seen_t_keys = set()
        unique_new_items_from_trakt = []
        for t_info in new_items_from_trakt:
            key = t_info["imdb_id"] or t_info["tmdb_id"]
            if key not in seen_t_keys:
                seen_t_keys.add(key)
                unique_new_items_from_trakt.append(t_info)

        # Add missing Trakt items to the local collection
        for t_info in unique_new_items_from_trakt:
            # Check if this movie exists in the main database at all
            existing_db_id = None
            if t_info["imdb_id"]:
                c.execute("SELECT id FROM items WHERE imdb_id = ?", (t_info["imdb_id"],))
                row = c.fetchone()
                if row:
                    existing_db_id = row[0]
            if not existing_db_id and t_info["title"] and t_info["year"]:
                c.execute(
                    "SELECT id FROM items WHERE title_norm = ? AND year = ?",
                    (normalize_title(t_info["title"]), t_info["year"]),
                )
                row = c.fetchone()
                if row:
                    existing_db_id = row[0]

            if existing_db_id:
                # Item exists in DB, just add to collection
                c.execute(
                    "INSERT OR IGNORE INTO collection_items (collection_id, item_id) VALUES (?, ?)",
                    (col_id, existing_db_id),
                )
                logger.info(
                    f"    [Local Sync] Added existing DB item to collection: {t_info['title']} ({t_info['year']})"
                )
            else:
                # We need to query TMDB and create a beautiful rich card
                logger.info(
                    f"    [Local Sync] Creating new card from Trakt: {t_info['title']} ({t_info['year']})"
                )
                tmdb_data = None
                if t_info["imdb_id"]:
                    tmdb_data = tmdb.find_by_imdb_id(t_info["imdb_id"])
                if not tmdb_data and t_info["title"] and t_info["year"]:
                    tmdb_data = tmdb.search_movie(t_info["title"], t_info["year"])

                if tmdb_data:
                    # Construct rich item
                    year_val = t_info["year"]
                    if not year_val and tmdb_data.get("release_date"):
                        try:
                            year_val = int(tmdb_data["release_date"][:4])
                        except Exception:
                            pass

                    title_ru = tmdb_data.get("title") or t_info["title"]
                    title_orig = tmdb_data.get("original_title") or ""

                    display_title = title_ru
                    if title_orig and title_orig.lower() != title_ru.lower():
                        display_title = f"{title_ru} / {title_orig}"
                    if year_val:
                        display_title += f" ({year_val})"

                    parsed = {
                        "title": display_title,
                        "original_title": title_orig,
                        "year": year_val,
                        "category_id": 1 if t_info["type"] == "movie" else 4,
                        "poster_url": tmdb_data.get("poster_url") or "",
                        "description": tmdb_data.get("description") or "",
                        "imdb_id": t_info["imdb_id"] or tmdb_data.get("imdb_id") or "",
                        "kp_id": None,
                        "imdb_rating": 0.0,
                        "kp_rating": 0.0,
                        "is_metadata_fixed": 0,
                        "title_norm": normalize_title(title_ru),
                    }

                    item_id = db.insert_item(parsed, conn=conn)
                    if item_id:
                        c.execute(
                            "INSERT OR IGNORE INTO collection_items (collection_id, item_id) VALUES (?, ?)",
                            (col_id, item_id),
                        )
                        logger.info(
                            f"      [+] Successfully created card & added to collection: {title_ru}"
                        )
                else:
                    # Fallback to create a stub item if TMDB search failed
                    logger.warning(
                        f"      [-] TMDB search failed, creating simple stub for {t_info['title']}"
                    )
                    parsed = {
                        "title": t_info["title"]
                        + (f" ({t_info['year']})" if t_info["year"] else ""),
                        "original_title": "",
                        "year": t_info["year"],
                        "category_id": 1 if t_info["type"] == "movie" else 4,
                        "poster_url": "",
                        "description": "Синхронизировано из Trakt.tv",
                        "imdb_id": t_info["imdb_id"] or "",
                        "kp_id": None,
                        "imdb_rating": 0.0,
                        "kp_rating": 0.0,
                        "is_metadata_fixed": 0,
                        "title_norm": normalize_title(t_info["title"]),
                    }
                    item_id = db.insert_item(parsed, conn=conn)
                    if item_id:
                        c.execute(
                            "INSERT OR IGNORE INTO collection_items (collection_id, item_id) VALUES (?, ?)",
                            (col_id, item_id),
                        )

        # 5. Push items only in project collection to Trakt list
        movies_to_push = []
        shows_to_push = []

        for item in local_items:
            item_id, title_ru, _original_title, year, imdb_id, _kp_id, category_id = item

            # Check if already present on Trakt
            in_trakt = False
            if imdb_id and imdb_id in trakt_imdb_map:
                in_trakt = True

            # If not in Trakt, prepare push payload
            if not in_trakt:
                # We need a valid IMDb or TMDB ID. If missing, let's search TMDB right now and fill DB metadata!
                resolved_imdb = imdb_id
                resolved_tmdb = None

                if not resolved_imdb:
                    logger.info(
                        f"    [Trakt Sync] Missing IMDb ID for '{title_ru}' ({year}). Resolving on TMDB..."
                    )
                    clean_t = title_ru.split(" / ")[0].split("/")[0].strip()
                    tmdb_data = tmdb.search_movie(clean_t, year)
                    if tmdb_data:
                        resolved_imdb = tmdb_data.get("imdb_id")
                        resolved_tmdb = tmdb_data.get("tmdb_id")
                        # Fill DB metadata so we have it saved!
                        db.fill_item_metadata(
                            item_id,
                            conn=conn,
                            imdb_id=resolved_imdb,
                            poster_url=tmdb_data.get("poster_url"),
                        )
                        logger.info(
                            f"      [+] Resolved: IMDb {resolved_imdb}, TMDB {resolved_tmdb}"
                        )

                # Check again if resolved item is already in Trakt list
                if resolved_imdb and resolved_imdb in trakt_imdb_map:
                    continue

                # Format payload
                item_payload = {}
                if resolved_imdb:
                    item_payload["ids"] = {"imdb": resolved_imdb}
                elif resolved_tmdb:
                    item_payload["ids"] = {"tmdb": int(resolved_tmdb)}
                else:
                    logger.warning(
                        f"      [-] Could not resolve TMDB/IMDb ID for '{title_ru}' - skipping push."
                    )
                    continue

                if category_id in (4, 6, 10):  # TV / Show categories
                    shows_to_push.append(item_payload)
                else:
                    movies_to_push.append(item_payload)

        # Batch push to Trakt (using batch sizes of 100 for safety, though lists are usually small)
        if movies_to_push or shows_to_push:
            logger.info(
                f"    [Trakt Sync] Pushing {len(movies_to_push)} movies, {len(shows_to_push)} shows to Trakt list..."
            )
            client.add_items_to_custom_list(list_id, movies=movies_to_push, shows=shows_to_push)
        else:
            logger.info("    [Trakt Sync] Trakt list is already fully up-to-date.")

        conn.commit()

        # Be nice to the Trakt API
        import time

        time.sleep(1)

    conn.close()
    logger.info("\n=== TRAKT.TV SYNC COMPLETE ===")


if __name__ == "__main__":
    sync_trakt_collections()
