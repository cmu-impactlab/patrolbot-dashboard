import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useTelemetrySocket } from "./useTelemetrySocket";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { BaseStateData } from "../types/protocol";
import type { SocketCallbacks } from "./client";

const socket = vi.hoisted(() => ({ callbacks: null as SocketCallbacks | null,
  send: vi.fn((_frame: string) => true), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => socket }));
vi.mock("./client", () => ({ telemetryUrl: () => "ws://test", DashboardSocket: class {
  constructor(_url: string, callbacks: SocketCallbacks) { socket.callbacks = callbacks; }
  connect() {} close() {} send(frame: string) { return socket.send(frame); }
} }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("never publishes a saved/default pose or destination on refresh and reconnect", () => {
  useTelemetryStore.setState({ lastKnownPose: { x: 0, y: 0, yaw: 0 }, poseSetThisSession: false });
  useCommandStore.setState({ active: null, lastAttempt: null, allowUnlocalized: true,
    stoppedGoal: { x: 9, y: 8 }, pickMode: "initialpose" });
  const view = renderHook(() => useTelemetrySocket());
  act(() => socket.callbacks!.onOpen());
  expect(useCommandStore.getState().allowUnlocalized).toBe(false);
  act(() => socket.callbacks!.onFrame({ version: 1, type: "server.snapshot", robot_id: "patrolbot-01",
    sequence: 1, timestamp: new Date().toISOString(), data: {
      connection: { state: "online" }, robot_status: { status: "ready", detail: "Robot state" },
      system_health: { overall: "healthy", subsystems: [] }, map_version: 1, events: [],
      last_known_pose: { x: 0, y: 0, yaw: 0 },
      base_state: { odom_epoch_valid: true, localization_recovery_required: true, localization_seed_stamp_ns: 0 } as BaseStateData,
      slice_ages_s: { base_state: 0 },
    } }));
  expect(useTelemetryStore.getState().poseSetThisSession).toBe(false);
  act(() => { socket.callbacks!.onClose(); socket.callbacks!.onOpen(); });
  expect(useCommandStore.getState().pickMode).toBe("none");
  expect(socket.send).not.toHaveBeenCalled();
  view.unmount();
  renderHook(() => useTelemetrySocket());
  act(() => socket.callbacks!.onOpen());
  expect(socket.send).not.toHaveBeenCalled();
});
