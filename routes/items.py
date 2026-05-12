from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import db

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


@router.post("/api/update_item/{item_id}")
async def update_item(item_id: int):
    from main import run_script_with_args, task_queue

    log_file = "single_update_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Обновление карточки ID {item_id} ===\n")

    await task_queue.add_task(
        run_script_with_args,
        "single_update",
        "single_item_update.py",
        [str(item_id)],
        "single_update",
        log_file,
    )
    return {"status": "started"}


@router.post("/api/ignore/{item_id}")
def ignore_item(item_id: int):
    new_state = db.toggle_ignore(item_id)
    if new_state < 0:
        return {"status": "error"}
    return {"status": "success"}


@router.post("/api/reset_item/{item_id}")
def reset_item(item_id: int, data: ResetFieldsRequest):
    db.reset_item(item_id, data.fields)
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
