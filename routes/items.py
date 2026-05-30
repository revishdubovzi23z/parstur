from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import db
from runtime.processes import run_script_with_args, task_queue

router = APIRouter()


class ResetFieldsRequest(BaseModel):
    fields: list[str]


class SetIdsRequest(BaseModel):
    kp_id: str | None = None
    imdb_id: str | None = None


class RebindRequest(BaseModel):
    kp_id: str | None = None
    imdb_id: str | None = None
    rezka_url: str | None = None


@router.get("/api/item/{item_id}")
def get_item(item_id: int):
    """Return a single item with its releases and collection ids.

    ROADMAP 10.7f — SPA item-card modal needs the same payload shape
    that the feed embeds per item, but for one item at a time. We
    bundle the item row, its releases (`db.get_releases`) and
    `db.get_item_collections` to avoid three separate round-trips.
    """
    item = db.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "item not found"}, status_code=404)
    releases = db.get_releases(item_id)
    collections = db.get_item_collections(item_id)
    from settings import settings

    return {
        "item": item,
        "releases": releases,
        "collections": collections,
        "config": {
            "rezka_enabled": getattr(settings, "rezka_enabled", True),
            "kinohub_enabled": getattr(settings, "kinohub_enabled", True),
            "kinopub_enabled": settings.kinopub_enabled,
        },
    }


async def _run_single_update(item_id: int, log_file: str):
    from runtime.ws import ws_manager

    await run_script_with_args(
        "single_item_update.py",
        [str(item_id)],
        "single_update",
        log_file,
    )
    # Broadcast specifically that THIS item was updated so the frontend can refresh it
    await ws_manager.broadcast({"type": "item_updated", "item_id": item_id})


@router.post("/api/update_item/{item_id}")
async def update_item(item_id: int):
    log_file = "single_update_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Обновление карточки ID {item_id} ===\n")

    await task_queue.add_task(_run_single_update, "single_update", item_id, log_file)
    return {"status": "started"}


@router.post("/api/ignore/{item_id}")
def ignore_item(item_id: int):
    new_state = db.toggle_ignore(item_id)
    if new_state < 0:
        return {"status": "error"}
    return {"status": "success"}


@router.post("/api/reset_item/{item_id}")
async def reset_item(item_id: int, data: ResetFieldsRequest):
    from logging_config import setup_logging
    from settings import settings

    logger = setup_logging("routes.items", settings.log_file_path)
    logger.info(f"[API] Reset request for item {item_id}, fields: {data.fields}")
    db.reset_item(item_id, data.fields)
    from runtime.ws import ws_manager

    await ws_manager.broadcast({"type": "item_updated", "item_id": item_id})
    return {"status": "success"}


@router.post("/api/set_ids/{item_id}")
def set_ids(item_id: int, data: SetIdsRequest):
    db.set_ids(item_id, kp_id=data.kp_id, imdb_id=data.imdb_id)
    return {"status": "success"}


@router.post("/api/rebind/{item_id}")
def rebind_item(item_id: int, data: RebindRequest):
    import json as _json

    with db._conn() as c:
        row = c.execute(
            "SELECT kp_id, imdb_id, rezka_url FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return JSONResponse({"error": "item not found"}, status_code=404)
        before = {
            "kp_id": row["kp_id"],
            "imdb_id": row["imdb_id"],
            "rezka_url": row["rezka_url"],
        }

    after = {**before}
    sets: list[str] = []
    params: list = []
    if data.kp_id is not None:
        after["kp_id"] = data.kp_id.strip() or None
        sets.append("kp_id = ?")
        params.append(after["kp_id"])
        sets.extend(["checked_poiskkino = 0", "checked_tech = 0", "checked_rezka = 0"])
    if data.imdb_id is not None:
        after["imdb_id"] = data.imdb_id.strip() or None
        sets.append("imdb_id = ?")
        params.append(after["imdb_id"])
        sets.append("checked_rezka = 0")
    if data.rezka_url is not None:
        after["rezka_url"] = data.rezka_url.strip() or None
        sets.append("rezka_url = ?")
        params.append(after["rezka_url"])
    if not sets:
        return {"status": "noop"}
    sets.extend(["is_metadata_fixed = 0", "is_reprocessed = 0"])
    params.append(item_id)
    with db._conn() as c:
        c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", params)

    db.append_audit(
        action="rebind",
        item_id=item_id,
        field="kp_id,imdb_id,rezka_url",
        old_value=_json.dumps(before, ensure_ascii=False),
        new_value=_json.dumps(after, ensure_ascii=False),
    )
    return {"status": "success", "before": before, "after": after}


class RateRequest(BaseModel):
    rating: int | None = None  # 1–10 or None


class WatchedRequest(BaseModel):
    watched: bool


@router.post("/api/item/{item_id}/rate")
def rate_item_endpoint(item_id: int, data: RateRequest):
    if data.rating is not None and (data.rating < 1 or data.rating > 10):
        return JSONResponse({"error": "rating must be between 1 and 10"}, status_code=400)
    db.rate_item(item_id, data.rating)
    return {"status": "success"}


@router.post("/api/item/{item_id}/watched")
def mark_watched_endpoint(item_id: int, data: WatchedRequest):
    db.mark_watched(item_id, data.watched)
    return {"status": "success"}
