import { describe, it, expect } from "vitest";
import { lidarHardwareReady, lidarReadyReason } from "./lidar";
import type { BaseStateData } from "../types/protocol";

const ready = { lidar_state: "READY", lidar_scan_age: .1, telemetry_age: .1,
  hardware_state_valid: true, link_connected: true } as BaseStateData;
const unavailable = "BaseState telemetry is unavailable — wait for fresh hardware readings.";
const staleBase = "BaseState telemetry is stale or invalid — wait for fresh hardware readings.";

describe("LiDAR readiness", () => {
  it("distinguishes missing BaseState and receipt from invalid or stale hardware before scanner state", () => {
    expect(lidarReadyReason(null, 1000, 1000)).toBe(unavailable);
    expect(lidarReadyReason(ready, null, 1000)).toBe(unavailable);
    for (const [changes, reason] of [
      [{ link_connected: false }, "The drive base is disconnected — LiDAR readiness is unavailable."],
      [{ hardware_state_valid: false }, "BaseState hardware readings are invalid — LiDAR readiness is unavailable."],
      [{ telemetry_age: .501 }, staleBase],
      [{ telemetry_age: NaN }, staleBase],
      [{ telemetry_age: -1 }, staleBase],
    ] as const) {
      expect(lidarReadyReason({ ...ready, lidar_state: "OFF", ...changes }, 1000, 1000)).toBe(reason);
    }
    for (const at of [399, 1001, NaN, Infinity]) {
      expect(lidarReadyReason(ready, at, 1000)).toBe(staleBase);
    }
  });

  it.each([
    ["OFF", "LiDAR is off — turn it on before waiting for fresh scans."],
    ["STARTING", "LiDAR is warming up — wait for fresh scans."],
    ["UNKNOWN", "LiDAR state is unavailable — wait for a ready state."],
    ["FAULT", "LiDAR is reporting a fault — wait for a ready state."],
  ] as const)("explains %s before unavailable scan age", (lidar_state, reason) => {
    expect(lidarReadyReason({ ...ready, lidar_state, lidar_scan_age: -1 }, 1000, 1000)).toBe(reason);
  });

  it("distinguishes unknown scan age from measured stale scans and counts receipt age", () => {
    for (const lidar_scan_age of [undefined, -1, NaN, Infinity, -Infinity]) {
      expect(lidarReadyReason({ ...ready, lidar_scan_age } as BaseStateData, 1000, 1000))
        .toBe("LiDAR scan age is unavailable — wait for fresh scans.");
    }
    expect(lidarReadyReason({ ...ready, lidar_scan_age: .5 }, 1000, 1000)).toBeNull();
    expect(lidarReadyReason({ ...ready, lidar_scan_age: .4 }, 900, 1000)).toBeNull();
    expect(lidarReadyReason({ ...ready, lidar_scan_age: .4 }, 899, 1000))
      .toBe("LiDAR scans are stale — wait for fresh scans.");
    expect(lidarReadyReason({ ...ready, telemetry_age: .4 }, 900, 1000)).toBeNull();
    expect(lidarReadyReason({ ...ready, telemetry_age: .4 }, 899, 1000)).toBe(staleBase);
  });

  it("preserves the original admission predicate across states and age edge cases", () => {
    for (const lidar_state of ["READY", "OFF", "STARTING", "UNKNOWN", "FAULT"] as const) {
      for (const lidar_scan_age of [undefined, -1, 0, .5, .501, NaN, Infinity]) {
        for (const telemetry_age of [-1, 0, .5, .501, NaN, Infinity]) {
          for (const at of [null, 0, 500, 1000, 1001, NaN, Infinity]) {
            const base = { ...ready, lidar_state, lidar_scan_age, telemetry_age } as BaseStateData;
            const receipt = at === null ? NaN : (1000 - at) / 1000;
            const allowed = lidarHardwareReady(base, at, 1000) && base.lidar_state === "READY" &&
              lidar_scan_age !== undefined && Number.isFinite(lidar_scan_age) && lidar_scan_age >= 0 &&
              Number.isFinite(receipt) && receipt >= 0 && lidar_scan_age + receipt <= .5;
            expect(lidarReadyReason(base, at, 1000) === null).toBe(allowed);
          }
        }
      }
    }
  });
});
