import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import db
from settings import settings
from trakt_client import TraktClient

logger = logging.getLogger("parsclode.routes.trakt")

router = APIRouter(prefix="/api/trakt", tags=["trakt"])


class ExchangeRequest(BaseModel):
    pin: str = Field(min_length=1, max_length=128)


@router.get("/status")
def get_status():
    client = TraktClient()
    return {
        "enabled": bool(settings.trakt_client_id and settings.trakt_client_secret),
        "authenticated": bool(client.access_token),
    }


@router.post("/auth/start")
def auth_start():
    if not settings.trakt_client_id or not settings.trakt_client_secret:
        return {
            "status": "error",
            "message": "TRAKT_CLIENT_ID or TRAKT_CLIENT_SECRET is not set in .env",
        }

    client = TraktClient()
    return {"status": "success", "url": client.get_auth_url()}


@router.post("/auth/exchange")
def auth_exchange(body: ExchangeRequest):
    client = TraktClient()
    if client.exchange_code(body.pin):
        return {"status": "success", "message": "Successfully authenticated with Trakt.tv!"}
    else:
        raise HTTPException(
            status_code=400,
            detail="Failed to authenticate. Please check the PIN-code and try again.",
        )


@router.post("/logout")
def logout():
    with db._conn() as c:
        c.execute("DELETE FROM app_state WHERE key = 'trakt_access_token'")
    return {"status": "success"}
