import asyncio
import csv
import io
import logging
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from db import db
from runtime.admin import _reset_tokens, _trigger_restart
from settings import settings

logger = logging.getLogger("parsclode.routes.admin")

router = APIRouter()


@router.get("/api/backup/download")
async def backup_download():
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
    logger.info(f"[BACKUP] wrote {dest} ({size} bytes)")
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
    import sys

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frontend_dir = os.path.join(project_root, "frontend")

    def run_cmd(cmd: list[str], cwd: str, timeout: int = 60):
        logger.info(f"[UPDATE] Running: {' '.join(cmd)} in {cwd}")
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        if res.returncode != 0:
            raise Exception(f"Command '{' '.join(cmd)}' failed: {res.stderr}")
        return res.stdout.strip()

    try:
        # 1. Git Pull
        output = run_cmd(["git", "pull"], project_root)
        if "Already up to date" in output and os.path.isdir(os.path.join(frontend_dir, "dist")):
            # If code is up to date AND dist exists, we might not need to do anything.
            # But to be safe, let's at least check dependencies if the user clicked.
            pass

        # 2. Pip Install
        # Use sys.executable to ensure we use the same python/venv
        run_cmd(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], project_root, 120
        )

        # 3. Frontend Build (if directory exists)
        if os.path.isdir(frontend_dir):
            # Check for npm
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            try:
                run_cmd([npm_cmd, "install"], frontend_dir, 300)
                run_cmd([npm_cmd, "run", "build"], frontend_dir, 300)
            except Exception as e:
                logger.warning(f"[UPDATE] Frontend build skipped or failed: {e}")
                # We don't fail the whole update if only frontend failed,
                # but we should report it.

        # 4. Trigger Restart
        restarted = _trigger_restart()
        msg = "Update successful. "
        if restarted:
            msg += "Server is restarting..."
        else:
            msg += "Please restart the server manually to apply changes."

        return {"status": "updated", "message": msg}

    except Exception as e:
        logger.error(f"[UPDATE] Update failed: {e}")
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
            logger.info(f"Backup created: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            # We continue anyway, as the user requested an import.

    with open(db_path, "wb") as f:
        f.write(content)

    restarted = _trigger_restart()
    if restarted:
        return {"status": "success", "message": "Database imported, server is restarting..."}
    return {"status": "success", "message": "Database imported, please restart server manually"}


@router.get("/api/reset_database/token")
def get_reset_token():
    from datetime import datetime, timedelta

    token = secrets.token_urlsafe(16)
    expiry = datetime.now() + timedelta(seconds=60)
    _reset_tokens[token] = expiry
    return {"token": token}


@router.post("/api/reset_database")
def reset_database(confirm: str | None = None):
    from datetime import datetime

    if not confirm or confirm not in _reset_tokens:
        return JSONResponse(
            {"status": "error", "message": "Confirmation token missing or invalid"},
            status_code=403,
        )

    expiry = _reset_tokens.pop(confirm)
    if datetime.now() > expiry:
        return JSONResponse(
            {"status": "error", "message": "Confirmation token expired"}, status_code=403
        )

    db_path = settings.resolved_db_path
    if not os.path.exists(db_path):
        return {"status": "error", "message": "Database file not found"}
    os.remove(db_path)

    restarted = _trigger_restart()
    if restarted:
        return {"status": "success", "message": "Database deleted, server is restarting..."}
    return {"status": "success", "message": "Database deleted, please restart server manually"}
