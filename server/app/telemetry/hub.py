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
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from ..protocol.envelope import Envelope, encode
from ..protocol.messages import BatteryData, CapabilitiesData, EventData
from ..settings import Settings
from .store import RobotState

if TYPE_CHECKING:
    from ..database.repo import Database

log = logging.getLogger("hub")

# Message types that must never be dropped from browser queues.
PROTECTED_TYPES = {
    "server.snapshot", "state.connection", "state.robot_status",
    "state.system_health", "state.capabilities", "event.append", "telemetry.map",
    "command.ack", "command.progress", "command.result",
}


@dataclass
class RobotSession:
    robot_id: str
    state: RobotState
    websocket: WebSocket | None = None


QUEUE_LIMIT = 64
# WebSocket close code 1013 "Try Again Later" — the browser's own reconnect
# logic treats this like any other drop and comes back for a fresh snapshot.
OVERLOADED_CLOSE_CODE = 1013


class BrowserClient:
    """One browser's outbound queue.

    Frames carry whether they are protected, because *which* frame gets dropped
    under backpressure is a safety question. The queue used to drop its oldest
    entry regardless: a browser that fell behind during a lidar burst could
    lose the command.result closing out a command (leaving the operator staring
    at a spinner for a command that finished), the state.connection saying the
    robot went offline, or an event.append reporting an e-stop — while the
    lidar frames that caused the backlog sailed through.

    So eviction only ever takes an unprotected frame. If there is no such frame
    the queue is 64 deep in safety-relevant messages, which means this browser
    is too far behind to be shown a coherent picture at all; it is disconnected
    and resyncs from a fresh snapshot rather than being fed a version of events
    with a hole in it.
    """

    def __init__(self, websocket: WebSocket, user: Any = None,
                 source_ip: str | None = None) -> None:
        self.websocket = websocket
        self.user = user  # authentication.local.User (identity + role)
        self.source_ip = source_ip
        self._queue: deque[tuple[str, bool]] = deque()
        self._wakeup = asyncio.Event()
        # Set when a protected frame had to be dropped: this client's view is
        # now missing something it must not miss, and only a resync fixes it.
        self.overloaded = False
        self.dropped = 0

    @property
    def depth(self) -> int:
        return len(self._queue)

    def offer(self, frame: str, protected: bool) -> None:
        if self.overloaded:
            return  # already condemned; the sender is closing the socket
        if len(self._queue) >= QUEUE_LIMIT and not self._evict_unprotected():
            self.overloaded = True
            self._wakeup.set()
            log.warning("browser queue saturated with protected frames; "
                        "disconnecting to force a resync")
            return
        self._queue.append((frame, protected))
        self._wakeup.set()

    def _evict_unprotected(self) -> bool:
        """Drop the oldest expendable frame. False when there isn't one."""
        for index, (_frame, protected) in enumerate(self._queue):
            if not protected:
                del self._queue[index]
                self.dropped += 1
                return True
        return False

    async def sender(self) -> None:
        while True:
            while not self._queue and not self.overloaded:
                self._wakeup.clear()
                await self._wakeup.wait()
            if self.overloaded:
                with contextlib.suppress(Exception):
                    await self.websocket.close(
                        code=OVERLOADED_CLOSE_CODE,
                        reason="too far behind; reconnect for a fresh snapshot")
                return
            frame, _protected = self._queue.popleft()
            await self.websocket.send_text(frame)


class TelemetryHub:
    def __init__(self, settings: Settings, db: "Database | None" = None) -> None:
        from ..commands.broker import CommandBroker
        from ..database.writer import BackgroundWriter
        from ..recordings.recorder import Recorder

        self.settings = settings
        self.db = db
        self.robots: dict[str, RobotSession] = {}
        self.browsers: set[BrowserClient] = set()
        self.commands = CommandBroker(self)
        self.recorder = Recorder(db)
        # Battery history is the other telemetry-path write. Separate from the
        # recorder's queue so a recording that saturates the disk cannot also
        # cost the long-term battery log, and vice versa.
        self.battery_writer = BackgroundWriter("battery")
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
        await self.recorder.recover()
        self.battery_writer.start()
        self.recorder.writer.start()
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        # Stop the monitor first: it can emit connection events, which write.
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None
        for session in self.robots.values():
            await self._persist_last_pose(session)
        await self.commands.shutdown()
        # Drain last, so everything above has been queued, and before
        # create_app's lifespan closes the database underneath them.
        await self.recorder.shutdown()
        await self.battery_writer.stop()

    @property
    def write_failures(self) -> dict[str, int]:
        """Dropped/failed counts for /api/health. Non-zero means telemetry
        history is incomplete — the writes are deliberately lossy under
        pressure, so the loss has to be visible somewhere."""
        return {
            "recording_dropped": self.recorder.writer.dropped,
            "recording_failed": self.recorder.writer.failed,
            "battery_dropped": self.battery_writer.dropped,
            "battery_failed": self.battery_writer.failed,
        }

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
        if t == "robot.hello":
            # A later hello is a re-announcement, not a new session: the robot
            # is telling us its capability set changed (e.g. its dock manager
            # came up after the bridge did). Update and fan out so an open
            # dashboard enables the matching controls without a reload.
            state.set_capabilities(payload.capabilities)
            self.publish("state.capabilities", session.robot_id,
                         CapabilitiesData(capabilities=payload.capabilities))
            rebroadcast = False
        elif t == "telemetry.heartbeat":
            rebroadcast = False
        elif t == "telemetry.pose":
            events = state.record_pose(payload)
            self.recorder.offer("pose", envelope.timestamp, envelope.data)
        elif t == "telemetry.lidar":
            events = state.record_lidar(payload)
            self.recorder.offer("lidar", envelope.timestamp, envelope.data)
        elif t == "telemetry.path":
            events = state.record_path(payload)
            self.recorder.offer("path", envelope.timestamp, envelope.data)
        elif t == "telemetry.battery":
            events = state.record_battery(payload)
            self.recorder.offer("battery", envelope.timestamp, envelope.data)
            enriched: BatteryData = payload
            if state.battery_estimate is not None:
                data_out = {**envelope.data, "estimate": state.battery_estimate.as_dict()}
            if self.db is not None:
                self.battery_writer.submit(
                    self.db.add_battery_sample,
                    session.robot_id, envelope.timestamp, enriched.voltage,
                    enriched.percentage, enriched.current, enriched.charging,
                )
        elif t == "telemetry.base_state":
            events = state.record_base_state(payload)
            self.recorder.offer("base_state", envelope.timestamp, envelope.data)
        elif t == "telemetry.diagnostics":
            events = state.record_diagnostics(payload)
            self.recorder.offer("diagnostics", envelope.timestamp, envelope.data)
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

    async def browser_connected(self, websocket: WebSocket, user: Any = None,
                                source_ip: str | None = None) -> BrowserClient:
        from ..protocol.messages import GoalData

        client = BrowserClient(websocket, user=user, source_ip=source_ip)
        self.browsers.add(client)
        session = self.primary()
        robot_id = session.robot_id if session else self.settings.default_robot_id
        snapshot = (session.state.snapshot() if session else
                    RobotState(self.settings, robot_id).snapshot())
        if self.db is not None:
            with contextlib.suppress(Exception):
                saved = await self.db.get_last_pose(robot_id)
                if saved is not None:
                    snapshot.last_known_pose = GoalData(**saved)
        client.offer(encode("server.snapshot", robot_id, self._next_seq(), snapshot), protected=True)
        return client

    def browser_disconnected(self, client: BrowserClient) -> None:
        self.browsers.discard(client)
        # Free the command lease if this client held it, so another operator
        # can take control without a takeover.
        self.commands.lease.release_client(client)

    # -- fan-out ---------------------------------------------------------------

    def publish(self, type_: str, robot_id: str, data: Any) -> None:
        frame = encode(type_, robot_id, self._next_seq(), data)
        protected = type_ in PROTECTED_TYPES
        for client in self.browsers:
            client.offer(frame, protected)

    async def _emit_events(self, session: RobotSession, events: list[EventData]) -> None:
        for event in events:
            self.publish("event.append", session.robot_id, event)
            self.recorder.offer("event", event.ts, event.model_dump())
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
            if new_state == "offline":
                await self._persist_last_pose(session)
            events = session.state.set_connection(new_state)
            self.publish("state.connection", session.robot_id, session.state.connection_data())
            await self._emit_events(session, events)
            self._emit_derived(session)

    async def _persist_last_pose(self, session: RobotSession) -> None:
        """Save the robot's last-known pose so the next session can offer to
        resume from where it left off. Called on the transition to offline."""
        if self.db is None:
            return
        pose = session.state.pose.data
        if pose is None:
            return
        with contextlib.suppress(Exception):
            await self.db.save_last_pose(session.robot_id, {
                "x": pose.x, "y": pose.y, "yaw": pose.yaw,
            })

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            for session in list(self.robots.values()):
                try:
                    await self._apply_connection(session)
                except Exception:
                    log.exception("monitor tick failed for %s", session.robot_id)
