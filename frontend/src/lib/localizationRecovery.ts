import type { BaseStateData } from "../types/protocol";

// Command receipt freshness matches the server command gate, not the looser health display.
export const RECOVERY_RECEIPT_MAX_MS = 3_000;
export function localizationRecoveryReason(base: BaseStateData | null, at: number | null,
  now = performance.now(), allowUnlocalized = false): string | null {
  const age = at === null ? NaN : now - at;
  if (!base || !Number.isFinite(age) || age < 0 || age > RECOVERY_RECEIPT_MAX_MS)
    return "Wait for fresh robot updates before sending a destination.";
  // The Advanced override waives the recovery triple (epoch / recovery flag /
  // seed barrier) together with the localized verdict — the operator accepts
  // planning from an untrusted fix. Receipt freshness above still applies:
  // "unsure where it is" is waivable, "no longer saying where it is" is not.
  if (allowUnlocalized) return null;
  if (base.odom_epoch_valid !== true || base.localization_recovery_required !== false
      || !Number.isFinite(base.localization_seed_stamp_ns) || (base.localization_seed_stamp_ns ?? 0) <= 0)
    return "The robot is recovering its location. Wait before sending a destination.";
  return null;
}
