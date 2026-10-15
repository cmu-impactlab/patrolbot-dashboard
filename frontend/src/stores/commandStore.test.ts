import { useTelemetryStore } from "./telemetryStore";
import type { BaseStateData } from "../types/protocol";
const readyBase: BaseStateData = { map_id: "cmuq-floor2", map_revision: "test-revision",
  lidar_state: "READY", lidar_scan_age: 0.1, session_generation: 1, link_connected: true, telemetry_age: 0,
  hardware_state_valid: true, charge_state: "idle", motors_enabled: true,
  estop_pressed: false, fault_flags: 0, stall_value: 0,
  bumpers_front: false, bumpers_rear: false,
  odom_epoch_valid: true, localization_recovery_required: false, localization_seed_stamp_ns: 1,
};
beforeEach(() => useTelemetryStore.setState({ baseState: readyBase, baseStateAt: performance.now(),
  pose: { x: 0, y: 0, yaw: 0, linear_velocity: 0, angular_velocity: 0, localized: true },
  poseReceivedAt: performance.now() }));
import { beforeEach, describe, expect, it, vi } from "vitest";
import { registerCommandSender, useCommandStore } from "./commandStore";

describe("navigation progress", () => {
  beforeEach(() => useCommandStore.setState({ active: {
    commandId: "current", command: "navigate_to_pose", phase: "running",
    stage: null, distanceRemaining: null,
  } }));

  it("keeps distance unknown until a current-command path exists", () => {
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "navigating",
      detail: "Heading to the destination", distance_remaining: 0 });
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
    expect(useCommandStore.getState().active?.stage).toBe("Planning a route to the destination.");
  });

  it("retains genuine zero after a valid path and clears it when invalidated", () => {
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "navigating",
      distance_remaining: 0, path_valid: true });
    expect(useCommandStore.getState().active?.distanceRemaining).toBe(0);
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "planning",
      distance_remaining: 0, path_valid: false });
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
  });

  it("ignores an old command's valid path feedback", () => {
    useCommandStore.getState().handleProgress({ command_id: "previous", stage: "navigating",
      distance_remaining: 0, path_valid: true });
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
  });

  it("shows a pending stop while keeping its distance unknown", () => {
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "stopping",
      detail: "Stopping; awaiting the robot's result.", distance_remaining: 0, path_valid: false });
    expect(useCommandStore.getState().active?.stage).toBe("Stopping; awaiting the robot's result.");
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
  });

  it.each([NaN, Infinity, -1])("keeps invalid distance %s unknown", distance => {
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "navigating",
      distance_remaining: distance, path_valid: true });
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
  });

  it("clears navigation distance on connection loss", () => {
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "navigating",
      distance_remaining: 2, path_valid: true });
    useCommandStore.getState().connectionLost();
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
  });
});

describe("commandStore.cancel", () => {
  beforeEach(() => {
    useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastResult: null, pickMode: "none" });
    registerCommandSender(null);
  });

  it("discards a paused destination without sending anything", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useCommandStore.setState({ stoppedGoal: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 }, active: null });

    useCommandStore.getState().cancel();

    expect(useCommandStore.getState().stoppedGoal).toBeNull();
    expect(sender).not.toHaveBeenCalled(); // robot already stopped; no command needed
  });

  it("halts an active command and offers no resume", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useCommandStore.setState({
      active: { commandId: "c1", command: "navigate_to_pose", phase: "running", stage: null, distanceRemaining: null },
      stoppedGoal: null,
    });

    useCommandStore.getState().cancel();

    expect(sender).toHaveBeenCalledTimes(1); // a stop was sent
    expect(useCommandStore.getState().stoppedGoal).toBeNull(); // nothing to resume
  });
});

describe("commandStore.takeOver", () => {
  beforeEach(() => {
    useCommandStore.setState({
      active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastResult: null, pickMode: "none", lastAttempt: null,
    });
    registerCommandSender(null);
  });

  it("re-sends the last attempted command with takeover=true", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => {
      sent.push(frame);
      return true;
    });

    // First attempt (no takeover) — remembered as the last attempt.
    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 3, y: 4, yaw: 0 });
    const first = JSON.parse(sent[0]);
    expect(first.data.takeover).toBe(false);

    // Operator confirms takeover: same command + goal, takeover flag set.
    useCommandStore.getState().takeOver();
    const second = JSON.parse(sent[1]);
    expect(second.data.command).toBe("navigate_to_pose");
    expect(second.data.goal).toEqual({ map_id: "cmuq-floor2", map_revision: "test-revision", x: 3, y: 4, yaw: 0 });
    expect(second.data.takeover).toBe(true);
  });

  it("does nothing when there is no prior attempt", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);

    useCommandStore.getState().takeOver();

    expect(sender).not.toHaveBeenCalled();
  });
});

describe("commandStore reconnect safety", () => {
  beforeEach(() => {
    useCommandStore.setState({
      active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastResult: null, pickMode: "none", lastAttempt: null,
    });
    registerCommandSender(null);
  });

  it("does not auto-resume a stopped goal when the socket reconnects", () => {
    const sender = vi.fn(() => true);
    // A destination is paused (Stop was pressed) and Resume is on offer.
    useCommandStore.setState({ stoppedGoal: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 } });

    // A socket reconnect re-registers the sender (see useTelemetrySocket).
    registerCommandSender(sender);

    // Nothing is sent implicitly — a goal is only re-sent on an explicit action.
    expect(sender).not.toHaveBeenCalled();
    useCommandStore.getState().resume();
    expect(sender).toHaveBeenCalledTimes(1);
  });
});

describe("commandStore.allowUnlocalized", () => {
  beforeEach(() => {
    useCommandStore.setState({
      active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastResult: null, pickMode: "none",
      lastAttempt: null, allowUnlocalized: false,
    });
    registerCommandSender(null);
  });

  it("is off by default, so an ordinary goal carries no override", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => { sent.push(frame); return true; });

    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 });

    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(false);
  });

  it("carries the override once, then disarms itself", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => { sent.push(frame); return true; });
    useCommandStore.getState().setAllowUnlocalized(true);

    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 });
    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(true);
    expect(useCommandStore.getState().allowUnlocalized).toBe(false);

    // Suppressing a safety gate must be re-armed deliberately every time; a
    // second destination cannot inherit the first one's override.
    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 5, y: 6, yaw: 0 });
    expect(JSON.parse(sent[1]).data.allow_unlocalized).toBe(false);
  });

  it("does not attach the override to commands that are not destinations", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => { sent.push(frame); return true; });
    useCommandStore.getState().setAllowUnlocalized(true);

    useCommandStore.getState().send("stop");

    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(false);
    // ...and it is still armed for the destination it was meant for.
    expect(useCommandStore.getState().allowUnlocalized).toBe(true);
  });
});

describe("commandStore override safety", () => {
  beforeEach(() => {
    useCommandStore.setState({
      active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastResult: null, pickMode: "none",
      lastAttempt: null, allowUnlocalized: false,
    });
    registerCommandSender(null);
  });

  it("keeps the override armed when the frame could not be sent", () => {
    registerCommandSender(() => false); // socket refused the frame
    useCommandStore.getState().setAllowUnlocalized(true);

    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 });

    // Nothing reached the robot, so the operator should not have to re-arm.
    expect(useCommandStore.getState().allowUnlocalized).toBe(true);
  });

  it("drops the override on reconnect, which may be a different robot", () => {
    useCommandStore.getState().setAllowUnlocalized(true);
    useCommandStore.getState().resetOverrides();
    expect(useCommandStore.getState().allowUnlocalized).toBe(false);
  });

  it("does not let takeOver replay a destination with a spent override", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => { sent.push(frame); return true; });
    useCommandStore.getState().setAllowUnlocalized(true);

    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 });
    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(true);

    // Taking control back re-sends the same destination; suppressing a safety
    // gate has to be re-armed deliberately rather than inherited.
    useCommandStore.getState().takeOver();
    expect(JSON.parse(sent[1]).data.allow_unlocalized).toBe(false);
  });
});


describe("localization recovery", () => {
  it.each([
    { odom_epoch_valid: undefined }, { odom_epoch_valid: false },
    { localization_recovery_required: true }, { localization_seed_stamp_ns: 0 },
  ])("blocks direct navigation and override during recovery: %o", fields => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useCommandStore.setState({ allowUnlocalized: true, active: null });
    useTelemetryStore.setState({ baseState: { map_id: "cmuq-floor2", map_revision: "test-revision",  ...readyBase, ...fields } });
    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 });
    expect(sender).not.toHaveBeenCalled();
    expect(useCommandStore.getState().lastResult?.outcome).toBe("rejected");
  });
  it("keeps pose initialization available and accepts navigation after recovery", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useTelemetryStore.setState({ baseState: { map_id: "cmuq-floor2", map_revision: "test-revision",  ...readyBase, localization_recovery_required: true } });
    useCommandStore.getState().send("set_initial_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2, yaw: 0 });
    expect(sender).toHaveBeenCalledTimes(1);
    useTelemetryStore.setState({ baseState: readyBase });
    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 });
    expect(sender).toHaveBeenCalledTimes(2);
  });
  it("rechecks freshness when resuming a destination", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useCommandStore.setState({ stoppedGoal: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 }, active: null });
    useTelemetryStore.setState({ baseStateAt: performance.now() - 3001 });
    useCommandStore.getState().resume();
    expect(sender).not.toHaveBeenCalled();
    expect(useCommandStore.getState().stoppedGoal).toEqual({ map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 });
  });
});

it("refuses a saved destination after the active floor changes", () => {
  const sender = vi.fn(() => true);
  registerCommandSender(sender);
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: {
    map_id: "cmuq-floor1", map_revision: "first", x: 1, y: 2,
  } });
  useCommandStore.getState().resume();
  expect(sender).not.toHaveBeenCalled();
  expect(useCommandStore.getState().lastResult?.outcome).toBe("rejected");
});

it("refuses takeover of a pose attempt after the robot changes floors", () => {
  const sender = vi.fn(() => true);
  registerCommandSender(sender);
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, lastAttempt: {
    command: "set_initial_pose", activeMap: readyBase,
    goal: { map_id: "cmuq-floor1", map_revision: "first", x: 1, y: 2 },
  } });
  useTelemetryStore.setState({ baseState: { ...readyBase, map_id: "cmuq-floor1", map_revision: "first" } });
  useCommandStore.getState().takeOver();
  expect(sender).not.toHaveBeenCalled();
  expect(useCommandStore.getState().lastAttempt).toBeNull();
});

it("refuses legacy spatial commands without map context", () => {
  const sender = vi.fn(() => true);
  registerCommandSender(sender);
  useCommandStore.getState().send("set_initial_pose", { x: 1, y: 2 });
  expect(sender).not.toHaveBeenCalled();
  expect(useCommandStore.getState().lastResult?.outcome).toBe("rejected");
});


it("keeps the submitted destination when Stop precedes path telemetry", () => {
  const sender = vi.fn((_frame: string) => true);
  registerCommandSender(sender);
  useTelemetryStore.setState({ path: null });
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null });
  const goal = { map_id: "cmuq-floor2", map_revision: "test-revision", x: 3, y: 4 };
  useCommandStore.getState().send("navigate_to_pose", goal);
  useCommandStore.getState().stop();
  expect(useCommandStore.getState().stoppedGoal).toEqual(goal);
  expect(JSON.parse(sender.mock.calls[1][0]).data.command).toBe("stop");
  useCommandStore.getState().stop();
  expect(useCommandStore.getState().stoppedGoal).toEqual(goal);
});


describe("LiDAR power barrier", () => {
  it("requires a post-terminal BaseState before another spatial command", () => {
    const clock = vi.spyOn(performance,"now").mockReturnValue(100);
    const sent: string[] = [];
    registerCommandSender(frame => { sent.push(frame); return true; });
    useCommandStore.setState({active:null,lidarPending:null,lidarRecoveryRequired:false,lidarStateAfter:null});
    useTelemetryStore.setState({baseState:readyBase,baseStateAt:99});
    useCommandStore.getState().send("set_lidar_power",undefined,false,true);
    const power = JSON.parse(sent[0]).data.command_id;
    useCommandStore.getState().handleResult({command_id:power,outcome:"succeeded"});
    const goal = {map_id:"cmuq-floor2",map_revision:"test-revision",x:0,y:0};
    useCommandStore.getState().send("set_initial_pose",goal);
    expect(sent).toHaveLength(1);
    clock.mockReturnValue(101);
    useTelemetryStore.setState({baseStateAt:101});
    useCommandStore.getState().send("set_initial_pose",goal);
    expect(sent).toHaveLength(2);
    clock.mockRestore(); registerCommandSender(null);
  });
  it("requires a deliberate successful power recovery after a definitive failure", () => {
    const sent: string[] = [];
    registerCommandSender(frame => { sent.push(frame); return true; });
    useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null });
    useTelemetryStore.setState({ baseState: readyBase, baseStateAt: performance.now() });
    useCommandStore.getState().send("set_lidar_power", undefined, false, true);
    const first = JSON.parse(sent[0]).data.command_id;
    useCommandStore.getState().handleResult({ command_id:first, outcome:"failed", detail:"Timed out; FAULT" });
    useCommandStore.getState().send("set_initial_pose", {map_id:"cmuq-floor2",map_revision:"test-revision",x:0,y:0});
    expect(sent).toHaveLength(1);
    useTelemetryStore.setState({baseStateAt:performance.now()});
    useCommandStore.getState().send("set_lidar_power", undefined, false, true);
    expect(sent).toHaveLength(2);
    const recover = JSON.parse(sent[1]).data.command_id;
    useCommandStore.getState().handleResult({command_id:recover,outcome:"succeeded",detail:"Commanded on"});
    useTelemetryStore.setState({baseStateAt:performance.now()});
    useCommandStore.getState().send("set_initial_pose", {map_id:"cmuq-floor2",map_revision:"test-revision",x:0,y:0});
    expect(sent).toHaveLength(3);
    registerCommandSender(null);
  });
  it("keeps spatial requests blocked while Stop runs and through ambiguous timeout", () => {
    const sent: string[] = [];
    registerCommandSender(frame => { sent.push(frame); return true; });
    useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastResult: null });
    useTelemetryStore.setState({ baseState: readyBase, baseStateAt: performance.now() });
    useCommandStore.getState().send("set_lidar_power", undefined, false, false);
    const power = JSON.parse(sent[0]).data.command_id;
    useCommandStore.getState().stop();
    useCommandStore.getState().handleResult({ command_id: power, outcome: "timeout", detail: "Unknown" });
    useCommandStore.getState().send("set_initial_pose", { map_id:"cmuq-floor2", map_revision:"test-revision", x:0, y:0 });
    expect(sent).toHaveLength(2);
    expect(useCommandStore.getState().lidarPending).toBe(power);
    useCommandStore.getState().handleResult({ command_id: power, outcome:"succeeded", detail:"Commanded off" });
    expect(useCommandStore.getState().lidarPending).toBeNull();
    expect(useCommandStore.getState().lastResult?.detail).toBe("Commanded off");
    expect(useTelemetryStore.getState().poseSetThisSession).toBe(false);
    expect(useCommandStore.getState().stoppedGoal).toBeNull();
    registerCommandSender(null);
  });
});


describe("emergency software reset", () => {
  it("escapes an unresolved LiDAR command and discards the old destination", () => {
    const sender = vi.fn((frame: string) => !!frame);
    registerCommandSender(sender);
    useCommandStore.setState({ lidarPending: "unknown-power", lidarRecoveryRequired: false,
      stoppedGoal: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 },
      active: { commandId: "unknown-power", command: "set_lidar_power", phase: "running", stage: null, distanceRemaining: null } });
    useCommandStore.getState().send("software_reset");
    expect(JSON.parse(sender.mock.calls[0][0]).data.command).toBe("software_reset");
    expect(useCommandStore.getState().stoppedGoal).toBeNull();
    expect(useCommandStore.getState().lidarPending).toBeNull();
    expect(useCommandStore.getState().lidarRecoveryRequired).toBe(true);
    expect(useCommandStore.getState().active?.command).toBe("software_reset");
    registerCommandSender(null);
  });
});


it("preserves the explicit measured profile through operator takeover", () => {
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, stoppedGoal: null, lastAttempt: null });
  const sent: string[] = [];
  registerCommandSender(frame => { sent.push(frame); return true; });
  useCommandStore.getState().send("set_initial_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: -3.25, y: 8.5, yaw: Math.PI / 2 }, false, undefined, "measured");
  useCommandStore.getState().takeOver();
  expect(sent).toHaveLength(2);
  for (const frame of sent) expect(JSON.parse(frame).data.seed_profile).toBe("measured");
  expect(JSON.parse(sent[1]).data.takeover).toBe(true);
});


it("motor disable bypasses LiDAR uncertainty and discards saved movement", () => {
  const sent: string[] = [];
  registerCommandSender(frame => { sent.push(frame); return true; });
  useCommandStore.setState({ active: null, lidarPending: "unknown", lidarRecoveryRequired: true, lidarStateAfter: performance.now(),
    stoppedGoal: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 }, allowUnlocalized: true });
  useCommandStore.getState().send("motor_disable");
  expect(JSON.parse(sent[0]).data.command).toBe("motor_disable");
  expect(useCommandStore.getState().stoppedGoal).toBeNull();
  expect(useCommandStore.getState().allowUnlocalized).toBe(false);
});

it("stopping Undock sends Stop once and cannot resume an earlier path or saved goal", () => {
  const sent: string[] = [];
  registerCommandSender(frame => { sent.push(frame); return true; });
  const oldGoal = { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 };
  useTelemetryStore.setState({ path: { ...oldGoal, command_id: "old-navigation",
    path_valid: true, frame_id: "map", points: [[0, 0], [1, 2]], goal: oldGoal } });
  useCommandStore.setState({ active: { commandId: "undock", command: "undock", phase: "running",
    stage: "Turning away from the dock", distanceRemaining: null },
    stoppedGoal: oldGoal, lidarPending: null });
  useCommandStore.getState().stop();
  expect(sent).toHaveLength(1);
  expect(JSON.parse(sent[0]).data.command).toBe("stop");
  expect(useCommandStore.getState().stoppedGoal).toBeNull();
});


describe("specific navigation reasons", () => {
  beforeEach(() => useCommandStore.setState({ active: { commandId: "current", command: "navigate_to_pose",
    phase: "running", stage: null, distanceRemaining: 2 }, lastResult: null }));
  it.each(["refreshing", "failed", "planning"])("retains %s reason while distance is unknown", stage => {
    useCommandStore.getState().handleProgress({ command_id: "current", stage,
      detail: "Specific native planning reason", path_valid: false, distance_remaining: 0 });
    expect(useCommandStore.getState().active?.stage).toBe("Specific native planning reason");
    expect(useCommandStore.getState().active?.distanceRemaining).toBeNull();
  });
  it("retains a specific terminal reason and ignores late feedback", () => {
    useCommandStore.getState().handleResult({ command_id: "current", outcome: "failed",
      detail: "Start blocked. Navigation error 205.", error_code: 205, error_message: "all exits blocked" });
    useCommandStore.getState().handleProgress({ command_id: "current", stage: "navigating", path_valid: true, distance_remaining: 0 });
    expect(useCommandStore.getState().active).toBeNull();
    expect(useCommandStore.getState().lastResult?.detail).toContain("205");
  });
  it("age-only admission sends a normal goal with no localization override", () => {
    const sent: string[] = [];
    registerCommandSender(frame => { sent.push(frame); return true; });
    useTelemetryStore.setState({ capabilities: ["navigation_stationary_refresh_v1"],
      pose: { ...useTelemetryStore.getState().pose!, frame_id: "map", map_id: "cmuq-floor2", map_revision: "test-revision",
        localized: false, planar_covariance: [.01, .02, .03], covariance_limit: .25,
        localization_age_s: 31, localization_max_age_s: 30 }, poseReceivedAt: performance.now() });
    useCommandStore.setState({ allowUnlocalized: false, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null });
    useCommandStore.getState().send("navigate_to_pose", { map_id: "cmuq-floor2", map_revision: "test-revision", x: 1, y: 2 });
    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(false);
    expect(useTelemetryStore.getState().pose?.localized).toBe(false);
  });
});
