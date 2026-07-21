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
