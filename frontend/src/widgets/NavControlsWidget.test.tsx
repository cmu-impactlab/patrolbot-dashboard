/**
 * Undock is the only dock-related control: the robot has no automatic dock-in
 * path, so it is driven onto its charger by hand. The button is present only
 * while the robot is on the dock.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NavControlsWidget } from "./NavControlsWidget";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { BaseStateData, PoseData } from "../types/protocol";

const CAPS = ["undock", "motor_enable"];

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
    bumpers_valid: true,
    ...overrides,
  };
}

function setState(base: BaseStateData | null, pose: PoseData | null = PARKED,
                  capabilities: string[] = CAPS) {
  useTelemetryStore.setState({
    connection: { state: "online", last_seen: null },
    // The controls read stale telemetry as absent, the way the server's gates
    // do, so these fixtures have to say the frames just arrived.
    baseStateAt: base === null ? null : performance.now(),
    poseReceivedAt: pose === null ? 0 : performance.now(),
    baseState: base,
    pose,
    capabilities,
    poseSetThisSession: true,
  });
}

function dockButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Undock|Undocking/ }) as HTMLButtonElement;
}

function dockButtonOrNull(): HTMLButtonElement | null {
  return screen.queryByRole("button", { name: /Undock|Undocking/ }) as HTMLButtonElement | null;
}

function openAdvanced() {
  fireEvent.click(screen.getByText("Advanced"));
}

function motorEnableButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Turn motors on|Turning motors on/ }) as HTMLButtonElement;
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

  it("is absent once the robot is off the dock", () => {
    setState(baseState({ charge_state: "not_charging", motors_enabled: true }));
    render(<NavControlsWidget />);
    // Not merely disabled: there is nothing to undock from, and there is no
    // Dock control to take its place.
    expect(dockButtonOrNull()).toBeNull();
  });

  it("stays absent when raw charge re-latches after confirmed clearance", () => {
    setState(baseState({
      charge_state: "float",
      dock_state: "CLEAR_CONFIRMED",
      dock_state_valid: true,
      undock_profile_commissioned: true,
    }));
    render(<NavControlsWidget />);
    expect(dockButtonOrNull()).toBeNull();
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

  it("is a single control, and there is no Dock button anywhere", () => {
    setState(baseState({ charge_state: "charging" }));
    render(<NavControlsWidget />);
    expect(screen.getAllByRole("button", { name: /Undock/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /Dock & Charge/ })).toBeNull();
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

  it("is absent without hardware telemetry to say the robot is docked", () => {
    setState(null, PARKED);
    render(<NavControlsWidget />);
    expect(dockButtonOrNull()).toBeNull();
  });

  it("keeps charge release out of the UI", () => {
    setState(baseState({ charge_state: "charging" }));
    render(<NavControlsWidget />);

    expect(screen.queryByRole("button", { name: /Release charging/ })).toBeNull();
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

describe("NavControlsWidget advanced motor enable", () => {
  beforeEach(() => {
    useCommandStore.setState({ active: null, lastResult: null, stoppedGoal: null });
  });

  afterEach(cleanup);

  it("keeps motor enable inside the collapsed Advanced disclosure", () => {
    setState(baseState({ charge_state: "not_charging", motors_enabled: false }));
    render(<NavControlsWidget />);

    const advanced = screen.getByText("Advanced").closest("details") as HTMLDetailsElement;
    expect(advanced.open).toBe(false);
    expect(advanced.contains(motorEnableButton())).toBe(true);
    openAdvanced();
    expect(advanced.open).toBe(true);
  });

  it("requires confirmation before sending motor_enable from Advanced", () => {
    setState(baseState({ charge_state: "not_charging", motors_enabled: false }));
    const sent: string[] = [];
    useCommandStore.setState({ send: (command) => { sent.push(command); } });
    render(<NavControlsWidget />);

    openAdvanced();
    const button = motorEnableButton();
    expect(button.disabled).toBe(false);
    fireEvent.click(button);

    expect(sent).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm motor enable" }));

    expect(sent).toEqual(["motor_enable"]);
  });

  it.each([
    [{ motors_enabled: true }, "already on"],
    [{ charge_state: "charging" }, "release charging"],
    [{ estop_pressed: true }, "emergency stop"],
    [{ fault_flags: 4 }, "fault"],
  ])("disables motor enable when %o", (overrides, reason) => {
    setState(baseState({ charge_state: "not_charging", ...overrides }));
    render(<NavControlsWidget />);

    openAdvanced();
    const button = motorEnableButton();
    expect(button.disabled).toBe(true);
    expect(button.title.toLowerCase()).toContain(reason);
  });

  it("disables motor enable while a navigation goal is active", () => {
    setState(baseState({ charge_state: "not_charging" }));
    useCommandStore.setState({
      active: {
        commandId: "nav-1", command: "navigate_to_pose", phase: "running",
        stage: null, distanceRemaining: null,
      },
    });
    render(<NavControlsWidget />);

    openAdvanced();
    const button = motorEnableButton();
    expect(button.disabled).toBe(true);
    expect(button.title.toLowerCase()).toContain("stop it before enabling the motors");
  });

  it("shows the in-flight state and prevents a duplicate request", () => {
    setState(baseState({ charge_state: "not_charging" }));
    useCommandStore.setState({
      active: {
        commandId: "enable-1", command: "motor_enable", phase: "running",
        stage: null, distanceRemaining: null,
      },
    });
    render(<NavControlsWidget />);

    openAdvanced();
    expect(motorEnableButton().textContent).toContain("Turning motors on");
    expect(motorEnableButton().disabled).toBe(true);
  });
});
