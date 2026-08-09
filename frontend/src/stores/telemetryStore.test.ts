import { beforeEach, describe, expect, it } from "vitest";
import type { Envelope, PoseData, SnapshotData } from "../types/protocol";
import { useTelemetryStore } from "./telemetryStore";

function poseFrame(x: number, y: number, sequence = 1): Envelope<"telemetry.pose", PoseData> {
  return {
    version: 1,
    type: "telemetry.pose",
    robot_id: "patrolbot-01",
    sequence,
    timestamp: "2026-07-17T10:00:00.000Z",
    data: { x, y, yaw: 0, linear_velocity: 0.4, angular_velocity: 0 },
  };
}

const snapshot: SnapshotData = {
  connection: { state: "online", last_seen: "2026-07-17T10:00:00Z" },
  robot_status: { status: "ready", detail: "Ready." },
  system_health: { overall: "healthy", subsystems: [] },
  map_version: 2,
  pose: { x: 1, y: 2, yaw: 0.5, linear_velocity: 0, angular_velocity: 0 },
  battery: { voltage: 24.5, percentage: 80, charging: false },
  events: [{ id: 5, ts: "2026-07-17T10:00:00Z", severity: "info", title: "Hi", message: "m" }],
};

describe("telemetryStore", () => {
  beforeEach(() => {
    useTelemetryStore.setState({
      pose: null, trajectory: [], events: [], mapVersion: 0,
      status: { status: "offline", detail: "" },
      connection: { state: "offline" }, wsConnected: false, frameCount: 0,
      lastKnownPose: null, poseSetThisSession: false,
    });
  });

  it("hydrates from a snapshot", () => {
    useTelemetryStore.getState().handleFrame({
      version: 1, type: "server.snapshot", robot_id: "patrolbot-01",
      sequence: 1, timestamp: "2026-07-17T10:00:00Z", data: snapshot,
    });
    const state = useTelemetryStore.getState();
    expect(state.status.status).toBe("ready");
    expect(state.pose?.x).toBe(1);
    expect(state.mapVersion).toBe(2);
    expect(state.events).toHaveLength(1);
  });

  it("builds a trajectory from poses, skipping sub-5cm moves", () => {
    const store = useTelemetryStore.getState();
    store.handleFrame(poseFrame(0, 0));
    useTelemetryStore.getState().handleFrame(poseFrame(0.01, 0)); // skipped
    useTelemetryStore.getState().handleFrame(poseFrame(0.5, 0));
    expect(useTelemetryStore.getState().trajectory).toEqual([[0, 0], [0.5, 0]]);
  });

  it("caps the trajectory ring buffer", () => {
    for (let i = 0; i < 2100; i++) {
      useTelemetryStore.getState().handleFrame(poseFrame(i * 0.1, 0, i));
    }
    expect(useTelemetryStore.getState().trajectory.length).toBeLessThanOrEqual(2000);
  });

  it("prepends events and caps at 200", () => {
    for (let i = 1; i <= 210; i++) {
      useTelemetryStore.getState().handleFrame({
        version: 1, type: "event.append", robot_id: "patrolbot-01",
        sequence: i, timestamp: "2026-07-17T10:00:00Z",
        data: { id: i, ts: "2026-07-17T10:00:00Z", severity: "info", title: `e${i}`, message: "" },
      });
    }
    const events = useTelemetryStore.getState().events;
    expect(events).toHaveLength(200);
    expect(events[0].id).toBe(210);
  });

  it("marks robot offline when the dashboard socket drops", () => {
    useTelemetryStore.getState().handleFrame({
      version: 1, type: "state.connection", robot_id: "patrolbot-01",
      sequence: 1, timestamp: "2026-07-17T10:00:00Z",
      data: { state: "online", last_seen: null },
    });
    useTelemetryStore.getState().setWsConnected(false);
    const state = useTelemetryStore.getState();
    expect(state.connection.state).toBe("offline");
    expect(state.status.status).toBe("offline");
  });

  it("hydrates last-known pose and leaves the pose gate unset on snapshot", () => {
    useTelemetryStore.setState({ poseSetThisSession: true });
    useTelemetryStore.getState().handleFrame({
      version: 1, type: "server.snapshot", robot_id: "patrolbot-01",
      sequence: 1, timestamp: "2026-07-17T10:00:00Z",
      data: { ...snapshot, last_known_pose: { x: 4, y: 5, yaw: 0.2 } },
    });
    const state = useTelemetryStore.getState();
    expect(state.lastKnownPose).toEqual({ x: 4, y: 5, yaw: 0.2 });
    expect(state.poseSetThisSession).toBe(false);
  });

  it("clears the pose gate when the socket drops and on reconnect", () => {
    // A successful set-location satisfies the gate...
    useTelemetryStore.getState().markPoseSet();
    expect(useTelemetryStore.getState().poseSetThisSession).toBe(true);
    // ...but a dropped socket ends the session.
    useTelemetryStore.getState().setWsConnected(false);
    expect(useTelemetryStore.getState().poseSetThisSession).toBe(false);

    // Set again, then a robot reconnect (offline -> online) resets it too.
    useTelemetryStore.getState().markPoseSet();
    useTelemetryStore.getState().handleFrame({
      version: 1, type: "state.connection", robot_id: "patrolbot-01",
      sequence: 2, timestamp: "2026-07-17T10:00:00Z",
      data: { state: "online", last_seen: null },
    });
    expect(useTelemetryStore.getState().poseSetThisSession).toBe(false);
  });
});

describe("snapshot freshness", () => {
  it("backdates hydrated slices instead of timing them from arrival", () => {
    // A snapshot catches up a browser that just connected, and its values can
    // be arbitrarily old. Timing them from arrival showed days-old readings as
    // current for the first 15 seconds after loading the page.
    const store = useTelemetryStore.getState();
    store.handleFrame({
      version: 1,
      type: "server.snapshot",
      robot_id: "patrolbot-01",
      sequence: 1,
      timestamp: new Date().toISOString(),
      data: {
        connection: { state: "online", last_seen: null },
        robot_status: { status: "ready", detail: "" },
        system_health: { overall: "healthy", subsystems: [] },
        map_version: 1,
        base_state: { bumpers_valid: true } as never,
        events: [],
        slice_ages_s: { base_state: 270_000, pose: 270_000 },
      },
    } as never);

    const { baseStateAt, poseReceivedAt } = useTelemetryStore.getState();
    expect(baseStateAt).not.toBeNull();
    // 270 000 s ago, not "now".
    expect(performance.now() - (baseStateAt as number)).toBeGreaterThan(1e8);
    expect(performance.now() - poseReceivedAt).toBeGreaterThan(1e8);
  });

  it("treats a slice the server never received as never received", () => {
    useTelemetryStore.getState().handleFrame({
      version: 1,
      type: "server.snapshot",
      robot_id: "patrolbot-01",
      sequence: 2,
      timestamp: new Date().toISOString(),
      data: {
        connection: { state: "online", last_seen: null },
        robot_status: { status: "ready", detail: "" },
        system_health: { overall: "healthy", subsystems: [] },
        map_version: 1,
        events: [],
        slice_ages_s: { base_state: null },
      },
    } as never);
    expect(useTelemetryStore.getState().baseStateAt).toBeNull();
  });
});
