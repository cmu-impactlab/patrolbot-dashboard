"""Inbound WebSocket endpoint for robots (real bridge or mock): /ws/robot."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..protocol.envelope import ProtocolError, decode, encode
from ..protocol.messages import COMMAND_PREFIX, HelloAckData, HelloData

log = logging.getLogger("robot_gateway")
router = APIRouter()


@router.websocket("/ws/robot")
async def robot_ws(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    settings = websocket.app.state.settings

    token = websocket.query_params.get("token", "")
    if token != settings.robot_token:
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

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                envelope, payload = decode(raw)
            except ProtocolError as exc:
                log.warning("dropping bad frame from %s: %s", session.robot_id, exc)
                continue
            if envelope.type.startswith(COMMAND_PREFIX):
                await hub.handle_robot_command_reply(session, envelope, payload)
                continue
            await hub.handle_robot_message(session, envelope, payload)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.robot_disconnected(session, websocket)
        log.info("robot %s disconnected", session.robot_id)
