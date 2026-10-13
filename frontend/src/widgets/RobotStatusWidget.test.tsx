import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RobotStatusWidget } from "./RobotStatusWidget";
import { useAuthStore } from "../stores/authStore";
import { registerCommandSender, useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { BaseStateData } from "../types/protocol";
let sent: string[];
beforeEach(() => {
  sent = [];
  registerCommandSender(frame => { sent.push(frame); return true; });
  useAuthStore.setState({ user: { id: 1, username: "operator", display_name: "Operator", role: "operator", auth_mode: "local" } });
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastResult: null, stoppedGoal: null });
  useTelemetryStore.setState({ wsConnected: true, connection: { state: "online", last_seen: null },
    capabilities: ["set_lidar_power"], baseStateAt: performance.now(), poseReceivedAt: performance.now(),
    baseState: { lidar_state: "OFF", lidar_power_supported: true, lidar_scan_age: -1, hardware_state_valid: true,
      link_connected: true, telemetry_age: .1, charge_state: "not_charging", fault_flags: 0 } as BaseStateData,
    pose: { x: 0, y: 0, yaw: 0, linear_velocity: 0, angular_velocity: 0, localized: false } });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });
describe("LiDAR power in Robot Status", () => {
  it("uses a labeled native button and sends an explicit target once", () => {
    render(<RobotStatusWidget />);
    const button = screen.getByRole("button", { name: "Turn LiDAR on" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(screen.getByText("Commanded off")).toBeTruthy();
    fireEvent.click(button);
    fireEvent.click(button);
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0]).data).toMatchObject({ command: "set_lidar_power", enabled: true });
    expect(screen.getByRole("button", { name: /Changing LiDAR/ }).getAttribute("disabled")).not.toBeNull();
  });
  it("explains uncommissioned controls and read-only access", () => {
    useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, lidar_power_supported: false } });
    render(<RobotStatusWidget />);
    expect((screen.getByRole("button", { name: "Turn LiDAR on" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/not commissioned/)).toBeTruthy();
  });
  it("disables power control for observers", () => {
    useAuthStore.setState({ user: { ...useAuthStore.getState().user!, role: "observer" } });
    render(<RobotStatusWidget />);
    expect((screen.getByRole("button", { name: "Turn LiDAR on" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/read-only/)).toBeTruthy();
  });
  it("surfaces robot refusal text", () => {
    useCommandStore.setState({ lastResult: { commandId: "power", command: "set_lidar_power", outcome: "rejected", detail: "Power wiring not commissioned", at: Date.now() } });
    render(<RobotStatusWidget />);
    expect(screen.getByRole("status").textContent).toContain("Power wiring not commissioned");
  });
});


it.each(["OFF", "READY", "FAULT"] as const)("admits %s power requests without a map pose", (state) => {
  useTelemetryStore.setState({ pose: null, poseReceivedAt: undefined,
    baseState: { ...useTelemetryStore.getState().baseState!, lidar_state: state } });
  render(<RobotStatusWidget />);
  const button = screen.getByRole("button", { name: state === "READY" ? "Turn LiDAR off" : "Turn LiDAR on" }) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(JSON.parse(sent[0]).data).toMatchObject({ command: "set_lidar_power", enabled: state !== "READY" });
});

describe("localization uncertainty in Robot Status", () => {
  function estimate(age = 0.2) {
    useTelemetryStore.setState({ poseReceivedAt: performance.now(), pose: {
      frame_id: "map", x: 0, y: 0, yaw: 0, linear_velocity: 0, angular_velocity: 0,
      localized: false, covariance_trace: 999, planar_covariance: [0.2238, 0.2453, 0.0683],
      covariance_limit: 0.25, localization_age_s: age, localization_max_age_s: 30,
    } });
  }

  it("shows the reviewed spread and signed margin without declaring localization ready", () => {
    estimate();
    render(<RobotStatusWidget />);
    expect(screen.getByText("Largest map-axis spread (1σ)")).toBeTruthy();
    expect(screen.getByText("0.495 m")).toBeTruthy();
    expect(screen.getByText("15.0°")).toBeTruthy();
    expect(screen.getByText(/XY \+1.9%.*heading \+72.7%/)).toBeTruthy();
    expect(screen.getByText("Uncertain")).toBeTruthy();
    expect(screen.getByText(/not measured pose accuracy/)).toBeTruthy();
    expect(useTelemetryStore.getState().pose?.localized).toBe(false);
    expect(sent).toEqual([]);
  });

  it("replaces the numerical estimate when time passes without an AMCL refresh", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance"] });
    estimate(29.5);
    render(<RobotStatusWidget />);
    expect(screen.getByText("0.495 m")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText("Not refreshed recently")).toBeTruthy();
    expect(screen.queryByText("0.495 m")).toBeNull();
    expect(screen.queryByText(/XY \+1.9%/)).toBeNull();
    expect(sent).toEqual([]);
  });

  it.each([0, 0.2])("withdraws Location known when the filter refresh expires at speed %s", (speed) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance"] });
    estimate(29.5);
    useTelemetryStore.setState({ pose: { ...useTelemetryStore.getState().pose!, localized: true, linear_velocity: speed } });
    render(<RobotStatusWidget />);
    const location = screen.getByText("Location known").parentElement!;
    expect(location.textContent).toContain("Yes");
    act(() => vi.advanceTimersByTime(1000));
    expect(location.textContent).toContain("Unknown");
    expect(screen.getByText("Not refreshed recently")).toBeTruthy();
    expect(sent).toEqual([]);
  });

  it.each([
    { planar_covariance: undefined, detail: "Unavailable: covariance not reported" },
    { planar_covariance: [null, 0.1, 0.1], detail: "Unavailable: invalid covariance" },
    { planar_covariance: [0.1, 0.1, -1e-9], detail: "Unavailable: degenerate covariance" },
  ])("explains $detail and omits numerical estimates", ({ planar_covariance, detail }) => {
    estimate();
    useTelemetryStore.setState({ pose: { ...useTelemetryStore.getState().pose!,
      planar_covariance: planar_covariance as [number | null, number | null, number | null] | undefined } });
    render(<RobotStatusWidget />);
    expect(screen.getByText(detail)).toBeTruthy();
    expect(screen.queryByText("Largest map-axis spread (1σ)")).toBeNull();
    expect(screen.queryByText("Dashboard variance margin")).toBeNull();
  });
});

it.each(["wait-navigation-idle-service-unavailable", "wait-navigation-idle-confirmation"])("explains recovery wait %s using fresh robot state", stage => {
  useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, localization_recovery_required: true, localization_recovery_stage: stage } });
  const view = render(<RobotStatusWidget />);
  expect(screen.getByRole("status").textContent).toContain("Location recovery cannot continue yet.");
  useTelemetryStore.setState({ baseStateAt: null });
  view.rerender(<RobotStatusWidget />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(sent).toEqual([]);
});
