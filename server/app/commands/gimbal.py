"""Camera gimbal control authority and the server-side deadman.

Gimbal rate frames are a *stream*, not discrete commands, so they deliberately
do not go through CommandBroker. That path duplicate-checks every command_id,
writes an audit row per request, and rate-limits per minute — all correct for
"go to this pose", all wrong for thirty frames a second. Pushing camera frames
through it would flood the audit table and trip the limiter within seconds.

What is kept from the command path is the part that matters: the *same*
OperatorLease. Camera authority is driving authority, so one operator controls
the robot including where it is looking, and a takeover is explicit.

What is added is a server-side deadman. The browser holds a button and streams
frames; if those frames stop — a closed tab, a dropped connection, a wedged
renderer — the server emits a hold on its own. This is what makes
hold-to-move safe over a network: the camera stops when the operator's
connection does, not when the operator gets around to releasing the button.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# How long a gimbal stream survives without a new frame. Comfortably longer
# than the widget's ~20 Hz resend so ordinary jitter never trips it, short
# enough that a dead browser stops the camera before it travels far.
GIMBAL_EXPIRY_S = 0.3

# Ceiling on accepted frames per second per client. A normal stream is 20-30
# Hz; this only bites on a stuck or hostile client trying to saturate the
# robot link.
GIMBAL_MAX_HZ = 60.0


@dataclass
class GimbalFrame:
    """One camera rate frame, in degrees/second."""

    pan: float = 0.0
    tilt: float = 0.0
    roll: float = 0.0
    valid: bool = True

    def is_finite(self) -> bool:
        return all(
            isinstance(v, (int, float)) and math.isfinite(v)
            for v in (self.pan, self.tilt, self.roll)
        )


HOLD_FRAME = GimbalFrame(0.0, 0.0, 0.0, valid=False)


@dataclass
class Decision:
    accepted: bool
    reason: str | None = None


@dataclass
class AuditEvent:
    action: str
    robot_id: str
    user_id: int | None
    username: str | None
    detail: str | None = None


@dataclass
class _Stream:
    user_id: int
    username: str
    client: Any
    last_frame_at: float
    held: bool = False
    tokens: float = GIMBAL_MAX_HZ
    tokens_updated: float = 0.0


class GimbalAuthority:
    """Gates camera control on the operator lease and expires idle streams."""

    def __init__(self, lease, clock: Callable[[], float] = time.monotonic,
                 expiry_s: float = GIMBAL_EXPIRY_S,
                 max_hz: float = GIMBAL_MAX_HZ) -> None:
        self.lease = lease
        self._clock = clock
        self._expiry = float(expiry_s)
        self._max_hz = float(max_hz)
        self._streams: dict[str, _Stream] = {}
        self._audit: list[AuditEvent] = field(default_factory=list)  # type: ignore
        self._audit = []

    # ------------------------------------------------------------ submit --

    def submit(self, robot_id: str, user: Any, client: Any,
               frame: GimbalFrame, takeover: bool = False) -> Decision:
        """Authorize and record one camera frame."""
        now = self._clock()

        # 1. Role gate. Read-only accounts may watch but never aim.
        if user is None or not getattr(user, "can_command", False):
            return Decision(False, "Your account has read-only access — "
                                   "you cannot move the camera.")

        # 2. Reject values that would propagate into the serial encoder as
        #    garbage. The driver rejects these too; refusing here as well
        #    means a broken client is told, rather than silently ignored.
        if not frame.is_finite():
            return Decision(False, "Invalid camera command.")

        # 3. Single-operator lease — the same one that gates driving.
        if not self.lease.can_command(robot_id, user.id):
            holder = self.lease.holder(robot_id)
            who = holder.username if holder else "another operator"
            if not takeover:
                self._record("gimbal_control_refused", robot_id, user,
                             f"{who} holds control")
                return Decision(
                    False,
                    f"{who} is currently in control of the robot. "
                    "Take over to move the camera.")
            self._record("gimbal_control_taken_over", robot_id, user,
                         f"from {who}")
        self.lease.acquire(robot_id, user.id, user.username, client)

        stream = self._streams.get(robot_id)

        # 4. Flood cap, per stream, token bucket in frames per second.
        if stream is not None and stream.client is client:
            elapsed = max(0.0, now - stream.tokens_updated)
            stream.tokens = min(self._max_hz,
                                stream.tokens + elapsed * self._max_hz)
            stream.tokens_updated = now
            if stream.tokens < 1.0:
                return Decision(False, "Too many camera frames — slow down.")
            stream.tokens -= 1.0
        else:
            # New or changed owner: this is a control acquisition.
            if stream is None or stream.user_id != user.id:
                self._record("gimbal_control_acquired", robot_id, user)
            stream = _Stream(
                user_id=user.id, username=user.username, client=client,
                last_frame_at=now, tokens=self._max_hz - 1.0,
                tokens_updated=now)
            self._streams[robot_id] = stream

        stream.client = client
        stream.user_id = user.id
        stream.username = user.username

        if not frame.valid:
            # An explicit release: stop now rather than waiting for expiry.
            stream.held = True
            stream.last_frame_at = now - self._expiry - 1.0
        else:
            stream.held = False
            stream.last_frame_at = now

        return Decision(True)

    # ------------------------------------------------------------ expiry --

    def expired(self, robot_id: str) -> bool:
        stream = self._streams.get(robot_id)
        if stream is None:
            return True
        return (self._clock() - stream.last_frame_at) > self._expiry

    def collect_holds(self) -> list[tuple[str, GimbalFrame]]:
        """Return a hold for every stream that has just gone quiet.

        Emitted once per lapse, not repeatedly: the robot-side driver has its
        own watchdog, so one hold is enough to stop the camera and repeating
        it would only add traffic.
        """
        holds: list[tuple[str, GimbalFrame]] = []
        for robot_id, stream in self._streams.items():
            if stream.held:
                continue
            if (self._clock() - stream.last_frame_at) > self._expiry:
                stream.held = True
                holds.append((robot_id, HOLD_FRAME))
                self._record("gimbal_control_expired", robot_id, None,
                             f"no frames for {self._expiry:.2f}s "
                             f"from {stream.username}")
        return holds

    def release_client(self, client: Any) -> list[tuple[str, GimbalFrame]]:
        """A browser went away: stop whatever it was moving."""
        holds: list[tuple[str, GimbalFrame]] = []
        for robot_id, stream in list(self._streams.items()):
            if stream.client is not client:
                continue
            if not stream.held:
                holds.append((robot_id, HOLD_FRAME))
            self._record("gimbal_control_released", robot_id, None,
                         f"{stream.username} disconnected")
            del self._streams[robot_id]
        return holds

    # ------------------------------------------------------------- audit --

    def _record(self, action: str, robot_id: str, user: Any,
                detail: str | None = None) -> None:
        self._audit.append(AuditEvent(
            action=action,
            robot_id=robot_id,
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            detail=detail,
        ))

    def drain_audit(self) -> list[AuditEvent]:
        """Take the pending audit events, clearing the buffer.

        Transitions are recorded — acquired, taken over, refused, expired,
        released — never individual frames. Auditing every frame would write
        thousands of rows a minute and bury the events that matter.
        """
        events, self._audit = self._audit, []
        return events

    def holder_username(self, robot_id: str) -> str | None:
        stream = self._streams.get(robot_id)
        return stream.username if stream else None
