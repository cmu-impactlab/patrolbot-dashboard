import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutonomousSpeedControl } from "../components/AutonomousSpeedControl";
import { useAutonomousSpeedStore } from "../stores/autonomousSpeedStore";
import { useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { AUTONOMOUS_SPEED_CAPABILITY, AUTONOMOUS_SPEED_RECEIPT, AUTONOMOUS_SPEED_REQUEST } from "../types/protocol";
import type { AnyFrame } from "../types/protocol";
import type { SocketCallbacks } from "./client";
import { useTelemetrySocket } from "./useTelemetrySocket";

const fake = vi.hoisted(() => ({ callbacks: null as SocketCallbacks | null,
  send: vi.fn((_frame: string) => true), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => fake }));
vi.mock("./client", () => ({ telemetryUrl: () => "ws://isolated-test", DashboardSocket: class {
  constructor(_url: string, callbacks: SocketCallbacks) { fake.callbacks = callbacks; }
  connect() {} close() {} send(frame: string) { return fake.send(frame); }
} }));

const session = "9e5fced6-62ed-424f-8563-55e2e17a2964";
const operator = { id: 1, username: "op", display_name: "Operator", role: "operator", auth_mode: "local" as const };
const sendButton = () => screen.getByRole("button", { name: "Request autonomous caps" });
const status = () => screen.getByRole("status").textContent;
const request = () => JSON.parse(fake.send.mock.calls.at(-1)![0]);

function TestControl() {
  useTelemetrySocket(useAutonomousSpeedStore.getInitialState());
  return <AutonomousSpeedControl />;
}
function receive(type: AnyFrame["type"], data: unknown, robot_id = "fake-robot") {
  act(() => fake.callbacks!.onFrame({ version: 1, type, robot_id, sequence: 1, timestamp: new Date().toISOString(), data } as AnyFrame));
}
function claim(sessionId = session) {
  receive("state.capabilities", {
    capabilities: [AUTONOMOUS_SPEED_CAPABILITY], autonomous_speed_caps_session: sessionId,
  });
}
function open() {
  render(<TestControl />);
  act(() => fake.callbacks!.onOpen());
  receive("state.connection", { state: "online" });
}

beforeEach(() => {
  vi.useFakeTimers();
  fake.send.mockReset().mockReturnValue(true);
  useAutonomousSpeedStore.setState({ result: { phase: "idle" } });
  useAuthStore.setState({ user: operator });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("does not render the picker or send a draft without the distinct bound capability", () => {
  open();
  receive("state.capabilities", { capabilities: ["robot_tuning_v1", "web_teleop_v1", AUTONOMOUS_SPEED_CAPABILITY] });
  expect(screen.queryByRole("group", { name: "Autonomous speed" })).toBeNull();
  expect(fake.send).not.toHaveBeenCalled();
});

it("observers can see profiles but cannot edit even a draft or send a command", () => {
  useAuthStore.setState({ user: { ...operator, role: "observer" } });
  open();
  claim();
  expect(screen.getByRole("radio", { name: "Fast" })).toHaveProperty("checked", true);
  const custom = screen.getByRole("radio", { name: "Custom" });
  expect(custom.matches(":disabled")).toBe(true);
  fireEvent.click(custom);
  fireEvent.click(sendButton());
  expect(screen.queryByLabelText("Linear speed (m/s)")).toBeNull();
  expect(fake.send).not.toHaveBeenCalled();
});

it("sends an explicit bounded request and displays only the exact current robot readback", () => {
  open();
  claim();
  expect(screen.getByRole("radio", { name: "Really Fast" }).matches(":disabled")).toBe(true);
  expect(fake.send).not.toHaveBeenCalled();
  fireEvent.click(sendButton());
  const sent = request();
  expect(sent).toMatchObject({ type: AUTONOMOUS_SPEED_REQUEST, robot_id: "fake-robot",
    data: { session_id: session, selection: { profile: "Fast", linear_m_s: .22, angular_rad_s: .70 } } });
  expect(sent.data).not.toHaveProperty("operator_authorized");
  expect(status()).toContain("Waiting");
  receive("command.ack", { command_id: sent.data.command_id, accepted: true });
  receive(AUTONOMOUS_SPEED_RECEIPT, {
    command_id: sent.data.command_id, outcome: "applied", readback: sent.data.selection,
  }, "other-robot");
  expect(status()).toContain("Waiting");
  receive(AUTONOMOUS_SPEED_RECEIPT, {
    command_id: sent.data.command_id, outcome: "applied", readback: sent.data.selection,
  });
  expect(status()).toContain("Robot confirmed this request: Applied Fast");
  expect(useCommandStore.getState().active).toBeNull();
  act(() => vi.advanceTimersByTime(5_001));
  expect(status()).toContain("unknown");
});

it.each(["mismatch", "missing", "unknown", "timeout", "send error", "stale", "disconnect", "replacement"])(
  "%s leaves outcome unknown, and late readback never restores applied",
  (condition) => {
    open();
    claim();
    if (condition === "send error") fake.send.mockImplementationOnce(() => { throw Error("disconnected"); });
    fireEvent.click(sendButton());
    const sent = request();
    const reply = { command_id: sent.data.command_id, outcome: "applied", readback: sent.data.selection };
    if (condition === "mismatch") receive(AUTONOMOUS_SPEED_RECEIPT, { ...reply, readback: { ...reply.readback, linear_m_s: .12 } });
    if (condition === "missing") receive(AUTONOMOUS_SPEED_RECEIPT, { ...reply, readback: undefined });
    if (condition === "unknown") receive(AUTONOMOUS_SPEED_RECEIPT, { ...reply, outcome: "unknown" });
    if (condition === "timeout") act(() => vi.advanceTimersByTime(6_001));
    if (condition === "stale") receive("state.connection", { state: "stale" });
    if (condition === "disconnect") act(() => fake.callbacks!.onClose());
    if (condition === "replacement") claim("4d2218f0-e06c-4684-ae46-0ef0278af210");
    expect(status()).toContain("unknown");
    receive(AUTONOMOUS_SPEED_RECEIPT, reply);
    expect(status()).toContain("unknown");
    expect(fake.send).toHaveBeenCalledTimes(1);
  },
);
