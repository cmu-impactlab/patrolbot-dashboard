"""Preconditions for the charging, motor-power and dock commands.

Pure functions over a snapshot of robot state, so the rules are unit-testable
without a socket and produce the *same* plain-language sentence the operator
sees in the UI. The frontend mirrors these rules in
frontend/src/lib/dockGates.ts to grey out controls and explain why; that copy
is presentation only — this module is the authorization, and the broker
consults it on every request regardless of what the browser believed.

`dock` and `undock` are single operator actions: the robot releases its own
charger and powers its own motors as part of executing them. The dashboard
does not sequence that from the browser, so what is gated here is the state
the robot cannot recover from on its own — stale or invalid telemetry, an
active fault, a pressed e-stop, an obstructed rear bumper, a robot already
moving, or a base that never claimed the capability at all.

`charge_release` and `motor_enable` remain available as separate, separately
audited commands for the robot-side and diagnostic paths; the dashboard UI
does not send them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Base-state telemetry older than this is not a basis for allowing motion.
MAX_TELEMETRY_AGE_S = 2.0
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
    hardware_state_valid: bool = False
    charge_state: str = "unknown"
    motors_enabled: bool = False
    estop_pressed: bool = False
    fault_flags: int = 0
    bumpers_front: bool = False
    bumpers_rear: bool = False
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
        # A present dock observer supersedes raw charge_state. The latter can
        # re-latch after a proven departure; CLEAR_CONFIRMED must remain clear.
        if self.dock_state_valid is not None:
            return (self.dock_state_valid
                    and (self.dock_state or "").strip().upper()
                    in DOCK_OBSERVER_DOCKED)
        return self.charge_state.strip().lower() in ON_DOCK_STATES

    @property
    def telemetry_fresh(self) -> bool:
        return self.link_connected and self.telemetry_age <= MAX_TELEMETRY_AGE_S


def facts_from_state(connection: str, base_state: Any | None, pose: Any | None,
                     capabilities: list[str] | None = None,
                     navigating: bool = False) -> StateFacts:
    """Flatten live robot state into gate facts. Missing telemetry stays at the
    fail-closed defaults, so an absent base_state refuses everything."""
    facts = StateFacts(online=connection == "online",
                       capabilities=tuple(capabilities or ()),
                       navigating=navigating)
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
        facts.dock_state = getattr(base_state, "dock_state", None)
        facts.dock_state_valid = getattr(base_state, "dock_state_valid", None)
        facts.undock_active = bool(getattr(base_state, "undock_active", False))
        facts.undock_profile_commissioned = getattr(
            base_state, "undock_profile_commissioned", None)
    if pose is not None:
        facts.localized = bool(getattr(pose, "localized", True))
        facts.stationary = (abs(pose.linear_velocity) <= STATIONARY_LINEAR_MS
                            and abs(pose.angular_velocity) <= STATIONARY_ANGULAR_RPS)
    return facts


# -- shared clauses ---------------------------------------------------------

def _hardware_reason(facts: StateFacts) -> str | None:
    """Checks every one of these commands shares: is the robot's own report of
    itself recent, valid and fault-free?"""
    if not facts.online:
        return "The robot is not connected right now."
    if not facts.telemetry_fresh:
        return ("The robot's hardware readings are stale — wait for fresh "
                "data before changing charging or motor power.")
    if not facts.hardware_state_valid:
        return "The robot's drive base is not reporting valid data."
    if facts.fault_flags:
        return (f"The drive base is reporting a fault (code {facts.fault_flags}). "
                "Clear it on the robot first.")
    return None


def _stationary_reason(facts: StateFacts) -> str | None:
    if not facts.stationary:
        return "The robot is still moving — wait until it has stopped."
    return None


# -- per-command gates ------------------------------------------------------

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
    if facts.bumpers_rear:
        return ("The rear bumper is pressed — clear whatever is behind the "
                "robot before backing it off the dock.")
    if facts.navigating:
        return ("The robot is still driving to a destination. Stop it before "
                "backing it off the dock.")
    return _stationary_reason(facts)


def dock_reason(facts: StateFacts) -> str | None:
    """Drive to the charging dock and charge. Needs to navigate there, so
    unlike undocking it does require the robot to know where it is."""
    reason = _hardware_reason(facts)
    if reason is not None:
        return reason
    if "dock" not in facts.capabilities:
        return ("Automatic docking is not commissioned on this robot yet — "
                "drive it onto the dock by hand.")
    if facts.on_dock and facts.charging:
        return "The robot is already charging."
    if facts.estop_pressed:
        return "The emergency stop is pressed. Release it on the robot first."
    if not facts.localized:
        return "The robot does not know where it is. Set its location first."
    return None


GATES = {
    "charge_release": charge_release_reason,
    "motor_enable": motor_enable_reason,
    "undock": undock_reason,
    "dock": dock_reason,
}


def rejection_reason(command: str, facts: StateFacts) -> str | None:
    """Plain-language refusal for `command`, or None when it may proceed.
    Commands without a gate here (navigate/stop/pose) return None."""
    gate = GATES.get(command)
    return gate(facts) if gate is not None else None
