import { create } from "zustand";

export interface RecordingRow {
  id: number;
  robot_id: string;
  name: string;
  /** Channels the recording captured; drives the export picker. */
  channels?: string[];
  started_at: string;
  ended_at: string | null;
  status: "recording" | "done";
  sample_count: number;
}

export interface RecordingSample {
  ts: string;
  kind: string;
  data: Record<string, unknown>;
}

/** All replay series share one clock: milliseconds since the recording's
 *  earliest sample, whatever channel that came from. */
export interface ReplayPose {
  tMs: number;
  x: number;
  y: number;
  yaw: number;
  linearVelocity: number;
}

export interface ReplayEvent {
  tMs: number;
  severity: string;
  title: string;
  message: string;
}

export interface ReplayBattery {
  tMs: number;
  percentage: number | null;
  voltage: number | null;
}

export const REPLAY_SPEEDS = [0.5, 1, 2, 4, 8] as const;

interface ReplayState {
  /** The recording being replayed, or null when nothing is loaded. */
  recording: RecordingRow | null;
  poses: ReplayPose[];
  events: ReplayEvent[];
  battery: ReplayBattery[];
  durationMs: number;
  playing: boolean;
  speed: number;
  tMs: number;
  /** Wall-clock anchor for the advancing clock while playing. */
  startedAtMono: number;
  startedAtT: number;

  load: (recording: RecordingRow, samples: RecordingSample[]) => void;
  close: () => void;
  play: () => void;
  pause: () => void;
  seek: (tMs: number) => void;
  setSpeed: (speed: number) => void;
  /** Current playhead, advancing in real time (times `speed`) while playing. */
  now: () => number;
  /** Pose interpolated at the playhead (null before the first pose). */
  poseAt: (tMs: number) => ReplayPose | null;
  /** Distance travelled along the recorded route, in metres. */
  routeLength: () => number;
}

function num(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export const useReplayStore = create<ReplayState>((set, get) => ({
  recording: null,
  poses: [],
  events: [],
  battery: [],
  durationMs: 0,
  playing: false,
  speed: 1,
  tMs: 0,
  startedAtMono: 0,
  startedAtT: 0,

  load: (recording, samples) => {
    const stamps = samples
      .map((sample) => Date.parse(sample.ts))
      .filter((ms) => Number.isFinite(ms));
    // One origin for every series, so an alert that fired before the first
    // pose still lands in the right place on the timeline.
    const t0 = stamps.length > 0 ? Math.min(...stamps) : 0;
    const at = (sample: RecordingSample) => Date.parse(sample.ts) - t0;

    const poses: ReplayPose[] = [];
    const events: ReplayEvent[] = [];
    const battery: ReplayBattery[] = [];
    for (const sample of samples) {
      const tMs = at(sample);
      if (!Number.isFinite(tMs)) continue;
      if (sample.kind === "pose") {
        poses.push({
          tMs,
          x: num(sample.data.x),
          y: num(sample.data.y),
          yaw: num(sample.data.yaw),
          linearVelocity: num(sample.data.linear_velocity),
        });
      } else if (sample.kind === "event") {
        events.push({
          tMs,
          severity: String(sample.data.severity ?? "info"),
          title: String(sample.data.title ?? ""),
          message: String(sample.data.message ?? ""),
        });
      } else if (sample.kind === "battery") {
        battery.push({
          tMs,
          percentage: sample.data.percentage == null ? null : num(sample.data.percentage),
          voltage: sample.data.voltage == null ? null : num(sample.data.voltage),
        });
      }
    }
    poses.sort((a, b) => a.tMs - b.tMs);
    events.sort((a, b) => a.tMs - b.tMs);
    battery.sort((a, b) => a.tMs - b.tMs);

    const durationMs = Math.max(
      0,
      ...[poses, events, battery].map((series) =>
        series.length > 0 ? series[series.length - 1].tMs : 0),
    );

    set({
      recording,
      poses,
      events,
      battery,
      durationMs,
      playing: poses.length > 1,
      tMs: 0,
      startedAtMono: performance.now(),
      startedAtT: 0,
    });
  },

  close: () => set({
    recording: null, poses: [], events: [], battery: [],
    playing: false, tMs: 0, durationMs: 0,
  }),

  play: () => {
    const state = get();
    // Replaying from the end restarts rather than sitting stuck at the tail.
    const from = state.tMs >= state.durationMs ? 0 : state.tMs;
    set({ playing: true, tMs: from, startedAtMono: performance.now(), startedAtT: from });
  },
  pause: () => set({ playing: false, tMs: get().now() }),
  seek: (tMs) => set({ tMs, startedAtMono: performance.now(), startedAtT: tMs }),
  setSpeed: (speed) => {
    // Re-anchor first: the elapsed-time maths ahead of this point used the
    // old rate, so the playhead must not jump when the rate changes.
    const t = get().now();
    set({ speed, tMs: t, startedAtMono: performance.now(), startedAtT: t });
  },

  now: () => {
    const state = get();
    if (!state.playing) return state.tMs;
    const t = state.startedAtT + (performance.now() - state.startedAtMono) * state.speed;
    return Math.min(t, state.durationMs);
  },

  poseAt: (tMs) => {
    const { poses } = get();
    if (poses.length === 0) return null;
    if (tMs <= poses[0].tMs) return poses[0];
    for (let i = 1; i < poses.length; i++) {
      if (poses[i].tMs >= tMs) {
        const a = poses[i - 1];
        const b = poses[i];
        const f = b.tMs === a.tMs ? 0 : (tMs - a.tMs) / (b.tMs - a.tMs);
        // Interpolate yaw along the shortest arc.
        let dyaw = b.yaw - a.yaw;
        if (dyaw > Math.PI) dyaw -= 2 * Math.PI;
        if (dyaw < -Math.PI) dyaw += 2 * Math.PI;
        return {
          tMs,
          x: a.x + (b.x - a.x) * f,
          y: a.y + (b.y - a.y) * f,
          yaw: a.yaw + dyaw * f,
          linearVelocity: a.linearVelocity + (b.linearVelocity - a.linearVelocity) * f,
        };
      }
    }
    return poses[poses.length - 1];
  },

  routeLength: () => {
    const { poses } = get();
    let total = 0;
    for (let i = 1; i < poses.length; i++) {
      total += Math.hypot(poses[i].x - poses[i - 1].x, poses[i].y - poses[i - 1].y);
    }
    return total;
  },
}));
