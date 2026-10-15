/**
 * Undock is the only dock-related control: the robot has no automatic dock-in
 * path, so it is driven onto its charger by hand. The button is present only
 * while the robot is on the dock.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NavControlsWidget } from "./NavControlsWidget";
import { useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { BaseStateData, PoseData } from "../types/protocol";

const CAPS = ["undock", "motor_enable", "motor_disable"];

const PARKED: PoseData = {
  x: 1, y: 1, yaw: 0, linear_velocity: 0, angular_velocity: 0, localized: true,
};

function baseState(overrides: Partial<BaseStateData> = {}): BaseStateData {
  return {
    lidar_state: "READY", lidar_scan_age: 0.1, session_generation: 1,
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
  useAuthStore.setState({ user: { id: 1, username: "test", display_name: "Test", role: "operator", auth_mode: "local" } });
  useTelemetryStore.setState({
    wsConnected: true,
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
    useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastResult: null, stoppedGoal: null });
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

  it("hides a new Undock once charging is released, even on the dock", () => {
    setState(baseState({ charge_state: "docked", motors_enabled: true,
      dock_state: "DOCKED_CONFIRMED", dock_state_valid: true }));
    render(<NavControlsWidget />);
    expect(dockButtonOrNull()).toBeNull();
  });

  it("allows charging Undock without a map pose or localization seed", () => {
    setState(baseState({ localization_recovery_required: true }), null);
    useTelemetryStore.setState({ poseSetThisSession: false });
    render(<NavControlsWidget />);
    expect(dockButton().disabled).toBe(false);
  });

  it("defers stale map velocity to the robot instead of treating it as current", () => {
    setState(baseState(), { ...PARKED, linear_velocity: 0.3 });
    useTelemetryStore.setState({ poseReceivedAt: performance.now() - 4000 });
    render(<NavControlsWidget />);
    expect(dockButton().disabled).toBe(false);
  });

  it("keeps active Undock, progress and Stop after charging releases", () => {
    setState(baseState({ charge_state: "docked" }), null);
    useCommandStore.setState({ active: { commandId: "undock-1", command: "undock",
      phase: "running", stage: "Turning away from the dock", distanceRemaining: null } });
    render(<NavControlsWidget />);
    expect(dockButton().textContent).toContain("Undocking");
    expect(dockButton().disabled).toBe(true);
    expect(screen.getByText("Turning away from the dock")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Stop" }) as HTMLButtonElement).disabled).toBe(false);
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
    useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastResult: null, stoppedGoal: null });
  });

  afterEach(cleanup);

  it("keeps motor enable outside the collapsed Advanced disclosure", () => {
    setState(baseState({ charge_state: "not_charging", motors_enabled: false }));
    render(<NavControlsWidget />);

    const advanced = screen.getByText("Advanced").closest("details") as HTMLDetailsElement;
    expect(advanced.open).toBe(false);
    expect(advanced.contains(motorEnableButton())).toBe(false);
    openAdvanced();
    expect(advanced.open).toBe(true);
  });

  it("requires confirmation before sending motor_enable", () => {
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
    [{ charge_state: "charging" }, "release charging"],
    [{ estop_pressed: true }, "emergency stop"],
    [{ fault_flags: 4 }, "fault"],
  ])("leaves the enable request available when %o", (overrides, _reason) => {
    setState(baseState({ charge_state: "not_charging", ...overrides }));
    render(<NavControlsWidget />);

    openAdvanced();
    const button = motorEnableButton();
    expect(button.disabled).toBe(false);
  });

  it("keeps the enable request available while a navigation goal is active", () => {
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
    expect(button.disabled).toBe(false);
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


describe("localization recovery controls", () => {
  afterEach(cleanup);
it("blocks destinations during recovery but leaves operator pose initialization available", () => {
  setState(baseState({ odom_epoch_valid: true, localization_recovery_required: true,
    localization_seed_stamp_ns: 1 }));
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, pickMode: "none", allowUnlocalized: true });
  render(<NavControlsWidget />);
  expect((screen.getByRole("button", { name: "Send Robot Here" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Set Robot Location" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText(/recovering its location/)).toBeTruthy();
});

it("offers destinations after recovery with an operator-confirmed pose", () => {
  setState(baseState({ odom_epoch_valid: true, localization_recovery_required: false,
    localization_seed_stamp_ns: 1 }));
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, pickMode: "none", allowUnlocalized: false });
  render(<NavControlsWidget />);
  expect((screen.getByRole("button", { name: "Send Robot Here" }) as HTMLButtonElement).disabled).toBe(false);
});

});


describe("software reset", () => {
  beforeEach(() => useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastResult: null, stoppedGoal: null }));
  afterEach(cleanup);
  function setup() {
    setState(baseState({ dock_state: "DOCKED_CONFIRMED", dock_state_valid: true }), PARKED, [...CAPS, "software_reset"]);
    const sent: string[] = [];
    useCommandStore.setState({ send: command => { sent.push(command); } });
    render(<NavControlsWidget />); openAdvanced();
    return sent;
  }
  it("keeps reset visible outside Advanced when the robot lacks the capability", () => {
    setState(baseState({ dock_state: "DOCKED_CONFIRMED", dock_state_valid: true }), PARKED, CAPS);
    render(<NavControlsWidget />);
    const button = screen.getByRole("button", { name: "Restart robot software" }) as HTMLButtonElement;
    expect(button.closest("details")).toBeNull();
    expect(button.disabled).toBe(true);
    expect(button.title).toContain("Software reset is not available");
  });
  it("keeps an available reset visible without opening Advanced", () => {
    setState(baseState({ dock_state: "DOCKED_CONFIRMED", dock_state_valid: true }), PARKED, [...CAPS, "software_reset"]);
    render(<NavControlsWidget />);
    const button = screen.getByRole("button", { name: "Restart robot software" }) as HTMLButtonElement;
    expect(button.closest("details")).toBeNull();
    expect(button.disabled).toBe(false);
  });
  it("allows a recovery request before any pose or hardware telemetry exists", () => {
    setState(null, null, [...CAPS, "software_reset"]);
    const sent: string[] = [];
    useCommandStore.setState({ send: command => { sent.push(command); } });
    render(<NavControlsWidget />);
    const reset = screen.getByRole("button", { name: "Restart robot software" }) as HTMLButtonElement;
    expect(reset.disabled).toBe(false);
    fireEvent.click(reset);
    fireEvent.click(screen.getByRole("button", { name: "Confirm software reset" }));
    expect(sent).toEqual(["software_reset"]);
  });
  it("allows recovery from a stuck pose command without retrying a pending reset", () => {
    setState(null, null, [...CAPS, "software_reset"]);
    useCommandStore.setState({ active: { commandId: "stuck", command: "set_initial_pose", phase: "running", stage: null, distanceRemaining: null } });
    render(<NavControlsWidget />);
    const reset = screen.getByRole("button", { name: "Restart robot software" }) as HTMLButtonElement;
    expect(reset.disabled).toBe(false);
    act(() => useCommandStore.setState({ active: { commandId: "reset", command: "software_reset", phase: "running", stage: null, distanceRemaining: null } }));
    expect((screen.getByRole("button", { name: "Reset pending…" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("requires confirmation and cancellation sends nothing", () => {
    const sent=setup();
    fireEvent.click(screen.getByRole("button", {name:"Restart robot software"}));
    expect(sent).toEqual([]);
    fireEvent.click(screen.getByRole("button", {name:"Cancel"}));
    expect(sent).toEqual([]);
    fireEvent.click(screen.getByRole("button", {name:"Restart robot software"}));
    fireEvent.click(screen.getByRole("button", {name:"Confirm software reset"}));
    expect(sent).toEqual(["software_reset"]);
  });
  it("refuses confirmation after connection loss", () => {
    const sent=setup();
    fireEvent.click(screen.getByRole("button", {name:"Restart robot software"}));
    setState(baseState({ dock_state: "DOCKED_CONFIRMED", dock_state_valid: true }), PARKED, [...CAPS,"software_reset"]);
    useTelemetryStore.setState({wsConnected:false,connection:{state:"offline",last_seen:null}});
    fireEvent.click(screen.getByRole("button", {name:"Confirm software reset"}));
    expect(sent).toEqual([]);
  });
});


describe("motor power requests", () => {
  afterEach(cleanup);
  beforeEach(() => useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastResult: null, stoppedGoal: null }));
  it("allows an enable request without a map pose", () => {
    setState(baseState({ charge_state: "not_charging", motors_enabled: false }), null);
    render(<NavControlsWidget />);
    expect(motorEnableButton().disabled).toBe(false);
  });
  it("offers a different color and immediate disable while moving with faults", () => {
    setState(baseState({ motors_enabled: true, fault_flags: 4, estop_pressed: true }), { ...PARKED, linear_velocity: .2 });
    const sent: string[] = [];
    useCommandStore.setState({ send: command => { sent.push(command); }, active: { commandId: "nav", command: "navigate_to_pose", phase: "running", stage: null, distanceRemaining: null } });
    render(<NavControlsWidget />);
    const button = screen.getByRole("button", { name: "Turn motors off" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.className).toContain("danger");
    fireEvent.click(button);
    expect(sent).toEqual(["motor_disable"]);
  });
  it.each(["missing", "stale off", "invalid hardware", "fresh off"])("keeps disable available with %s motor telemetry", (state) => {
    setState(state === "missing" ? null : baseState({ motors_enabled: false }), null);
    if (state === "stale off") useTelemetryStore.setState({ baseStateAt: performance.now() - 16_000 });
    if (state === "invalid hardware") useTelemetryStore.setState({ baseState: baseState({ hardware_state_valid: false, link_connected: false, motors_enabled: false }) });
    const sent: string[] = [];
    useCommandStore.setState({ send: command => { sent.push(command); } });
    render(<NavControlsWidget />);
    const button = screen.getByRole("button", { name: "Turn motors off" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(sent).toEqual(["motor_disable"]);
  });
  it("takes the target from fresh readback after opening enable confirmation", () => {
    setState(baseState({ charge_state: "not_charging" }));
    const sent: string[] = [];
    useCommandStore.setState({ send: command => { sent.push(command); } });
    render(<NavControlsWidget />);
    fireEvent.click(motorEnableButton());
    act(() => useTelemetryStore.setState({ baseState: baseState({ motors_enabled: true }) }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm motor enable" }));
    expect(sent).toEqual([]);
  });
});


it("keeps Stop and motor disable available while LiDAR is off", () => {
  setState(baseState({ lidar_state: "OFF", lidar_scan_age: -1, motors_enabled: true }));
  useCommandStore.setState({ active: null, lidarPending: "power-unknown", lidarRecoveryRequired: true, lidarStateAfter: null });
  render(<NavControlsWidget />);
  expect((screen.getByRole("button", { name: "Stop Robot" }) as HTMLButtonElement).disabled).toBe(false);
  expect((screen.getByRole("button", { name: "Turn motors off" }) as HTMLButtonElement).disabled).toBe(false);
  cleanup();
});


it("offers age-only refresh only for a verified native guard without arming override", () => {
  const base = baseState({ map_id: "floor1", map_revision: "revision", odom_epoch_valid: true,
    localization_recovery_required: false, localization_seed_stamp_ns: 1, charge_state: "not_charging", motors_enabled: true });
  const pose: PoseData = { ...PARKED, frame_id: "map", map_id: "floor1", map_revision: "revision",
    localized: false, planar_covariance: [.01,.01,.01], covariance_limit: .25,
    localization_age_s: 31, localization_max_age_s: 30 };
  setState(base, pose, []);
  useCommandStore.setState({ allowUnlocalized: false, active: null, pickMode: "none", lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null });
  const view = render(<NavControlsWidget />);
  expect((screen.getByRole("button", { name: "Send Robot Here" }) as HTMLButtonElement).disabled).toBe(true);
  act(() => useTelemetryStore.setState({ capabilities: ["navigation_stationary_refresh_v1"] }));
  expect((screen.getByRole("button", { name: "Send Robot Here" }) as HTMLButtonElement).disabled).toBe(false);
  expect(useCommandStore.getState().allowUnlocalized).toBe(false);
  view.unmount();
});
