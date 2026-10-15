import { describe, it, expect } from "vitest";
import { localizationRecoveryReason } from "./localizationRecovery";
import type { BaseStateData } from "../types/protocol";
const base = { odom_epoch_valid: true, localization_recovery_required: false,
  localization_seed_stamp_ns: 1 } as BaseStateData;
describe("recovery receipt freshness", () => {
  it.each([null, NaN, Infinity, -Infinity, 6999, 10001])("rejects missing or invalid receipt %s", at => {
    expect(localizationRecoveryReason(base, at, 10000)).not.toBeNull();
  });
  it("accepts the current contract inside the server receipt limit", () => {
    expect(localizationRecoveryReason(base, 7001, 10000)).toBeNull();
  });
  it.each([
    { odom_epoch_valid: false }, { odom_epoch_valid: undefined },
    { localization_recovery_required: true },
    { localization_seed_stamp_ns: 0 },
  ])("blocks navigation during recovery without the override: %o", fields => {
    expect(localizationRecoveryReason({ ...base, ...fields }, 7001, 10000)).not.toBeNull();
  });
  it.each([
    { odom_epoch_valid: false },
    { localization_recovery_required: true },
    { localization_seed_stamp_ns: 0 },
  ])("override waives the recovery triple but not stale receipt: %o", fields => {
    expect(localizationRecoveryReason({ ...base, ...fields }, 7001, 10000, true)).toBeNull();
    expect(localizationRecoveryReason({ ...base, ...fields }, null, 10000, true)).not.toBeNull();
  });
});
