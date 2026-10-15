import { MAX_BASE_STATE_AGE_MS } from "./freshness";
import type { PoseData } from "../types/protocol";

type Uncertainty =
  | { state: "unavailable" | "invalid" | "degenerate" | "stale"; detail: string }
  | { state: "usable"; positionSpreadM: number; headingSpreadDeg: number;
      varianceLimit: number | null; positionMarginPercent: number | null; headingMarginPercent: number | null };

/** Display arithmetic only; the robot's localized verdict remains authoritative. */
export function localizationUncertainty(pose: PoseData | null, receivedAt: number | null,
  now = performance.now()): Uncertainty {
  if (!pose || pose.frame_id === "odom" || !pose.planar_covariance)
    return { state: "unavailable", detail: "Unavailable: covariance not reported" };
  const covariance = pose.planar_covariance;
  if (!Array.isArray(covariance) || covariance.length !== 3 || covariance.some(value => value === null || !Number.isFinite(value)))
    return { state: "invalid", detail: "Unavailable: invalid covariance" };
  const [xx, yy, heading] = covariance as [number, number, number];
  if (xx <= 0 || yy <= 0 || heading <= 0)
    return { state: "degenerate", detail: "Unavailable: degenerate covariance" };

  const age = pose.localization_age_s;
  const maxAge = pose.localization_max_age_s;
  if (age == null || maxAge == null)
    return { state: "unavailable", detail: "Unavailable: refresh age not reported" };
  if (!Number.isFinite(age) || age < 0 || !Number.isFinite(maxAge) || maxAge <= 0)
    return { state: "invalid", detail: "Unavailable: invalid refresh age" };
  const elapsed = receivedAt === null ? Infinity : (now - receivedAt) / 1000;
  if (elapsed < 0 || Number.isNaN(elapsed))
    return { state: "invalid", detail: "Unavailable: invalid receipt time" };
  if (elapsed * 1000 > MAX_BASE_STATE_AGE_MS || age + elapsed > maxAge)
    return { state: "stale", detail: "Not refreshed recently" };

  const limit = pose.covariance_limit;
  const varianceLimit = limit != null && Number.isFinite(limit) && limit > 0 ? limit : null;
  const positionMargin = varianceLimit === null ? NaN : 100 * (1 - Math.max(xx, yy) / varianceLimit);
  const headingMargin = varianceLimit === null ? NaN : 100 * (1 - heading / varianceLimit);
  return { state: "usable", positionSpreadM: Math.sqrt(Math.max(xx, yy)),
    headingSpreadDeg: Math.sqrt(heading) * 180 / Math.PI, varianceLimit,
    positionMarginPercent: Number.isFinite(positionMargin) ? positionMargin : null,
    headingMarginPercent: Number.isFinite(headingMargin) ? headingMargin : null };
}
