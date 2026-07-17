"""In-process telemetry fan-out: one hub, N robots (currently 1), M browsers.

Design rules:
- A slow browser never back-pressures the robot path: per-browser bounded
  queues, drop-oldest for high-rate telemetry.
- Snapshots, events, state changes and map frames are never dropped.
- All state mutation happens on the event loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from ..protocol.envelope import Envelope, encode
from ..protocol.messages import BatteryData, EventData
from ..settings import Settings
from .store import RobotState

if TYPE_CHECKING:
    from ..database.repo import Database

log = logging.getLogger("hub")

# Message types that must never be dropped from browser queues.
PROTECTED_TYPES = {
    "server.snapshot", "state.connection", "state.robot_status",
    "state.system_health", "event.append", "telemetry.map",
    "command.ack", "command.progress", "command.result",
}


@dataclass
class RobotSession:
    robot_id: str
    state: RobotState
    websocket: WebSocket | None = None


class BrowserClient:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)

    def offer(self, frame: str, protected: bool) -> None:
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop the oldest frame to make room; protected frames therefore
            # always land (high-rate telemetry ahead of them is expendable).
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(frame)

    async def sender(self) -> None:
        while True:
            frame = await self.queue.get()
            await self.websocket.send_text(frame)


class TelemetryHub:
    def __init__(self, settings: Settings, db: "Database | None" = None) -> None:
        from ..commands.broker import CommandBroker

        self.settings = settings
        self.db = db
        self.robots: dict[str, RobotSession] = {}
        self.browsers: set[BrowserClient] = set()
        self.commands = CommandBroker(self)
        self._sequence = 0
        self._monitor_task: asyncio.Task | None = None
        self._event_id_seed = 1

    def set_event_seed(self, next_id: int) -> None:
        """Continue event ids from the persisted event log."""
        self._event_id_seed = next_id
        for session in self.robots.values():
            session.state.seed_event_id(next_id)

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        await self.commands.shutdown()
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    def get_or_create_state(self, robot_id: str) -> RobotSession:
        session = self.robots.get(robot_id)
        if session is None:
            session = RobotSession(robot_id=robot_id, state=RobotState(self.settings, robot_id))
            session.state.seed_event_id(self._event_id_seed)
            self.robots[robot_id] = session
        return session

    def primary(self) -> RobotSession | None:
        if not self.robots:
            return None
        return next(iter(self.robots.values()))

    # -- robot side ----------------------------------------------------------

    async def robot_connected(self, robot_id: str, websocket: WebSocket) -> RobotSession:
        session = self.get_or_create_state(robot_id)
        if session.websocket is not None:
            log.info("robot %s reconnected; superseding old socket", robot_id)
            with contextlib.suppress(Exception):
                await session.websocket.close(code=4000, reason="superseded by new connection")
        session.websocket = websocket
        session.state.record_heartbeat()
        await self._apply_connection(session)
        return session

    async def robot_disconnected(self, session: RobotSession, websocket: WebSocket) -> None:
        if session.websocket is websocket:
            session.websocket = None
            session.state.last_heartbeat_mono = None
            await self._apply_connection(session)

    async def handle_robot_message(self, session: RobotSession, envelope: Envelope, payload: Any) -> None:
        state = session.state
        state.record_heartbeat()
        events: list[EventData] = []
        rebroadcast = True
        data_out: Any = envelope.data

        t = envelope.type
        if t == "telemetry.heartbeat":
            rebroadcast = False
        elif t == "telemetry.pose":
            events = state.record_pose(payload)
        elif t == "telemetry.lidar":
            events = state.record_lidar(payload)
        elif t == "telemetry.path":
            events = state.record_path(payload)
        elif t == "telemetry.battery":
            events = state.record_battery(payload)
            enriched: BatteryData = payload
            if state.battery_estimate is not None:
                data_out = {**envelope.data, "estimate": state.battery_estimate.as_dict()}
            if self.db is not None:
                asyncio.get_running_loop().create_task(self.db.add_battery_sample(
                    session.robot_id, envelope.timestamp, enriched.voltage,
                    enriched.percentage, enriched.current, enriched.charging,
                ))
        elif t == "telemetry.base_state":
            events = state.record_base_state(payload)
        elif t == "telemetry.diagnostics":
            events = state.record_diagnostics(payload)
        elif t == "telemetry.resources":
            events = state.record_resources(payload)
        elif t == "telemetry.map":
            events = state.record_map(payload)
        else:
            rebroadcast = False

        if rebroadcast:
            self.publish(t, session.robot_id, data_out)
        await self._emit_events(session, events)
        self._emit_derived(session)

    async def handle_robot_command_reply(self, session: RobotSession, envelope: Envelope, payload: Any) -> None:
        session.state.record_heartbeat()
        await self.commands.handle_robot_reply(session, envelope, payload)

    async def emit_event(self, session: RobotSession, severity: str, title: str, message: str) -> None:
        await self._emit_events(session, [session.state.add_event(severity, title, message)])

    # -- browser side ----------------------------------------------------------

    async def browser_connected(self, websocket: WebSocket) -> BrowserClient:
        client = BrowserClient(websocket)
        self.browsers.add(client)
        session = self.primary()
        robot_id = session.robot_id if session else self.settings.default_robot_id
        snapshot = (session.state.snapshot() if session else
                    RobotState(self.settings, robot_id).snapshot())
        client.offer(encode("server.snapshot", robot_id, self._next_seq(), snapshot), protected=True)
        return client

    def browser_disconnected(self, client: BrowserClient) -> None:
        self.browsers.discard(client)

    # -- fan-out ---------------------------------------------------------------

    def publish(self, type_: str, robot_id: str, data: Any) -> None:
        frame = encode(type_, robot_id, self._next_seq(), data)
        protected = type_ in PROTECTED_TYPES
        for client in self.browsers:
            client.offer(frame, protected)

    async def _emit_events(self, session: RobotSession, events: list[EventData]) -> None:
        for event in events:
            self.publish("event.append", session.robot_id, event)
            if self.db is not None:
                await self.db.add_event(session.robot_id, event)

    def _emit_derived(self, session: RobotSession) -> None:
        status, health = session.state.recompute()
        if status is not None:
            self.publish("state.robot_status", session.robot_id, status)
        if health is not None:
            self.publish("state.system_health", session.robot_id, health)

    async def _apply_connection(self, session: RobotSession) -> None:
        age = session.state.heartbeat_age()
        if session.websocket is None or age is None:
            new_state = "offline"
        elif age < self.settings.online_threshold_s:
            new_state = "online"
        elif age < self.settings.offline_threshold_s:
            new_state = "stale"
        else:
            new_state = "offline"
        if new_state != session.state.connection:
            events = session.state.set_connection(new_state)
            self.publish("state.connection", session.robot_id, session.state.connection_data())
            await self._emit_events(session, events)
            self._emit_derived(session)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            for session in list(self.robots.values()):
                try:
                    await self._apply_connection(session)
                except Exception:
                    log.exception("monitor tick failed for %s", session.robot_id)
