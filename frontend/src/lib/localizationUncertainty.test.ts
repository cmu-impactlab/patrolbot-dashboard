import { describe, expect, it } from "vitest";
import { localizationUncertainty } from "./localizationUncertainty";
import type { PoseData } from "../types/protocol";

const pose: PoseData = {
  frame_id: "map", x: 0, y: 0, yaw: 0, linear_velocity: 0, angular_velocity: 0,
  localized: false, planar_covariance: [0.2238, 0.2453, 0.0683],
  covariance_limit: 0.25, localization_age_s: 0.2, localization_max_age_s: 30,
};

describe("localization uncertainty display contract", () => {
  it("uses independent variance units, not the mixed-unit trace or localized verdict", () => {
    const value = localizationUncertainty({ ...pose, covariance_trace: 999 }, 1000, 1100);
    expect(value.state).toBe("usable");
    if (value.state !== "usable") throw new Error("expected usable covariance");
    expect(value.positionSpreadM).toBeCloseTo(Math.sqrt(0.2453));
    expect(value.headingSpreadDeg).toBeCloseTo(Math.sqrt(0.0683) * 180 / Math.PI);
    expect(value.positionMarginPercent).toBeCloseTo(1.88);
    expect(value.headingMarginPercent).toBeCloseTo(72.68);
    expect(pose.localized).toBe(false);
  });

  it("keeps negative over-limit margins and the exact zero-margin boundary", () => {
    const value = localizationUncertainty({ ...pose, planar_covariance: [0.5, 0.1, 0.25] }, 1000, 1100);
    expect(value.state).toBe("usable");
    if (value.state !== "usable") throw new Error("expected usable covariance");
    expect(value.positionMarginPercent).toBe(-100);
    expect(value.headingMarginPercent).toBe(0);
  });

  it.each([undefined, null])("makes legacy covariance %s unavailable", planar_covariance => {
    expect(localizationUncertainty({ ...pose, planar_covariance }, 1000, 1100).state).toBe("unavailable");
  });

  it("never derives a filter estimate from odometry or an empty pose", () => {
    expect(localizationUncertainty({ ...pose, frame_id: "odom" }, 1000, 1100).state).toBe("unavailable");
    expect(localizationUncertainty(null, 1000, 1100).state).toBe("unavailable");
  });

  it("refuses values that JSON validation could have coerced", () => {
    const malformed = { ...pose, planar_covariance: ["0.1", 0.1, 0.1] } as unknown as PoseData;
    expect(localizationUncertainty(malformed, 1000, 1100).state).toBe("invalid");
    expect(localizationUncertainty({ ...pose, planar_covariance: "0.1" } as unknown as PoseData,
      1000, 1100).state).toBe("invalid");
  });

  it.each([null, NaN, Infinity])("rejects an invalid planar entry %s", bad => {
    const value = localizationUncertainty({ ...pose, planar_covariance: [0.1, bad, 0.1] }, 1000, 1100);
    expect(value).toEqual({ state: "invalid", detail: "Unavailable: invalid covariance" });
  });

  it.each([0, -1e-9])("refuses degenerate diagonal %s without inventing confidence", bad => {
    expect(localizationUncertainty({ ...pose, planar_covariance: [0.1, 0.1, bad] }, 1000, 1100))
      .toEqual({ state: "degenerate", detail: "Unavailable: degenerate covariance" });
  });

  it("ages the AMCL refresh independently from a fresh telemetry receipt", () => {
    expect(localizationUncertainty({ ...pose, localization_age_s: 30 }, 1000, 1000).state).toBe("usable");
    expect(localizationUncertainty({ ...pose, localization_age_s: 30 }, 1000, 1001).state).toBe("stale");
    expect(localizationUncertainty(pose, 1000, 16001).state).toBe("stale");
    expect(localizationUncertainty(pose, null, 1000).state).toBe("stale");
  });

  it.each([undefined, null, -1, Infinity])("suppresses numbers for missing or invalid refresh age %s", localization_age_s => {
    expect(localizationUncertainty({ ...pose, localization_age_s }, 1000, 1100).state).not.toBe("usable");
  });

  it.each([undefined, null, 0, -1, Infinity])("omits only margins for an unusable threshold %s", covariance_limit => {
    const value = localizationUncertainty({ ...pose, covariance_limit }, 1000, 1100);
    expect(value.state).toBe("usable");
    if (value.state !== "usable") throw new Error("expected usable covariance");
    expect(value.positionMarginPercent).toBeNull();
    expect(value.headingMarginPercent).toBeNull();
  });

  it("does not display arithmetic overflow as a numeric margin", () => {
    const value = localizationUncertainty({ ...pose, planar_covariance: [1e308, 1e308, 1e308],
      covariance_limit: 1e-308 }, 1000, 1100);
    expect(value.state).toBe("usable");
    if (value.state !== "usable") throw new Error("expected finite spread");
    expect(Number.isFinite(value.positionSpreadM)).toBe(true);
    expect(Number.isFinite(value.headingSpreadDeg)).toBe(true);
    expect(value.positionMarginPercent).toBeNull();
    expect(value.headingMarginPercent).toBeNull();
  });
});
