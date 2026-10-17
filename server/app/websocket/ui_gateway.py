"""Browser-facing WebSocket endpoint: /ws/ui.

Commands are validated and audited; manual samples have their own connection-
bound admission and rate cap. Authorized end-input commands never wait for a
normal-command flood budget.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import get_args

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..protocol.envelope import Envelope, ProtocolError, decode
from ..protocol.messages import CommandRequestData, CommandType, TeleopVelocityData

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
            try:
                envelope, payload = decode(raw)
            except ProtocolError as exc:
                if frames_in_window > frame_cap:
                    continue
                log.warning("dropping bad browser frame: %s", exc)
                try:
                    invalid = Envelope.model_validate_json(raw)
                except ValueError:
                    continue
                command_id = invalid.data.get("command_id")
                if (invalid.type != "command.request" or not isinstance(command_id, str)
                        or not command_id.strip() or len(command_id) > 128):
                    continue
                command = invalid.data.get("command")
                if not isinstance(command, str) or len(command) > 64:
                    command = "invalid"
                reason = ("Unsupported command. Refresh the dashboard and try again."
                          if command not in get_args(CommandType) else
                          "Invalid command fields. Check the goal coordinates and request fields.")
                await hub.commands._reject_audited(
                    client, invalid.robot_id, command_id, command, reason)
                continue
            if envelope.type == "teleop.velocity" and isinstance(payload, TeleopVelocityData):
                await hub.commands.teleop.velocity(client, envelope, payload)
                continue
            emergency = (isinstance(payload, CommandRequestData) and payload.command in
                         {"stop", "motor_disable", "teleop_stop", "software_reset"})
            if frames_in_window > frame_cap and not emergency:
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
