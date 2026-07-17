"""Browser-facing WebSocket endpoint: /ws/ui.

Inbound frames from browsers are ignored except command.request, which is
routed through the CommandBroker (validated, audited, timeout-protected).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..protocol.envelope import ProtocolError, decode
from ..protocol.messages import CommandRequestData

log = logging.getLogger("ui_gateway")
router = APIRouter()


@router.websocket("/ws/ui")
async def ui_ws(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    settings = websocket.app.state.settings
    if settings.auth_mode == "oidc":
        from ..authentication.sessions import COOKIE_NAME, verify

        if verify(settings.session_secret, websocket.cookies.get(COOKIE_NAME)) is None:
            await websocket.close(code=4401, reason="not signed in")
            return
    await websocket.accept()
    client = await hub.browser_connected(websocket)
    sender = asyncio.create_task(client.sender())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                envelope, payload = decode(raw)
            except ProtocolError as exc:
                log.warning("dropping bad browser frame: %s", exc)
                continue
            if envelope.type == "command.request" and isinstance(payload, CommandRequestData):
                await hub.commands.handle_browser_request(envelope, payload)
            else:
                log.warning("ignoring unexpected browser frame: %s", envelope.type)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        hub.browser_disconnected(client)
