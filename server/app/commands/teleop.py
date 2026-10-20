"""Connection-bound manual input, never a claim that the physical robot stopped.

The robot owns the 300 ms zero watchdog. This server also ends a lost producer
after 300 ms, but keeps unresolved ownership until an actual terminal reply or
an explicitly authorized software reset. No reconnect or takeover can resume it.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import tuning_policy
from .gates import teleop_reason
from ..protocol.envelope import Envelope, encode
from ..protocol.messages import CommandRequestData, TeleopVelocityData


@dataclass
class ManualOwner:
    command_id: str
    client: Any
    session: Any
    websocket: Any
    generation: int
    base_generation: int
    phase: str = "pending"
    seq: int = -1
    last_frame: float = field(default_factory=time.monotonic)
    frame_times: deque = field(default_factory=deque)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    start_terminal: bool = False
    stop_ids: set[str] = field(default_factory=set)
    stop_issued: bool = False
    # The trusted policy captured when this session was admitted. Every nonzero
    # frame is revalidated against the live policy and this signature; a change
    # ends the session instead of being replayed against stale limits.
    policy: Any = None
    policy_signature: Any = None


class TeleopController:
    def __init__(self, broker):
        self.broker = broker
        self.hub = broker.hub
        self.owners: dict[str, ManualOwner] = {}
        self.revisions: dict[str, int] = {}
        self._watchdog: asyncio.Task | None = None

    def prepare(self, client, session, command_id):
        robot_id = session.robot_id
        if robot_id in self.owners:
            return "Manual drive is active, ending, or its outcome is unknown. Stop it and wait for the robot result."
        for entry in self.broker.active.values():
            if entry.robot_id != robot_id:
                continue
            if entry.command in {"navigate_to_pose", "set_initial_pose"}:
                return "Stop navigation before manual drive."
            return "Stop the active robot operation and wait for its result before manual drive."
        reason = teleop_reason(session.state)
        if reason:
            return reason
        # A new socket must provide its own fresh evidence, not inherit a prior
        # connection's report with an apparently fresh server receipt time.
        if session.state.base_state.received_mono < session.connected_mono:
            return "Wait for fresh hardware readings from this robot connection."
        # Capture the trusted runtime policy for the whole session. A robot that
        # advertises dynamic tuning with no fresh, bound policy is refused here,
        # never handed the old higher fixed default.
        policy, reason = self._resolve_policy(session)
        if reason:
            return reason
        self.owners[robot_id] = ManualOwner(
            command_id, client, session, session.websocket, session.generation,
            session.state.base_state.data.session_generation,
            policy=policy, policy_signature=policy.signature)
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._monitor())
        return None

    # -- runtime policy --------------------------------------------------------

    def _current_robot_generation(self, session):
        base = session.state.base_state.data
        return getattr(base, "session_generation", None) if base is not None else None

    def _resolve_policy(self, session):
        """Resolve the trusted policy for a connection.

        The provider is ``hub.tuning.teleop_policy(robot_id)`` when the parent
        wired the tuning adapter, else the documented
        ``session.state.tuning_policy`` attribute. Neither is browser-supplied.
        """
        return tuning_policy.resolve_policy(
            capabilities=session.state.capabilities,
            state=session.state,
            robot_id=session.robot_id,
            provider=tuning_policy.provider_from_hub(self.hub),
            robot_generation=self._current_robot_generation(session),
        )

    def _policy_problem(self, owner):
        """Reason the live policy no longer authorizes this session, or None."""
        policy, reason = self._resolve_policy(owner.session)
        if reason:
            return reason
        if policy.signature != owner.policy_signature:
            return "The active speed policy changed; manual input ended. Start again explicitly."
        return None

    def bound(self, owner):
        session = owner.session
        base = session.state.base_state.data
        return (self.hub.robots.get(session.robot_id) is session
                and session.websocket is owner.websocket
                and session.generation == owner.generation
                and base is not None and base.session_generation == owner.base_generation)

    def owns_lease(self, owner):
        holder = self.broker.lease.holder(owner.session.robot_id)
        return (holder is not None and holder.client is owner.client
                and getattr(owner.client, "connected", True))

    async def send(self, owner, payload, timestamp=None):
        # Callers never queue nonzero input. A release waits only for the one
        # bounded in-flight send, then zeroes the exact originally bound socket.
        async def bounded_send():
            async with owner.send_lock:
                if (not self.bound(owner) or
                        (payload.deadman and (owner.phase != "active" or not self.owns_lease(owner)))):
                    return False
                await owner.websocket.send_text(encode(
                    "teleop.velocity", owner.session.robot_id, self.hub._next_seq(),
                    payload, timestamp=timestamp))
                return self.bound(owner)
        try:
            return await asyncio.wait_for(bounded_send(), timeout=max(.001,
                min(.3, self.hub.settings.teleop_send_timeout_s)))
        except Exception:
            return False

    async def velocity(self, client, envelope: Envelope, payload: TeleopVelocityData):
        owner = self.owners.get(envelope.robot_id)
        # Invalid spam is deliberately neither broadcast nor written per frame.
        if (owner is None or owner.client is not client
                or not getattr(getattr(client, "user", None), "can_command", False)
                or owner.command_id != payload.command_id or payload.seq <= owner.seq
                or not self.bound(owner) or not self.owns_lease(owner)):
            return False
        try:
            stamp = datetime.fromisoformat(envelope.timestamp.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return False
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            if not -.05 <= age <= .3:
                return False
        except (ValueError, OverflowError):
            return False
        if payload.deadman:
            if owner.phase != "active":
                return False
            reason = teleop_reason(owner.session.state)
            if reason:
                await self.revoke(envelope.robot_id, reason)
                return False
            # Revalidate the live trusted policy on every nonzero frame. A
            # changed generation, a now-missing/stale policy, or an
            # over-policy request ends the session rather than being clamped or
            # replayed against the limits captured at start.
            problem = self._policy_problem(owner)
            if problem:
                await self.revoke(envelope.robot_id, problem)
                return False
            if owner.policy is not None and owner.policy.over_policy(payload.linear, payload.angular):
                await self.revoke(envelope.robot_id,
                    "Manual input exceeds the active speed policy; input ended.")
                return False
            now = time.monotonic()
            while owner.frame_times and now - owner.frame_times[0] >= 1:
                owner.frame_times.popleft()
            if (owner.send_lock.locked() or len(owner.frame_times) >=
                    max(1, self.hub.settings.teleop_frames_per_s)):
                return False
            owner.frame_times.append(now)
        # Releases bypass rate/hardware/ack/closing barriers, not identity,
        # freshness, sequence or lease checks. Never turn a zero into motion —
        # and a release must always reach the robot even when the runtime policy
        # itself has gone missing or stale.
        owner.seq = payload.seq
        owner.last_frame = time.monotonic()
        sent = await self.send(owner, payload, envelope.timestamp)
        if not sent:
            await self.revoke(envelope.robot_id, "Manual publication failed; robot outcome unknown.")
        elif not payload.deadman:
            await self.revoke(envelope.robot_id, "Manual deadman released.", zero_sent=True)
        return sent

    async def revoke(self, robot_id, reason, *, issue_stop=True, zero_sent=False):
        self.revisions[robot_id] = self.revisions.get(robot_id, 0) + 1
        owner = self.owners.get(robot_id)
        if owner is None:
            return
        first = owner.phase in {"pending", "active"}
        if owner.phase != "unknown":
            owner.phase = "closing"
        if first and not zero_sent:
            sent = await self.send(owner, TeleopVelocityData(
                command_id=owner.command_id, seq=owner.seq + 1,
                linear=0., angular=0., deadman=False))
            if not sent:
                owner.phase = "unknown"
        if issue_stop and not owner.stop_issued:
            owner.stop_issued = True
            if self.bound(owner):
                payload = CommandRequestData(command_id=str(uuid.uuid4()), command="teleop_stop")
                envelope = Envelope.model_validate_json(encode("command.request", robot_id, 0, payload))
                await self.broker.handle_browser_request(owner.client, envelope, payload)
            else:
                owner.phase = "unknown"

    def stop_pending(self, robot_id, command_id):
        owner = self.owners.get(robot_id)
        if owner:
            owner.stop_ids.add(command_id)
            owner.stop_issued = True

    def terminal(self, entry):
        owner = self.owners.get(entry.robot_id)
        if owner is None:
            return
        if entry.command_id == owner.command_id:
            self.revisions[entry.robot_id] = self.revisions.get(entry.robot_id, 0) + 1
            owner.start_terminal = True
            owner.phase = "closing"
        owner.stop_ids.discard(entry.command_id)
        if owner.start_terminal and not owner.stop_ids:
            self.owners.pop(entry.robot_id, None)

    async def changed(self, session):
        owner = self.owners.get(session.robot_id)
        if owner and owner.phase in {"active", "pending"}:
            reason = teleop_reason(session.state)
            if not self.bound(owner) or not self.owns_lease(owner) or reason:
                await self.revoke(session.robot_id, reason or "Manual session connection or lease changed.")
                return
            problem = self._policy_problem(owner)
            if problem:
                await self.revoke(session.robot_id, problem)

    async def _monitor(self):
        while True:
            await asyncio.sleep(.05)
            for robot_id, owner in list(self.owners.items()):
                if owner.phase not in {"pending", "active"}:
                    continue
                # Pending starts are zero-only on the robot while its single
                # adapter drains old autonomous output. Do not apply the active
                # producer watchdog before that acceptance barrier completes.
                deadline = (self.broker.ack_timeout_s if owner.phase == "pending" else .3)
                if time.monotonic() - owner.last_frame >= min(deadline,
                        max(.001, self.hub.settings.teleop_session_ttl_s)):
                    await self.revoke(robot_id, "Manual input stream ended; physical stop is not confirmed.")
                else:
                    await self.changed(owner.session)

    async def shutdown(self):
        if self._watchdog:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
        for robot_id in list(self.owners):
            await self.revoke(robot_id, "Dashboard shutting down.")
