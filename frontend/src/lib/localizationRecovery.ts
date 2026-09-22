import type { BaseStateData } from "../types/protocol";

// Command receipt freshness matches the server command gate, not the looser health display.
export const RECOVERY_RECEIPT_MAX_MS = 3_000;
export function localizationRecoveryReason(base: BaseStateData | null, at: number | null,
  now = performance.now()): string | null {
  const age = at === null ? NaN : now - at;
  if (!base || !Number.isFinite(age) || age < 0 || age > RECOVERY_RECEIPT_MAX_MS)
    return "Wait for fresh robot updates before sending a destination.";
  if (base.odom_epoch_valid !== true || base.localization_recovery_required !== false
      || !Number.isFinite(base.localization_seed_stamp_ns) || (base.localization_seed_stamp_ns ?? 0) <= 0)
    return "The robot is recovering its location. Wait before sending a destination.";
  return null;
}
