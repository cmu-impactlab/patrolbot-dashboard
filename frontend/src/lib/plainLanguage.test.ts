import { describe, expect, it } from "vitest";
import {
  CONNECTION_COPY,
  ESTIMATE_COPY,
  HEALTH_COPY,
  SEVERITY_COPY,
  STATUS_COPY,
  healthOverallText,
} from "./plainLanguage";

const ROS_JARGON = /amcl|costmap|nav2|rosbag|\/scan|\/cmd_vel|dds|initialpose/i;

describe("plain-language copy tables", () => {
  it("covers every robot status", () => {
    const statuses = [
      "ready", "navigating", "recording", "docked",
      "charging", "paused", "needs_attention", "offline",
    ] as const;
    for (const status of statuses) {
      expect(STATUS_COPY[status]?.label, status).toBeTruthy();
    }
  });

  it("covers every connection state, health level and severity", () => {
    for (const state of ["online", "stale", "offline"] as const) {
      expect(CONNECTION_COPY[state]?.label).toBeTruthy();
      expect(CONNECTION_COPY[state]?.description).toBeTruthy();
    }
    for (const level of ["healthy", "warning", "fault", "offline"] as const) {
      expect(HEALTH_COPY[level]?.label).toBeTruthy();
    }
    for (const severity of ["info", "warning", "critical"] as const) {
      expect(SEVERITY_COPY[severity]?.label).toBeTruthy();
    }
  });

  it("covers every battery estimate state", () => {
    for (const state of ["charging", "calculating", "stable", "estimated", "threshold_reached", "unavailable"]) {
      expect(ESTIMATE_COPY[state], state).toBeTruthy();
    }
  });

  it("contains no ROS jargon in user-facing text", () => {
    const all = [
      ...Object.values(STATUS_COPY).map((copy) => copy.label),
      ...Object.values(CONNECTION_COPY).flatMap((copy) => [copy.label, copy.description]),
      ...Object.values(HEALTH_COPY).map((copy) => copy.label),
      ...Object.values(ESTIMATE_COPY),
    ];
    for (const text of all) {
      expect(text).not.toMatch(ROS_JARGON);
    }
  });

  it("overall health text is grammatical", () => {
    expect(healthOverallText("healthy", 0)).toBe("All systems operational");
    expect(healthOverallText("warning", 1)).toBe("1 system needs attention");
    expect(healthOverallText("fault", 2)).toBe("2 systems need attention");
  });
});
