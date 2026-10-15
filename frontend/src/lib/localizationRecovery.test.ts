import { describe, it, expect } from "vitest";
import { ageOnlyRefreshEligible, localizationRecoveryReason, navigationLocalizationReason } from "./localizationRecovery";
import type { BaseStateData, PoseData } from "../types/protocol";
const base = { odom_epoch_valid: true, localization_recovery_required: false,
  localization_seed_stamp_ns: 1 } as BaseStateData;
describe("recovery receipt freshness", () => {
  it.each([null, NaN, Infinity, -Infinity, 6999, 10001])("rejects missing or invalid receipt %s", at => {
    expect(localizationRecoveryReason(base, at, 10000)).not.toBeNull();
  });
  it("accepts the current contract inside the server receipt limit", () => {
    expect(localizationRecoveryReason(base, 7001, 10000)).toBeNull();
  });
});


const mappedBase = { ...base, map_id: "floor1", map_revision: "revision" };
const stalePose: PoseData = { map_id: "floor1", map_revision: "revision", frame_id: "map",
  x: 0, y: 0, yaw: 0, linear_velocity: 0, angular_velocity: 0, localized: false,
  planar_covariance: [.01, .02, .03], covariance_limit: .25, localization_age_s: 31, localization_max_age_s: 30 };
const verified = ["navigation_stationary_refresh_v1"];
describe("age-only admission to native stationary refresh", () => {
  it("requires the installed native capability and preserves the localized verdict", () => {
    expect(ageOnlyRefreshEligible(mappedBase, stalePose, 9990, [], 10000)).toBe(false);
    expect(ageOnlyRefreshEligible(mappedBase, stalePose, 9990, verified, 10000)).toBe(true);
    expect(stalePose.localized).toBe(false);
    expect(navigationLocalizationReason(mappedBase, 9990, stalePose, 9990, verified, false, 10000)).toBeNull();
  });
  it.each([
    { frame_id: "odom" }, { map_revision: "old" }, { planar_covariance: null },
    { planar_covariance: [0, .01, .01] }, { planar_covariance: [.26, .01, .01] },
    { planar_covariance: [.01, NaN, .01] }, { covariance_limit: null },
    { localization_age_s: 29 }, { localization_age_s: Infinity }, { localization_max_age_s: 0 },
  ])("refuses a non-age localization failure %s", spoiled => {
    expect(ageOnlyRefreshEligible(mappedBase, { ...stalePose, ...spoiled } as PoseData, 9990, verified, 10000)).toBe(false);
  });
  it.each([null, NaN, Infinity, 6999, 10001])("rejects unavailable/stale pose receipt %s", at => {
    expect(ageOnlyRefreshEligible(mappedBase, stalePose, at, verified, 10000)).toBe(false);
  });
  it("retains epoch/seed/recovery guards for age-only admission and overrides", () => {
    for (const spoiled of [{ odom_epoch_valid: false }, { localization_recovery_required: true }, { localization_seed_stamp_ns: 0 }]) {
      for (const override of [false, true])
        expect(navigationLocalizationReason({ ...mappedBase, ...spoiled }, 9990, stalePose, 9990, verified, override, 10000)).not.toBeNull();
    }
  });
});
