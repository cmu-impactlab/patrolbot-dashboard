import { describe, expect, it } from "vitest";
import type { MapData } from "../../types/protocol";
import { fitPoints, fitView, followView, screenToWorld, worldToScreen, zoomAt } from "./transform";

const MAP: MapData = {
  map_version: 1,
  name: "test",
  resolution: 0.1,
  width: 100,
  height: 50,
  origin: { x: -5, y: -2.5, yaw: 0 },
  rle: [[0, 5000]],
};

describe("map transform", () => {
  it("world<->screen round-trips", () => {
    const view = { zoom: 40, panX: 300, panY: 200 };
    const [sx, sy] = worldToScreen(view, 1.25, -3.5);
    const [wx, wy] = screenToWorld(view, sx, sy);
    expect(wx).toBeCloseTo(1.25);
    expect(wy).toBeCloseTo(-3.5);
  });

  it("y axis is flipped (world +y goes up on screen)", () => {
    const view = { zoom: 10, panX: 0, panY: 100 };
    const [, syLow] = worldToScreen(view, 0, 0);
    const [, syHigh] = worldToScreen(view, 0, 5);
    expect(syHigh).toBeLessThan(syLow);
  });

  it("fitView centers the map inside the canvas", () => {
    const view = fitView(MAP, 800, 600);
    // Map is 10 m x 5 m centered at (0, 0).
    const [cx, cy] = worldToScreen(view, 0, 0);
    expect(cx).toBeCloseTo(400);
    expect(cy).toBeCloseTo(300);
    // The whole map fits.
    const [left] = worldToScreen(view, MAP.origin.x, 0);
    const [right] = worldToScreen(view, MAP.origin.x + 10, 0);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(right).toBeLessThanOrEqual(800);
  });

  it("zoomAt keeps the cursor's world point fixed", () => {
    const view = { zoom: 20, panX: 100, panY: 100 };
    const [wxBefore, wyBefore] = screenToWorld(view, 250, 180);
    const zoomed = zoomAt(view, 250, 180, 1.5);
    const [wxAfter, wyAfter] = screenToWorld(zoomed, 250, 180);
    expect(wxAfter).toBeCloseTo(wxBefore);
    expect(wyAfter).toBeCloseTo(wyBefore);
    expect(zoomed.zoom).toBeCloseTo(30);
  });

  it("fitPoints frames a recorded route rather than the whole map", () => {
    const route: [number, number][] = [[10, 10], [20, 10], [20, 18]];
    const view = fitPoints(route, 800, 600);
    const [cx, cy] = worldToScreen(view, 15, 14); // the route's centre
    expect(cx).toBeCloseTo(400);
    expect(cy).toBeCloseTo(300);
    // Every waypoint lands inside the canvas...
    for (const [x, y] of route) {
      const [sx, sy] = worldToScreen(view, x, y);
      expect(sx).toBeGreaterThanOrEqual(0);
      expect(sx).toBeLessThanOrEqual(800);
      expect(sy).toBeGreaterThanOrEqual(0);
      expect(sy).toBeLessThanOrEqual(600);
    }
    // ...and much closer in than fitting a real building would have been: a
    // patrol covers a corner of the floor, not the whole of it.
    const building: MapData = { ...MAP, width: 3192, height: 2205, resolution: 0.05 };
    expect(view.zoom).toBeGreaterThan(fitView(building, 800, 600).zoom * 5);
  });

  it("fitPoints caps the zoom for a robot that barely moved", () => {
    const view = fitPoints([[3, 3], [3.02, 3.01]], 800, 600);
    expect(view.zoom).toBeLessThanOrEqual(120);
    const [cx] = worldToScreen(view, 3.01, 3.005);
    expect(cx).toBeCloseTo(400, 0);
  });

  it("fitPoints survives an empty or non-finite route", () => {
    expect(fitPoints([], 800, 600).zoom).toBeGreaterThan(0);
    const view = fitPoints([[NaN, 1], [4, 6]], 800, 600);
    const [cx, cy] = worldToScreen(view, 4, 6);
    expect(cx).toBeCloseTo(400);
    expect(cy).toBeCloseTo(300);
  });

  it("followView centers the robot", () => {
    const view = followView({ zoom: 25, panX: 0, panY: 0 }, 640, 480, 2.0, -1.0);
    const [sx, sy] = worldToScreen(view, 2.0, -1.0);
    expect(sx).toBeCloseTo(320);
    expect(sy).toBeCloseTo(240);
  });
});
