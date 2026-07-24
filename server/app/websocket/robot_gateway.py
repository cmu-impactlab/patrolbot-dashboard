"""Inbound WebSocket endpoint for robots (real bridge or mock): /ws/robot."""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..protocol.envelope import ProtocolError, decode, encode
from ..protocol.messages import COMMAND_PREFIX, HelloAckData, HelloData

log = logging.getLogger("robot_gateway")
router = APIRouter()


def _extract_token(websocket: WebSocket) -> str:
    """Prefer the Authorization header so the credential never lands in
    reverse-proxy access logs. Fall back to the legacy ?token= query string
    (deprecated) so a not-yet-updated bridge keeps connecting during rollout."""
    auth = websocket.headers.get("authorization", "")
    if auth:
        prefix = "bearer "
        return auth[len(prefix):].strip() if auth.lower().startswith(prefix) else auth.strip()
    token = websocket.query_params.get("token", "")
    if token:
        peer = websocket.client.host if websocket.client else "unknown"
        log.warning("robot from %s authenticated via the deprecated ?token= query "
                    "param; switch the bridge to the Authorization header", peer)
    return token


@router.websocket("/ws/robot")
async def robot_ws(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    settings = websocket.app.state.settings

    token = _extract_token(websocket)
    if not token or not hmac.compare_digest(token, settings.robot_token):
        await websocket.close(code=4401, reason="invalid token")
        return
    await websocket.accept()

    # First frame must be robot.hello.
    try:
        raw = await websocket.receive_text()
        envelope, payload = decode(raw)
    except WebSocketDisconnect:
        return
    except ProtocolError as exc:
        await websocket.close(code=4400, reason=str(exc)[:120])
        return
    if envelope.type != "robot.hello" or not isinstance(payload, HelloData):
        await websocket.close(code=4400, reason="first frame must be robot.hello")
        return

    session = await hub.robot_connected(envelope.robot_id, websocket)
    state_map = session.state.map.data
    want_map = state_map is None or state_map.map_version != payload.map_version
    await websocket.send_text(
        encode("server.hello_ack", envelope.robot_id, 0, HelloAckData(want_map=want_map))
    )
    log.info("robot %s connected (want_map=%s)", envelope.robot_id, want_map)

    last_seq = -1
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                envelope, payload = decode(raw)
            except ProtocolError as exc:
                log.warning("dropping bad frame from %s: %s", session.robot_id, exc)
                continue
            # A frame must belong to this robot's session and carry a strictly
            # increasing sequence (TCP preserves order within a connection, so
            # anything else is a stale replay or a spoofed identity).
            if envelope.robot_id != session.robot_id:
                log.warning("dropping frame from %s with mismatched robot_id %s",
                            session.robot_id, envelope.robot_id)
                continue
            if envelope.sequence <= last_seq:
                log.warning("dropping out-of-order frame from %s (seq %s <= %s)",
                            session.robot_id, envelope.sequence, last_seq)
                continue
            last_seq = envelope.sequence
            if envelope.type.startswith(COMMAND_PREFIX):
                await hub.handle_robot_command_reply(session, envelope, payload)
                continue
            await hub.handle_robot_message(session, envelope, payload)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.robot_disconnected(session, websocket)
        log.info("robot %s disconnected", session.robot_id)
