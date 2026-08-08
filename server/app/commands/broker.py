"""Command lifecycle broker: browser request -> robot, replies -> browsers.

Safety posture:
- Goal-based commands only (navigate_to_pose, set_initial_pose, stop) plus the
  discrete charging/motor/undock steps (charge_release, motor_enable, undock);
  there is deliberately no velocity teleop path — undock included, which is an
  action on the robot, never browser-published velocity. There is no dock
  command: the robot has no automatic dock-in path.
- navigate_to_pose and the charging/motor/undock steps are additionally gated
  on live hardware telemetry by commands.gates before they are forwarded.
- Fail-closed: any request this broker refuses gets a synthesized command.ack,
  and an accepted one is never left open — missing acks and results are closed
  out by server-side timeouts. A frame malformed enough to fail payload
  validation never reaches here and is dropped by the gateway without a reply.
- Every request is written to the command_audit table before it reaches
  the robot.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import gates
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
    "charge_release": "Release charging",
    "motor_enable": "Enable motors",
    "undock": "Move robot off its charging dock",
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
        from .lease import OperatorLease, RateLimiter

        self.hub = hub
        self.ack_timeout_s = ack_timeout_s
        self.result_timeout_s = result_timeout_s
        self.active: dict[str, ActiveCommand] = {}
        # Every command_id ever seen (bounded) — duplicate protection must
        # cover finished commands too, or a replayed frame re-runs them.
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.lease = OperatorLease()
        self.rate = RateLimiter(hub.settings.command_rate_per_min)

    # -- browser side ---------------------------------------------------------

    async def handle_browser_request(self, client: Any, envelope: Envelope,
                                     payload: CommandRequestData) -> None:
        command_id = payload.command_id
        robot_id = envelope.robot_id
        user = getattr(client, "user", None)

        command = payload.command

        # 1. Role gate: command authorization is read-only by default. Reject
        #    observers (and any unauthenticated client) before touching state.
        if user is None or not getattr(user, "can_command", False):
            await self._reject_audited(client, robot_id, command_id, command,
                                       "Your account has read-only access — "
                                       "you cannot send robot commands.")
            return

        # 2. Per-operator rate limit — a stuck or hostile client cannot flood
        #    the robot with commands.
        if not self.rate.allow(user.id):
            await self._reject_audited(client, robot_id, command_id, command,
                                       "Too many commands too quickly — "
                                       "wait a moment and try again.")
            return

        # 3. Single-operator lease: a different operator must explicitly take
        #    over before they can send, stop, resume, or cancel.
        if not self.lease.can_command(robot_id, user.id):
            holder = self.lease.holder(robot_id)
            if not payload.takeover:
                who = holder.username if holder else "another operator"
                await self._reject_audited(client, robot_id, command_id, command,
                                           f"{who} is currently in control of the robot. "
                                           "Take over to send commands.")
                return
            log.info("operator %s took over control of %s from %s", user.username,
                     robot_id, holder.username if holder else "-")
        self.lease.acquire(robot_id, user.id, user.username, client)

        # 4. Duplicate protection — in-memory fast path plus a DB lookup so a
        #    replayed command_id is caught even after a server restart.
        if command_id in self._seen or (
                self.hub.db is not None and await self.hub.db.command_recorded(command_id)):
            await self._reject_audited(client, robot_id, command_id, command,
                                       "Duplicate command — already received.")
            return
        self._remember(command_id)

        if command in GOAL_REQUIRED:
            if payload.goal is None:
                await self._reject_audited(client, robot_id, command_id, command,
                                           "This command needs a destination.")
                return
            reason = self._validate_goal(robot_id, payload.goal)
            if reason is not None:
                await self._reject_audited(client, robot_id, command_id, command, reason)
                return

        session = self.hub.robots.get(envelope.robot_id)
        if session is None or session.websocket is None or session.state.connection != "online":
            await self._reject_audited(client, robot_id, command_id, command,
                                       "The robot is not connected right now — "
                                       "try again once it is online.")
            return

        # 5. Hardware-state gate for the motion, charging and undock commands.
        #    The UI mirrors the charging/undock rules to grey those controls
        #    out, and applies its own weaker check to navigation — but a hidden
        #    or disabled button is not authorization. The decision is made
        #    here, from telemetry, whatever the browser believed.
        reason = self._validate_robot_state(session, command)
        if reason is not None:
            await self._reject_audited(client, robot_id, command_id, command, reason)
            return

        if self.hub.db is not None:
            await self.hub.db.add_command_audit(
                envelope.robot_id, command_id, command,
                payload.goal.model_dump() if payload.goal else None,
                **self._identity(client, robot_id),
            )

        entry = ActiveCommand(command_id=command_id, command=payload.command,
                              robot_id=envelope.robot_id)
        self.active[command_id] = entry
        entry.timers.append(asyncio.create_task(self._ack_timeout(entry)))
        entry.timers.append(asyncio.create_task(self._result_timeout(entry)))

        # Stamp authorization from the verified session, overwriting whatever
        # the browser sent. The robot's guarded operations trust this flag, so
        # it must never be attacker-controlled.
        forwarded = payload.model_copy(update={"operator_authorized": True})
        frame = encode("command.request", envelope.robot_id, self.hub._next_seq(), forwarded)
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

    def _validate_goal(self, robot_id: str, goal: Any) -> str | None:
        """Reject non-finite or out-of-bounds coordinates before they reach
        Nav2. Bounds come from the robot's occupancy map when it has streamed
        one; otherwise a coarse sanity limit applies."""
        if not (math.isfinite(goal.x) and math.isfinite(goal.y)):
            return "The destination coordinates are invalid."
        if goal.yaw is not None and not math.isfinite(goal.yaw):
            return "The destination heading is invalid."

        session = self.hub.robots.get(robot_id)
        map_data = session.state.map.data if session is not None else None
        if map_data is not None:
            min_x, min_y = map_data.origin.x, map_data.origin.y
            max_x = min_x + map_data.width * map_data.resolution
            max_y = min_y + map_data.height * map_data.resolution
            if not (min_x <= goal.x <= max_x and min_y <= goal.y <= max_y):
                return "That destination is outside the known map."
        else:
            limit = self.hub.settings.max_map_coordinate_m
            if abs(goal.x) > limit or abs(goal.y) > limit:
                return "That destination is too far away to be valid."
        return None

    def _validate_robot_state(self, session: "RobotSession", command: str) -> str | None:
        """Refusal reason for a gated command, from live state."""
        if command not in gates.GATES:
            return None
        state = session.state
        navigating = any(entry.command == "navigate_to_pose"
                         and entry.robot_id == session.robot_id
                         for entry in self.active.values())
        facts = gates.facts_from_state(state.connection, state.base_state.data,
                                       state.pose.data, state.capabilities,
                                       navigating=navigating,
                                       # Server receipt ages, not the robot's
                                       # self-report: a slice that stopped
                                       # arriving keeps its last self-reported
                                       # age forever. See gates.MAX_RECEIPT_AGE_S.
                                       base_state_age=state.base_state.age(),
                                       pose_age=state.pose.age())
        return gates.rejection_reason(command, facts)

    def _remember(self, command_id: str) -> None:
        self._seen[command_id] = None
        while len(self._seen) > 512:
            self._seen.popitem(last=False)

    def _reject(self, robot_id: str, command_id: str, reason: str) -> None:
        self.hub.publish("command.ack", robot_id,
                         CommandAckData(command_id=command_id, accepted=False, reason=reason))
        log.info("rejected command %s: %s", command_id, reason)

    def _identity(self, client: Any, robot_id: str) -> dict[str, Any]:
        """Requesting user, source IP, and the robot's session generation — the
        full identity recorded with every command attempt."""
        user = getattr(client, "user", None)
        session = self.hub.robots.get(robot_id)
        base_state = session.state.base_state.data if session is not None else None
        return {
            "user_id": getattr(user, "id", None),
            "username": getattr(user, "username", None),
            "source_ip": getattr(client, "source_ip", None),
            "session_generation": getattr(base_state, "session_generation", None),
        }

    async def _reject_audited(self, client: Any, robot_id: str, command_id: str,
                              command: str, reason: str) -> None:
        """Reject and record the attempt (who, from where, why) in the audit."""
        self._reject(robot_id, command_id, reason)
        if self.hub.db is not None:
            await self.hub.db.add_command_rejection(
                robot_id, command_id, command, reason, **self._identity(client, robot_id))

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
