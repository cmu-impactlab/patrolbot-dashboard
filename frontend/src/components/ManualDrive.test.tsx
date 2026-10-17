import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ManualDrive } from "./ManualDrive";
import { canCommand, useAuthStore } from "../stores/authStore";
import { registerCommandSender, useCommandStore } from "../stores/commandStore";
import { useTeleopStore } from "../stores/teleopStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { BaseStateData, TeleopVelocityData } from "../types/protocol";

const base: BaseStateData = { session_generation: 1, link_connected: true, telemetry_age: 0,
  hardware_state_valid: true, charge_state: "not_charging", motors_enabled: true,
  estop_pressed: false, fault_flags: 0, stall_value: 0, bumpers_front: false, bumpers_rear: false,
  lidar_state: "READY", lidar_scan_age: 0, raw_odom_age_s: .02, odom_epoch_valid: true,
  bumpers_valid: true, dock_state_valid: true, dock_state: "CLEAR_CONFIRMED" };
function captureCommandSender() {
  const frames: { type: string; data: Record<string, unknown> }[] = [];
  registerCommandSender(frame => { frames.push(JSON.parse(frame)); return true; });
  return frames;
}
let frames: ReturnType<typeof captureCommandSender>;
const velocity = () => frames.filter(frame => frame.type === "teleop.velocity").map(frame => frame.data as unknown as TeleopVelocityData);
const requests = (command: string) => frames.filter(frame => frame.type === "command.request" && frame.data.command === command);
const panel = () => screen.getByRole("region", { name: "Manual drive keyboard panel" });
const arm = () => fireEvent.click(screen.getByRole("button", { name: "Arm manual drive" }));
function ack() {
  act(() => useTeleopStore.getState().handleAck({ command_id: String(requests("teleop_start").at(-1)!.data.command_id), accepted: true }));
}
function key(key: string, repeat = false, target = panel()) { fireEvent.keyDown(target, { key, repeat }); }
function keyboardStart() { arm(); key("w"); key(" "); }
function tick(ms = 80) { act(() => vi.advanceTimersByTime(ms)); }
function healthy() { useTelemetryStore.setState({ baseState: { ...base }, baseStateAt: performance.now() }); }

describe("manual drive isolated software input", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("PointerEvent", MouseEvent);
    useTeleopStore.getState().end();
    useTeleopStore.setState({ phase: "idle", externalBusy: false, session: null, detail: null, deadman: false, linear: 0, angular: 0 });
    useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null,
      stoppedGoal: null, pickMode: "none", allowUnlocalized: false });
    useAuthStore.setState({ user: { id: 1, username: "operator", display_name: "Operator", role: "operator", auth_mode: "local" } });
    useTelemetryStore.setState({ wsConnected: true, robotId: "mock", connection: { state: "online" },
      capabilities: ["web_teleop_v1"], status: { status: "ready", detail: "" }, pose: null, poseSetThisSession: false });
    healthy();
    frames = captureCommandSender();
  });
  afterEach(() => { cleanup(); useTeleopStore.getState().end(); registerCommandSender(null); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("opening the page never arms, enables motors or sends a command", () => {
    render(<ManualDrive />); tick();
    expect(frames).toEqual([]);
    expect(screen.getByText("Not armed")).toBeTruthy();
  });
  it("waits for robot acceptance, then samples bounded keyboard input with the exact start ID", () => {
    render(<ManualDrive />); keyboardStart(); tick(160);
    expect(requests("teleop_start")).toHaveLength(1);
    expect(velocity()).toEqual([]);
    ack(); key("a"); tick(160);
    expect(velocity()).toHaveLength(2);
    expect(velocity()[0]).toMatchObject({ command_id: requests("teleop_start")[0].data.command_id, seq: 0, linear: .3, angular: .7, deadman: true });
    expect(velocity()[1].seq).toBe(1);
  });
  it("permits healthy raw hardware with no AMCL, seed, map or pose confirmation", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    expect(velocity()[0].linear).toBe(.3);
  });
  it("waits through a one-second robot quiescence handshake without positive output", () => {
    render(<ManualDrive />); keyboardStart();
    for (let i = 0; i < 10; i++) { act(() => healthy()); tick(100); }
    expect(velocity()).toHaveLength(0);
    expect(useTeleopStore.getState().phase).toBe("pending");
    ack(); tick(); expect(velocity()[0].linear).toBe(.3);
  });
  it("sends latest pointer position only, then immediate strict zero and exactly one stop on release", () => {
    render(<ManualDrive />); arm();
    const stick = screen.getByRole("group", { name: "Hold joystick to drive" });
    vi.spyOn(stick, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 160, height: 160 } as DOMRect);
    fireEvent.pointerDown(stick, { button: 0, clientX: 80, clientY: 0 });
    fireEvent.pointerMove(stick, { clientX: 80, clientY: 40 });
    expect(velocity()).toHaveLength(0); ack(); tick();
    expect(velocity()[0]).toMatchObject({ linear: .15, angular: 0, deadman: true });
    fireEvent.pointerUp(stick); fireEvent.lostPointerCapture(stick);
    expect(velocity().at(-1)).toMatchObject({ linear: 0, angular: 0, deadman: false });
    expect(requests("teleop_stop")).toHaveLength(1);
    tick(160); expect(velocity()).toHaveLength(2);
  });
  it("zeroes immediately on Space release and does not clear ownership from a stop request", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    fireEvent.keyUp(panel(), { key: " " });
    expect(velocity().at(-1)).toMatchObject({ linear: 0, angular: 0, deadman: false });
    expect(useTeleopStore.getState().phase).toBe("stopping");
    const stopId = String(requests("teleop_stop")[0].data.command_id);
    act(() => useTeleopStore.getState().handleAck({ command_id: stopId, accepted: true }));
    expect(useTeleopStore.getState().phase).toBe("stopping");
    act(() => useTeleopStore.getState().handleResult({ command_id: stopId, outcome: "succeeded" }));
    expect(useTeleopStore.getState().phase).toBe("stopping");
    act(() => useTeleopStore.getState().handleResult({ command_id: String(requests("teleop_start")[0].data.command_id), outcome: "canceled" }));
    expect(useTeleopStore.getState().phase).toBe("idle");
  });
  it.each(["blur", "visibility", "unmount", "pointercancel", "lostcapture"])("ends input on %s", event => {
    const view = render(<ManualDrive />); keyboardStart(); ack(); tick();
    if (event === "blur") fireEvent.blur(window);
    if (event === "visibility") { vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden"); fireEvent(document, new Event("visibilitychange")); }
    if (event === "unmount") view.unmount();
    if (event === "pointercancel") fireEvent.pointerCancel(screen.getByRole("group"));
    if (event === "lostcapture") fireEvent.lostPointerCapture(screen.getByRole("group"));
    expect(velocity().at(-1)).toMatchObject({ linear: 0, angular: 0, deadman: false });
    expect(requests("teleop_stop")).toHaveLength(1);
    tick(160); expect(velocity()).toHaveLength(2);
    vi.restoreAllMocks();
  });
  it("ignores repeated held keys after blur and does not resume on focus", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick(); fireEvent.blur(window);
    const stopId = String(requests("teleop_stop")[0].data.command_id);
    act(() => useTeleopStore.getState().handleResult({ command_id: stopId, outcome: "succeeded" }));
    act(() => useTeleopStore.getState().handleResult({ command_id: String(requests("teleop_start")[0].data.command_id), outcome: "canceled" }));
    arm(); fireEvent.focus(panel()); key("w", true); key(" ", true); tick();
    expect(requests("teleop_start")).toHaveLength(1);
    expect(velocity().at(-1)?.deadman).toBe(false);
  });
  it("does not drive when typing in a text field", () => {
    render(<ManualDrive />); arm();
    const input = document.createElement("input"); panel().append(input);
    key("w", false, input); key(" ", false, input); tick();
    expect(requests("teleop_start")).toHaveLength(0);
  });
  it("cannot resume old input after connection loss/reconnect", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    act(() => useTelemetryStore.setState({ wsConnected: false }));
    act(() => useTelemetryStore.setState({ wsConnected: true }));
    key(" ", true); tick(160);
    expect(velocity().at(-1)?.deadman).toBe(false);
    expect(requests("teleop_start")).toHaveLength(1);
  });
  it("a late accepted start after release is closed and never samples positive input", () => {
    render(<ManualDrive />); keyboardStart(); fireEvent.keyUp(panel(), { key: " " });
    ack(); tick(160); ack();
    expect(velocity().every(frame => !frame.deadman && frame.linear === 0 && frame.angular === 0)).toBe(true);
    expect(requests("teleop_stop")).toHaveLength(2); // release + late acceptance closure, no duplicate ACK spam.
  });
  it("retains pending start ownership if the stop result arrives before its late acceptance", () => {
    render(<ManualDrive />); keyboardStart(); fireEvent.keyUp(panel(), { key: " " });
    act(() => useTeleopStore.getState().handleResult({ command_id: String(requests("teleop_stop")[0].data.command_id), outcome: "succeeded" }));
    expect(useTeleopStore.getState().phase).toBe("stopping");
    ack();
    act(() => useTeleopStore.getState().handleResult({ command_id: String(requests("teleop_start")[0].data.command_id), outcome: "canceled" }));
    expect(useTeleopStore.getState().phase).toBe("stopping");
    act(() => useTeleopStore.getState().handleResult({ command_id: String(requests("teleop_stop")[1].data.command_id), outcome: "succeeded" }));
    expect(useTeleopStore.getState().phase).toBe("idle");
    expect(velocity().every(frame => !frame.deadman)).toBe(true);
  });
  it("a rejected start requires fresh arming and preserves the server's reason", () => {
    render(<ManualDrive />); keyboardStart();
    act(() => useTeleopStore.getState().handleAck({ command_id: String(requests("teleop_start")[0].data.command_id), accepted: false, reason: "Another operator is in control of the robot." }));
    key(" ", true); tick();
    expect(screen.getByText("Another operator is in control of the robot.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Take over" })).toBeTruthy();
    expect(velocity().every(frame => !frame.deadman)).toBe(true);
    expect(requests("teleop_stop")).toHaveLength(0); // A refusal must not stop the other operator's session.
    fireEvent.keyUp(panel(), { key: " " }); fireEvent.keyUp(panel(), { key: "w" });
    fireEvent.click(screen.getByRole("button", { name: "Take over" }));
    expect(requests("teleop_start")).toHaveLength(1); // takeover still requires a new hold.
    key("a"); key(" ");
    expect(requests("teleop_start")[1].data.takeover).toBe(true);
  });
  it("F710 preemption ends input and never resumes from held keys", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    act(() => useTeleopStore.getState().handleResult({ command_id: String(requests("teleop_start")[0].data.command_id), outcome: "canceled", detail: "F710 preempted manual drive" }));
    key(" ", true); tick();
    expect(screen.getByText("F710 preempted manual drive")).toBeTruthy();
    expect(velocity().at(-1)?.deadman).toBe(false);
  });
  it("keeps unknown timeout ownership blocked until a terminal result", () => {
    render(<ManualDrive />); keyboardStart();
    const commandId = String(requests("teleop_start")[0].data.command_id);
    act(() => useTeleopStore.getState().handleResult({ command_id: commandId, outcome: "timeout" }));
    expect(useTeleopStore.getState().phase).toBe("stopping");
    ack(); tick(); expect(velocity().every(frame => !frame.deadman)).toBe(true);
    act(() => useTeleopStore.getState().handleResult({ command_id: commandId, outcome: "canceled" }));
    expect(useTeleopStore.getState().phase).toBe("stopping");
    for (const stop of requests("teleop_stop")) act(() => useTeleopStore.getState().handleResult({ command_id: String(stop.data.command_id), outcome: "succeeded" }));
    expect(useTeleopStore.getState().phase).toBe("idle");
  });
  it("ignores another tab's ACK and never binds it to this pending start", () => {
    render(<ManualDrive />); keyboardStart();
    act(() => expect(useTeleopStore.getState().handleAck({ command_id: "another-tab-start", accepted: true })).toBe(false));
    tick(); expect(velocity()).toHaveLength(0);
    ack(); tick(); expect(velocity()[0].command_id).toBe(requests("teleop_start")[0].data.command_id);
  });
  it("ends held keyboard input when focus moves to an internal control", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    fireEvent.blur(panel(), { relatedTarget: screen.getByRole("button", { name: "End manual input" }) });
    expect(velocity().at(-1)?.deadman).toBe(false);
  });
  it.each([
    ["motors off", { motors_enabled: false }], ["EStop", { estop_pressed: true }],
    ["fault", { fault_flags: 1 }], ["invalid raw state", { hardware_state_valid: false }],
    ["SBC link down", { link_connected: false }], ["stale raw state", { telemetry_age: 1 }],
    ["LiDAR off", { lidar_state: "OFF" }], ["on dock", { dock_state: "DOCKED_CONFIRMED" }],
    ["missing raw odom", { raw_odom_age_s: undefined }], ["stale raw odom", { raw_odom_age_s: .6 }],
    ["invalid raw odom", { raw_odom_age_s: NaN }], ["negative raw odom", { raw_odom_age_s: -1 }],
    ["invalid origin", { odom_epoch_valid: false }], ["unknown bumper validity", { bumpers_valid: undefined }],
    ["front bumper", { bumpers_front: true }], ["rear bumper", { bumpers_rear: true }], ["unconfirmed dock clearance", { dock_state_valid: false }],
  ] as [string, Partial<BaseStateData>][])("blocks %s before a start and terminates active input if it appears", (_name, patch) => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    act(() => useTelemetryStore.setState({ baseState: { ...base, ...patch } }));
    expect(velocity().at(-1)?.deadman).toBe(false);
    const before = requests("teleop_start").length;
    act(() => useTeleopStore.getState().arm()); tick();
    expect(requests("teleop_start")).toHaveLength(before);
  });
  it("stale receipt age terminates active input even when heartbeat/connection are online", () => {
    render(<ManualDrive />); keyboardStart(); ack(); tick(560);
    expect(velocity().at(-1)?.deadman).toBe(false);
    expect(requests("teleop_stop")).toHaveLength(1);
  });
  it.each(["observer", "logout", "capability", "other action", "robot generation"])("terminates on %s", change => {
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    act(() => {
      if (change === "observer") useAuthStore.setState({ user: { ...useAuthStore.getState().user!, role: "observer" } });
      if (change === "logout") useAuthStore.setState({ user: null });
      if (change === "capability") useTelemetryStore.setState({ capabilities: [] });
      if (change === "robot generation") useTelemetryStore.setState({ baseState: { ...base, session_generation: 2 } });
      if (change === "other action") useCommandStore.setState({ active: { commandId: "nav", command: "navigate_to_pose", phase: "running", stage: null, distanceRemaining: null } });
    });
    expect(velocity().at(-1)?.deadman).toBe(false);
    expect(requests("teleop_stop")).toHaveLength(1);
  });
  it("displays missing-capability reason and keeps observer controls disabled", () => {
    useTelemetryStore.setState({ capabilities: [] }); render(<ManualDrive />);
    expect(screen.getByText(/Update the robot bridge first/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Arm manual drive" }) as HTMLButtonElement).disabled).toBe(true);
    act(() => useAuthStore.setState({ user: { ...useAuthStore.getState().user!, role: "observer" } }));
    expect(canCommand(useAuthStore.getState().user)).toBe(false);
    expect(screen.getByText(/An operator account is required/)).toBeTruthy();
  });
  it("clears paused goals/overrides/map picks on arm and global Stop always ends manual input", () => {
    useCommandStore.setState({ stoppedGoal: { x: 1, y: 2 }, allowUnlocalized: true, pickMode: "goal" });
    render(<ManualDrive />); keyboardStart(); ack(); tick();
    expect(useCommandStore.getState()).toMatchObject({ stoppedGoal: null, allowUnlocalized: false, pickMode: "none" });
    act(() => useCommandStore.getState().stop());
    expect(velocity().at(-1)?.deadman).toBe(false);
    expect(requests("teleop_stop")).toHaveLength(1);
    expect(requests("stop")).toHaveLength(0);
  });
  it("bounds raw input and never resets seq for an open ID", () => {
    render(<ManualDrive />); arm();
    act(() => useTeleopStore.getState().input(99, -99, true)); ack(); tick();
    expect(velocity()[0]).toMatchObject({ linear: .3, angular: -.7, seq: 0 });
    act(() => useTeleopStore.getState().input(.1, .2, true)); tick();
    act(() => useTeleopStore.getState().end());
    expect(velocity().map(frame => frame.seq)).toEqual([0, 1, 2]);
  });
  it("blocks a second tab's start but permits an explicit authorized stop without a Resume offer", () => {
    useTeleopStore.setState({ externalBusy: true });
    useTelemetryStore.setState({ path: { points: [], goal: { x: 3, y: 4 } } });
    render(<ManualDrive />);
    expect(screen.getByText(/Another tab is using manual drive/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Arm manual drive" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "End manual input" }));
    expect(requests("stop")).toHaveLength(1);
    expect(useCommandStore.getState().stoppedGoal).toBeNull();
    expect(useTeleopStore.getState().externalBusy).toBe(true); // A request is not owner confirmation.
  });
});
