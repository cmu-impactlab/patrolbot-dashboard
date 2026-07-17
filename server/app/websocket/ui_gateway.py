"""Browser-facing WebSocket endpoint: /ws/ui.

Browsers are read-only in Phases 1–2: any inbound frame other than a ping is
ignored, and command.* frames are explicitly rejected until Phase 3.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("ui_gateway")
router = APIRouter()


@router.websocket("/ws/ui")
async def ui_ws(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    await websocket.accept()
    client = await hub.browser_connected(websocket)
    sender = asyncio.create_task(client.sender())
    try:
        while True:
            raw = await websocket.receive_text()
            if '"command.' in raw:
                log.warning("browser sent a command frame; commands arrive in Phase 3 — ignored")
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        hub.browser_disconnected(client)
