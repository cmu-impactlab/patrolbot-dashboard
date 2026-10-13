import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
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
afterEach(cleanup);
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

it.each(["wait-navigation-idle-service-unavailable", "wait-navigation-idle-confirmation"])("explains recovery wait %s using fresh robot state", stage => {
  useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, localization_recovery_required: true, localization_recovery_stage: stage } });
  const view = render(<RobotStatusWidget />);
  expect(screen.getByRole("status").textContent).toContain("Location recovery cannot continue yet.");
  useTelemetryStore.setState({ baseStateAt: null });
  view.rerender(<RobotStatusWidget />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(sent).toEqual([]);
});
