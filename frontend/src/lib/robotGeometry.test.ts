/**
 * Guards the footprint against drift from the robot's real dimensions.
 *
 * The polygon here must stay identical to the `footprint` Nav2 plans with in
 * patrolbot-repo's nav2_params.yaml. If someone nudges a vertex to make a
 * drawing look nicer, these fail — the dashboard showing a different shape
 * than the planner uses is exactly the bug worth preventing.
 */
import { describe, expect, it } from "vitest";
import {
  ROBOT_FOOTPRINT_M, ROBOT_LENGTH_M, ROBOT_SWING_RADIUS_M, ROBOT_WIDTH_M,
  WHEEL_Y_M, traceFootprint,
} from "./robotGeometry";

/** Verbatim from patrolbot_navigation/config/nav2_params.yaml. */
const NAV2_FOOTPRINT = [
  [0.255, 0.138], [0.197, 0.213], [-0.197, 0.213], [-0.255, 0.138],
  [-0.255, -0.138], [-0.197, -0.213], [0.197, -0.213], [0.255, -0.138],
];

describe("robot footprint", () => {
  it("is the same set of vertices Nav2 collision-checks with", () => {
    const sort = (points: readonly (readonly number[])[]) =>
      [...points].map((p) => `${p[0]},${p[1]}`).sort();
    expect(sort(ROBOT_FOOTPRINT_M)).toEqual(sort(NAV2_FOOTPRINT));
  });

  it("matches the ARIA RobotLength of 510 mm", () => {
    const xs = ROBOT_FOOTPRINT_M.map(([x]) => x);
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(ROBOT_LENGTH_M, 3);
    expect(ROBOT_LENGTH_M).toBeCloseTo(0.51, 3);
  });

  it("matches the ARIA RobotWidth of ~425 mm", () => {
    const ys = ROBOT_FOOTPRINT_M.map(([, y]) => y);
    expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(ROBOT_WIDTH_M, 3);
    expect(ROBOT_WIDTH_M).toBeCloseTo(0.426, 3);
  });

  it("is longer than it is wide — the axes are not swapped", () => {
    expect(ROBOT_LENGTH_M).toBeGreaterThan(ROBOT_WIDTH_M);
  });

  it("puts every corner on the User's Guide 0.29 m swing circle", () => {
    for (const [x, y] of ROBOT_FOOTPRINT_M) {
      expect(Math.hypot(x, y)).toBeCloseTo(ROBOT_SWING_RADIUS_M, 2);
    }
  });

  it("is a closed convex octagon", () => {
    expect(ROBOT_FOOTPRINT_M).toHaveLength(8);
    // Every cross product has the same sign in a convex traversal.
    const signs = ROBOT_FOOTPRINT_M.map((_, i) => {
      const a = ROBOT_FOOTPRINT_M[i];
      const b = ROBOT_FOOTPRINT_M[(i + 1) % 8];
      const c = ROBOT_FOOTPRINT_M[(i + 2) % 8];
      const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
      return Math.sign(cross);
    });
    expect(new Set(signs).size).toBe(1);
  });

  it("puts the drive wheels on the flat side faces", () => {
    const sideY = Math.max(...ROBOT_FOOTPRINT_M.map(([, y]) => y));
    expect(WHEEL_Y_M).toBeCloseTo(sideY, 3);
  });
});

describe("traceFootprint", () => {
  it("scales to pixels per metre and flips y for canvas coordinates", () => {
    const calls: [number, number][] = [];
    const ctx = {
      beginPath: () => {},
      closePath: () => {},
      moveTo: (x: number, y: number) => calls.push([x, y]),
      lineTo: (x: number, y: number) => calls.push([x, y]),
    } as unknown as CanvasRenderingContext2D;

    traceFootprint(ctx, 100);

    expect(calls).toHaveLength(8);
    // First vertex is the front-right corner: +x forward, -y right, y flipped.
    expect(calls[0]).toEqual([25.5, 13.8]);
    // At 100 px/m the drawn shape is 51 px long and 42.6 px wide.
    const xs = calls.map(([x]) => x);
    const ys = calls.map(([, y]) => y);
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(51, 6);
    expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(42.6, 6);
  });
});
