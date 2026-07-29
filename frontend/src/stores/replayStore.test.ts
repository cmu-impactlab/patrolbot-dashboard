import { beforeEach, describe, expect, it } from "vitest";
import { useReplayStore, type RecordingRow, type RecordingSample } from "./replayStore";

const ROW: RecordingRow = {
  id: 1,
  robot_id: "patrolbot-01",
  name: "Night patrol",
  channels: ["pose", "battery", "event"],
  started_at: "2026-07-28 09:00:00",
  ended_at: "2026-07-28 09:00:10",
  status: "done",
  sample_count: 5,
};

function sample(ts: string, kind: string, data: Record<string, unknown>): RecordingSample {
  return { ts, kind, data };
}

const SAMPLES: RecordingSample[] = [
  // Deliberately out of order and starting with a non-pose channel: the
  // clock origin must come from the earliest sample of any kind.
  sample("2026-07-28T09:00:04.000Z", "pose",
         { x: 4, y: 0, yaw: 0, linear_velocity: 0.5 }),
  sample("2026-07-28T09:00:00.000Z", "event",
         { severity: "warning", title: "Bumper", message: "Front bumper pressed" }),
  sample("2026-07-28T09:00:02.000Z", "pose",
         { x: 0, y: 0, yaw: 0, linear_velocity: 0.5 }),
  sample("2026-07-28T09:00:03.000Z", "battery", { voltage: 25.1, percentage: 80 }),
  sample("2026-07-28T09:00:06.000Z", "pose",
         { x: 4, y: 3, yaw: Math.PI / 2, linear_velocity: 0.5 }),
  sample("2026-07-28T09:00:05.000Z", "battery", { voltage: 25.0, percentage: 79 }),
];

beforeEach(() => {
  useReplayStore.getState().close();
});

describe("replay store", () => {
  it("puts every channel on one clock, zeroed at the earliest sample", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    const state = useReplayStore.getState();
    expect(state.poses.map((pose) => pose.tMs)).toEqual([2000, 4000, 6000]);
    expect(state.events.map((event) => event.tMs)).toEqual([0]);
    expect(state.battery.map((reading) => reading.tMs)).toEqual([3000, 5000]);
    expect(state.durationMs).toBe(6000);
  });

  it("keeps series sorted regardless of the order samples arrived in", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    const { poses } = useReplayStore.getState();
    expect(poses.map((pose) => pose.x)).toEqual([0, 4, 4]);
  });

  it("interpolates position and yaw between recorded poses", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    const mid = useReplayStore.getState().poseAt(3000);
    expect(mid!.x).toBeCloseTo(2);
    expect(mid!.y).toBeCloseTo(0);
    const turning = useReplayStore.getState().poseAt(5000);
    expect(turning!.yaw).toBeCloseTo(Math.PI / 4);
    expect(turning!.y).toBeCloseTo(1.5);
  });

  it("clamps the playhead to the recorded range", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    expect(useReplayStore.getState().poseAt(-500)!.x).toBe(0);
    expect(useReplayStore.getState().poseAt(99_000)!.y).toBe(3);
  });

  it("measures the route the robot actually drove", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    expect(useReplayStore.getState().routeLength()).toBeCloseTo(7); // 4 across, 3 up
  });

  it("changing speed does not jump the playhead", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    useReplayStore.getState().seek(2500);
    useReplayStore.getState().pause();
    useReplayStore.getState().setSpeed(4);
    expect(useReplayStore.getState().now()).toBeCloseTo(2500, 0);
    expect(useReplayStore.getState().speed).toBe(4);
  });

  it("playing from the end restarts instead of sitting at the tail", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    useReplayStore.getState().pause();
    useReplayStore.getState().seek(6000);
    useReplayStore.getState().pause();
    useReplayStore.getState().play();
    expect(useReplayStore.getState().tMs).toBe(0);
    expect(useReplayStore.getState().playing).toBe(true);
  });

  it("a recording with no position data loads without a route to play", () => {
    useReplayStore.getState().load(ROW, [
      sample("2026-07-28T09:00:00.000Z", "event", { severity: "info", title: "Started" }),
      sample("2026-07-28T09:00:08.000Z", "event", { severity: "info", title: "Finished" }),
    ]);
    const state = useReplayStore.getState();
    expect(state.poses).toEqual([]);
    expect(state.poseAt(0)).toBeNull();
    expect(state.playing).toBe(false);
    // The timeline still spans the alerts, so the page is not empty.
    expect(state.durationMs).toBe(8000);
    expect(state.routeLength()).toBe(0);
  });

  it("close clears the recording so a stale route cannot linger", () => {
    useReplayStore.getState().load(ROW, SAMPLES);
    useReplayStore.getState().close();
    const state = useReplayStore.getState();
    expect(state.recording).toBeNull();
    expect(state.poses).toEqual([]);
    expect(state.durationMs).toBe(0);
  });
});
