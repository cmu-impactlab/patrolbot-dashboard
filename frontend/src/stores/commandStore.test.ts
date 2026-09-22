import { useTelemetryStore } from "./telemetryStore";
import type { BaseStateData } from "../types/protocol";
const readyBase: BaseStateData = {
  session_generation: 1, link_connected: true, telemetry_age: 0,
  hardware_state_valid: true, charge_state: "idle", motors_enabled: true,
  estop_pressed: false, fault_flags: 0, stall_value: 0,
  bumpers_front: false, bumpers_rear: false,
  odom_epoch_valid: true, localization_recovery_required: false, localization_seed_stamp_ns: 1,
};
beforeEach(() => useTelemetryStore.setState({ baseState: readyBase, baseStateAt: performance.now() }));
import { beforeEach, describe, expect, it, vi } from "vitest";
import { registerCommandSender, useCommandStore } from "./commandStore";

describe("commandStore.cancel", () => {
  beforeEach(() => {
    useCommandStore.setState({ active: null, stoppedGoal: null, lastResult: null, pickMode: "none" });
    registerCommandSender(null);
  });

  it("discards a paused destination without sending anything", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useCommandStore.setState({ stoppedGoal: { x: 1, y: 2, yaw: 0 }, active: null });

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
      active: null, stoppedGoal: null, lastResult: null, pickMode: "none", lastAttempt: null,
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
    useCommandStore.getState().send("navigate_to_pose", { x: 3, y: 4, yaw: 0 });
    const first = JSON.parse(sent[0]);
    expect(first.data.takeover).toBe(false);

    // Operator confirms takeover: same command + goal, takeover flag set.
    useCommandStore.getState().takeOver();
    const second = JSON.parse(sent[1]);
    expect(second.data.command).toBe("navigate_to_pose");
    expect(second.data.goal).toEqual({ x: 3, y: 4, yaw: 0 });
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
      active: null, stoppedGoal: null, lastResult: null, pickMode: "none", lastAttempt: null,
    });
    registerCommandSender(null);
  });

  it("does not auto-resume a stopped goal when the socket reconnects", () => {
    const sender = vi.fn(() => true);
    // A destination is paused (Stop was pressed) and Resume is on offer.
    useCommandStore.setState({ stoppedGoal: { x: 1, y: 2, yaw: 0 } });

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
      active: null, stoppedGoal: null, lastResult: null, pickMode: "none",
      lastAttempt: null, allowUnlocalized: false,
    });
    registerCommandSender(null);
  });

  it("is off by default, so an ordinary goal carries no override", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => { sent.push(frame); return true; });

    useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2, yaw: 0 });

    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(false);
  });

  it("carries the override once, then disarms itself", () => {
    const sent: string[] = [];
    registerCommandSender((frame) => { sent.push(frame); return true; });
    useCommandStore.getState().setAllowUnlocalized(true);

    useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2, yaw: 0 });
    expect(JSON.parse(sent[0]).data.allow_unlocalized).toBe(true);
    expect(useCommandStore.getState().allowUnlocalized).toBe(false);

    // Suppressing a safety gate must be re-armed deliberately every time; a
    // second destination cannot inherit the first one's override.
    useCommandStore.getState().send("navigate_to_pose", { x: 5, y: 6, yaw: 0 });
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
      active: null, stoppedGoal: null, lastResult: null, pickMode: "none",
      lastAttempt: null, allowUnlocalized: false,
    });
    registerCommandSender(null);
  });

  it("keeps the override armed when the frame could not be sent", () => {
    registerCommandSender(() => false); // socket refused the frame
    useCommandStore.getState().setAllowUnlocalized(true);

    useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2, yaw: 0 });

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

    useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2, yaw: 0 });
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
    useTelemetryStore.setState({ baseState: { ...readyBase, ...fields } });
    useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2 });
    expect(sender).not.toHaveBeenCalled();
    expect(useCommandStore.getState().lastResult?.outcome).toBe("rejected");
  });
  it("keeps pose initialization available and accepts navigation after recovery", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useTelemetryStore.setState({ baseState: { ...readyBase, localization_recovery_required: true } });
    useCommandStore.getState().send("set_initial_pose", { x: 1, y: 2, yaw: 0 });
    expect(sender).toHaveBeenCalledTimes(1);
    useTelemetryStore.setState({ baseState: readyBase });
    useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2 });
    expect(sender).toHaveBeenCalledTimes(2);
  });
  it("rechecks freshness when resuming a destination", () => {
    const sender = vi.fn(() => true);
    registerCommandSender(sender);
    useCommandStore.setState({ stoppedGoal: { x: 1, y: 2 }, active: null });
    useTelemetryStore.setState({ baseStateAt: performance.now() - 3001 });
    useCommandStore.getState().resume();
    expect(sender).not.toHaveBeenCalled();
    expect(useCommandStore.getState().stoppedGoal).toEqual({ x: 1, y: 2 });
  });
});
