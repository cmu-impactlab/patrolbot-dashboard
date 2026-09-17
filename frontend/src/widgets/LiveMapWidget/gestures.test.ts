import { describe, expect, it } from "vitest";
import { MapGesture } from "./gestures";
import { screenToWorld } from "./transform";

describe("shared map gestures", () => {
  it("does not select after cancellation or an unknown pointer release", () => {
    const gesture = new MapGesture();
    gesture.down(1, { x: 10, y: 20 });
    expect(gesture.up(2)).toBe(false);
    gesture.cancel();
    expect(gesture.up(1)).toBe(false);
  });
  it("a second finger makes every remaining release non-selecting", () => {
    const gesture = new MapGesture();
    gesture.down(1, { x: 10, y: 20 });
    gesture.down(2, { x: 30, y: 20 });
    expect(gesture.up(2)).toBe(false);
    expect(gesture.up(1)).toBe(false);
    gesture.down(3, { x: 5, y: 6 });
    expect(gesture.up(3)).toBe(true);
  });
  it("pinches around the midpoint while following its translation", () => {
    const gesture = new MapGesture();
    gesture.down(1, { x: 10, y: 20 });
    gesture.down(2, { x: 30, y: 20 });
    const view = { zoom: 10, panX: 0, panY: 0 };
    const world = screenToWorld(view, 20, 20);
    const next = gesture.move(2, { x: 50, y: 20 }, view);
    expect(next.zoom).toBe(20);
    expect(screenToWorld(next, 30, 20)).toEqual(world);
    gesture.up(2);
    expect(gesture.move(1, { x: 12, y: 23 }, next)).toEqual({ ...next, panX: next.panX + 2, panY: next.panY + 3 });
  });
});
