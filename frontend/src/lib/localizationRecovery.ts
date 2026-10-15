import type { BaseStateData, PoseData } from "../types/protocol";

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


export function ageOnlyRefreshEligible(base: BaseStateData | null, pose: PoseData | null,
  poseAt: number | null, capabilities: string[], now = performance.now()): boolean {
  const receipt = poseAt === null ? NaN : now - poseAt;
  const limit = pose?.covariance_limit;
  const age = pose?.localization_age_s;
  const maxAge = pose?.localization_max_age_s;
  return capabilities.includes("navigation_stationary_refresh_v1") && !!base && !!pose &&
    pose.localized === false && pose.frame_id === "map" &&
    !!base.map_id && !!base.map_revision && pose.map_id === base.map_id && pose.map_revision === base.map_revision &&
    Number.isFinite(receipt) && receipt >= 0 && receipt <= RECOVERY_RECEIPT_MAX_MS &&
    typeof limit === "number" && Number.isFinite(limit) && limit > 0 &&
    Array.isArray(pose.planar_covariance) && pose.planar_covariance.length === 3 &&
    pose.planar_covariance.every(value => typeof value === "number" && Number.isFinite(value) && value > 0 && value <= limit) &&
    typeof age === "number" && Number.isFinite(age) && typeof maxAge === "number" && Number.isFinite(maxAge) &&
    maxAge > 0 && age > maxAge;
}

export function navigationLocalizationReason(base: BaseStateData | null, baseAt: number | null,
  pose: PoseData | null, poseAt: number | null, capabilities: string[], allowUnlocalized: boolean,
  now = performance.now()): string | null {
  const recovery = localizationRecoveryReason(base, baseAt, now);
  if (recovery) return recovery;
  if (allowUnlocalized) return null;
  const receipt = poseAt === null ? NaN : now - poseAt;
  if (pose && Number.isFinite(receipt) && receipt >= 0 && receipt <= RECOVERY_RECEIPT_MAX_MS &&
      (pose.localized === true || ageOnlyRefreshEligible(base, pose, poseAt, capabilities, now))) return null;
  return "The robot does not have usable localization. Set its location or wait for fresh updates.";
}
