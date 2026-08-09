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
  /** Alert ids the user has read; read alerts move to the "Seen" section. */
  seenEventIds: number[];
  /** Robot's last-known pose persisted server-side when it last went offline. */
  lastKnownPose: GoalData | null;
  /** Whether a 2D location has been set this session (gates navigation). */
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
            // A dropped socket ends the session; the pose must be set again.
            poseSetThisSession: false,
            // Whatever reconnects may be a different robot — the snapshot
            // that follows re-declares what it can do.
            capabilities: [],
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
        set({
          ...bump,
          connection: data.connection,
          status: data.robot_status,
          health: data.system_health,
          pose: data.pose ?? null,
          battery: data.battery ?? null,
          baseState: data.base_state ?? null,
          baseStateAt: receivedAt("base_state"),
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
          // A snapshot starts a fresh session — drop lines drawn for the
          // previous robot/connection instead of mixing them in, and require
          // the 2D location to be set again before navigating.
          trajectory: [],
          lidar: null,
          poseSetThisSession: false,
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
            poseSetThisSession: false,
          });
        } else {
          set({ ...bump, connection: frame.data });
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
          trajectory: appendTrajectory(get().trajectory, frame.data),
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
        set({ ...bump, baseState: frame.data, baseStateAt: performance.now() });
        break;
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
