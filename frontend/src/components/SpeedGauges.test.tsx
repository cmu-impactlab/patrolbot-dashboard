import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SpeedGauges } from "./SpeedGauges";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { MotionData } from "../types/protocol";

const limits = { max_linear_m_s: 0.3, max_angular_rad_s: 0.7 };

function motion(overrides: Partial<MotionData> = {}): MotionData {
  return {
    linear_velocity: 0.1,
    angular_velocity: -0.2,
    source: "raw_odom",
    sample_age_s: 0.01,
    receipt_age_s: 0.01,
    session_generation: 1,
    odom_epoch: "epoch-a",
    odom_epoch_generation: 1,
    ...overrides,
  };
}

function setMotion(sample: MotionData | null, at: number | null = performance.now(), generation = 1) {
  useTelemetryStore.setState({
    motion: sample,
    motionAt: sample === null ? null : at,
    baseState: { session_generation: generation } as never,
    motionTransportAgeS: 0,
  });
}

const meter = (name: string) => screen.getByRole("meter", { name });
const figureOf = (name: string) => meter(`Measured ${name} speed`).closest("figure")!;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useTelemetryStore.setState({ motion: null, motionAt: null, baseState: null });
});

describe("measured speed gauges", () => {
  it("does not flicker when motion arrives between the 500 ms clock ticks", () => {
    const clock = vi.spyOn(performance, "now").mockReturnValue(0);
    setMotion(motion(), 0);
    render(<SpeedGauges limits={limits} />);
    for (const at of [100, 200, 300, 400]) {
      clock.mockReturnValue(at);
      act(() => setMotion(motion({ linear_velocity: at / 2000 }), at));
      expect(meter("Measured linear speed").getAttribute("aria-valuenow")).toBe(String(at / 2000));
      expect(screen.queryByText(/invalid age/)).toBeNull();
    }
  });

  it("still rejects genuinely future-dated motion", () => {
    vi.spyOn(performance, "now").mockReturnValue(0);
    setMotion(motion(), 100);
    render(<SpeedGauges limits={limits} />);
    expect(meter("Measured linear speed").getAttribute("aria-valuenow")).toBeNull();
  });

  it("expires observed caps without hiding fresh measured speed", () => {
    const clock = vi.spyOn(performance, "now").mockReturnValue(0);
    setMotion(motion(), 0);
    render(<SpeedGauges limits={limits} limitsAt={0} />);
    clock.mockReturnValue(4000);
    act(() => setMotion(motion(), 4000));
    expect(meter("Measured linear speed").getAttribute("aria-valuenow")).toBe("0.1");
    expect(screen.getAllByText(/active cap unknown/)).toHaveLength(2);
  });
  it("publishes signed measured values, direction and percent of the active cap", () => {
    setMotion(motion());
    render(<SpeedGauges limits={limits} />);
    const linear = meter("Measured linear speed");
    expect(linear.getAttribute("aria-valuenow")).toBe("0.1");
    expect(linear.getAttribute("aria-valuetext")).toContain("forward");
    expect(linear.getAttribute("aria-valuetext")).toContain("33% of cap");
    const angular = meter("Measured angular speed");
    expect(angular.getAttribute("aria-valuenow")).toBe("-0.2");
    expect(angular.getAttribute("aria-valuetext")).toContain("right (CW)");
  });

  it("band-colours the whole gauge by measured magnitude", () => {
    setMotion(motion({ linear_velocity: 0.1 }));
    const { unmount } = render(<SpeedGauges limits={limits} />);
    expect(figureOf("linear").className).toContain("gauge-ok");
    unmount();

    setMotion(motion({ linear_velocity: 0.2 }));
    const warn = render(<SpeedGauges limits={limits} />);
    expect(figureOf("linear").className).toContain("gauge-warn");
    warn.unmount();

    setMotion(motion({ linear_velocity: 0.27 }));
    render(<SpeedGauges limits={limits} />);
    expect(figureOf("linear").className).toContain("gauge-danger");
  });

  it("shows an honest no-data state instead of a fabricated zero", () => {
    setMotion(null);
    render(<SpeedGauges limits={limits} />);
    expect(screen.getAllByText("No fresh speed data").length).toBeGreaterThanOrEqual(2);
    expect(meter("Measured linear speed").getAttribute("aria-valuenow")).toBeNull();
    expect(figureOf("linear").className).toContain("gauge-stale");
    expect(figureOf("linear").querySelector(".gauge-needle")).toBeNull();
  });

  it("marks stale samples and never shows their values", () => {
    setMotion(motion(), performance.now() - 3000);
    render(<SpeedGauges limits={limits} />);
    expect(meter("Measured linear speed").getAttribute("aria-valuenow")).toBeNull();
    expect(screen.getAllByText(/stale/i).length).toBeGreaterThan(0);
  });

  it("drops a sample from an older robot session", () => {
    setMotion(motion({ session_generation: 3 }), performance.now(), 4);
    render(<SpeedGauges limits={limits} />);
    expect(screen.getAllByText(/older robot session/).length).toBeGreaterThan(0);
    expect(meter("Measured angular speed").getAttribute("aria-valuenow")).toBeNull();
  });

  it("never renders NaN and flags motion over a zero active cap", () => {
    setMotion(motion({ linear_velocity: 0.1 }));
    render(<SpeedGauges limits={{ max_linear_m_s: 0, max_angular_rad_s: 0 }} />);
    expect(screen.getAllByRole("alert", { name: "Over the active cap" })).toHaveLength(2);
    expect(document.body.textContent).not.toContain("NaN");
  });

  it("shows the active cap as unknown when no snapshot limit is present", () => {
    setMotion(motion());
    render(<SpeedGauges limits={null} />);
    expect(screen.getAllByText(/active cap unknown/).length).toBe(2);
    expect(meter("Measured linear speed").getAttribute("aria-valuetext")).toContain("cap unknown");
    expect(figureOf("linear").querySelector(".gauge-needle")).toBeNull();
  });

  it("draws the value band on the same signed side as the needle", () => {
    setMotion(motion({ linear_velocity: -.15 }));
    render(<SpeedGauges limits={limits} />);
    expect(figureOf("linear").querySelector(".gauge-needle")?.getAttribute("transform")).toContain("rotate(-45.0");
    expect(figureOf("linear").querySelector("path.gauge-value")?.getAttribute("d")).toContain("0 0 0 34.75");
  });
});
