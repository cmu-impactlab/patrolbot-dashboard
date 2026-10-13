import { describe, it, expect } from "vitest";
import { lidarReadyReason } from "./lidar";
import type { BaseStateData } from "../types/protocol";
describe("LiDAR readiness", () => {
  it("refuses scans attached to invalid, disconnected or stale hardware", () => {
    const base = { lidar_state:"READY", lidar_scan_age:.1, telemetry_age:.1, hardware_state_valid:true, link_connected:true } as BaseStateData;
    for (const changes of [{link_connected:false},{hardware_state_valid:false},{telemetry_age:.5},{telemetry_age:NaN}]) {
      expect(lidarReadyReason({...base,...changes}, performance.now() - 100)).toBeTruthy();
    }
  });
  it("fails closed and ages cached scan data", () => {
    expect(lidarReadyReason(null, performance.now())).toBeTruthy();
    for (const lidar_state of ["OFF", "UNKNOWN", "STARTING", "FAULT"]) {
      expect(lidarReadyReason({ lidar_state, lidar_scan_age: 0 } as BaseStateData, performance.now())).toBeTruthy();
    }
    const ready = { link_connected:true, hardware_state_valid:true, telemetry_age:.1, lidar_state: "READY", lidar_scan_age: 0.1 } as BaseStateData;
    expect(lidarReadyReason(ready, performance.now())).toBeNull();
    expect(lidarReadyReason(ready, performance.now() - 600)).toBeTruthy();
  });
});
