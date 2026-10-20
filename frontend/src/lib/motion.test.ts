import { afterEach, describe, expect, it } from "vitest";
import { motionFreshness, readMotionSnapshot, MOTION_MAX_AGE_S, type MotionData, type MotionReadout } from "./motion";
import { useTelemetryStore } from "../stores/telemetryStore";

const sample: MotionData = {
  linear_velocity: 0.12,
  angular_velocity: -0.2,
  source: "raw_odom",
  sample_age_s: 0.02,
  receipt_age_s: 0.01,
  session_generation: 7,
  odom_epoch: "epoch-a",
  odom_epoch_generation: 3,
};

const readout = (overrides: Partial<MotionReadout> = {}): MotionReadout => ({
  motion: sample,
  motionAt: 1000,
  transportAgeS: 0,
  ...overrides,
});

afterEach(() => {
  useTelemetryStore.setState({ motion: null, motionAt: null });
});

describe("motion freshness", () => {
  it("shows no value at all when there is no sample, never a zero", () => {
    const freshness = motionFreshness({ motion: null, motionAt: null, transportAgeS: null }, 1000, 7);
    expect(freshness).toMatchObject({ fresh: false, stale: true, linear: null, angular: null });
    expect(freshness.reason).toBe("No fresh speed data");
  });
  it("requires a receipt time even when a sample object exists", () => {
    expect(motionFreshness({ motion: sample, motionAt: null, transportAgeS: 0 }, 1000, 7).reason).toBe("No fresh speed data");
  });
  it("drops a sample from an older robot session generation", () => {
    expect(motionFreshness(readout(), 1000, 8).reason).toMatch(/older robot session/);
    expect(motionFreshness(readout(), 1000, null).reason).toMatch(/session state/);
  });
  it("rejects non-finite or negative ages instead of treating them as zero", () => {
    expect(motionFreshness(readout({ motion: { ...sample, sample_age_s: Number.NaN } }), 1000, 7).reason).toMatch(/invalid/);
    expect(motionFreshness(readout({ motion: { ...sample, receipt_age_s: -1 } }), 1000, 7).reason).toMatch(/invalid age/);
    expect(motionFreshness(readout({ motionAt: 2000 }), 1000, 7).reason).toMatch(/invalid age/);
  });
  it("sums sample, receipt, transport and local ages and rejects a stale total", () => {
    const fresh = motionFreshness(readout({ transportAgeS: 0.9 }), 1000.05, 7);
    expect(fresh.fresh).toBe(true);
    expect(fresh.ageS).toBeCloseTo(0.02 + 0.01 + 0.9 + 0.00005, 3);
    const stale = motionFreshness(readout({ transportAgeS: MOTION_MAX_AGE_S }), 1000, 7);
    expect(stale.fresh).toBe(false);
    expect(stale.reason).toMatch(/stale/);
  });
  it("passes through the measured values only when fresh", () => {
    const freshness = motionFreshness(readout(), 1000, 7);
    expect(freshness).toMatchObject({ fresh: true, linear: 0.12, angular: -0.2, reason: null, stale: false });
  });
});

describe("store adapter", () => {
  it("reads only telemetry.motion and ignores pose", () => {
    useTelemetryStore.setState({ motion: null, motionAt: null });
    useTelemetryStore.setState({ pose: { x: 1, y: 2, yaw: 0.5, covariance: null, frame: "map" } as never });
    expect(readMotionSnapshot().motion).toBeNull();
    useTelemetryStore.setState({ motion: sample, motionAt: 1234 });
    expect(readMotionSnapshot()).toMatchObject({ motion: sample, motionAt: 1234, transportAgeS: null });
  });
});
