"""Stage 14 — 🔒 Proxy HTTP endpoints.

Status / connectivity-test / reload for the per-service outbound proxy
manager. Proxy tests do blocking network I/O so they run off the loop.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from proxy_manager import KNOWN_SERVICES, proxy_manager

router = APIRouter()


@router.get("/api/proxy/status")
async def proxy_status():
    return await asyncio.to_thread(proxy_manager.status)


@router.post("/api/proxy/test/{service}")
async def proxy_test(service: str):
    if service not in KNOWN_SERVICES:
        return JSONResponse({"error": f"unknown service: {service}"}, status_code=400)
    return await asyncio.to_thread(proxy_manager.test_proxy, service)


@router.post("/api/proxy/reload")
async def proxy_reload():
    await asyncio.to_thread(proxy_manager.stop_all)
    return {
        "status": "success",
        "detail": "Proxy processes stopped; they will restart on next request.",
    }
