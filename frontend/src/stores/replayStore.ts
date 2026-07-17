import { create } from "zustand";

export interface RecordingRow {
  id: number;
  robot_id: string;
  name: string;
  started_at: string;
  ended_at: string | null;
  status: "recording" | "done";
  sample_count: number;
}

export interface RecordingSample {
  ts: string;
  kind: "pose" | "battery" | "event";
  data: Record<string, unknown>;
}

export interface ReplayPose {
  tMs: number; // milliseconds since the first sample
  x: number;
  y: number;
  yaw: number;
}

interface ReplayState {
  /** Loaded recording being replayed on the Live Map, or null. */
  recording: RecordingRow | null;
  poses: ReplayPose[];
  durationMs: number;
  playing: boolean;
  tMs: number;
  /** Wall-clock anchor for the advancing clock while playing. */
  startedAtMono: number;
  startedAtT: number;

  load: (recording: RecordingRow, samples: RecordingSample[]) => void;
  close: () => void;
  play: () => void;
  pause: () => void;
  seek: (tMs: number) => void;
  /** Current playhead, advancing in real time while playing. */
  now: () => number;
  /** Pose interpolated at the playhead (null before the first pose). */
  poseAt: (tMs: number) => ReplayPose | null;
}

export const useReplayStore = create<ReplayState>((set, get) => ({
  recording: null,
  poses: [],
  durationMs: 0,
  playing: false,
  tMs: 0,
  startedAtMono: 0,
  startedAtT: 0,

  load: (recording, samples) => {
    const poseSamples = samples.filter((sample) => sample.kind === "pose");
    if (poseSamples.length === 0) {
      set({ recording, poses: [], durationMs: 0, playing: false, tMs: 0 });
      return;
    }
    const t0 = Date.parse(poseSamples[0].ts);
    const poses: ReplayPose[] = poseSamples.map((sample) => ({
      tMs: Date.parse(sample.ts) - t0,
      x: Number(sample.data.x),
      y: Number(sample.data.y),
      yaw: Number(sample.data.yaw ?? 0),
    }));
    set({
      recording,
      poses,
      durationMs: poses[poses.length - 1].tMs,
      playing: true,
      tMs: 0,
      startedAtMono: performance.now(),
      startedAtT: 0,
    });
  },

  close: () => set({ recording: null, poses: [], playing: false, tMs: 0, durationMs: 0 }),

  play: () => set({ playing: true, startedAtMono: performance.now(), startedAtT: get().tMs }),
  pause: () => set({ playing: false, tMs: get().now() }),
  seek: (tMs) => set({ tMs, startedAtMono: performance.now(), startedAtT: tMs }),

  now: () => {
    const state = get();
    if (!state.playing) return state.tMs;
    const t = state.startedAtT + (performance.now() - state.startedAtMono);
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
        return { tMs, x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, yaw: a.yaw + dyaw * f };
      }
    }
    return poses[poses.length - 1];
  },
}));
