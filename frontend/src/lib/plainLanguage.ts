/**
 * The single source of user-facing copy. Every enum the protocol can produce
 * has an entry here; a vitest checks completeness. No ROS terminology —
 * technical names live only inside collapsible "technical details" sections.
 */
import type {
  CommandType, ConnectionState, EventSeverity, HealthLevel, RobotStatus,
} from "../types/protocol";

export const STATUS_COPY: Record<RobotStatus, { label: string; tone: string }> = {
  ready: { label: "Ready", tone: "ok" },
  navigating: { label: "Navigating", tone: "active" },
  recording: { label: "Recording", tone: "active" },
  docked: { label: "Docked", tone: "ok" },
  charging: { label: "Charging", tone: "ok" },
  paused: { label: "Paused", tone: "warn" },
  stuck: { label: "May Be Stuck", tone: "warn" },
  needs_attention: { label: "Needs Attention", tone: "danger" },
  offline: { label: "Offline", tone: "muted" },
};

export const CONNECTION_COPY: Record<ConnectionState, { label: string; description: string }> = {
  online: { label: "Live", description: "Receiving live data from the robot." },
  stale: { label: "Slow link", description: "Data from the robot is arriving slowly." },
  offline: { label: "Disconnected", description: "The dashboard is not receiving data from the robot." },
};

export const HEALTH_COPY: Record<HealthLevel, { label: string; tone: string }> = {
  healthy: { label: "Healthy", tone: "ok" },
  warning: { label: "Attention", tone: "warn" },
  fault: { label: "Fault", tone: "danger" },
  offline: { label: "No data", tone: "muted" },
};

export const SEVERITY_COPY: Record<EventSeverity, { label: string; tone: string }> = {
  info: { label: "Info", tone: "info" },
  warning: { label: "Warning", tone: "warn" },
  critical: { label: "Critical", tone: "danger" },
};

/** What each command is called when the dashboard reports on it. */
export const COMMAND_COPY: Record<CommandType, string> = {
  navigate_to_pose: "Send to destination",
  set_initial_pose: "Set robot location",
  stop: "Stop",
  charge_release: "Release charging",
  motor_enable: "Turn motors on",
  dock: "Send to dock",
  undock: "Undock",
};

export const ESTIMATE_COPY: Record<string, string> = {
  charging: "Charging",
  calculating: "Estimating…",
  stable: "Battery level is steady",
  estimated: "Estimated",
  threshold_reached: "Battery is at its low threshold",
  unavailable: "Estimate unavailable",
};

export function healthOverallText(overall: HealthLevel, attentionCount: number): string {
  switch (overall) {
    case "healthy":
      return "All systems operational";
    case "offline":
      return "The robot is not connected";
    default:
      return attentionCount === 1
        ? "1 system needs attention"
        : `${attentionCount} systems need attention`;
  }
}
