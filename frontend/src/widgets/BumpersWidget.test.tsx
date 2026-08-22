/**
 * The diagram must be the robot's real shape, drawn front-up: taller than it
 * is wide (510 mm front-to-back vs 426 mm across), in proportion, and inside
 * the viewBox. The bug this pins down is the axes being swapped, which makes
 * a squat wide robot that reads as a completely different machine.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BumpersWidget } from "./BumpersWidget";
import { ROBOT_LENGTH_M, ROBOT_WIDTH_M } from "../lib/robotGeometry";
import { useTelemetryStore } from "../stores/telemetryStore";

const VIEWBOX_W = 200;
const VIEWBOX_H = 240;

/** Every coordinate pair in an SVG path's "M x y L x y …" data. */
function pathPoints(d: string): [number, number][] {
  const numbers = d.match(/-?\d+(\.\d+)?/g)?.map(Number) ?? [];
  const points: [number, number][] = [];
  for (let i = 0; i + 1 < numbers.length; i += 2) points.push([numbers[i], numbers[i + 1]]);
  return points;
}

function bodyPoints(container: HTMLElement): [number, number][] {
  // The chassis is the only path with all eight octagon vertices.
  const paths = Array.from(container.querySelectorAll("path"));
  const body = paths.find((p) => pathPoints(p.getAttribute("d") ?? "").length === 8);
  if (!body) throw new Error("no 8-vertex chassis path found");
  return pathPoints(body.getAttribute("d") ?? "");
}

describe("BumpersWidget robot diagram", () => {
  afterEach(cleanup);

  it("draws the chassis longer front-to-back than it is wide", () => {
    const { container } = render(<BumpersWidget />);
    const points = bodyPoints(container);
    const xs = points.map(([x]) => x);
    const ys = points.map(([, y]) => y);

    const widthPx = Math.max(...xs) - Math.min(...xs);
    const lengthPx = Math.max(...ys) - Math.min(...ys); // FRONT is up
    expect(lengthPx).toBeGreaterThan(widthPx);
  });

  it("keeps the real 510:426 aspect ratio", () => {
    const { container } = render(<BumpersWidget />);
    const points = bodyPoints(container);
    const xs = points.map(([x]) => x);
    const ys = points.map(([, y]) => y);

    const widthPx = Math.max(...xs) - Math.min(...xs);
    const lengthPx = Math.max(...ys) - Math.min(...ys);
    expect(lengthPx / widthPx).toBeCloseTo(ROBOT_LENGTH_M / ROBOT_WIDTH_M, 2);
  });

  it("keeps the whole drawing, bumper panels included, inside the viewBox", () => {
    const { container } = render(<BumpersWidget />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe(`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`);

    const all = Array.from(container.querySelectorAll("path"))
      .flatMap((p) => pathPoints(p.getAttribute("d") ?? ""));
    for (const [x, y] of all) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(VIEWBOX_W);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(VIEWBOX_H);
    }

    // The wheel rectangles too.
    for (const rect of Array.from(container.querySelectorAll("rect"))) {
      const x = Number(rect.getAttribute("x"));
      const y = Number(rect.getAttribute("y"));
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x + Number(rect.getAttribute("width"))).toBeLessThanOrEqual(VIEWBOX_W);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y + Number(rect.getAttribute("height"))).toBeLessThanOrEqual(VIEWBOX_H);
    }
  });

  it("puts the wheels on the side faces, symmetric about the centreline", () => {
    const { container } = render(<BumpersWidget />);
    const rects = Array.from(container.querySelectorAll("rect"));
    expect(rects).toHaveLength(2);

    const centres = rects
      .map((r) => Number(r.getAttribute("x")) + Number(r.getAttribute("width")) / 2)
      .sort((a, b) => a - b);
    expect((centres[0] + centres[1]) / 2).toBeCloseTo(VIEWBOX_W / 2, 6);

    // Each wheel straddles its side face, not the front or rear.
    const points = bodyPoints(container);
    const halfWidth = Math.max(...points.map(([x]) => x)) - VIEWBOX_W / 2;
    expect(Math.abs(centres[1] - VIEWBOX_W / 2)).toBeCloseTo(halfWidth, 6);
  });

  it("draws each bumper group as one continuous solid bar", () => {
    const { container } = render(<BumpersWidget />);
    const bars = Array.from(container.querySelectorAll<SVGPathElement>("[data-bumper]"));

    expect(bars).toHaveLength(2);
    expect(bars.map((bar) => bar.getAttribute("data-bumper"))).toEqual(["front", "rear"]);
    for (const bar of bars) {
      // Four connected points make the three facets one bar. There must be no
      // dash pattern that turns an unknown bumper into separate bubbles.
      expect(pathPoints(bar.getAttribute("d") ?? "")).toHaveLength(4);
      expect(bar.getAttribute("stroke-dasharray")).toBeNull();
      expect(bar.getAttribute("stroke-linecap")).toBe("round");
      expect(bar.getAttribute("stroke-linejoin")).toBe("round");
    }
  });
});

describe("bumper readings the robot cannot make", () => {
  afterEach(cleanup);

  function setBaseState(overrides: Record<string, unknown> | null) {
    useTelemetryStore.setState({
      connection: { state: "online", last_seen: null },
      baseStateAt: overrides === null ? null : performance.now(),
      baseState: overrides === null ? null : ({
        session_generation: 1,
        link_connected: true,
        telemetry_age: 0.1,
        hardware_state_valid: true,
        charge_state: "not_charging",
        motors_enabled: true,
        estop_pressed: false,
        fault_flags: 0,
        stall_value: 0,
        bumpers_front: false,
        bumpers_rear: false,
        ...overrides,
      } as never),
    });
  }

  it("says Unknown when the robot never vouches for the readings", () => {
    // Silence is not an all-clear from a safety sensor.
    setBaseState({});
    const { getAllByText, queryByText } = render(<BumpersWidget />);
    expect(getAllByText("Unknown")).toHaveLength(2);
    expect(queryByText("Clear")).toBeNull();
  });

  it("says Unknown once the drive base stops reporting", () => {
    // The Pi keeps the socket alive with the drive base switched off, so a
    // valid "clear" frame would otherwise stay on screen indefinitely.
    setBaseState({ bumpers_valid: true });
    useTelemetryStore.setState({ baseStateAt: performance.now() - 60_000 });
    const { getAllByText } = render(<BumpersWidget />);
    expect(getAllByText("Unknown")).toHaveLength(2);
  });

  it("says Unknown rather than Clear when the readings are invalid", () => {
    // The drive base zeroes its bumpers when it cannot read them. Rendering
    // that as "Clear" is an all-clear from a sensor nothing could read.
    setBaseState({ bumpers_valid: false });
    const { getAllByText, queryByText } = render(<BumpersWidget />);
    expect(getAllByText("Unknown")).toHaveLength(2);
    expect(queryByText("Clear")).toBeNull();
  });

  it("explains that the operator has to look themselves", () => {
    setBaseState({ bumpers_valid: false });
    const { getByText } = render(<BumpersWidget />);
    expect(getByText(/cannot confirm the robot's bumper readings/)).toBeTruthy();
  });

  it("does not show a pressed bumper as pressed when it cannot be read", () => {
    setBaseState({ bumpers_valid: false, bumpers_rear: true });
    const { queryByText, getAllByText } = render(<BumpersWidget />);
    expect(queryByText("PRESSED")).toBeNull();
    expect(getAllByText("Unknown")).toHaveLength(2);
  });

  it("still reads Clear for a robot currently vouching for them", () => {
    setBaseState({ bumpers_valid: true });
    const { getAllByText } = render(<BumpersWidget />);
    expect(getAllByText("Clear")).toHaveLength(2);
  });

  it("keeps the continuous bar shape when a bumper is pressed", () => {
    setBaseState({ bumpers_valid: true, bumpers_front: true });
    const { container } = render(<BumpersWidget />);
    const front = container.querySelector<SVGPathElement>('[data-bumper="front"]');

    expect(front?.getAttribute("data-state")).toBe("pressed");
    expect(front?.classList.contains("bumper-hit")).toBe(true);
    expect(pathPoints(front?.getAttribute("d") ?? "")).toHaveLength(4);
  });
});
