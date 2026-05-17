import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import db
from settings import settings

logger = logging.getLogger("parsclode.routes.tmdb")

router = APIRouter(prefix="/api/tmdb", tags=["tmdb"])


@router.get("/status")
def get_status():
    with db._conn() as c:
        row = c.execute("SELECT value FROM app_state WHERE key = 'tmdb_access_token'").fetchone()
        token = row[0] if row else None
    return {
        "enabled": bool(settings.tmdb_api_key),
        "authenticated": bool(token),
    }


@router.post("/auth/start")
async def auth_start(request: Request):
    if not settings.tmdb_api_token:
        return {
            "status": "error",
            "message": "TMDB_API_TOKEN is not set in .env",
        }

    url = "https://api.themoviedb.org/4/auth/request_token"

    # We need to know our own URL for callback
    base_url = str(request.base_url).rstrip("/")
    callback_url = f"{base_url}/api/tmdb/auth/callback"

    payload = {"redirect_to": callback_url}
    headers = {
        "Authorization": f"Bearer {settings.tmdb_api_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error(f"Failed to get TMDB request token: {resp.text}")
                return {
                    "status": "error",
                    "message": f"Failed to get request token: {resp.text}",
                }

            data = resp.json()
            request_token = data.get("request_token")
        except Exception as e:
            logger.error(f"Error calling TMDB request_token: {e}")
            return {"status": "error", "message": str(e)}

    if not request_token:
        return {"status": "error", "message": "No request token returned"}

    # Save to app_state
    with db._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES ('pending_tmdb_request_token', ?)",
            (request_token,),
        )

    redirect_url = f"https://www.themoviedb.org/auth/access?request_token={request_token}"
    return {"status": "success", "url": redirect_url}


@router.get("/auth/callback")
async def auth_callback(request_token: str | None = None):
    # If TMDB didn't pass it, read it from app_state
    if not request_token:
        with db._conn() as c:
            row = c.execute(
                "SELECT value FROM app_state WHERE key = 'pending_tmdb_request_token'"
            ).fetchone()
            request_token = row[0] if row else None

    if not request_token:
        return HTMLResponse("<h1>Ошибка: не найден request token</h1>")

    url = "https://api.themoviedb.org/4/auth/access_token"
    payload = {"request_token": request_token}
    headers = {
        "Authorization": f"Bearer {settings.tmdb_api_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error(f"Failed to exchange TMDB access token: {resp.text}")
                return HTMLResponse(f"<h1>Ошибка авторизации: {resp.text}</h1>")

            data = resp.json()
            access_token = data.get("access_token")
            account_id = data.get("account_id")
        except Exception as e:
            logger.error(f"Error exchanging TMDB access token: {e}")
            return HTMLResponse(f"<h1>Ошибка: {e}</h1>")

    if not access_token:
        return HTMLResponse("<h1>Не удалось получить access token</h1>")

    # Save to app_state
    with db._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES ('tmdb_access_token', ?)",
            (access_token,),
        )
        if account_id:
            c.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES ('tmdb_account_id', ?)",
                (str(account_id),),
            )

    return HTMLResponse("<h1>Авторизация успешна! Вы можете закрыть это окно.</h1>")


@router.post("/logout")
def logout():
    with db._conn() as c:
        c.execute("DELETE FROM app_state WHERE key = 'tmdb_access_token'")
        c.execute("DELETE FROM app_state WHERE key = 'tmdb_account_id'")
    return {"status": "success"}
