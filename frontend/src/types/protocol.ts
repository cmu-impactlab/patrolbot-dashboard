/**
 * Hand-mirrored from server/app/protocol/messages.py (the authoritative
 * schema). Golden fixtures in shared/schemas/fixtures are type-checked
 * against these in protocol.test.ts — keep the three in sync.
 */

export type DiagLevel = "OK" | "WARN" | "ERROR" | "STALE";
export type ConnectionState = "online" | "stale" | "offline";
export type RobotStatus =
  | "ready" | "navigating" | "recording" | "docked"
  | "charging" | "paused" | "stuck" | "needs_attention" | "offline";
export type HealthLevel = "healthy" | "warning" | "fault" | "offline";
export type EventSeverity = "info" | "warning" | "critical";

export interface Envelope<T extends string = string, D = unknown> {
  version: 1;
  type: T;
  robot_id: string;
  sequence: number;
  timestamp: string;
  data: D;
}

export interface HelloData {
  protocol_version: number;
  capabilities: string[];
  map_version: number;
  software_version: string;
}

export interface HeartbeatData {
  uptime_s: number;
}

export interface PoseData {
  frame_id?: string;
  x: number;
  y: number;
  yaw: number;
  linear_velocity: number;
  angular_velocity: number;
  covariance_trace?: number | null;
  localized?: boolean;
}

export interface LidarData {
  angle_min: number;
  angle_increment: number;
  ranges: (number | null)[];
}

export interface GoalData {
  x: number;
  y: number;
  yaw?: number | null;
}

export interface PathData {
  frame_id?: string;
  points: [number, number][];
  goal?: GoalData | null;
}

export interface BatteryEstimate {
  state: string;
  minutes_remaining: number | null;
  confidence: string;
}

export interface BatteryData {
  voltage: number;
  current?: number | null;
  percentage?: number | null;
  charging: boolean;
  estimate?: BatteryEstimate | null;
}

export interface BaseStateData {
  session_generation: number;
  link_connected: boolean;
  telemetry_age: number;
  hardware_state_valid: boolean;
  charge_state: string;
  motors_enabled: boolean;
  estop_pressed: boolean;
  fault_flags: number;
  stall_value: number;
  bumpers_front: boolean;
  bumpers_rear: boolean;
  /** Authoritative SBC dock observer. charge_state can remain latched after
   * physical departure, so a valid CLEAR_CONFIRMED wins over it. Optional for
   * recordings and robots from before the observer was added. */
  dock_state?: string | null;
  dock_state_valid?: boolean | null;
  dock_phase?: number | null;
  dock_phase_name?: string | null;
  undock_active?: boolean;
  undock_release_attempts?: number | null;
  minimum_rear_range?: number | null;
  rear_sonar_usable?: boolean | null;
  redock_inhibited?: boolean | null;
  redock_inhibit_remaining?: number | null;
  undock_profile_commissioned?: boolean | null;
}

export interface DiagnosticItem {
  name: string;
  level: DiagLevel;
  message: string;
  values?: Record<string, string> | null;
}

export interface DiagnosticsData {
  items: DiagnosticItem[];
}

export interface ResourcesData {
  cpu_percent: number;
  memory_percent: number;
  cpu_temp_c?: number | null;
  disk_percent: number;
  wifi_signal_dbm?: number | null;
}

export interface MapOrigin {
  x: number;
  y: number;
  yaw: number;
}

export interface MapData {
  map_version: number;
  name: string;
  resolution: number;
  width: number;
  height: number;
  origin: MapOrigin;
  rle: [number, number][];
}

export interface ConnectionData {
  state: ConnectionState;
  last_seen?: string | null;
}

export interface RobotStatusData {
  status: RobotStatus;
  detail: string;
}

/** What the connected robot declared in robot.hello. An optional control the
 *  robot does not advertise — Undock — is shown disabled with the reason,
 *  rather than silently missing, whenever it is on screen at all. */
export interface CapabilitiesData {
  capabilities: string[];
}

export interface Subsystem {
  id: string;
  label: string;
  level: HealthLevel;
  message: string;
  action?: string | null;
  updated_at: string;
}

export interface SystemHealthData {
  overall: HealthLevel;
  subsystems: Subsystem[];
}

export interface EventData {
  id: number;
  ts: string;
  severity: EventSeverity;
  title: string;
  message: string;
}

export interface SnapshotData {
  connection: ConnectionData;
  robot_status: RobotStatusData;
  system_health: SystemHealthData;
  map_version: number;
  pose?: PoseData | null;
  battery?: BatteryData | null;
  battery_estimate?: BatteryEstimate | null;
  base_state?: BaseStateData | null;
  diagnostics?: DiagnosticsData | null;
  resources?: ResourcesData | null;
  path?: PathData | null;
  last_known_pose?: GoalData | null;
  capabilities?: string[];
  events: EventData[];
}

export type CommandType =
  | "navigate_to_pose" | "set_initial_pose" | "stop"
  // The guarded UI sends `undock` as one action. The two service-level
  // commands remain in the protocol for robot-side diagnostics and recovery.
  // There is no `dock`: the robot has no automatic dock-in path.
  | "charge_release" | "motor_enable" | "undock";
export type CommandOutcome = "succeeded" | "failed" | "rejected" | "canceled" | "timeout";

export interface CommandRequestData {
  command_id: string;
  command: CommandType;
  goal?: GoalData | null;
  /** Set only when the operator confirms taking control from the current
   *  lease holder. */
  takeover?: boolean;
  /** Server-stamped from the verified session role on the way to the robot.
   *  Browsers never set this; anything sent here is overwritten. */
  operator_authorized?: boolean;
}

export interface CommandAckData {
  command_id: string;
  accepted: boolean;
  reason?: string | null;
}

export interface CommandProgressData {
  command_id: string;
  stage: string;
  detail?: string | null;
  distance_remaining?: number | null;
}

export interface CommandResultData {
  command_id: string;
  outcome: CommandOutcome;
  detail?: string | null;
}

export type AnyFrame =
  | Envelope<"server.snapshot", SnapshotData>
  | Envelope<"state.connection", ConnectionData>
  | Envelope<"state.robot_status", RobotStatusData>
  | Envelope<"state.system_health", SystemHealthData>
  | Envelope<"state.capabilities", CapabilitiesData>
  | Envelope<"event.append", EventData>
  | Envelope<"telemetry.pose", PoseData>
  | Envelope<"telemetry.lidar", LidarData>
  | Envelope<"telemetry.path", PathData>
  | Envelope<"telemetry.battery", BatteryData>
  | Envelope<"telemetry.base_state", BaseStateData>
  | Envelope<"telemetry.diagnostics", DiagnosticsData>
  | Envelope<"telemetry.resources", ResourcesData>
  | Envelope<"telemetry.map", MapData>
  | Envelope<"command.ack", CommandAckData>
  | Envelope<"command.progress", CommandProgressData>
  | Envelope<"command.result", CommandResultData>;

/** Decode an RLE-encoded occupancy grid into a flat cell array. */
export function decodeRle(rle: [number, number][]): Int8Array {
  const total = rle.reduce((sum, [, count]) => sum + count, 0);
  const cells = new Int8Array(total);
  let index = 0;
  for (const [value, count] of rle) {
    cells.fill(value, index, index + count);
    index += count;
  }
  return cells;
}
