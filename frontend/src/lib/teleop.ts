import contractSource from "../../../contracts/web-teleop.json?raw";
import { lidarReadyReason } from "./lidar";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useCommandStore } from "../stores/commandStore";
import { useTeleopStore } from "../stores/teleopStore";
import { useTuningStore } from "../stores/tuningStore";

const contract = JSON.parse(contractSource) as {
  velocity_limits: { linear_m_s: { min: number; max: number }; angular_rad_s: { min: number; max: number } };
};
export const TELEOP_LIMITS = contract.velocity_limits;
export function operationalLimits() {
  const telemetry = useTelemetryStore.getState();
  const required = telemetry.capabilities.some(cap => cap === "robot_tuning_v1" || cap === "robot_tuning_policy_v1");
  if (!required) return TELEOP_LIMITS;
  const tuning = useTuningStore.getState();
  const snapshot = tuning.snapshot;
  const age = tuning.receivedAt === null ? Infinity : (performance.now() - tuning.receivedAt) / 1000;
  const caps = snapshot?.activeKnown ? snapshot.effectiveLimits : null;
  if (!snapshot?.available || !caps || age < 0 || age > 5) return null;
  return { linear_m_s: { min: -caps.max_linear_m_s, max: caps.max_linear_m_s },
    angular_rad_s: { min: -caps.max_angular_rad_s, max: caps.max_angular_rad_s } };
}
export const TELEOP_TICK_MS = 80; // 12.5 Hz, latest sample, never a movement queue.

/** Keyboard/digital input ramps from zero over this long; analog never ramps. */
export const TELEOP_RAMP_SECONDS = 1.0;
export const TELEOP_RAMP_STEP_LINEAR = TELEOP_LIMITS.linear_m_s.max * (TELEOP_TICK_MS / 1000) / TELEOP_RAMP_SECONDS;
export const TELEOP_RAMP_STEP_ANGULAR = TELEOP_LIMITS.angular_rad_s.max * (TELEOP_TICK_MS / 1000) / TELEOP_RAMP_SECONDS;

/**
 * Move `current` toward `target` by at most `step`. Reversal passes through
 * zero; a small analog target never grows into the full ceiling because the
 * analog path sets its target directly and never ramps.
 */
export function rampToward(current: number, target: number, step: number): number {
  const delta = target - current;
  if (Math.abs(delta) <= step) return target;
  const next = current + Math.sign(delta) * step;
  // A direction reversal passes through exactly zero rather than jumping sign.
  if (current !== 0 && Math.sign(next) !== Math.sign(current)) return 0;
  return next;
}

/** Deliberately no map, AMCL, seed, confidence or pose-set prerequisite. */
export function manualDriveReason(): string | null {
  const t = useTelemetryStore.getState();
  const commands = useCommandStore.getState();
  const b = t.baseState;
  if (!canCommand(useAuthStore.getState().user)) return "An operator account is required for manual drive.";
  if (!t.capabilities.includes("web_teleop_v1")) return "Manual drive is unavailable. Update the robot bridge first (web_teleop_v1).";
  if (!t.wsConnected || t.connection.state !== "online") return "The robot is not connected — manual drive is unavailable.";
  if (operationalLimits() === null) return "A fresh verified active speed policy is required for manual drive.";
  const manual = useTeleopStore.getState();
  if (manual.externalBusy && !manual.session) return "Another tab is using manual drive. End manual input there or use Stop first.";
  if (commands.active || b?.undock_active || ["navigating", "recording"].includes(t.status.status)) return "Stop navigation first — another robot action is active.";
  if (commands.lidarPending || commands.lidarRecoveryRequired || (commands.lidarStateAfter !== null && (t.baseStateAt === null || t.baseStateAt <= commands.lidarStateAfter))) return "Wait for fresh LiDAR state after the power command.";
  const lidar = lidarReadyReason(b, t.baseStateAt);
  if (lidar) return lidar;
  const rawAge = b?.raw_odom_age_s;
  const receipt = t.baseStateAt === null ? NaN : (performance.now() - t.baseStateAt) / 1000;
  if (typeof rawAge !== "number" || !Number.isFinite(rawAge) || rawAge < 0 || !Number.isFinite(receipt) || receipt < 0 || rawAge + receipt > .5) return "Fresh raw odometry is required for manual drive — wait for the robot bridge.";
  if (b?.odom_epoch_valid !== true) return "A valid raw odometry origin is required for manual drive.";
  if (b?.motors_enabled !== true) return "Turn the motors on before manual drive.";
  if (b.estop_pressed !== false) return "Release the physical emergency stop before manual drive.";
  if (b.fault_flags !== 0) return "Clear the drive-base fault before manual drive.";
  if (b.bumpers_valid !== true || b.bumpers_front !== false || b.bumpers_rear !== false) return "Fresh valid clear front and rear bumpers are required for manual drive.";
  const offDock = b.dock_state_valid === true && b.dock_state === "CLEAR_CONFIRMED";
  if (!offDock) return "Manual drive requires confirmed clearance from the dock.";
  return null;
}
