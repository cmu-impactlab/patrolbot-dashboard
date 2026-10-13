import { useEffect, useState } from "react";
import type { BaseStateData } from "../types/protocol";

export function lidarHardwareReady(base: BaseStateData | null, at: number | null, now = performance.now()): boolean {
  const receipt = at === null ? NaN : (now - at) / 1000;
  const age = base?.telemetry_age ?? NaN;
  return base?.link_connected === true && base.hardware_state_valid === true &&
    Number.isFinite(age) && age >= 0 && Number.isFinite(receipt) && receipt >= 0 && age + receipt <= .5;
}

export function lidarReadyReason(base: BaseStateData | null, at: number | null, now = performance.now()): string | null {
  const age = base?.lidar_scan_age;
  const receipt = at === null ? NaN : (now - at) / 1000;
  return lidarHardwareReady(base, at, now) && base?.lidar_state === "READY" && age !== undefined && Number.isFinite(age) && age >= 0 &&
    Number.isFinite(receipt) && receipt >= 0 && age + receipt <= 0.5
    ? null : "LiDAR is off, starting, stale or unavailable — wait for fresh scans.";
}

export function useLidarReadyReason(base: BaseStateData | null, at: number | null): string | null {
  const [now, setNow] = useState(performance.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(timer);
  }, []);
  return lidarReadyReason(base, at, Math.max(now, performance.now()));
}
