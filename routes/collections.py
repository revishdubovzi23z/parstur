import asyncio
import csv
import io

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import db
from logging_config import setup_logging
from runtime import rezka as _rezka
from runtime.ws import broadcast_threadsafe
from settings import settings

logger = setup_logging("parsclode.routes.collections", settings.log_file_path)

router = APIRouter()


@router.get("/api/collections")
def get_collections():
    return db.get_collections()


class CollectionCreate(BaseModel):
    name: str


def _rezka_folder_action(action: str, params: dict):
    if not _rezka.rezka_session:
        return None
    try:
        params["action"] = action
        resp = _rezka._rezka_request(
            "POST",
            "https://rezka.ag/ajax/favorites/",
            data=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookies=_rezka.rezka_session.cookies,
            timeout=10,
        )
        if resp is not None:
            return resp.json()
    except Exception as e:
        logger.error(f"[REZKA FOLDER ACTION ERROR] {e}", exc_info=True)
    return None


@router.post("/api/collections")
def create_collection(data: CollectionCreate):
    try:
        db.create_collection(data.name)
    except Exception:
        return {"status": "error", "message": "Коллекция существует"}
    if _rezka.rezka_session:
        _rezka_folder_action("add_cat", {"name": data.name})
    return {"status": "success"}


@router.delete("/api/collections/{id}")
def delete_collection(id: int):
    if _rezka.rezka_session:
        row = (
            db.get_connection()
            .cursor()
            .execute("SELECT name FROM collections WHERE id = ?", (id,))
            .fetchone()
        )
        if row:
            from app_core import normalize_title

            coll_norm = normalize_title(row["name"])
            cache = _rezka.rezka_session_folders_cache
            if cache and coll_norm in cache:
                _rezka_folder_action("remove_cat", {"cat_id": cache[coll_norm]})

    from settings import settings

    if settings.tmdb_api_token:
        with db._conn() as c:
            row = c.execute(
                "SELECT value FROM app_state WHERE key = ?", (f"tmdb_list_id_{id}",)
            ).fetchone()
            list_id = row[0] if row else None
        if list_id:
            from tmdb_client import TMDBClient

            client = TMDBClient()
            client.delete_list(list_id)
            with db._conn() as c:
                c.execute("DELETE FROM app_state WHERE key = ?", (f"tmdb_list_id_{id}",))

    db.delete_collection(id)
    return {"status": "success"}


class CollectionRename(BaseModel):
    name: str


@router.put("/api/collections/{id}")
def rename_collection(id: int, data: CollectionRename):
    cat_id = None
    if _rezka.rezka_session:
        row = (
            db.get_connection()
            .cursor()
            .execute("SELECT name FROM collections WHERE id = ?", (id,))
            .fetchone()
        )
        if row:
            from app_core import normalize_title

            coll_norm = normalize_title(row["name"])
            cache = _rezka.rezka_session_folders_cache
            if cache and coll_norm in cache:
                cat_id = cache[coll_norm]

    from settings import settings

    if settings.tmdb_api_token:
        with db._conn() as c:
            row = c.execute(
                "SELECT value FROM app_state WHERE key = ?", (f"tmdb_list_id_{id}",)
            ).fetchone()
            list_id = row[0] if row else None
        if list_id:
            from tmdb_client import TMDBClient

            client = TMDBClient()
            client.update_list(list_id, data.name)

    db.rename_collection(id, data.name)
    if cat_id:
        _rezka_folder_action("change_cat_name", {"cat_id": cat_id, "name": data.name})
    return {"status": "success"}


@router.get("/api/collections/export")
def collections_export(fmt: str = "json"):
    payload = db.export_collections()
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "collection_name",
                "sort_order",
                "kp_id",
                "imdb_id",
                "rezka_url",
                "title",
                "original_title",
                "year",
                "added_at",
            ]
        )
        for col in payload:
            name = col["name"]
            sort_order = col.get("sort_order") or 0
            items = col.get("items") or []
            if not items:
                writer.writerow([name, sort_order, "", "", "", "", "", "", ""])
                continue
            for it in items:
                writer.writerow(
                    [
                        name,
                        sort_order,
                        it.get("kp_id") or "",
                        it.get("imdb_id") or "",
                        it.get("rezka_url") or "",
                        it.get("title") or "",
                        it.get("original_title") or "",
                        it.get("year") or "",
                        it.get("added_at") or "",
                    ]
                )
        from fastapi.responses import Response as _Resp

        return _Resp(
            content=out.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=collections.csv"},
        )
    return JSONResponse(
        {"version": 1, "collections": payload},
        headers={"Content-Disposition": "attachment; filename=collections.json"},
    )


class CollectionsImport(BaseModel):
    collections: list[dict]
    replace: bool = False


@router.post("/api/collections/import")
def collections_import(data: CollectionsImport):
    return db.import_collections(data.collections, replace=data.replace)


@router.post("/api/collections/import_csv")
async def collections_import_csv(request: Request, replace: bool = False):
    body = await request.body()
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    grouped: dict[str, dict] = {}
    for row in reader:
        name = (row.get("collection_name") or "").strip()
        if not name:
            continue
        sort_order_raw = (row.get("sort_order") or "").strip()
        try:
            sort_order = int(sort_order_raw) if sort_order_raw else 0
        except ValueError:
            sort_order = 0
        coll = grouped.setdefault(name, {"name": name, "sort_order": sort_order, "items": []})
        has_any_ref = any(
            (row.get(k) or "").strip() for k in ("kp_id", "imdb_id", "rezka_url", "title")
        )
        if not has_any_ref:
            continue
        year_raw = (row.get("year") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        coll["items"].append(
            {
                "kp_id": (row.get("kp_id") or "").strip() or None,
                "imdb_id": (row.get("imdb_id") or "").strip() or None,
                "rezka_url": (row.get("rezka_url") or "").strip() or None,
                "title": (row.get("title") or "").strip() or None,
                "original_title": (row.get("original_title") or "").strip() or None,
                "year": year,
                "added_at": (row.get("added_at") or "").strip() or None,
            }
        )
    return db.import_collections(list(grouped.values()), replace=replace)


class CollectionItemRequest(BaseModel):
    item_id: int


def _sync_rezka_folder(action, collection_id, item_id):
    try:
        from app_core import normalize_title

        if not _rezka.rezka_session:
            return

        _c = db.get_connection().cursor()
        _c.execute("SELECT name FROM collections WHERE id = ?", (collection_id,))
        _coll = _c.fetchone()
        if not _coll:
            return

        _c.execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        _item = _c.fetchone()
        if not _item or not _item["rezka_url"]:
            return

        import re as _re

        rezka_url = _item["rezka_url"]
        _m = _re.search(
            r"/(?:films|series|cartoons|animation|show|telecasts)/[^/]+/(\d+)-",
            rezka_url,
        )
        if not _m:
            return
        post_id = _m.group(1)

        coll_norm = normalize_title(_coll["name"])
        cat_id = None
        cache = _rezka.rezka_session_folders_cache
        if cache and coll_norm in cache:
            cat_id = cache[coll_norm]
        else:
            _rezka._refresh_rezka_folders_cache()
            cache = _rezka.rezka_session_folders_cache
            if cache and coll_norm in cache:
                cat_id = cache[coll_norm]

        if not cat_id:
            return

        data = {"post_id": post_id, "cat_id": cat_id, "action": "add_post"}
        if action == "removed":
            data["del"] = "1"

        _rezka._rezka_request(
            "POST",
            "https://rezka.ag/ajax/favorites/",
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookies=_rezka.rezka_session.cookies,
            timeout=10,
        )
        _rezka._refresh_rezka_folders_cache()
    except Exception as e:
        logger.error(f"[REZKA SYNC ERROR] {e}", exc_info=True)


def _sync_rezka_folder_wrapper(action, collection_id, item_id):
    try:
        _sync_rezka_folder(action, collection_id, item_id)
    except Exception as e:
        broadcast_threadsafe(
            {
                "type": "rezka_sync_error",
                "message": f"Ошибка синхронизации с Rezka: {e}",
                "item_id": item_id,
                "collection_id": collection_id,
                "action": action,
            }
        )


def _sync_tmdb_list(action, collection_id, item_id):
    try:
        from tmdb_client import TMDBClient
        from tmdb_sync import CATEGORY_TO_MEDIA_TYPE

        client = TMDBClient()
        if not client.api_token:
            return

        with db._conn() as c:
            row = c.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (f"tmdb_list_id_{collection_id}",),
            ).fetchone()
            list_id = row[0] if row else None

        if not list_id:
            with db._conn() as c:
                row = c.execute(
                    "SELECT name FROM collections WHERE id = ?", (collection_id,)
                ).fetchone()
                coll_name = row[0] if row else None
            if not coll_name:
                return
            list_id = client.create_list(
                coll_name,
                f"Синхронизировано из Antigravity Tracker (Коллекция ID {collection_id})",
            )
            if list_id:
                with db._conn() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                        (f"tmdb_list_id_{collection_id}", list_id),
                    )
            else:
                return

        item = db.get_item(item_id)
        if not item:
            return

        imdb_id = item.get("imdb_id")
        category_id = item.get("category_id")
        media_type = CATEGORY_TO_MEDIA_TYPE.get(category_id, "movie")

        tmdb_id = None
        if imdb_id:
            meta = client.find_by_imdb_id(imdb_id, return_meta=True)
            if meta:
                tmdb_id = meta.get("tmdb_id")
                media_type = meta.get("media_type") or media_type

        if not tmdb_id:
            title = item.get("title")
            year = item.get("year")
            if title:
                meta = client.search_movie(title, year)
                if meta:
                    tmdb_id = meta.get("tmdb_id")
                    media_type = meta.get("media_type") or media_type

        if not tmdb_id:
            logger.warning(f"[TMDB SYNC] Не нашли TMDB ID для элемента {item_id}")
            return

        items_payload = [{"media_type": media_type, "media_id": int(tmdb_id)}]
        if action == "added":
            res = client.add_items_to_list(list_id, items_payload)
            logger.info(
                f"[TMDB SYNC] Добавили элемент {item_id} (TMDB: {tmdb_id}) в список {list_id}. Успешно: {res}"
            )
        elif action == "removed":
            res = client.remove_items_from_list(list_id, items_payload)
            logger.info(
                f"[TMDB SYNC] Удалили элемент {item_id} (TMDB: {tmdb_id}) из списка {list_id}. Успешно: {res}"
            )

    except Exception as e:
        logger.error(f"[TMDB SYNC ERROR] {e}", exc_info=True)


def _sync_tmdb_list_wrapper(action, collection_id, item_id):
    try:
        _sync_tmdb_list(action, collection_id, item_id)
    except Exception as e:
        logger.error(f"[TMDB SYNC WRAPPER ERROR] {e}", exc_info=True)


def _sync_kinopub_folder(action, collection_id, item_id):
    try:
        from app_core import normalize_title
        from runtime.kinopub import (
            _authenticated_client,
            add_item_to_folder,
            create_folder,
            list_bookmark_folders,
        )

        if action != "added":
            return

        try:
            api = _authenticated_client()
        except Exception:
            return

        with db._conn() as c:
            row = c.execute(
                "SELECT name FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
            coll_name = row[0] if row else None
        if not coll_name:
            return

        folders = list_bookmark_folders(client=api)
        folder_id = None
        for f in folders:
            if normalize_title(f.get("title") or "") == normalize_title(coll_name):
                folder_id = int(f["id"])
                break

        if not folder_id:
            logger.info(f"[KINOPUB SYNC] Creating folder '{coll_name}' on kino.pub")
            new_folder = create_folder(coll_name, client=api)
            folder_id = int(new_folder["id"])

        item = db.get_item(item_id)
        if not item:
            return

        kp_id_raw = item.get("kinopub_id")
        try:
            kp_id = int(kp_id_raw) if kp_id_raw else None
        except (TypeError, ValueError):
            kp_id = None

        if not kp_id:
            logger.info(f"[KINOPUB SYNC] Item {item_id} not bound to kino.pub, skipping auto-push.")
            return

        add_item_to_folder(item=kp_id, folder=folder_id, client=api)
        logger.info(f"[KINOPUB SYNC] Added item {item_id} (kp_id={kp_id}) to folder {folder_id}")

    except Exception as e:
        logger.error(f"[KINOPUB SYNC ERROR] {e}", exc_info=True)


def _sync_kinopub_folder_wrapper(action, collection_id, item_id):
    try:
        _sync_kinopub_folder(action, collection_id, item_id)
    except Exception as e:
        logger.error(f"[KINOPUB SYNC WRAPPER ERROR] {e}", exc_info=True)


@router.post("/api/collections/{collection_id}/toggle")
async def toggle_collection_item(collection_id: int, data: CollectionItemRequest):
    action = db.toggle_collection_item(collection_id, data.item_id)

    if action in ("added", "removed"):
        loop = asyncio.get_running_loop()
        if _rezka.rezka_session:
            loop.run_in_executor(
                None,
                _sync_rezka_folder_wrapper,
                action,
                collection_id,
                data.item_id,
            )
        loop.run_in_executor(
            None,
            _sync_tmdb_list_wrapper,
            action,
            collection_id,
            data.item_id,
        )
        loop.run_in_executor(
            None,
            _sync_kinopub_folder_wrapper,
            action,
            collection_id,
            data.item_id,
        )

    return {"status": "success", "action": action}


@router.get("/api/item_collections/{item_id}")
def get_item_collections(item_id: int):
    return db.get_item_collections(item_id)


class BatchCollectionsRequest(BaseModel):
    ids: list[int]


@router.post("/api/batch_item_collections")
def batch_item_collections(data: BatchCollectionsRequest):
    batch_result = db.get_item_collections_batch(data.ids)
    return {str(k): v for k, v in batch_result.items()}


class SaveOrderRequest(BaseModel):
    order: list[int]


@router.post("/api/collections/save_order")
def save_collections_order(data: SaveOrderRequest):
    db.save_collections_order(data.order)
    return {"status": "success"}
