"""Bounded autonomous cap selection over the existing authorized command sockets.

No motion, parameter names, takeover, or fallback path. Runtime admission and
actual effective-cap readback are the responsibility of a separately reviewed Pi.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..protocol.envelope import encode
from ..protocol.messages import (
    AUTONOMOUS_SPEED_CAPABILITY, AUTONOMOUS_SPEED_RECEIPT, AUTONOMOUS_SPEED_REQUEST,
    AutonomousSpeedReceiptData, AutonomousSpeedRequestData, AutonomousSpeedSelection,
)

if TYPE_CHECKING:
    from .broker import CommandBroker
    from ..telemetry.hub import RobotSession

COMMAND = "set_autonomous_speed_caps"
RECEIPT_TIMEOUT_S = 5.0
SEND_TIMEOUT_S = 0.3
log = logging.getLogger("commands.autonomous_speed")


@dataclass
class PendingCaps:
    client: Any
    session: RobotSession
    websocket: Any
    request: AutonomousSpeedRequestData
    deadline: float
    timer: asyncio.Task | None = None
    settled: bool = False


class AutonomousSpeedBroker:
    def __init__(self, commands: CommandBroker) -> None:
        self.commands = commands
        self.hub = commands.hub
        self.pending: dict[str, PendingCaps] = {}

    def _live(self, session: RobotSession, token: str) -> bool:
        age = session.state.heartbeat_age()
        return (self.hub.robots.get(session.robot_id) is session and session.websocket is not None
                and not session.autonomous_speed_caps_retired
                and session.autonomous_speed_caps_session == token
                and AUTONOMOUS_SPEED_CAPABILITY in session.state.capabilities
                and session.state.connection == "online"
                and age is not None and age < self.hub.settings.online_threshold_s)

    def _idle(self, robot_id: str) -> bool:
        return (robot_id not in self.commands.teleop.owners
                and not any(entry.robot_id == robot_id for entry in self.commands.active.values()))

    def _reply(self, client: Any, robot_id: str, receipt: AutonomousSpeedReceiptData) -> None:
        if client.connected:
            client.offer(encode(AUTONOMOUS_SPEED_RECEIPT, robot_id, self.hub._next_seq(), receipt),
                         protected=True)

    async def request(self, client: Any, robot_id: str, data: AutonomousSpeedRequestData) -> None:
        commands, db = self.commands, self.hub.db
        session = self.hub.robots.get(robot_id)
        command_id = data.command_id

        async def refuse(reason: str) -> None:
            if db is not None:
                await db.add_command_rejection(robot_id, command_id, COMMAND, reason,
                                               **commands._identity(client, robot_id))
            self._reply(client, robot_id, AutonomousSpeedReceiptData(command_id=command_id, outcome="unknown"))

        user = commands.current_user(client)
        if not getattr(user, "can_command", False):
            await refuse("Navigation command permission is required.")
            return
        if session is None or not self._live(session, data.session_id):
            await refuse("Autonomous speed-cap capability is unavailable on this robot connection.")
            return
        if (not self._idle(robot_id) or robot_id in self.pending
                or not commands.lease.can_command(robot_id, user.id)):
            await refuse("The robot is busy, another operator holds the lease, or the previous cap change is unresolved.")
            return
        if not commands.rate.allow(user.id):
            await refuse("Command rate limit reached.")
            return
        recorded = db is not None and await db.command_recorded(command_id)
        if recorded or command_id in commands.active or command_id in commands._seen:
            await refuse("Duplicate command.")
            return
        commands._remember(command_id)
        websocket = session.websocket
        if db is not None:
            await db.add_command_audit(robot_id, command_id, COMMAND, data.selection.model_dump(),
                                       **commands._identity(client, robot_id))

        forwarded = data.model_copy(update={"operator_authorized": True})
        entry = PendingCaps(client, session, websocket, data, time.monotonic() + RECEIPT_TIMEOUT_S)

        async def send_bound() -> bool:
            # The audit and scheduling can yield to disconnect, expiry or an
            # emergency command. No lease is taken or revoked by this operation.
            current = commands.current_user(client)
            if (not getattr(current, "can_command", False) or current.id != user.id
                    or not commands.lease.can_command(robot_id, current.id)
                    or not self._live(session, data.session_id) or session.websocket is not websocket
                    or not self._idle(robot_id)):
                return False
            self.pending[robot_id] = entry
            entry.timer = asyncio.create_task(self._timeout(entry))
            await websocket.send_text(encode(AUTONOMOUS_SPEED_REQUEST, robot_id, self.hub._next_seq(), forwarded))
            return True

        try:
            sent = await asyncio.wait_for(send_bound(), SEND_TIMEOUT_S)
        except asyncio.CancelledError:
            if self.pending.get(robot_id) is entry:
                await self._finish(entry)
            elif not entry.settled:
                await self._not_sent(entry)
            raise
        except Exception:
            log.warning("Autonomous-cap publication failed for %s", robot_id, exc_info=True)
            if self.pending.get(robot_id) is entry:
                await self._finish(entry)
            elif not entry.settled:
                await self._not_sent(entry)
            return
        if not sent:
            await self._not_sent(entry)

    async def _not_sent(self, entry: PendingCaps) -> None:
        command_id = entry.request.command_id
        self._reply(entry.client, entry.session.robot_id,
                    AutonomousSpeedReceiptData(command_id=command_id, outcome="unknown"))
        if self.hub.db is not None:
            await self.hub.db.complete_command_audit(command_id, "rejected", "Admission changed before sending.")

    async def receipt(self, session: RobotSession, data: AutonomousSpeedReceiptData) -> None:
        entry = self.pending.get(session.robot_id)
        if (entry is None or entry.settled or entry.session is not session
                or session.websocket is not entry.websocket or data.command_id != entry.request.command_id):
            return
        applied = (data.outcome == "applied" and data.readback == entry.request.selection
                   and self._live(session, entry.request.session_id) and time.monotonic() < entry.deadline)
        await self._finish(entry, readback=data.readback if applied else None)

    async def _timeout(self, entry: PendingCaps) -> None:
        await asyncio.sleep(max(0, entry.deadline - time.monotonic()))
        await self._finish(entry)

    async def _finish(self, entry: PendingCaps, *, readback: AutonomousSpeedSelection | None = None) -> None:
        robot_id, command_id = entry.session.robot_id, entry.request.command_id
        if self.pending.get(robot_id) is not entry or entry.settled:
            return
        entry.settled = True
        if entry.timer is not None and entry.timer is not asyncio.current_task():
            entry.timer.cancel()
        if readback is not None:
            del self.pending[robot_id]
        # Leave uncertain writes blocked on this connection; never automatically
        # retry or reinterpret a late reply as a successful current readback.
        self._reply(entry.client, robot_id, AutonomousSpeedReceiptData(
            command_id=command_id, outcome="applied" if readback is not None else "unknown", readback=readback))
        if self.hub.db is not None:
            await self.hub.db.complete_command_audit(command_id, "succeeded" if readback is not None else "timeout",
                "Exact effective-cap readback received." if readback is not None else "Autonomous speed-cap outcome unknown.")

    async def disconnected(self, session: RobotSession) -> None:
        entry = self.pending.get(session.robot_id)
        if entry is not None and entry.session is session:
            await self._finish(entry)
            if self.pending.get(session.robot_id) is entry:
                del self.pending[session.robot_id]

    async def unavailable(self, session: RobotSession) -> None:
        entry = self.pending.get(session.robot_id)
        if entry is not None and entry.session is session:
            await self._finish(entry)

    async def shutdown(self) -> None:
        for entry in list(self.pending.values()):
            await self.disconnected(entry.session)
