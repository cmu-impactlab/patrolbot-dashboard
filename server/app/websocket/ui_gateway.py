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


def _origin_allowed(settings, websocket: WebSocket) -> bool:
    """Reject cross-origin command sockets. An empty allowlist disables the
    check (local dev); production startup requires it to be set."""
    allowed = {o.strip() for o in settings.allowed_origins.split(",") if o.strip()}
    if not allowed:
        return True
    return websocket.headers.get("origin") in allowed


@router.websocket("/ws/ui")
async def ui_ws(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    settings = websocket.app.state.settings
    from ..authentication.local import resolve_ws_user

    if not _origin_allowed(settings, websocket):
        log.warning("rejecting /ws/ui from disallowed origin: %s",
                    websocket.headers.get("origin"))
        await websocket.close(code=4403, reason="origin not allowed")
        return

    user = resolve_ws_user(settings, websocket)
    if user is None:  # OIDC mode with no valid session
        await websocket.close(code=4401, reason="not signed in")
        return
    await websocket.accept()
    source_ip = websocket.client.host if websocket.client else None
    client = await hub.browser_connected(websocket, user=user, source_ip=source_ip)
    sender = asyncio.create_task(client.sender())
    frame_cap = max(1, settings.max_ui_frames_per_s)
    window_start = 0.0
    frames_in_window = 0
    try:
        while True:
            raw = await websocket.receive_text()
            # Per-connection flood guard: drop frames beyond the per-second cap.
            now = asyncio.get_running_loop().time()
            if now - window_start >= 1.0:
                window_start, frames_in_window = now, 0
            frames_in_window += 1
            if frames_in_window > frame_cap:
                log.warning("dropping browser frame: rate cap (%d/s) exceeded", frame_cap)
                continue
            try:
                envelope, payload = decode(raw)
            except ProtocolError as exc:
                log.warning("dropping bad browser frame: %s", exc)
                continue
            if envelope.type == "command.request" and isinstance(payload, CommandRequestData):
                await hub.commands.handle_browser_request(client, envelope, payload)
            else:
                log.warning("ignoring unexpected browser frame: %s", envelope.type)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        hub.browser_disconnected(client)
