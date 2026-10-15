import { lidarReadyReason } from "./lidar";
import { localizationRecoveryReason } from "./localizationRecovery";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useCommandStore, type PickMode } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";

/** Re-read at confirmation, never authorize from a cached preview. Server gates remain authoritative. */
export function mapCommandReason(mode: PickMode): string | null {
  const telemetry = useTelemetryStore.getState();
  const commands = useCommandStore.getState();
  if (!canCommand(useAuthStore.getState().user)) return "An operator account is required.";
  if (!telemetry.wsConnected || telemetry.connection.state !== "online") return "The robot is disconnected.";
  if (mode === "none" || commands.pickMode !== mode) return "Selection has ended. Choose a new position.";
  if (commands.lidarPending !== null) return "LiDAR power is pending or its outcome is unknown. Wait for its result.";
  if (commands.lidarRecoveryRequired) return "LiDAR power recovery is required. Make a new explicit power request first.";
  if (commands.lidarStateAfter !== null && (telemetry.baseStateAt === null || telemetry.baseStateAt <= commands.lidarStateAfter)) return "Waiting for fresh LiDAR state after the power command.";
  const lidarReason = lidarReadyReason(telemetry.baseState, telemetry.baseStateAt);
  if (lidarReason) return lidarReason;
  if (commands.active?.phase === "sending" || (commands.active && commands.active.command !== "navigate_to_pose")) return "Wait for the current command to finish.";
  if (mode === "goal") {
    const reason = localizationRecoveryReason(telemetry.baseState, telemetry.baseStateAt);
    if (reason) return reason;
  }
  if (mode === "goal" && !telemetry.poseSetThisSession && !commands.allowUnlocalized)
    return "Set the robot's 2D location before sending it anywhere.";
  return null;
}
