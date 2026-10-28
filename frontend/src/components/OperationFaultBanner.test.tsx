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
