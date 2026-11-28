import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OperationFaultBanner } from "./OperationFaultBanner";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useAuthStore } from "../stores/authStore";
import { registerCommandSender, useCommandStore } from "../stores/commandStore";
import { useTeleopStore } from "../stores/teleopStore";
import type { BaseStateData } from "../types/protocol";

let frames: { data: { command: string } }[];
beforeEach(() => {
  vi.useFakeTimers();
  frames = [];
  registerCommandSender(frame => { frames.push(JSON.parse(frame)); return true; });
  useAuthStore.setState({ user: { id: 1, username: "operator", display_name: "Operator", role: "operator", auth_mode: "local" } });
  useCommandStore.setState({ active: null, lastResult: null, lidarPending: null, lidarStateAfter: null, lidarRecoveryRequired: false });
  useTeleopStore.setState({ phase: "idle", externalBusy: false });
  useTelemetryStore.setState({ wsConnected: true, robotId: "robot", connection: { state: "online" },
    capabilities: ["dock_reset"], pose: null, poseReceivedAt: 0,
    baseStateAt: performance.now(), baseState: {
      session_generation: 1, link_connected: true, hardware_state_valid: true,
      telemetry_age: .01, dock_state_valid: true, dock_state: "CLEAR_CONFIRMED",
      operation_fault_reason: "turn_path_blocked", motors_enabled: true,
    } as BaseStateData,
    motionAt: performance.now(), motionTransportAgeS: 0, motion: {
      source: "raw_odom", session_generation: 1, odom_epoch: "epoch", odom_epoch_generation: 1,
      linear_velocity: 0, angular_velocity: 0, sample_age_s: .01, receipt_age_s: .01,
    },
  });
});
afterEach(() => { cleanup(); registerCommandSender(null); vi.useRealTimers(); });

it("requires confirmation, sends only recovery without AMCL, and waits for telemetry to clear the banner", () => {
  render(<OperationFaultBanner />);
  expect(frames).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  expect(frames).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm recovery" }));
  expect(frames.map(frame => frame.data.command)).toEqual(["dock_reset"]);
  expect(screen.getByText("Robot operation fault")).toBeTruthy();
  act(() => useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, operation_fault_reason: "" } }));
  expect(screen.queryByText("Robot operation fault")).toBeNull();
});

it("disables recovery when raw speed freezes even if base state remains fresh", () => {
  render(<OperationFaultBanner />);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  expect((screen.getByRole("button", { name: "Confirm recovery" }) as HTMLButtonElement).disabled).toBe(false);
  act(() => vi.advanceTimersByTime(1750));
  expect((screen.getByRole("button", { name: "Confirm recovery" }) as HTMLButtonElement).disabled).toBe(true);
  expect(frames).toEqual([]);
});

it("does not bypass an unknown outcome", () => {
  useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, operation_outcome_unknown: true } });
  render(<OperationFaultBanner />);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  expect((screen.getByRole("button", { name: "Confirm recovery" }) as HTMLButtonElement).disabled).toBe(true);
  expect(frames).toEqual([]);
});

it("keeps the rest of the screen accessible and allows closing details without clearing the fault", () => {
  const outside = vi.fn();
  render(<><OperationFaultBanner /><button onClick={outside}>Other dashboard control</button></>);
  const toggle = screen.getByRole("button", { name: "Details / recovery" });
  fireEvent.click(toggle);
  expect(screen.getByRole("region", { name: "Recover operation fault" })).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Other dashboard control" }));
  expect(outside).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Close details" }));
  expect(document.activeElement).toBe(toggle);
  expect(screen.queryByRole("region", { name: "Recover operation fault" })).toBeNull();
  expect(screen.getByText("Robot operation fault")).toBeTruthy();
  expect(frames).toEqual([]);
});

it("does not carry an open recovery panel over to another fault", () => {
  render(<OperationFaultBanner />);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  act(() => useCommandStore.setState({ lastResult: {
    command: "dock_reset", commandId: "other-recovery", outcome: "succeeded",
    at: Date.now(), detail: "Unrelated result",
  } }));
  expect(screen.queryByText("Recovery succeeded: Unrelated result")).toBeNull();
  act(() => useTelemetryStore.setState({
    baseState: { ...useTelemetryStore.getState().baseState!, operation_fault_request_id: "a-new-operation" },
  }));
  expect(screen.queryByRole("region", { name: "Recover operation fault" })).toBeNull();
  expect(screen.getByText("Robot operation fault")).toBeTruthy();
  expect(frames).toEqual([]);
});

it.each(["succeeded", "failed", "rejected", "timeout"] as const)("shows only the current recovery's %s result and still waits for telemetry", outcome => {
  render(<OperationFaultBanner />);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm recovery" }));
  const commandId = useCommandStore.getState().active!.commandId;
  act(() => useCommandStore.setState({ lastResult: { commandId: "other", command: "dock_reset", outcome, at: Date.now(), detail: "Unrelated" } }));
  expect(screen.queryByText(/Recovery .*Unrelated/)).toBeNull();
  act(() => useCommandStore.getState().handleResult({ command_id: commandId, outcome, detail: "Current attempt" }));
  expect(screen.getByText(`Recovery ${outcome}: Current attempt`)).toBeTruthy();
  expect(screen.getByText("Robot operation fault")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close details" }));
  expect(screen.getByText("Robot operation fault")).toBeTruthy();
  act(() => useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, operation_fault_reason: "" } }));
  expect(screen.queryByText("Robot operation fault")).toBeNull();
});

it("shows a synchronous transport failure for this attempt", () => {
  registerCommandSender(() => false);
  render(<OperationFaultBanner />);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm recovery" }));
  expect(screen.getByText("Recovery failed: Not connected to the dashboard server.")).toBeTruthy();
  expect(screen.getByText("Robot operation fault")).toBeTruthy();
});

it.each(["robot", "session", "reason", "cleared"])("discards recovery details and old results after %s changes", change => {
  render(<OperationFaultBanner />);
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm recovery" }));
  act(() => useCommandStore.getState().handleResult({ command_id: useCommandStore.getState().active!.commandId, outcome: "succeeded", detail: "Old attempt" }));
  act(() => {
    const base = useTelemetryStore.getState().baseState!;
    if (change === "robot") useTelemetryStore.setState({ robotId: "another-robot" });
    if (change === "session") useTelemetryStore.setState({ baseState: { ...base, session_generation: 2 } });
    if (change === "reason") useTelemetryStore.setState({ baseState: { ...base, operation_fault_reason: "rear_bumper_pressed" } });
    if (change === "cleared") useTelemetryStore.setState({ baseState: { ...base, operation_fault_reason: "" } });
  });
  expect(screen.queryByRole("region", { name: "Recover operation fault" })).toBeNull();
  if (change === "cleared") act(() => useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, operation_fault_reason: "turn_path_blocked" } }));
  fireEvent.click(screen.getByRole("button", { name: "Details / recovery" }));
  expect(screen.queryByText(/Recovery succeeded: Old attempt/)).toBeNull();
});
