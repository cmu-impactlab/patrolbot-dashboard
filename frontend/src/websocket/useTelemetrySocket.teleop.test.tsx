import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useTelemetrySocket } from "./useTelemetrySocket";
import { useTeleopStore } from "../stores/teleopStore";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useAuthStore } from "../stores/authStore";
import type { SocketCallbacks } from "./client";
import type { AnyFrame } from "../types/protocol";

const socket = vi.hoisted(() => ({ callbacks: null as SocketCallbacks | null,
  send: vi.fn((_frame: string) => true), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => socket }));
vi.mock("./client", () => ({ telemetryUrl: () => "ws://isolated-test", DashboardSocket: class {
  constructor(_url: string, callbacks: SocketCallbacks) { socket.callbacks = callbacks; }
  connect() {} close() {} send(frame: string) { return socket.send(frame); }
} }));
const frames = () => socket.send.mock.calls.map(([frame]) => JSON.parse(frame));
function receive(type: "command.ack" | "command.result", data: unknown) {
  act(() => socket.callbacks!.onFrame({ version: 1, type, robot_id: "mock", sequence: 1, timestamp: new Date().toISOString(), data } as AnyFrame));
}
function start() {
  const view = renderHook(() => useTelemetrySocket());
  act(() => { socket.callbacks!.onOpen(); useTeleopStore.getState().arm(); useTeleopStore.getState().input(.2, .1, true); });
  return { view, id: useTeleopStore.getState().session!.id };
}
beforeEach(() => {
  vi.useFakeTimers(); socket.send.mockClear();
  useTeleopStore.setState({ phase: "idle", externalBusy: false, session: null, deadman: false, detail: null });
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastResult: null });
  useAuthStore.setState({ user: { id: 1, username: "test", display_name: "Test", role: "operator", auth_mode: "local" } });
  useTelemetryStore.setState({ wsConnected: false, robotId: "mock", connection: { state: "online" }, capabilities: ["web_teleop_v1"],
    status: { status: "ready", detail: "" }, baseStateAt: performance.now(), baseState: {
      session_generation: 1, link_connected: true, telemetry_age: 0, raw_odom_age_s: .02, hardware_state_valid: true,
      lidar_state: "READY", lidar_scan_age: 0, odom_epoch_valid: true, charge_state: "not_charging", motors_enabled: true,
      estop_pressed: false, fault_flags: 0, stall_value: 0, bumpers_front: false, bumpers_rear: false, bumpers_valid: true,
      dock_state_valid: true, dock_state: "CLEAR_CONFIRMED",
    } });
});
afterEach(() => { cleanup(); useTeleopStore.getState().end(); vi.useRealTimers(); });

it("routes the exact start ACK and both terminal results without confusing normal command state", () => {
  const { id } = start();
  act(() => vi.advanceTimersByTime(80));
  expect(frames().filter(frame => frame.type === "teleop.velocity")).toHaveLength(0);
  receive("command.ack", { command_id: id, accepted: true });
  expect(useCommandStore.getState().active).toBeNull();
  act(() => vi.advanceTimersByTime(80));
  expect(frames().at(-1).data).toMatchObject({ command_id: id, deadman: true, linear: .2, angular: .1 });
  act(() => useTeleopStore.getState().end());
  const stop = frames().at(-1);
  receive("command.result", { command_id: id, outcome: "canceled" });
  expect(useTeleopStore.getState().phase).toBe("stopping");
  receive("command.result", { command_id: stop.data.command_id, outcome: "succeeded" });
  expect(useTeleopStore.getState().phase).toBe("idle");
  expect(useCommandStore.getState().lastResult).toBeNull();
});
it("disconnect/reconnect never replays input, and ignores a stale start ACK", () => {
  const { id } = start();
  act(() => socket.callbacks!.onClose());
  act(() => socket.callbacks!.onOpen());
  receive("command.ack", { command_id: id, accepted: true });
  act(() => vi.advanceTimersByTime(160));
  const samples = frames().filter(frame => frame.type === "teleop.velocity");
  expect(samples.length).toBeGreaterThan(0);
  expect(samples.every(frame => frame.data.linear === 0 && frame.data.angular === 0 && frame.data.deadman === false)).toBe(true);
});
it("unmount sends zero and stop while the socket sender is still registered", () => {
  const { id, view } = start(); receive("command.ack", { command_id: id, accepted: true });
  act(() => vi.advanceTimersByTime(80));
  view.unmount();
  expect(frames().at(-2).data).toMatchObject({ command_id: id, linear: 0, angular: 0, deadman: false });
  expect(frames().at(-1).data.command).toBe("teleop_stop");
  const count = frames().length; act(() => vi.advanceTimersByTime(160)); expect(frames()).toHaveLength(count);
});
