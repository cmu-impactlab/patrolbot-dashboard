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
});
