/**
 * Mirror check for server/app/commands/gates.py. If these two ever disagree,
 * the UI enables a button the server refuses (or the reverse) — so the cases
 * here deliberately track the ones in server/tests/test_dock_commands.py.
 */
import { describe, expect, it } from "vitest";
import {
  chargeReleaseReason, dockAction, dockReason, factsFrom,
  motorEnableReason, rejectionReason, undockReason,
  type StateFacts,
} from "./dockGates";
import type { BaseStateData, PoseData } from "../types/protocol";

const ALL_CAPS = ["dock", "undock", "charge_release", "motor_enable"];

/** On the dock, charging, everything healthy. */
function facts(overrides: Partial<StateFacts> = {}): StateFacts {
  return {
    online: true,
    linkConnected: true,
    telemetryAge: 0.1,
    hardwareStateValid: true,
    chargeState: "charging",
    motorsEnabled: false,
    estopPressed: false,
    faultFlags: 0,
    bumpersRear: false,
    dockState: null,
    dockStateValid: null,
    undockActive: false,
    undockProfileCommissioned: null,
    localized: true,
    stationary: true,
    capabilities: ALL_CAPS,
    navigating: false,
    ...overrides,
  };
}

const RELEASED = { chargeState: "docked" };
const READY_TO_UNDOCK = { ...RELEASED, motorsEnabled: true };

describe("charge release", () => {
  it("is allowed while charging and parked", () => {
    expect(chargeReleaseReason(facts())).toBeNull();
  });

  it.each([
    [{ online: false }, "not connected"],
    [{ telemetryAge: 9 }, "stale"],
    [{ linkConnected: false }, "stale"],
    [{ hardwareStateValid: false }, "valid data"],
    [{ faultFlags: 4 }, "fault"],
    [{ chargeState: "not_charging" }, "not on charge"],
    [{ stationary: false }, "still moving"],
  ])("refuses %o", (overrides, fragment) => {
    expect(chargeReleaseReason(facts(overrides))?.toLowerCase()).toContain(fragment);
  });
});

describe("charging with the motors still on", () => {
  // A hand-docked robot charges with its motors left enabled. Charge release
  // used to demand motors off while motor enable demanded the charger
  // released, so both refused and pointed at each other.
  const stuck = { chargeState: "charging", motorsEnabled: true };

  it("offers a way out instead of two contradictory refusals", () => {
    expect(chargeReleaseReason(facts(stuck))).toBeNull();
  });

  it("still holds motor enable back until the charger is actually released", () => {
    expect(motorEnableReason(facts(stuck))?.toLowerCase()).toContain("release charging");
  });

  it("never leaves both steps blocked at once", () => {
    expect([chargeReleaseReason(facts(stuck)), motorEnableReason(facts(stuck))])
      .toContain(null);
  });
});

describe("motor enable", () => {
  it("refuses while the charger is engaged, allows once released", () => {
    expect(motorEnableReason(facts())?.toLowerCase()).toContain("release charging");
    expect(motorEnableReason(facts(RELEASED))).toBeNull();
  });

  it.each([
    [{ estopPressed: true }, "emergency stop"],
    [{ motorsEnabled: true }, "already on"],
    [{ stationary: false }, "still moving"],
  ])("refuses %o", (overrides, fragment) => {
    expect(motorEnableReason(facts({ ...RELEASED, ...overrides }))?.toLowerCase())
      .toContain(fragment);
  });
});

describe("undock", () => {
  it("is allowed straight off the charger — the robot handles the rest", () => {
    expect(undockReason(facts())).toBeNull();               // charging, motors off
    expect(undockReason(facts(READY_TO_UNDOCK))).toBeNull(); // released, motors on
  });

  it("trusts CLEAR_CONFIRMED over a stale float charge state", () => {
    const clear = facts({
      chargeState: "float",
      dockState: "CLEAR_CONFIRMED",
      dockStateValid: true,
      undockProfileCommissioned: true,
    });
    expect(undockReason(clear)?.toLowerCase()).toContain("not on its dock");
  });

  it("refuses an invalid or uncommissioned dock observer", () => {
    expect(undockReason(facts({
      dockState: "UNKNOWN",
      dockStateValid: false,
    }))?.toLowerCase()).toContain("dock observer");
    expect(undockReason(facts({
      dockState: "DOCKED_CONFIRMED",
      dockStateValid: true,
      undockProfileCommissioned: false,
    }))?.toLowerCase()).toContain("profile");
  });

  it("refuses a duplicate while the robot is already undocking", () => {
    expect(undockReason(facts({
      dockState: "DEPARTING",
      dockStateValid: true,
      undockProfileCommissioned: true,
      undockActive: true,
    }))?.toLowerCase()).toContain("already undocking");
  });

  it.each([
    [{ capabilities: ["dock"] }, "not commissioned"],
    [{ chargeState: "not_charging" }, "not on its dock"],
    [{ bumpersRear: true }, "rear bumper"],
    [{ estopPressed: true }, "emergency stop"],
    [{ stationary: false }, "still moving"],
    [{ faultFlags: 2 }, "fault"],
    [{ telemetryAge: 9 }, "stale"],
  ])("refuses %o", (overrides, fragment) => {
    expect(undockReason(facts(overrides))?.toLowerCase()).toContain(fragment);
  });
});

describe("dock", () => {
  const offDock = { chargeState: "not_charging" };

  it("needs a known location but not a prior motor enable", () => {
    expect(dockReason(facts({ ...offDock, motorsEnabled: false }))).toBeNull();
    expect(dockReason(facts({ ...offDock, localized: false }))).toContain("where it is");
  });

  it("refuses when the robot never claimed the capability", () => {
    expect(dockReason(facts({ ...offDock, capabilities: [] }))).toContain("not commissioned");
  });

  it("refuses when already charging", () => {
    expect(dockReason(facts())).toContain("already charging");
  });
});

describe("fail-closed defaults", () => {
  it("refuses everything with no telemetry at all", () => {
    const blank = factsFrom("online", null, null, ALL_CAPS);
    for (const command of ["charge_release", "motor_enable", "dock", "undock"] as const) {
      expect(rejectionReason(command, blank)).not.toBeNull();
    }
  });

  it("leaves navigation commands ungated here", () => {
    expect(rejectionReason("navigate_to_pose", facts())).toBeNull();
    expect(rejectionReason("stop", facts())).toBeNull();
  });
});

describe("factsFrom", () => {
  const baseState: BaseStateData = {
    session_generation: 3,
    link_connected: true,
    telemetry_age: 0.2,
    hardware_state_valid: true,
    charge_state: "docked",
    motors_enabled: true,
    estop_pressed: false,
    fault_flags: 0,
    stall_value: 0,
    bumpers_front: false,
    bumpers_rear: false,
  };
  const parked: PoseData = {
    x: 0, y: 0, yaw: 0, linear_velocity: 0.004, angular_velocity: -0.01, localized: true,
  };

  it("treats encoder noise on a parked robot as stationary", () => {
    expect(factsFrom("online", baseState, parked, ALL_CAPS).stationary).toBe(true);
    expect(factsFrom("online", baseState, { ...parked, linear_velocity: 0.3 }, ALL_CAPS).stationary)
      .toBe(false);
  });

  it("is not stationary without a pose to prove it", () => {
    expect(factsFrom("online", baseState, null, ALL_CAPS).stationary).toBe(false);
  });
});

describe("the single dock button", () => {
  it("offers undock on the charger and dock everywhere else", () => {
    expect(dockAction(facts())).toBe("undock");                        // charging
    expect(dockAction(facts(RELEASED))).toBe("undock");                // on dock, released
    expect(dockAction(facts({ chargeState: "not_charging" }))).toBe("dock");
    expect(dockAction(facts({ chargeState: "unknown" }))).toBe("dock");
    expect(dockAction(facts({
      chargeState: "float",
      dockState: "CLEAR_CONFIRMED",
      dockStateValid: true,
    }))).toBe("dock");
    expect(dockAction(facts({
      dockState: "DEPARTING",
      dockStateValid: true,
      undockActive: true,
    }))).toBe("undock");
    expect(dockAction(facts({
      chargeState: "float",
      dockState: "UNKNOWN",
      dockStateValid: false,
    }))).toBe("undock");
  });
});
