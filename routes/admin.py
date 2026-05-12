import asyncio
import csv
import io
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from db import db
from settings import settings

router = APIRouter()

@router.get("/api/backup/download")
async def backup_download():
    import main
    out_dir = "backups"
    os.makedirs(out_dir, exist_ok=True)
    from datetime import timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(out_dir, f"app_data-{stamp}.db")
    try:
        # SQLite backup is blocking I/O; push it off the loop.
        size = await asyncio.to_thread(db.backup_to, dest)
    except Exception as e:
        return JSONResponse(
            {"error": f"{type(e).__name__}: {e}"},
            status_code=500,
        )
    main.logger.info(f"[BACKUP] wrote {dest} ({size} bytes)")
    return FileResponse(
        dest,
        media_type="application/octet-stream",
        filename=os.path.basename(dest),
    )


@router.get("/api/export")
def export_data(fmt: str = "json", category_id: int = -1):
    items = db.export_items(category_id)

    if fmt == "csv":
        output = io.StringIO()
        if items:
            writer = csv.DictWriter(output, fieldnames=items[0].keys())
            writer.writeheader()
            writer.writerows(items)
        from fastapi.responses import Response as _Response

        return _Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )

    return JSONResponse(
        content=items,
        headers={"Content-Disposition": "attachment; filename=export.json"},
    )


@router.post("/api/self_update")
def self_update():
    import subprocess

    import main

    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr.strip()[:500]}
        if "Already up to date" in output:
            return {"status": "up_to_date", "message": output}

        restarted = main._trigger_restart()
        msg = output
        if restarted:
            msg += "\n\nServer is restarting..."
        else:
            msg += "\n\nUpdate complete. Please restart the server manually."

        return {"status": "updated", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)[:500]}


@router.get("/api/database_export")
def database_export():
    db_path = settings.resolved_db_path
    if not os.path.exists(db_path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(
        db_path,
        media_type="application/x-sqlite3",
        filename=os.path.basename(db_path),
    )


@router.post("/api/database_import")
async def database_import(file: UploadFile):
    import main
    db_path = settings.resolved_db_path
    content = await file.read()

    # 5.1: Security/Integrity check
    # SQLite file must start with specific magic bytes.
    if not content.startswith(b"SQLite format 3\x00"):
        return JSONResponse(
            {"status": "error", "message": "Invalid SQLite file (header mismatch)"},
            status_code=400,
        )

    if len(content) < 100:
        return JSONResponse({"status": "error", "message": "File too small"}, status_code=400)

    # 5.1: Create backup before overwrite
    if os.path.exists(db_path):
        import shutil
        from datetime import datetime

        backup_dir = os.path.join(settings.app_data_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"pre_import_{timestamp}.db")
        try:
            shutil.copy2(db_path, backup_path)
            main.logger.info(f"Backup created: {backup_path}")
        except Exception as e:
            main.logger.error(f"Failed to create backup: {e}")
            # We continue anyway, as the user requested an import.

    with open(db_path, "wb") as f:
        f.write(content)

    restarted = main._trigger_restart()
    if restarted:
        return {"status": "success", "message": "Database imported, server is restarting..."}
    return {"status": "success", "message": "Database imported, please restart server manually"}


@router.get("/api/reset_database/token")
def get_reset_token():
    from datetime import datetime, timedelta

    import main

    token = secrets.token_urlsafe(16)
    expiry = datetime.now() + timedelta(seconds=60)
    main._reset_tokens[token] = expiry
    return {"token": token}


@router.post("/api/reset_database")
def reset_database(confirm: str | None = None):
    from datetime import datetime

    import main

    if not confirm or confirm not in main._reset_tokens:
        return JSONResponse(
            {"status": "error", "message": "Confirmation token missing or invalid"},
            status_code=403,
        )

    expiry = main._reset_tokens.pop(confirm)
    if datetime.now() > expiry:
        return JSONResponse(
            {"status": "error", "message": "Confirmation token expired"}, status_code=403
        )

    db_path = settings.resolved_db_path
    if not os.path.exists(db_path):
        return {"status": "error", "message": "Database file not found"}
    os.remove(db_path)

    restarted = main._trigger_restart()
    if restarted:
        return {"status": "success", "message": "Database deleted, server is restarting..."}
    return {"status": "success", "message": "Database deleted, please restart server manually"}


