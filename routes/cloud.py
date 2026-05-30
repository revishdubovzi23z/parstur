"""Stage 13 — ☁️ Cloud sync HTTP endpoints.

Thin async wrappers around ``cloud_sync``; the actual push/pull is
blocking network + SQLite I/O so we push it off the event loop with
``asyncio.to_thread``.

``/progress`` and ``/cancel`` let the web UI poll the in-flight sync and
stop a long-running manual push/pull. ``cancel`` and ``progress`` only
touch lightweight in-memory state, so they stay responsive even while a
push/pull thread is busy.
"""

import asyncio

from fastapi import APIRouter

from cloud_sync import cloud_sync

router = APIRouter()


@router.get("/api/cloud/status")
async def cloud_status():
    return await asyncio.to_thread(cloud_sync.get_status)


@router.get("/api/cloud/progress")
async def cloud_progress():
    # In-memory snapshot only — safe to read synchronously.
    return cloud_sync.get_progress()


@router.post("/api/cloud/push")
async def cloud_push():
    return await asyncio.to_thread(cloud_sync.push)


@router.post("/api/cloud/pull")
async def cloud_pull():
    return await asyncio.to_thread(cloud_sync.pull)


@router.post("/api/cloud/cancel")
async def cloud_cancel():
    # Sets a thread-safe flag; the running push/pull stops at the next
    # batch boundary. Does not block on the worker thread.
    return cloud_sync.request_cancel()
