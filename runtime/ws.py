"""WebSocket fan-out and thread-safe broadcast.

`ConnectionManager` owns the list of active `WebSocket` connections and
exposes a parallel `broadcast` that fans out to every client via
`asyncio.gather`. Dead connections are reaped lazily through the
`return_exceptions=True` result inspection.

`broadcast_threadsafe` lets background threads (e.g. functions invoked
via `run_in_executor`) post messages onto the ASGI loop without holding
a reference to it themselves — they call this helper which forwards to
the loop captured during the FastAPI lifespan startup hook.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("parsclode.runtime.ws")

# Captured by `set_main_loop` during the FastAPI lifespan startup so
# worker threads (run_in_executor callbacks) can schedule coroutines
# back onto the main loop with asyncio.run_coroutine_threadsafe.
# In a worker thread asyncio.get_event_loop() returns a *different*
# loop (or a new one in 3.12+), so it cannot be used for that.
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Record the ASGI event loop for `broadcast_threadsafe` callers."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    return _main_loop


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        # Fan out send_json calls in parallel via asyncio.gather.
        # Previously a sequential loop blocked every client behind a
        # slow/disconnected one (TCP send to a half-closed socket
        # can sit for ~minutes). Fanning out keeps broadcast latency
        # at max(slowest client) rather than sum(all clients).
        # return_exceptions=True so a single failing client doesn't
        # abort the whole gather; failed clients are dropped via the
        # result inspection loop below.
        if not self.active:
            return
        sockets = list(self.active)
        results = await asyncio.gather(
            *(ws.send_json(message) for ws in sockets),
            return_exceptions=True,
        )
        for ws, result in zip(sockets, results, strict=True):
            if isinstance(result, BaseException):
                self.disconnect(ws)


ws_manager = ConnectionManager()


def broadcast_threadsafe(message: dict) -> None:
    """Schedule `ws_manager.broadcast` on the main loop from any thread.

    Safe to call from worker threads spawned by `run_in_executor` —
    grabs the loop captured during lifespan startup and submits the
    coroutine cross-thread. If the loop hasn't been captured yet
    (e.g. broadcast attempted before startup completed) or has been
    closed, silently drops the message rather than raising.
    """
    loop = _main_loop
    if loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(message), loop)
    except Exception as e:
        logger.error(f"[WS] threadsafe broadcast failed: {type(e).__name__}: {e}")
