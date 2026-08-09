/**
 * UI mirror of server/app/commands/gates.py — keep the two in sync.
 *
 * This decides which charging/dock control is live and, when one is not, the
 * sentence explaining why. It is presentation only: the server re-runs the
 * same rules on every request and is the actual authorization. A control is
 * greyed out with its reason rather than hidden, so an operator can tell the
 * difference between "this robot can't do that" and "not yet".
 *
 * One rule is deliberately NOT mirrored: the server also refuses when it has
 * not *received* a base_state or pose recently (gates.MAX_RECEIPT_AGE_S), which
 * catches a robot whose telemetry froze while its heartbeat kept beating. That
 * needs per-slice receipt times and a ticker to re-evaluate as time passes with
 * no new frames, neither of which this store has. The consequence is bounded
 * and in the safe direction: the button stays live for a few seconds longer
 * than it should, and pressing it returns the server's refusal sentence.
 */
import type { BaseStateData, CommandType, PoseData } from "../types/protocol";

export const MAX_TELEMETRY_AGE_S = 2.0;
export const STATIONARY_LINEAR_MS = 0.02;
export const STATIONARY_ANGULAR_RPS = 0.05;

const CHARGING_STATES = new Set(["charging", "charge", "bulk", "float", "overcharge"]);
const ON_DOCK_STATES = new Set([...CHARGING_STATES, "docked", "dock", "on_dock"]);
const DOCK_OBSERVER_DOCKED = new Set(["DOCKED_CONFIRMED"]);

export interface StateFacts {
  online: boolean;
  linkConnected: boolean;
  telemetryAge: number;
  hardwareStateValid: boolean;
  chargeState: string;
  motorsEnabled: boolean;
  estopPressed: boolean;
  faultFlags: number;
  bumpersRear: boolean;
  /** Whether bumpersRear means anything; null when the robot did not say. */
  bumpersValid: boolean | null;
  dockState: string | null;
  dockStateValid: boolean | null;
  undockActive: boolean;
  undockProfileCommissioned: boolean | null;
  localized: boolean;
  stationary: boolean;
  capabilities: string[];
  /**
   * A navigate_to_pose still in flight. `stationary` does not cover it: a goal
   * that has been accepted but has not started moving yet still reads as
   * stopped, which is how an undock got offered and then failed on the robot
   * with "another navigation or behavior action is active" (2026-07-28 12:27).
   */
  navigating: boolean;
}

/** Fail-closed defaults: with no telemetry at all, every gate refuses. */
export function factsFrom(
  connectionState: string,
  baseState: BaseStateData | null,
  pose: PoseData | null,
  capabilities: string[],
  navigating = false,
): StateFacts {
  return {
    online: connectionState === "online",
    linkConnected: baseState?.link_connected ?? false,
    telemetryAge: baseState?.telemetry_age ?? 999,
    hardwareStateValid: baseState?.hardware_state_valid ?? false,
    chargeState: baseState?.charge_state ?? "unknown",
    motorsEnabled: baseState?.motors_enabled ?? false,
    estopPressed: baseState?.estop_pressed ?? false,
    faultFlags: baseState?.fault_flags ?? 0,
    bumpersRear: baseState?.bumpers_rear ?? false,
    bumpersValid: baseState?.bumpers_valid ?? null,
    dockState: baseState?.dock_state ?? null,
    dockStateValid: baseState?.dock_state_valid ?? null,
    undockActive: baseState?.undock_active ?? false,
    undockProfileCommissioned: baseState?.undock_profile_commissioned ?? null,
    localized: pose?.localized ?? false,
    stationary:
      pose != null &&
      Math.abs(pose.linear_velocity) <= STATIONARY_LINEAR_MS &&
      Math.abs(pose.angular_velocity) <= STATIONARY_ANGULAR_RPS,
    capabilities,
    navigating,
  };
}

export function isCharging(facts: StateFacts): boolean {
  return CHARGING_STATES.has(facts.chargeState.trim().toLowerCase());
}

export function isOnDock(facts: StateFacts): boolean {
  // A usable SBC observer supersedes raw charge_state: that raw signal can
  // re-latch after the robot has proved it is physically clear. An observer
  // reporting itself invalid answers nothing, so it falls back to charge_state
  // rather than to "not docked".
  if (facts.dockStateValid) {
    return DOCK_OBSERVER_DOCKED.has((facts.dockState ?? "").trim().toUpperCase());
  }
  return ON_DOCK_STATES.has(facts.chargeState.trim().toLowerCase());
}

export function telemetryFresh(facts: StateFacts): boolean {
  return facts.linkConnected && facts.telemetryAge <= MAX_TELEMETRY_AGE_S;
}

function hardwareReason(facts: StateFacts): string | null {
  if (!facts.online) return "The robot is not connected right now.";
  if (!telemetryFresh(facts)) {
    return "The robot's hardware readings are stale — wait for fresh data before commanding the robot.";
  }
  if (!facts.hardwareStateValid) return "The robot's drive base is not reporting valid data.";
  if (facts.faultFlags) {
    return `The drive base is reporting a fault (code ${facts.faultFlags}). Clear it on the robot first.`;
  }
  return null;
}

function stationaryReason(facts: StateFacts): string | null {
  return facts.stationary ? null : "The robot is still moving — wait until it has stopped.";
}

/**
 * Release the charger. Zero-motion, and the motors are off afterwards.
 *
 * Deliberately does NOT require the motors to be off beforehand. The robot's
 * release operation disables and confirms them itself before opening the
 * charger (patrolbot_dock_manager/contracts.py: "Manual charging may leave
 * motors reported enabled"). Requiring it here created a deadlock in exactly
 * the case the robot documents — a hand-docked robot charging with its motors
 * still on had this step saying "turn the motors off first" while Turn motors
 * on said "release charging first", with both buttons disabled.
 */
export function chargeReleaseReason(facts: StateFacts): string | null {
  const reason = hardwareReason(facts);
  if (reason !== null) return reason;
  if (!isCharging(facts)) return "The robot is not on charge, so there is nothing to release.";
  return stationaryReason(facts);
}

export function motorEnableReason(facts: StateFacts): string | null {
  const reason = hardwareReason(facts);
  if (reason !== null) return reason;
  if (isCharging(facts)) return "The robot is still on charge. Release charging before enabling the motors.";
  if (facts.estopPressed) return "The emergency stop is pressed. Release it on the robot first.";
  if (facts.motorsEnabled) return "The motors are already on.";
  return stationaryReason(facts);
}

/** Back off the dock. The robot releases its own charger and powers its own
 *  motors as part of this, so neither is required beforehand. */
export function undockReason(facts: StateFacts): string | null {
  const reason = hardwareReason(facts);
  if (reason !== null) return reason;
  if (!facts.capabilities.includes("undock")) {
    return "Undocking is not commissioned on this robot yet — back it off the dock by hand.";
  }
  if (facts.dockStateValid === false) {
    return "The robot's dock observer is not reporting valid data. Check the robot before trying to undock.";
  }
  if (facts.undockProfileCommissioned === false) {
    return "The robot's guarded undock profile has not been commissioned.";
  }
  if (facts.undockActive) return "The robot is already undocking.";
  if (!isOnDock(facts)) return "The robot is not on its dock.";
  if (facts.estopPressed) return "The emergency stop is pressed. Release it on the robot first.";
  if (facts.bumpersValid !== true) {
    return "The robot cannot confirm its bumper readings, so it cannot tell whether anything is behind it. Check the robot before undocking.";
  }
  if (facts.bumpersRear) {
    return "The rear bumper is pressed — clear whatever is behind the robot before backing it off the dock.";
  }
  if (facts.navigating) {
    return "The robot is still driving to a destination. Stop it before backing it off the dock.";
  }
  return stationaryReason(facts);
}

const GATES: Partial<Record<CommandType, (facts: StateFacts) => string | null>> = {
  charge_release: chargeReleaseReason,
  motor_enable: motorEnableReason,
  undock: undockReason,
};

export function rejectionReason(command: CommandType, facts: StateFacts): string | null {
  return GATES[command]?.(facts) ?? null;
}

/**
 * Is there anything to undock from? There is no matching Dock control: the
 * robot has no automatic dock-in path, so it is driven onto its charger by
 * hand. The button appears only when the robot is on the dock.
 */
export function showsUndock(facts: StateFacts): boolean {
  // isOnDock already falls back to the raw charge signal when the observer is
  // unusable, so a robot visibly on charge behind a broken observer still gets
  // the control — disabled, carrying the observer error.
  return facts.undockActive || isOnDock(facts);
}
