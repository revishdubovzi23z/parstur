import asyncio
import csv
import io

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import db
from logging_config import setup_logging
from settings import settings

logger = setup_logging("parsclode.routes.collections", settings.log_file_path)

router = APIRouter()


@router.get("/api/collections")
def get_collections():
    return db.get_collections()


class CollectionCreate(BaseModel):
    name: str


def _rezka_folder_action(action: str, params: dict):
    import main
    if not main.rezka_session:
        return None
    try:
        params["action"] = action
        resp = main._rezka_request(
            "POST",
            "https://rezka.ag/ajax/favorites/",
            data=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookies=main.rezka_session.cookies,
            timeout=10,
        )
        if resp is not None:
            return resp.json()
    except Exception as e:
        logger.error(f"[REZKA FOLDER ACTION ERROR] {e}", exc_info=True)
    return None


@router.post("/api/collections")
def create_collection(data: CollectionCreate):
    import main
    try:
        db.create_collection(data.name)
    except Exception:
        return {"status": "error", "message": "Коллекция существует"}
    if main.rezka_session:
        _rezka_folder_action("add_cat", {"name": data.name})
    return {"status": "success"}


@router.delete("/api/collections/{id}")
def delete_collection(id: int):
    import main
    if main.rezka_session:
        row = (
            db.get_connection()
            .cursor()
            .execute("SELECT name FROM collections WHERE id = ?", (id,))
            .fetchone()
        )
        if row:
            from app_core import normalize_title

            coll_norm = normalize_title(row["name"])
            if main.rezka_session_folders_cache and coll_norm in main.rezka_session_folders_cache:
                _rezka_folder_action(
                    "remove_cat", {"cat_id": main.rezka_session_folders_cache[coll_norm]}
                )
    db.delete_collection(id)
    return {"status": "success"}


class CollectionRename(BaseModel):
    name: str


@router.put("/api/collections/{id}")
def rename_collection(id: int, data: CollectionRename):
    import main
    cat_id = None
    if main.rezka_session:
        row = (
            db.get_connection()
            .cursor()
            .execute("SELECT name FROM collections WHERE id = ?", (id,))
            .fetchone()
        )
        if row:
            from app_core import normalize_title

            coll_norm = normalize_title(row["name"])
            if main.rezka_session_folders_cache and coll_norm in main.rezka_session_folders_cache:
                cat_id = main.rezka_session_folders_cache[coll_norm]
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
    import main
    try:
        from app_core import normalize_title

        if not main.rezka_session:
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
        if main.rezka_session_folders_cache and coll_norm in main.rezka_session_folders_cache:
            cat_id = main.rezka_session_folders_cache[coll_norm]
        else:
            main._refresh_rezka_folders_cache()
            if main.rezka_session_folders_cache and coll_norm in main.rezka_session_folders_cache:
                cat_id = main.rezka_session_folders_cache[coll_norm]

        if not cat_id:
            return

        data = {"post_id": post_id, "cat_id": cat_id, "action": "add_post"}
        if action == "removed":
            data["del"] = "1"

        main._rezka_request(
            "POST",
            "https://rezka.ag/ajax/favorites/",
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookies=main.rezka_session.cookies,
            timeout=10,
        )
        main._refresh_rezka_folders_cache()
    except Exception as e:
        logger.error(f"[REZKA SYNC ERROR] {e}", exc_info=True)


def _sync_rezka_folder_wrapper(action, collection_id, item_id):
    import main
    try:
        _sync_rezka_folder(action, collection_id, item_id)
    except Exception as e:
        main._broadcast_threadsafe(
            {
                "type": "rezka_sync_error",
                "message": f"Ошибка синхронизации с Rezka: {e}",
                "item_id": item_id,
                "collection_id": collection_id,
                "action": action,
            }
        )


@router.post("/api/collections/{collection_id}/toggle")
async def toggle_collection_item(collection_id: int, data: CollectionItemRequest):
    import main
    action = db.toggle_collection_item(collection_id, data.item_id)

    if action in ("added", "removed") and main.rezka_session:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _sync_rezka_folder_wrapper, action, collection_id, data.item_id)

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
