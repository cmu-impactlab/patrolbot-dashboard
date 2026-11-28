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
  if (!lidarHardwareReady(base, at, now)) {
    if (base === null || at === null) return "BaseState telemetry is unavailable — wait for fresh hardware readings.";
    if (!base.link_connected) return "The drive base is disconnected — LiDAR readiness is unavailable.";
    if (!base.hardware_state_valid) return "BaseState hardware readings are invalid — LiDAR readiness is unavailable.";
    return "BaseState telemetry is stale or invalid — wait for fresh hardware readings.";
  }
  if (base?.lidar_state === "OFF") return "LiDAR is off — turn it on before waiting for fresh scans.";
  if (base?.lidar_state === "STARTING") return "LiDAR is warming up — wait for fresh scans.";
  if (base?.lidar_state === "FAULT") return "LiDAR is reporting a fault — wait for a ready state.";
  if (base?.lidar_state !== "READY") return "LiDAR state is unavailable — wait for a ready state.";
  if (age === undefined || !Number.isFinite(age) || age < 0) return "LiDAR scan age is unavailable — wait for fresh scans.";
  return Number.isFinite(receipt) && receipt >= 0 && age + receipt <= 0.5
    ? null : "LiDAR scans are stale — wait for fresh scans.";
}

export function useLidarReadyReason(base: BaseStateData | null, at: number | null): string | null {
  const [now, setNow] = useState(performance.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(timer);
  }, []);
  return lidarReadyReason(base, at, Math.max(now, performance.now()));
}
