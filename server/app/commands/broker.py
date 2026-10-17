"""Command lifecycle broker: browser request -> robot, replies -> browsers.

Safety posture:
- Goal-based commands only (navigate_to_pose, set_initial_pose, stop) plus the
  discrete charging/motor/undock steps (charge_release, motor_enable, undock).
  Manual input uses a separately bounded, connection-bound deadman session.
  Undock remains an action on the robot, never browser-published velocity. There is no dock
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
import time
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
    "motor_disable": "Disable motors",
    "undock": "Move robot off its charging dock",
    "software_reset": "Restart robot software",
    "set_lidar_power": "Change LiDAR power",
    "teleop_start": "Begin manual drive",
    "teleop_stop": "End manual input",
}
GOAL_REQUIRED = {"navigate_to_pose", "set_initial_pose"}


@dataclass
class ActiveCommand:
    command_id: str
    command: str
    robot_id: str
    initiator: Any = None
    acked: bool = False
    uncertain: bool = False
    robot_session: Any = None
    robot_websocket: Any = None
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
        self._admission_locks: dict[str, asyncio.Lock] = {}
        # A reset remains a command barrier until the supervisor reports a
        # terminal result. A disconnect or timeout cannot resolve the outcome.
        self.reset_pending: dict[str, str] = {}
        self.lidar_pending: dict[str, str] = {}
        self.lidar_recovery_required: set[str] = set()
        self.lidar_state_after: dict[str, float] = {}
        # Every command_id ever seen (bounded) — duplicate protection must
        # cover finished commands too, or a replayed frame re-runs them.
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.lease = OperatorLease()
        self.rate = RateLimiter(hub.settings.command_rate_per_min)
        from .teleop import TeleopController
        self.teleop = TeleopController(self)

    # -- browser side ---------------------------------------------------------

    async def handle_browser_request(self, client: Any, envelope: Envelope,
                                     payload: CommandRequestData) -> None:
        revision = self.teleop.revisions.get(envelope.robot_id, 0)
        if payload.command in {"stop", "motor_disable", "teleop_stop"}:
            await self._handle_browser_request(client, envelope, payload, revision)
            return
        lock = self._admission_locks.setdefault(envelope.robot_id, asyncio.Lock())
        async with lock:
            await self._handle_browser_request(client, envelope, payload, revision)

    async def _handle_browser_request(self, client, envelope, payload, revision):
        command_id = payload.command_id
        robot_id = envelope.robot_id
        user = getattr(client, "user", None)

        command = payload.command

        if user is None or not getattr(user, "can_command", False):
            await self._reject_audited(client, robot_id, command_id, command,
                "Your account has read-only access — you cannot send robot commands.")
            return

        if command == "teleop_start" and robot_id in self.teleop.owners:
            if payload.takeover:
                await self.teleop.revoke(robot_id, "Manual lease takeover requested.")
            await self._reject_audited(client, robot_id, command_id, command,
                "Manual drive is active, ending, or its outcome is unknown. Stop it and wait for the robot result.")
            return

        if robot_id in self.lidar_pending and command not in {"stop", "teleop_stop", "software_reset", "motor_disable", "motor_enable"}:
            await self._reject_audited(client, robot_id, command_id, command,
                "LiDAR power is pending or its outcome is unknown. Wait for the robot result before sending another command.")
            return

        pending_reset = self.reset_pending.get(robot_id)
        previous_reset = self.active.get(pending_reset) if pending_reset else None
        explicit_retry = command == "software_reset" and previous_reset is not None and previous_reset.uncertain
        if pending_reset and command not in {"stop", "teleop_stop", "motor_disable"} and not explicit_retry:
            await self._reject_audited(
                client, robot_id, command_id, command,
                "A software reset is still pending or its outcome is unknown. "
                "Wait for the robot supervisor to report its result before sending another command.",
            )
            return

        # Revoke before any await on audit/rate/lease admission. Emergency
        # operators may always end input, even from another tab or lease.
        if command in {"stop", "motor_disable", "software_reset", "teleop_stop"}:
            await self.teleop.revoke(robot_id, "Manual input ended by an operator.",
                                     issue_stop=command != "teleop_stop")
        elif command != "teleop_start" and robot_id in self.teleop.owners:
            await self.teleop.revoke(robot_id, "Another robot operation was requested.")
            await self._reject_audited(client, robot_id, command_id, command,
                "Manual drive is ending or its outcome is unknown. Wait for the robot terminal result before another operation.")
            return

        # 2. Per-operator rate limit — a stuck or hostile client cannot flood
        #    the robot with commands.
        if command not in {"stop", "teleop_stop", "software_reset", "motor_disable"} and not self.rate.allow(user.id):
            await self._reject_audited(client, robot_id, command_id, command,
                                       "Too many commands too quickly — "
                                       "wait a moment and try again.")
            return

        # 3. Single-operator lease: a different operator must explicitly take
        #    over before they can send, stop, resume, or cancel.
        if command not in {"stop", "teleop_stop", "software_reset", "motor_disable"}:
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
            maps = getattr(self.hub, "maps", {})
            target = maps.get(payload.goal.map_id)
            if target is None or target.map_revision != payload.goal.map_revision:
                await self._reject_audited(client, robot_id, command_id, command,
                                           "Map context is required and must match the catalog. Refresh and select a new position.")
                return
            reason = self._validate_goal(robot_id, payload.goal, target)
            if reason is not None:
                await self._reject_audited(client, robot_id, command_id, command, reason)
                return

        session = self.hub.robots.get(envelope.robot_id)
        if session is None or session.websocket is None or session.state.connection != "online":
            await self._reject_audited(client, robot_id, command_id, command,
                                       "The robot is not connected right now — "
                                       "try again once it is online.")
            return

        if command in GOAL_REQUIRED:
            if "map_context_v1" not in session.state.capabilities:
                await self._reject_audited(client, robot_id, command_id, command,
                                           "The robot does not support map-aware localization. Update its bridge first.")
                return
            active_map = session.state.base_state.data
            if command == "navigate_to_pose" and (active_map is None or
                    active_map.map_id != target.map_id or active_map.map_revision != target.map_revision):
                await self._reject_audited(client, robot_id, command_id, command,
                                           "Destination map differs from the robot active map.")
                return
        # 5. Hardware-state gate for the motion, charging and undock commands.
        #    The UI mirrors the charging/undock rules to grey those controls
        #    out, and applies its own weaker check to navigation — but a hidden
        #    or disabled button is not authorization. The decision is made
        #    here, from telemetry, whatever the browser believed.
        reason = self._validate_robot_state(
            session, command,
            allow_unlocalized=bool(payload.allow_unlocalized))
        if reason is not None:
            await self._reject_audited(client, robot_id, command_id, command, reason)
            return

        if command == "teleop_start":
            if revision != self.teleop.revisions.get(robot_id, 0):
                await self._reject_audited(client, robot_id, command_id, command,
                    "Manual input was canceled during admission. Start again explicitly.")
                return
            reason = self.teleop.prepare(client, session, command_id)
            if reason:
                await self._reject_audited(client, robot_id, command_id, command, reason)
                return

        if self.hub.db is not None:
            await self.hub.db.add_command_audit(
                envelope.robot_id, command_id, command,
                ({"enabled": payload.enabled} if command == "set_lidar_power"
                 else dict(payload.goal.model_dump(), **({"seed_profile": payload.seed_profile} if payload.seed_profile else {})) if payload.goal else None),
                **self._identity(client, robot_id),
            )

        if command == "software_reset":
            self.teleop.owners.pop(robot_id, None)
            # Retire old intents; the fixed host restart stops their publishers.
            for old in list(self.active.values()):
                if old.robot_id == robot_id:
                    if old.command == "set_lidar_power":
                        self.lidar_recovery_required.add(robot_id)
                    await self._finish(old, "timeout" if old.uncertain else "canceled",
                        "Previous outcome unknown; superseded by an explicit software reset." if old.uncertain
                        else "Superseded by an explicit software reset; this command will not resume.")

        entry = ActiveCommand(command_id=command_id, command=payload.command,
                              robot_id=envelope.robot_id, initiator=client,
                              robot_session=session, robot_websocket=session.websocket)
        self.active[command_id] = entry
        if command == "teleop_start":
            owner = self.teleop.owners.get(robot_id)
            if (owner is None or owner.command_id != command_id or owner.phase != "pending"
                    or revision != self.teleop.revisions.get(robot_id, 0)
                    or not self.teleop.bound(owner) or not self.teleop.owns_lease(owner)
                    or gates.teleop_reason(session.state) is not None):
                self.teleop.terminal(entry)  # known not sent, unlike a socket timeout
                self.hub.publish("command.ack", robot_id, CommandAckData(
                    command_id=command_id, accepted=False, reason="Manual input canceled before publication."))
                await self._finish(entry, "canceled", "Manual input canceled before publication.")
                return
        if command == "teleop_stop":
            self.teleop.stop_pending(robot_id, command_id)
        if command == "software_reset":
            self.reset_pending[robot_id] = command_id
        if command == "set_lidar_power":
            self.lidar_pending[robot_id] = command_id
        entry.timers.append(asyncio.create_task(self._ack_timeout(entry)))
        entry.timers.append(asyncio.create_task(self._result_timeout(entry)))

        # Stamp authorization from the verified session, overwriting whatever
        # the browser sent. The robot's guarded operations trust this flag, so
        # it must never be attacker-controlled.
        forwarded = payload.model_copy(update={"operator_authorized": True})
        frame = encode("command.request", envelope.robot_id, self.hub._next_seq(), forwarded)
        try:
            if command in {"teleop_start", "teleop_stop"}:
                await asyncio.wait_for(entry.robot_websocket.send_text(frame),
                    timeout=max(.001, min(.3, self.hub.settings.teleop_send_timeout_s)))
                if session.websocket is not entry.robot_websocket or self.hub.robots.get(robot_id) is not session:
                    raise ConnectionError("robot session changed during publication")
            else:
                await session.websocket.send_text(frame)
        except Exception as exc:  # socket died between the check and the send
            log.warning("failed to forward %s to robot: %s", command_id, exc)
            if command in {"teleop_start", "teleop_stop"}:
                await self.teleop.revoke(robot_id, "Manual publication outcome unknown.", issue_stop=False)
                await self._mark_uncertain(entry, "Manual publication outcome unknown; no physical stop is confirmed. Wait for the robot result or explicitly reset.")
            elif command == "set_lidar_power":
                await self._mark_uncertain(entry, "LiDAR power outcome is unknown after the connection dropped. Wait for its result; do not retry.")
            elif command == "software_reset":
                await self._mark_uncertain(entry,
                    "The robot connection dropped while sending. The reset outcome is unknown; "
                    "confirm a new reset to try again.")
            else:
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
        if entry is None or entry.robot_id != session.robot_id:
            log.warning("dropping %s for unknown/closed command", envelope.type)
            return
        if entry.command in {"teleop_start", "teleop_stop"} and (
                entry.robot_session is not session or entry.robot_websocket is not session.websocket
                or self.hub.robots.get(session.robot_id) is not session):
            return

        if isinstance(payload, CommandAckData):
            entry.acked = True
            owner = self.teleop.owners.get(session.robot_id)
            ack = payload
            if entry.command == "teleop_start" and payload.accepted:
                if (owner and owner.command_id == entry.command_id and owner.phase in {"pending", "active"}
                        and self.teleop.bound(owner) and self.teleop.owns_lease(owner)
                        and gates.teleop_reason(session.state) is None):
                    if owner.phase == "pending":
                        owner.last_frame = time.monotonic()
                    owner.phase = "active"
                else:
                    ack = payload.model_copy(update={"accepted": False,
                        "reason": "Manual input already ended; late acceptance cannot resume it."})
                    await self.teleop.revoke(session.robot_id, "Late acceptance after manual input ended.")
            self.hub.publish("command.ack", session.robot_id, ack)
            if not payload.accepted:
                if entry.command in {"teleop_start", "teleop_stop"}:
                    self.teleop.terminal(entry)
                await self._finish(entry, "rejected",
                                   payload.reason or "The robot declined the command.")
        elif isinstance(payload, CommandProgressData):
            self.hub.publish("command.progress", session.robot_id, payload)
        elif isinstance(payload, CommandResultData):
            self.hub.publish("command.result", session.robot_id, payload)
            if entry.command in {"teleop_start", "teleop_stop"}:
                if payload.outcome == "timeout":
                    await self.teleop.revoke(session.robot_id, "Manual result unknown.", issue_stop=False)
                    await self._mark_uncertain(entry, payload.detail or "Manual input outcome unknown; no physical stop confirmed.")
                    return
                self.teleop.terminal(entry)
            if entry.command in {"software_reset", "set_lidar_power"} and payload.outcome == "timeout":
                await self._mark_uncertain(entry, payload.detail or "Reset outcome unknown; a new reset needs confirmation.")
                return
            if entry.command == "set_lidar_power":
                self.lidar_state_after[entry.robot_id] = time.monotonic()
                if payload.outcome == "succeeded":
                    self.lidar_recovery_required.discard(entry.robot_id)
                else:
                    self.lidar_recovery_required.add(entry.robot_id)
            await self._finish(entry, payload.outcome, payload.detail or "")

    # -- internals ------------------------------------------------------------

    def _validate_goal(self, robot_id: str, goal: Any, target: Any = None) -> str | None:
        """Reject non-finite or out-of-bounds coordinates before they reach
        Nav2. Bounds come from the robot's occupancy map when it has streamed
        one; otherwise a coarse sanity limit applies."""
        if not (math.isfinite(goal.x) and math.isfinite(goal.y)):
            return "The destination coordinates are invalid."
        if goal.yaw is not None and not math.isfinite(goal.yaw):
            return "The destination heading is invalid."

        session = self.hub.robots.get(robot_id)
        map_data = target or (session.state.map.data if session is not None else None)
        if map_data is not None:
            dx, dy = goal.x - map_data.origin.x, goal.y - map_data.origin.y
            yaw = map_data.origin.yaw
            mx = math.cos(yaw) * dx + math.sin(yaw) * dy
            my = -math.sin(yaw) * dx + math.cos(yaw) * dy
            if not (0 <= mx < map_data.width * map_data.resolution and
                    0 <= my < map_data.height * map_data.resolution):
                return "That destination is outside the known map."
        else:
            limit = self.hub.settings.max_map_coordinate_m
            if abs(goal.x) > limit or abs(goal.y) > limit:
                return "That destination is too far away to be valid."
        return None

    def _validate_robot_state(self, session: "RobotSession", command: str,
                              allow_unlocalized: bool = False) -> str | None:
        """Refusal reason for a gated command, from live state.

        `allow_unlocalized` is the operator's per-command override, carried
        from the request rather than read from telemetry. Every other fact here
        still applies: it waives exactly one check, the one asking whether the
        robot currently knows where it is.
        """
        if command not in gates.GATES and command != "teleop_start":
            return None
        if session.robot_id in self.lidar_recovery_required and command in {"navigate_to_pose", "set_initial_pose", "undock", "charge_release", "teleop_start"}:
            return "LiDAR power recovery is required. Make a new explicit power request before commanding the robot."
        state = session.state
        if session.robot_id in self.lidar_state_after and command not in {"software_reset", "motor_disable", "motor_enable"}:
            received = getattr(state.base_state, "received_mono", None)
            if received is None or received <= self.lidar_state_after[session.robot_id]:
                return "Waiting for fresh LiDAR state after the power command."
        navigating = any(entry.command in {"navigate_to_pose", "set_initial_pose"}
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
                                       pose_age=state.pose.age(),
                                       allow_unlocalized=allow_unlocalized)
        if any(entry.command == "undock" and entry.robot_id == session.robot_id
               for entry in self.active.values()):
            facts.undock_active = True
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
        if self.hub.db is not None:
            await self.hub.db.add_command_rejection(
                robot_id, command_id, command, reason, **self._identity(client, robot_id))
        self._reject(robot_id, command_id, reason)

    async def _ack_timeout(self, entry: ActiveCommand) -> None:
        await asyncio.sleep(self.ack_timeout_s)
        if not entry.acked:
            if entry.command in {"teleop_start", "teleop_stop"}:
                await self.teleop.revoke(entry.robot_id, "Manual acceptance unknown.")
                await self._mark_uncertain(entry, "Manual acceptance unknown; no physical stop confirmed. Wait for a terminal result or explicitly reset.")
                return
            if entry.command == "set_lidar_power":
                await self._mark_uncertain(entry, "LiDAR power outcome is unknown: acceptance was not confirmed. Wait for its result; do not retry.")
                return
            if entry.command == "software_reset":
                await self._mark_uncertain(entry,
                    "The reset outcome is unknown because the robot did not confirm acceptance. "
                    "No automatic retry; confirm a new reset to try again.")
                return
            await self._close_out(entry, "timeout", "The robot did not confirm the command in time.")

    async def _result_timeout(self, entry: ActiveCommand) -> None:
        await asyncio.sleep(self.result_timeout_s)
        if entry.command in {"teleop_start", "teleop_stop"}:
            await self.teleop.revoke(entry.robot_id, "Manual outcome unknown.")
            await self._mark_uncertain(entry, "Manual outcome unknown; no physical stop confirmed. Wait for a terminal result or explicitly reset.")
            return
        if entry.command == "set_lidar_power":
            await self._mark_uncertain(entry, "LiDAR power outcome is unknown. Wait for the robot result; do not retry.")
            return
        if entry.command == "software_reset":
            await self._mark_uncertain(entry,
                "The reset outcome is unknown. No automatic retry; confirm a new reset to try again.")
            return
        await self._close_out(entry, "timeout", "The command took too long and was abandoned.")

    async def _mark_uncertain(self, entry: ActiveCommand, detail: str) -> None:
        """Hold ordinary commands; an explicit reset may supersede an unknown result."""
        if entry.command_id not in self.active:
            return
        entry.uncertain = True
        if entry.command in {"teleop_start", "teleop_stop"}:
            owner = self.teleop.owners.get(entry.robot_id)
            if owner is not None:
                owner.phase = "unknown"
        self.hub.publish("command.result", entry.robot_id, CommandResultData(
            command_id=entry.command_id, outcome="timeout", detail=detail))
        if self.hub.db is not None:
            await self.hub.db.complete_command_audit(entry.command_id, "timeout", detail)

    async def _close_out(self, entry: ActiveCommand, outcome: str, detail: str) -> None:
        """Synthesize a result for a command the robot never resolved."""
        if entry.command_id not in self.active:
            return
        if entry.command == "set_lidar_power":
            await self._mark_uncertain(entry, "LiDAR power outcome is unknown. Wait for the robot result; do not retry.")
            return
        await self._cancel_map(entry)
        self.hub.publish("command.result", entry.robot_id, CommandResultData(
            command_id=entry.command_id, outcome=outcome, detail=detail))
        await self._finish(entry, outcome, detail)

    async def _finish(self, entry: ActiveCommand, outcome: str, detail: str) -> None:
        if self.active.pop(entry.command_id, None) is None:
            return
        if entry.command == "software_reset":
            self.reset_pending.pop(entry.robot_id, None)
        if entry.command == "set_lidar_power":
            self.lidar_pending.pop(entry.robot_id, None)
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

    async def _cancel_map(self, entry: ActiveCommand) -> None:
        if entry.command != "set_initial_pose":
            return
        session = self.hub.robots.get(entry.robot_id)
        if session is not None and session.websocket is not None:
            with contextlib.suppress(Exception):
                await session.websocket.send_text(encode("command.cancel", entry.robot_id,
                    self.hub._next_seq(), {"command_id": entry.command_id}))

    async def cancel_for_client(self, client: Any) -> None:
        for robot_id, owner in list(self.teleop.owners.items()):
            if owner.client is client:
                await self.teleop.revoke(robot_id, "Manual input producer disconnected.")
        for entry in list(self.active.values()):
            if entry.initiator is client:
                await self._cancel_map(entry)

    async def shutdown(self) -> None:
        await self.teleop.shutdown()
        for entry in list(self.active.values()):
            await self._cancel_map(entry)
            entry.cancel_timers()
            for timer in entry.timers:
                with contextlib.suppress(asyncio.CancelledError):
                    await timer
        self.active.clear()
