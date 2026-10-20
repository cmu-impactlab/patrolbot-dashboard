import { sameMap } from "../lib/mapContext";

import { localizationRecoveryReason } from "../lib/localizationRecovery";
import { create } from "zustand";
import type {
  AnyFrame,
  BaseStateData,
  BatteryData,
  ConnectionData,
  DiagnosticsData,
  EventData,
  GoalData,
  LidarData,
  MotionData,
  PathData,
  PoseData,
  ResourcesData,
  RobotStatusData,
  SnapshotData,
  SystemHealthData,
} from "../types/protocol";

const TRAJECTORY_CAP = 2000;
const HISTORY_CAP = 300;

export interface ResourceSample {
  t: number;
  cpu: number;
  mem: number;
  temp: number | null;
  disk: number;
}

export interface BatterySample {
  t: number;
  voltage: number;
  percentage: number | null;
  charging: boolean;
}

export interface TelemetryState {
  wsConnected: boolean;
  robotId: string;
  connection: ConnectionData;
  status: RobotStatusData;
  health: SystemHealthData | null;
  pose: PoseData | null;
  poseReceivedAt: number;
  lidar: LidarData | null;
  path: PathData | null;
  battery: BatteryData | null;
  baseState: BaseStateData | null;
  /** Measured raw-odom twist; null means no measurement, never zero speed. */
  motion: MotionData | null;
  diagnostics: DiagnosticsData | null;
  resources: ResourcesData | null;
  mapVersion: number;
  events: EventData[];
  trajectory: [number, number][];
  resourceHistory: ResourceSample[];
  batteryHistory: BatterySample[];
  frameCount: number;
  lastFrameAt: string | null;
  /**
   * When the last drive-base frame arrived, by the browser's clock. `lastFrameAt`
   * moves on *any* frame, so with the drive base off the Pi's own heartbeat and
   * resources kept it current while every drive-base fact went stale — and the
   * widgets showing those facts had no way to tell.
   */
  baseStateAt: number | null;
  /** Same, for the battery slice. */
  batteryAt: number | null;
  /** Same, for the robot computer's own resource reports. */
  resourcesAt: number | null;
  /**
   * When the last measured-motion sample was received, by the browser's clock.
   * Raw receipt time: the envelope's transport age is kept separately in
   * `motionTransportAgeS` so the total age is sample + receipt + transport +
   * local elapsed (see `motionAgeMs`). Null with `motion` when there is no
   * sample.
   */
  motionAt: number | null;
  /**
   * Seconds between the server stamping the motion envelope and the browser
   * reading it (server->browser transport, plus clamped clock skew). Null when
   * there is no sample; a snapshot's motion leaves this at 0 because its
   * `slice_ages_s.motion` receipt age already covers robot->server time.
   */
  motionTransportAgeS: number | null;
  /** Alert ids the user has read; read alerts move to the "Seen" section. */
  seenEventIds: number[];
  /** Robot's last-known pose persisted server-side when it last went offline. */
  lastKnownPose: GoalData | null;
  /** Local pose gate: manual confirmation or fresh robot-accepted seed. */
  poseSetThisSession: boolean;
  /** Capabilities the connected robot declared; gates the undock control. */
  capabilities: string[];

  setWsConnected: (connected: boolean) => void;
  handleFrame: (frame: AnyFrame) => void;
  markSeen: (id: number) => void;
  markAllSeen: () => void;
  markPoseSet: () => void;
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

const SEEN_KEY = "patrolbot.seenEventIds";
const SEEN_CAP = 500;

function loadSeenIds(): number[] {
  try {
    const raw = safeStorage()?.getItem(SEEN_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id) => typeof id === "number") : [];
  } catch {
    return [];
  }
}

function persistSeen(ids: number[]): number[] {
  const capped = ids.slice(-SEEN_CAP);
  safeStorage()?.setItem(SEEN_KEY, JSON.stringify(capped));
  return capped;
}

const initialStatus: RobotStatusData = {
  status: "offline",
  detail: "The robot is not connected to the dashboard.",
};

function appendTrajectory(
  trajectory: [number, number][],
  pose: PoseData,
): [number, number][] {
  const last = trajectory[trajectory.length - 1];
  if (last) {
    const dx = pose.x - last[0];
    const dy = pose.y - last[1];
    if (dx * dx + dy * dy < 0.0025) return trajectory; // < 5 cm: skip
  }
  const next = trajectory.length >= TRAJECTORY_CAP ? trajectory.slice(-TRAJECTORY_CAP + 1) : trajectory.slice();
  next.push([pose.x, pose.y]);
  return next;
}

function cap<T>(items: T[], item: T, limit: number): T[] {
  const next = items.length >= limit ? items.slice(-limit + 1) : items.slice();
  next.push(item);
  return next;
}

/**
 * Delay between the server stamping the envelope and the browser reading it.
 *
 * The server re-stamps each frame with its own clock, so this is server->browser
 * transport plus any clock skew. Skew only ever makes the frame look older
 * (clamped at zero) and is capped, so a badly-set browser clock cannot show a
 * live reading as fresh — the safe direction for a speed gauge. Returns
 * milliseconds.
 */
export function motionTransportAgeMs(timestamp: string, receivedWallMs: number): number {
  const sent = Date.parse(timestamp);
  if (!Number.isFinite(sent)) return Infinity;
  const delta = receivedWallMs - sent;
  if (!Number.isFinite(delta) || delta < -1000) return Infinity;
  if (delta <= 0) return 0;
  return Math.min(delta, 5000);
}

/**
 * Total age of the measured-motion sample in milliseconds: the odom stamp age
 * and the bridge receipt age it carries, the envelope transport age, plus the
 * local elapsed time since it arrived. Null when there is no sample — never
 * treated as zero.
 */
export function motionAgeMs(
  motion: MotionData | null,
  motionAt: number | null,
  transportAgeS: number | null = null,
  now: number = performance.now(),
): number | null {
  if (motion === null || motionAt === null) return null;
  const local = Math.max(0, now - motionAt);
  const transport = Math.max(0, transportAgeS ?? 0) * 1000;
  return (motion.sample_age_s + motion.receipt_age_s) * 1000 + transport + local;
}

/**
 * Whether a motion sample is superseded and must not replace the live reading.
 * A sample from a different drive-base session, or from an older odometry epoch
 * generation, is obsolete; accepting it would mix sessions or move the gauge
 * backwards in origin time.
 */
export function motionSuperseded(
  sample: MotionData,
  baseState: BaseStateData | null,
  current: MotionData | null,
): boolean {
  if (sample.source !== "raw_odom" || ![sample.linear_velocity, sample.angular_velocity, sample.sample_age_s, sample.receipt_age_s].every(value => typeof value === "number" && Number.isFinite(value))
    || sample.sample_age_s < 0 || sample.receipt_age_s < 0 || Math.abs(sample.linear_velocity) > 5 || Math.abs(sample.angular_velocity) > 10
    || !Number.isSafeInteger(sample.session_generation) || !Number.isSafeInteger(sample.odom_epoch_generation)
    || typeof sample.odom_epoch !== "string" || sample.odom_epoch.length === 0 || sample.odom_epoch.length > 128) return true;
  if (baseState !== null && sample.session_generation !== baseState.session_generation) {
    return true;
  }
  if (current !== null && sample.odom_epoch_generation < current.odom_epoch_generation) {
    return true;
  }
  return false;
}

export const useTelemetryStore = create<TelemetryState>((set, get) => ({
  wsConnected: false,
  robotId: "patrolbot-01",
  connection: { state: "offline", last_seen: null },
  status: initialStatus,
  health: null,
  pose: null,
  poseReceivedAt: 0,
  lidar: null,
  path: null,
  battery: null,
  baseState: null,
  motion: null,
  diagnostics: null,
  resources: null,
  mapVersion: 0,
  events: [],
  trajectory: [],
  resourceHistory: [],
  batteryHistory: [],
  frameCount: 0,
  lastFrameAt: null,
  baseStateAt: null,
  batteryAt: null,
  resourcesAt: null,
  motionAt: null,
  motionTransportAgeS: null,
  seenEventIds: loadSeenIds(),
  lastKnownPose: null,
  poseSetThisSession: false,
  capabilities: [],

  setWsConnected: (connected) =>
    set(
      connected
        ? { wsConnected: true }
        : {
            wsConnected: false,
            connection: { state: "offline", last_seen: get().connection.last_seen },
            status: initialStatus,
            // Forget browser permission until fresh robot recovery state arrives.
            poseSetThisSession: false,
            // Whatever reconnects may be a different robot — the snapshot
            // that follows re-declares what it can do.
            capabilities: [],
            // The measured twist is bound to a connection/origin; without one
            // it must read as "no measurement", never as a stop.
            motion: null,
            motionAt: null,
            motionTransportAgeS: null,
          },
    ),

  markPoseSet: () => set({ poseSetThisSession: true }),

  markSeen: (id) => {
    const seen = get().seenEventIds;
    if (seen.includes(id)) return;
    set({ seenEventIds: persistSeen([...seen, id]) });
  },

  markAllSeen: () => {
    const ids = get().events.map((event) => event.id);
    const merged = [...new Set([...get().seenEventIds, ...ids])];
    set({ seenEventIds: persistSeen(merged) });
  },

  handleFrame: (frame) => {
    const bump = {
      frameCount: get().frameCount + 1,
      robotId: frame.robot_id,
      lastFrameAt: new Date().toISOString(),
    };
    switch (frame.type) {
      case "server.snapshot": {
        const data = frame.data as SnapshotData;
        // A snapshot catches a late-joining browser up on values that may be
        // arbitrarily old. Timing them from arrival would show days-old
        // readings as current; the server sends how long ago it received each
        // one, so they are backdated to when they actually arrived.
        const receivedAt = (slice: string): number | null => {
          const age = data.slice_ages_s?.[slice];
          return age == null ? null : performance.now() - age * 1000;
        };
        // A snapshot's motion is only usable if it belongs to the same
        // drive-base session it arrived with; an obsolete sample is dropped
        // rather than shown, and absence stays absent.
        const snapshotMotion =
          data.motion && !motionSuperseded(data.motion, data.base_state ?? null, null)
            ? data.motion
            : null;
        set({
          ...bump,
          connection: data.connection,
          status: data.robot_status,
          health: data.system_health,
          pose: data.pose ?? null,
          battery: data.battery ?? null,
          baseState: data.base_state ?? null,
          baseStateAt: receivedAt("base_state"),
          motion: snapshotMotion,
          motionAt: snapshotMotion === null ? null : receivedAt("motion"),
          motionTransportAgeS: snapshotMotion === null ? null : motionTransportAgeMs(frame.timestamp, Date.now()) / 1000,
          diagnostics: data.diagnostics ?? null,
          resources: data.resources ?? null,
          path: data.path ?? null,
          poseReceivedAt: receivedAt("pose") ?? 0,
          batteryAt: receivedAt("battery"),
          resourcesAt: receivedAt("resources"),
          mapVersion: data.map_version,
          events: data.events,
          lastKnownPose: data.last_known_pose ?? null,
          capabilities: data.capabilities ?? [],
          // Reconnect to the robot estimate without publishing a pose. Only
          // fresh, accepted recovery state can satisfy the local pose gate.
          trajectory: [],
          lidar: null,
          poseSetThisSession: data.connection.state === "online" &&
            localizationRecoveryReason(data.base_state ?? null, receivedAt("base_state")) === null,
        });
        break;
      }
      case "state.connection": {
        const wasOffline = get().connection.state === "offline";
        if (frame.data.state === "online" && wasOffline) {
          // The (re)connecting robot may be a different one (mock <-> real):
          // clear per-robot overlays; live frames repopulate them. A new
          // connection is a new session, so the pose gate resets.
          set({
            ...bump,
            connection: frame.data,
            trajectory: [],
            lidar: null,
            path: null,
            motion: null,
            motionAt: null,
            motionTransportAgeS: null,
            poseSetThisSession: false,
          });
        } else {
          set({ ...bump, connection: frame.data,
            ...(frame.data.state !== "online" ? { motion: null, motionAt: null, motionTransportAgeS: null } : {}) });
        }
        break;
      }
      case "state.robot_status":
        set({ ...bump, status: frame.data });
        break;
      case "state.system_health":
        set({ ...bump, health: frame.data });
        break;
      case "state.capabilities":
        set({ ...bump, capabilities: frame.data.capabilities });
        break;
      case "event.append":
        set({ ...bump, events: [frame.data, ...get().events].slice(0, 200) });
        break;
      case "telemetry.pose":
        set({
          ...bump,
          pose: frame.data,
          poseReceivedAt: performance.now(),
          trajectory: appendTrajectory(sameMap(get().pose, frame.data) ? get().trajectory : [], frame.data),
        });
        break;
      case "telemetry.lidar":
        set({ ...bump, lidar: frame.data });
        break;
      case "telemetry.path":
        set({ ...bump, path: frame.data });
        break;
      case "telemetry.battery":
        set({
          ...bump,
          battery: frame.data,
          batteryAt: performance.now(),
          batteryHistory: cap(
            get().batteryHistory,
            {
              t: Date.now(),
              voltage: frame.data.voltage,
              percentage: frame.data.percentage ?? null,
              charging: frame.data.charging,
            },
            HISTORY_CAP,
          ),
        });
        break;
      case "telemetry.base_state":
        set({ ...bump, baseState: frame.data, baseStateAt: performance.now(),
          // A new drive-base session supersedes the measured twist bound to the
          // old one; keeping it would let the gauge mix two sessions.
          ...(get().baseState !== null &&
              frame.data.session_generation !== get().baseState!.session_generation
            ? { motion: null, motionAt: null, motionTransportAgeS: null }
            : {}),
          // Full robot reset remains false until a new seed is accepted.
          poseSetThisSession: get().connection.state === "online" &&
            localizationRecoveryReason(frame.data, performance.now()) === null });
        break;
      case "telemetry.motion": {
        // Drop a sample from a superseded session or an older odom origin
        // generation instead of moving the gauge backwards.
        if (motionSuperseded(frame.data, get().baseState, get().motion)) {
          set({ ...bump });
          break;
        }
        // Raw receipt time plus the separately-tracked transport age: the
        // exported age helper and the gauge add them without double-counting.
        const transport = motionTransportAgeMs(frame.timestamp, Date.now());
        set({
          ...bump,
          motion: frame.data,
          motionAt: performance.now(),
          motionTransportAgeS: transport / 1000,
        });
        break;
      }
      case "telemetry.diagnostics":
        set({ ...bump, diagnostics: frame.data });
        break;
      case "telemetry.resources":
        set({
          ...bump,
          resourcesAt: performance.now(),
          resources: frame.data,
          resourceHistory: cap(
            get().resourceHistory,
            {
              t: Date.now(),
              cpu: frame.data.cpu_percent,
              mem: frame.data.memory_percent,
              temp: frame.data.cpu_temp_c ?? null,
              disk: frame.data.disk_percent,
            },
            HISTORY_CAP,
          ),
        });
        break;
      case "telemetry.map":
        // Map payload is fetched over REST (useMap); we only track the version.
        set({ ...bump, mapVersion: frame.data.map_version });
        break;
    }
  },
}));
