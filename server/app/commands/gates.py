"""Preconditions for the navigation, charging, motor-power and undock commands.

Pure functions over a snapshot of robot state, so the rules are unit-testable
without a socket and produce the *same* plain-language sentence the operator
sees in the UI. The frontend mirrors these rules in
frontend/src/lib/dockGates.ts to grey out controls and explain why; that copy
is presentation only — this module is the authorization, and the broker
consults it on every request regardless of what the browser believed.

`undock` is a single operator action: the robot releases its own charger and
powers its own motors as part of executing it. The dashboard does not sequence
that from the browser, so what is gated here is the state the robot cannot
recover from on its own — stale or invalid telemetry, an active fault, a
pressed e-stop, an obstructed rear bumper, a robot already moving, or a base
that never claimed the capability at all.

`charge_release` and `motor_enable` remain available as separate, separately
audited commands for the robot-side and diagnostic paths; the dashboard UI
does not send them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The robot's *own* report of how stale its drive-base link is. Anything older
# than this is not a basis for allowing motion.
MAX_TELEMETRY_AGE_S = 2.0

# How old the server's *receipt* of a telemetry slice may be. This is a
# different question from MAX_TELEMETRY_AGE_S and neither one implies the
# other: telemetry_age is a number inside the last base_state payload, so if
# base_state stops arriving altogether, the last one keeps reporting whatever
# age it had when it was sent — 0.05 s, forever. The heartbeat is no help
# either; it comes from the bridge's socket thread, which happily keeps
# beating after the ROS subscription behind base_state has died. So a robot
# whose drive-base driver crashed still looked online, still reported fresh
# telemetry, and would still have authorized charge release, motor enable
# and undock off a frozen snapshot.
#
# base_state arrives at 1 Hz (slow_interval in web_bridge.yaml) and pose at
# 10 Hz, so 3 s tolerates two missed base-state samples while staying well
# inside the 10 s offline threshold — the gate has to notice before the
# connection state does, or it adds nothing.
MAX_RECEIPT_AGE_S = 3.0
# Speeds at or below these count as stationary (encoder noise on a parked
# robot is non-zero).
STATIONARY_LINEAR_MS = 0.02
STATIONARY_ANGULAR_RPS = 0.05

# charge_state values that mean current is flowing into the pack.
CHARGING_STATES = {"charging", "charge", "bulk", "float", "overcharge"}
# ...and those that mean physically on the dock, charging or not.
ON_DOCK_STATES = CHARGING_STATES | {"docked", "dock", "on_dock"}
DOCK_OBSERVER_DOCKED = {"DOCKED_CONFIRMED"}


@dataclass
class StateFacts:
    """Everything the gates need, flattened out of base_state/pose/connection."""

    online: bool = False
    link_connected: bool = False
    telemetry_age: float = 999.0
    # Seconds since the server received each slice. None means "never
    # received", which fails closed exactly like a stale one.
    base_state_age: float | None = None
    pose_age: float | None = None
    hardware_state_valid: bool = False
    charge_state: str = "unknown"
    motors_enabled: bool = False
    estop_pressed: bool = False
    fault_flags: int = 0
    bumpers_front: bool = False
    bumpers_rear: bool = False
    # Whether the two above mean anything. None is "the robot did not say",
    # which older bridges do not; False is "it said they are meaningless".
    bumpers_valid: bool | None = None
    dock_state: str | None = None
    dock_state_valid: bool | None = None
    undock_active: bool = False
    undock_profile_commissioned: bool | None = None
    localized: bool = False
    stationary: bool = False
    capabilities: tuple[str, ...] = ()
    # A navigate_to_pose this server is still waiting on. `stationary` does not
    # cover it: a goal that has been accepted but has not started moving yet
    # still reads as stopped, which is exactly how an undock got offered and
    # then failed on the robot with "another navigation or behavior action is
    # active" (2026-07-28 12:27).
    navigating: bool = False

    @property
    def charging(self) -> bool:
        return self.charge_state.strip().lower() in CHARGING_STATES

    @property
    def on_dock(self) -> bool:
        # A *usable* dock observer supersedes raw charge_state. The latter can
        # re-latch after a proven departure; CLEAR_CONFIRMED must remain clear.
        # An observer reporting itself invalid answers nothing, so it falls
        # back to charge_state rather than to "not docked" — otherwise a broken
        # observer would clear the dock for a robot sitting on its charger.
        if self.dock_state_valid:
            return (self.dock_state or "").strip().upper() in DOCK_OBSERVER_DOCKED
        return self.charge_state.strip().lower() in ON_DOCK_STATES

    @property
    def telemetry_fresh(self) -> bool:
        return self.link_connected and self.telemetry_age <= MAX_TELEMETRY_AGE_S

    @property
    def base_state_fresh(self) -> bool:
        """Did the server actually hear from the drive base recently?"""
        return (self.base_state_age is not None
                and self.base_state_age <= MAX_RECEIPT_AGE_S)

    @property
    def pose_fresh(self) -> bool:
        return self.pose_age is not None and self.pose_age <= MAX_RECEIPT_AGE_S


def facts_from_state(connection: str, base_state: Any | None, pose: Any | None,
                     capabilities: list[str] | None = None,
                     navigating: bool = False,
                     base_state_age: float | None = None,
                     pose_age: float | None = None) -> StateFacts:
    """Flatten live robot state into gate facts. Missing telemetry stays at the
    fail-closed defaults, so an absent base_state refuses everything.

    base_state_age/pose_age are the server's own receipt ages (RobotState
    tracks them per slice); omitting them means "not received", which refuses
    just as a stale slice does.
    """
    facts = StateFacts(online=connection == "online",
                       capabilities=tuple(capabilities or ()),
                       navigating=navigating,
                       base_state_age=base_state_age,
                       pose_age=pose_age)
    if base_state is not None:
        facts.link_connected = bool(base_state.link_connected)
        facts.telemetry_age = float(base_state.telemetry_age)
        facts.hardware_state_valid = bool(base_state.hardware_state_valid)
        facts.charge_state = str(base_state.charge_state)
        facts.motors_enabled = bool(base_state.motors_enabled)
        facts.estop_pressed = bool(base_state.estop_pressed)
        facts.fault_flags = int(base_state.fault_flags)
        facts.bumpers_front = bool(base_state.bumpers_front)
        facts.bumpers_rear = bool(base_state.bumpers_rear)
        facts.bumpers_valid = getattr(base_state, "bumpers_valid", None)
        facts.dock_state = getattr(base_state, "dock_state", None)
        facts.dock_state_valid = getattr(base_state, "dock_state_valid", None)
        facts.undock_active = bool(getattr(base_state, "undock_active", False))
        facts.undock_profile_commissioned = getattr(
            base_state, "undock_profile_commissioned", None)
    if pose is not None:
        facts.localized = bool(getattr(pose, "localized", False))
        facts.stationary = (abs(pose.linear_velocity) <= STATIONARY_LINEAR_MS
                            and abs(pose.angular_velocity) <= STATIONARY_ANGULAR_RPS)
    return facts


# -- shared clauses ---------------------------------------------------------

def _hardware_reason(facts: StateFacts) -> str | None:
    """Checks every one of these commands shares: is the robot's own report of
    itself recent, valid and fault-free?"""
    if not facts.online:
        return "The robot is not connected right now."
    # Two independent staleness questions, one answer: did the drive base tell
    # us recently (base_state_fresh), and was what it told us current when it
    # said it (telemetry_fresh). To the operator these are the same problem —
    # "what I can see is not current" — and the distinction between the
    # robot's internal link and ours is not theirs to act on.
    if not facts.base_state_fresh or not facts.telemetry_fresh:
        return ("The robot's hardware readings are stale — wait for fresh "
                "data before commanding the robot.")
    if not facts.hardware_state_valid:
        return "The robot's drive base is not reporting valid data."
    if facts.fault_flags:
        return (f"The drive base is reporting a fault (code {facts.fault_flags}). "
                "Clear it on the robot first.")
    return None


def _stationary_reason(facts: StateFacts) -> str | None:
    """"Is the robot stopped?" is only answerable from a pose we actually have.

    Without the freshness check this read "the robot is still moving" for a
    missing pose and, worse, "the robot is stopped" for a stale one — a robot
    that was parked when its last pose arrived and has been driving ever since
    passed the check.
    """
    if not facts.pose_fresh:
        return ("The dashboard has not had a recent position update from the "
                "robot — wait for fresh data before moving it.")
    if not facts.stationary:
        return "The robot is still moving — wait until it has stopped."
    return None


# -- per-command gates ------------------------------------------------------

def navigate_reason(facts: StateFacts) -> str | None:
    """Send the robot to an operator-chosen destination.

    The frontend only requires that the operator has set the robot's location
    this session (NavControlsWidget), which is a claim about the browser, not
    about the robot — a stale or crafted frame reaching the gateway carried no
    such requirement at all. So the same questions are asked here from
    telemetry: is the drive base healthy and fresh, are the motors actually
    powered, and does the robot currently know where it is.

    Deliberately not gated on being stationary: replacing a destination while
    the robot drives is a supported operation, and Nav2 preempts the running
    goal itself.
    """
    reason = _hardware_reason(facts)
    if reason is not None:
        return reason
    if facts.estop_pressed:
        return "The emergency stop is pressed. Release it on the robot first."
    # Leaving the dock is undock's job, not navigation's — it is the operation
    # that releases the charger and backs off under the robot's own guarded
    # profile. Checked before the motors, because a hand-docked robot can sit
    # on charge with its motors still reported enabled (see
    # charge_release_reason), and that combination would otherwise read as
    # "ready to drive" and pull the robot off its charger under Nav2.
    if facts.on_dock:
        return ("The robot is on its charger. Move it off the dock before "
                "sending it anywhere.")
    if not facts.motors_enabled:
        return "The robot's motors are off. Enable them on the robot first."
    # `localized` lives in the pose slice, so a stale pose only says where the
    # robot used to believe it was — not a basis for sending it somewhere.
    if not facts.pose_fresh:
        return ("The dashboard has not had a recent position update from the "
                "robot — wait for fresh data before sending it anywhere.")
    if not facts.localized:
        return "The robot does not know where it is. Set its location first."
    return None


def charge_release_reason(facts: StateFacts) -> str | None:
    """Zero-motion: disengage the charger. The motors are off afterwards.

    Deliberately does NOT require the motors to be off beforehand. The robot's
    release operation disables and confirms them itself before opening the
    charger — patrolbot_dock_manager/contracts.py is explicit that "manual
    charging may leave motors reported enabled" and that motor state is
    intentionally not a precondition. Requiring it here deadlocked the exact
    case the robot documents: a hand-docked robot charging with its motors on
    had charge release demanding motors off, while motor enable demanded the
    charger released — each refusing until the other happened.
    """
    reason = _hardware_reason(facts)
    if reason is not None:
        return reason
    if not facts.charging:
        return "The robot is not on charge, so there is nothing to release."
    return _stationary_reason(facts)


def motor_enable_reason(facts: StateFacts) -> str | None:
    """Explicit, separate step: after this the robot can drive."""
    reason = _hardware_reason(facts)
    if reason is not None:
        return reason
    if facts.charging:
        return ("The robot is still on charge. Release charging before "
                "enabling the motors.")
    if facts.estop_pressed:
        return "The emergency stop is pressed. Release it on the robot first."
    if facts.motors_enabled:
        return "The motors are already on."
    return _stationary_reason(facts)


def undock_reason(facts: StateFacts) -> str | None:
    """Back off the dock. The robot releases its own charger and powers its own
    motors as part of this, so neither is required beforehand."""
    reason = _hardware_reason(facts)
    if reason is not None:
        return reason
    if "undock" not in facts.capabilities:
        return ("Undocking is not commissioned on this robot yet — back it off "
                "the dock by hand.")
    if facts.dock_state_valid is False:
        return ("The robot's dock observer is not reporting valid data. Check "
                "the robot before trying to undock.")
    if facts.undock_profile_commissioned is False:
        return ("The robot's guarded undock profile has not been commissioned.")
    if facts.undock_active:
        return "The robot is already undocking."
    if not facts.on_dock:
        return "The robot is not on its dock."
    if facts.estop_pressed:
        return "The emergency stop is pressed. Release it on the robot first."
    if facts.bumpers_valid is not True:
        # Backing off the dock is the one motion where the rear bumper is the
        # only thing watching, so this needs the robot to say its readings are
        # good — not merely to have not said they are bad. A robot that never
        # reports the flag does not get the benefit of the doubt for motion.
        return ("The robot cannot confirm its bumper readings, so it cannot "
                "tell whether anything is behind it. Check the robot before "
                "undocking.")
    if facts.bumpers_rear:
        return ("The rear bumper is pressed — clear whatever is behind the "
                "robot before backing it off the dock.")
    if facts.navigating:
        return ("The robot is still driving to a destination. Stop it before "
                "backing it off the dock.")
    return _stationary_reason(facts)


# There is no dock gate because there is no dock command: the robot has no
# automatic dock-in path (see protocol/messages.py). Driving onto the charger
# is done by hand, and `navigate_to_pose` refuses to drive off it.

GATES = {
    "navigate_to_pose": navigate_reason,
    "charge_release": charge_release_reason,
    "motor_enable": motor_enable_reason,
    "undock": undock_reason,
}


def rejection_reason(command: str, facts: StateFacts) -> str | None:
    """Plain-language refusal for `command`, or None when it may proceed.
    Commands without a gate here (stop, set_initial_pose — neither of which
    drives the robot) return None."""
    gate = GATES.get(command)
    return gate(facts) if gate is not None else None
