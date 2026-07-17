"""Command lifecycle broker: browser request -> robot, replies -> browsers.

Safety posture:
- Goal-based commands only (navigate_to_pose, set_initial_pose, stop);
  there is deliberately no velocity teleop path.
- Fail-closed: anything not explicitly valid is rejected with a synthesized
  command.ack, and the browser is never left waiting — missing acks and
  results are closed out by server-side timeouts.
- Every request is written to the command_audit table before it reaches
  the robot.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..protocol.envelope import Envelope, encode
from ..protocol.messages import (
    CommandAckData,
    CommandProgressData,
    CommandRequestData,
    CommandResultData,
)

if TYPE_CHECKING:
    from ..telemetry.hub import RobotSession, TelemetryHub

log = logging.getLogger("commands")

# Plain-language labels for events; raw command names stay out of the UI.
COMMAND_LABELS = {
    "navigate_to_pose": "Send robot to a destination",
    "set_initial_pose": "Set robot location on the map",
    "stop": "Stop the robot",
}
GOAL_REQUIRED = {"navigate_to_pose", "set_initial_pose"}


@dataclass
class ActiveCommand:
    command_id: str
    command: str
    robot_id: str
    acked: bool = False
    timers: list[asyncio.Task] = field(default_factory=list)

    def cancel_timers(self) -> None:
        for timer in self.timers:
            timer.cancel()


class CommandBroker:
    def __init__(self, hub: "TelemetryHub", ack_timeout_s: float = 5.0,
                 result_timeout_s: float = 120.0) -> None:
        self.hub = hub
        self.ack_timeout_s = ack_timeout_s
        self.result_timeout_s = result_timeout_s
        self.active: dict[str, ActiveCommand] = {}
        # Every command_id ever seen (bounded) — duplicate protection must
        # cover finished commands too, or a replayed frame re-runs them.
        self._seen: OrderedDict[str, None] = OrderedDict()

    # -- browser side ---------------------------------------------------------

    async def handle_browser_request(self, envelope: Envelope, payload: CommandRequestData) -> None:
        command_id = payload.command_id
        if command_id in self._seen:
            self._reject(envelope.robot_id, command_id, "Duplicate command — already received.")
            return
        self._remember(command_id)

        if payload.command in GOAL_REQUIRED and payload.goal is None:
            self._reject(envelope.robot_id, command_id, "This command needs a destination.")
            return

        session = self.hub.robots.get(envelope.robot_id)
        if session is None or session.websocket is None or session.state.connection != "online":
            self._reject(envelope.robot_id, command_id,
                         "The robot is not connected right now — try again once it is online.")
            return

        if self.hub.db is not None:
            await self.hub.db.add_command_audit(
                envelope.robot_id, command_id, payload.command,
                payload.goal.model_dump() if payload.goal else None,
            )

        entry = ActiveCommand(command_id=command_id, command=payload.command,
                              robot_id=envelope.robot_id)
        self.active[command_id] = entry
        entry.timers.append(asyncio.create_task(self._ack_timeout(entry)))
        entry.timers.append(asyncio.create_task(self._result_timeout(entry)))

        frame = encode("command.request", envelope.robot_id, self.hub._next_seq(), payload)
        try:
            await session.websocket.send_text(frame)
        except Exception as exc:  # socket died between the check and the send
            log.warning("failed to forward %s to robot: %s", command_id, exc)
            await self._close_out(entry, "failed", "The robot connection dropped while sending.")
            return

        await self.hub.emit_event(
            session, "info", COMMAND_LABELS.get(payload.command, payload.command),
            "Command sent to the robot.",
        )
        log.info("forwarded %s (%s) to %s", payload.command, command_id, envelope.robot_id)

    # -- robot side -----------------------------------------------------------

    async def handle_robot_reply(self, session: "RobotSession", envelope: Envelope, payload: Any) -> None:
        entry = self.active.get(getattr(payload, "command_id", ""))
        if entry is None:
            log.warning("dropping %s for unknown/closed command", envelope.type)
            return

        if isinstance(payload, CommandAckData):
            entry.acked = True
            self.hub.publish("command.ack", session.robot_id, payload)
            if not payload.accepted:
                await self._finish(entry, "rejected",
                                   payload.reason or "The robot declined the command.")
        elif isinstance(payload, CommandProgressData):
            self.hub.publish("command.progress", session.robot_id, payload)
        elif isinstance(payload, CommandResultData):
            self.hub.publish("command.result", session.robot_id, payload)
            await self._finish(entry, payload.outcome, payload.detail or "")

    # -- internals ------------------------------------------------------------

    def _remember(self, command_id: str) -> None:
        self._seen[command_id] = None
        while len(self._seen) > 512:
            self._seen.popitem(last=False)

    def _reject(self, robot_id: str, command_id: str, reason: str) -> None:
        self.hub.publish("command.ack", robot_id,
                         CommandAckData(command_id=command_id, accepted=False, reason=reason))
        log.info("rejected command %s: %s", command_id, reason)

    async def _ack_timeout(self, entry: ActiveCommand) -> None:
        await asyncio.sleep(self.ack_timeout_s)
        if not entry.acked:
            await self._close_out(entry, "timeout", "The robot did not confirm the command in time.")

    async def _result_timeout(self, entry: ActiveCommand) -> None:
        await asyncio.sleep(self.result_timeout_s)
        await self._close_out(entry, "timeout", "The command took too long and was abandoned.")

    async def _close_out(self, entry: ActiveCommand, outcome: str, detail: str) -> None:
        """Synthesize a result for a command the robot never resolved."""
        if entry.command_id not in self.active:
            return
        self.hub.publish("command.result", entry.robot_id, CommandResultData(
            command_id=entry.command_id, outcome=outcome, detail=detail))
        await self._finish(entry, outcome, detail)

    async def _finish(self, entry: ActiveCommand, outcome: str, detail: str) -> None:
        if self.active.pop(entry.command_id, None) is None:
            return
        entry.cancel_timers()
        if self.hub.db is not None:
            await self.hub.db.complete_command_audit(entry.command_id, outcome, detail)
        session = self.hub.robots.get(entry.robot_id)
        if session is not None and outcome != "succeeded":
            severity = "info" if outcome == "canceled" else "warning"
            await self.hub.emit_event(
                session, severity, COMMAND_LABELS.get(entry.command, entry.command),
                detail or f"The command did not complete ({outcome}).",
            )
        log.info("command %s closed: %s", entry.command_id, outcome)

    async def shutdown(self) -> None:
        for entry in list(self.active.values()):
            entry.cancel_timers()
            for timer in entry.timers:
                with contextlib.suppress(asyncio.CancelledError):
                    await timer
        self.active.clear()
