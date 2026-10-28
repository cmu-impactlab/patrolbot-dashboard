import type { BaseStateData } from "../types/protocol";

const REASONS: Record<string, string> = {
  turn_path_blocked: "Undocking stopped because an obstacle was detected in the turning area. The half-turn did not complete.",
  rear_clearance_insufficient_for_requested_distance: "There is not enough rear clearance for the requested undock distance.",
  rear_bumper_pressed: "Undocking stopped because the rear bumper was pressed.",
  front_bumper_pressed: "Undocking stopped because the front bumper was pressed.",
  rear_sonar_unusable: "Undocking stopped because rear sonar readings were missing, stale or inside their unreliable near field.",
  invalid_turn_odometry: "Undocking stopped because wheel odometry could not confirm a safe turn.",
  physical_clearance_lost: "The robot could no longer confirm separation from the charger.",
};

export function operationFaultReason(base: BaseStateData | null): string | null {
  if (!base) return null;
  if (base.operation_outcome_unknown) return "The undock outcome is unknown. Inspect the robot before further operation; normal fault recovery is blocked.";
  if (base.estop_pressed) return "The physical emergency stop is pressed. It cannot be bypassed from the dashboard.";
  if (base.fault_flags) return `The drive base reports hardware fault flags ${base.fault_flags}. Resolve the hardware fault before recovery.`;
  if (base.stall_value & 0x0101) return "The drive base reports a stalled wheel. Inspect the wheels and remove the cause before recovery.";
  if (base.lidar_state === "FAULT") return "The laser scanner reports a fault. Inspect the scanner and use its explicit power controls; operation recovery cannot bypass missing scan data.";
  const reason = base.operation_fault_reason;
  if (reason) return REASONS[reason] ?? `Undocking stopped: ${reason.replaceAll("_", " ")}.`;
  if (base.dock_state === "FAULT_STOPPED") return "A previous undock left the drive base fault-stopped. The detailed reason is unavailable from this robot session. Inspect the robot before recovery.";
  return null;
}
