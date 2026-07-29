/**
 * The dock control is one button whose label follows the robot's state:
 * Undock while it is on the charger, Dock & Charge when it is not.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NavControlsWidget } from "./NavControlsWidget";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { BaseStateData, PoseData } from "../types/protocol";

const CAPS = ["dock", "undock"];

const PARKED: PoseData = {
  x: 1, y: 1, yaw: 0, linear_velocity: 0, angular_velocity: 0, localized: true,
};

function baseState(overrides: Partial<BaseStateData> = {}): BaseStateData {
  return {
    session_generation: 1,
    link_connected: true,
    telemetry_age: 0.1,
    hardware_state_valid: true,
    charge_state: "charging",
    motors_enabled: false,
    estop_pressed: false,
    fault_flags: 0,
    stall_value: 0,
    bumpers_front: false,
    bumpers_rear: false,
    ...overrides,
  };
}

function setState(base: BaseStateData | null, pose: PoseData | null = PARKED,
                  capabilities: string[] = CAPS) {
  useTelemetryStore.setState({
    connection: { state: "online", last_seen: null },
    baseState: base,
    pose,
    capabilities,
    poseSetThisSession: true,
  });
}

function dockButton(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: /Undock|Undocking|Dock & Charge/,
  }) as HTMLButtonElement;
}

describe("NavControlsWidget dock control", () => {
  beforeEach(() => {
    useCommandStore.setState({ active: null, lastResult: null, stoppedGoal: null });
  });

  afterEach(cleanup);

  it("reads Undock, in red, while the robot is charging", () => {
    setState(baseState({ charge_state: "charging" }));
    render(<NavControlsWidget />);

    const button = dockButton();
    expect(button.textContent).toContain("Undock");
    expect(button.className).toContain("danger");
    expect(button.disabled).toBe(false);
  });

  it("still reads Undock once the charger is released but the robot is on the dock", () => {
    setState(baseState({ charge_state: "docked", motors_enabled: true }));
    render(<NavControlsWidget />);
    expect(dockButton().textContent).toContain("Undock");
  });

  it("reads Dock & Charge, not red, once off the dock", () => {
    setState(baseState({ charge_state: "not_charging", motors_enabled: true }));
    render(<NavControlsWidget />);

    const button = dockButton();
    expect(button.textContent).toContain("Dock & Charge");
    expect(button.className).not.toContain("danger");
    expect(button.disabled).toBe(false);
  });

  it("stays off-dock when raw charge re-latches after confirmed clearance", () => {
    setState(baseState({
      charge_state: "float",
      dock_state: "CLEAR_CONFIRMED",
      dock_state_valid: true,
      undock_profile_commissioned: true,
    }));
    render(<NavControlsWidget />);

    const button = dockButton();
    expect(button.textContent).toContain("Dock & Charge");
    expect(button.textContent).not.toContain("Undock");
  });

  it("shows robot-side undock activity started by another dashboard", () => {
    setState(baseState({
      charge_state: "float",
      dock_state: "DEPARTING",
      dock_state_valid: true,
      undock_active: true,
      undock_profile_commissioned: true,
    }));
    render(<NavControlsWidget />);

    const button = dockButton();
    expect(button.textContent).toContain("Undocking");
    expect(button.disabled).toBe(true);
  });

  it("is a single control — never both a dock and an undock button", () => {
    setState(baseState({ charge_state: "charging" }));
    render(<NavControlsWidget />);
    expect(screen.getAllByRole("button", { name: /Undock|Dock & Charge/ })).toHaveLength(1);
  });

  it("greys out with the reason when the robot never claimed the capability", () => {
    setState(baseState({ charge_state: "charging" }), PARKED, []);
    render(<NavControlsWidget />);

    expect(dockButton().disabled).toBe(true);
    expect(screen.getByText(/not commissioned on this robot yet/)).toBeTruthy();
  });

  it("greys out while the robot is still moving", () => {
    setState(baseState({ charge_state: "charging" }),
             { ...PARKED, linear_velocity: 0.3 });
    render(<NavControlsWidget />);

    expect(dockButton().disabled).toBe(true);
    expect(screen.getByText(/still moving/)).toBeTruthy();
  });

  it("greys out without hardware telemetry to justify moving", () => {
    setState(null, PARKED);
    render(<NavControlsWidget />);
    expect(dockButton().disabled).toBe(true);
  });

  it("does not expose the old manual charge-release or motor-enable sequence", () => {
    setState(baseState({ charge_state: "charging" }));
    render(<NavControlsWidget />);

    expect(screen.queryByRole("button", { name: /Release charging/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Turn motors on/ })).toBeNull();
  });

  it("sends the matching command when pressed", () => {
    setState(baseState({ charge_state: "charging" }));
    const sent: string[] = [];
    useCommandStore.setState({ send: (command) => { sent.push(command); } });
    render(<NavControlsWidget />);

    fireEvent.click(dockButton());
    expect(sent).toEqual(["undock"]);
  });
});
