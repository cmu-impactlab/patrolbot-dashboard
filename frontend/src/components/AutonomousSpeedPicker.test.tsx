import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AutonomousSpeedPicker } from "./AutonomousSpeedPicker";

afterEach(cleanup);

const radio = (name: string) => screen.getByRole<HTMLInputElement>("radio", { name });
const speed = (name: RegExp) => screen.getByRole<HTMLInputElement>("spinbutton", { name });
const confirm = () => screen.getByRole<HTMLButtonElement>("button", { name: "Confirm and stage" });
const stageStatus = () => screen.getByRole("status").textContent;

describe("AutonomousSpeedPicker", () => {
  it("shows the proposals with Fast as the unconfirmed default and Really Fast unavailable", () => {
    const onStage = vi.fn();
    render(<AutonomousSpeedPicker onStage={onStage} />);
    expect(radio("Fast").checked).toBe(true);
    expect(screen.getByRole("radio", { name: "Fast", description: "0.22 m/s, 0.70 rad/s (default)" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Slow", description: "0.12 m/s, 0.40 rad/s" })).toBeTruthy();
    const unavailable = screen.getByRole<HTMLInputElement>("radio", {
      name: "Really Fast", description: "2.00 m/s, 0.70 rad/s (uncommissioned; unavailable)",
    });
    expect(unavailable.disabled).toBe(true);
    fireEvent.click(unavailable);
    expect(unavailable.checked).toBe(false);
    expect(onStage).not.toHaveBeenCalled();
    expect(stageStatus()).toBe("Last locally staged: none");

    fireEvent.click(confirm());
    expect(onStage).toHaveBeenCalledExactlyOnceWith({ profile: "Fast", linear_m_s: 0.22, angular_rad_s: 0.70 });
    expect(stageStatus()).toBe("Last locally staged: Fast — 0.22 m/s, 0.70 rad/s");
    expect(screen.getByText("Applied speed: not reported by this picker.")).toBeTruthy();
  });

  it("keeps the prior local stage on draft changes and confirms Slow without submitting a parent form", () => {
    const onStage = vi.fn();
    const onSubmit = vi.fn();
    render(<form onSubmit={onSubmit}><AutonomousSpeedPicker onStage={onStage} /></form>);
    fireEvent.click(confirm());
    fireEvent.click(radio("Slow"));
    expect(onStage).toHaveBeenCalledTimes(1);
    expect(stageStatus()).toBe("Last locally staged: Fast — 0.22 m/s, 0.70 rad/s");

    fireEvent.click(confirm());
    expect(onStage).toHaveBeenLastCalledWith({ profile: "Slow", linear_m_s: 0.12, angular_rad_s: 0.40 });
    expect(stageStatus()).toBe("Last locally staged: Slow — 0.12 m/s, 0.40 rad/s");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("stages the inclusive Custom caps only on explicit confirmation", () => {
    const onStage = vi.fn();
    render(<AutonomousSpeedPicker onStage={onStage} />);
    expect(screen.queryByRole("spinbutton")).toBeNull();
    fireEvent.click(radio("Custom"));
    const linear = speed(/Linear speed/);
    const angular = speed(/Angular speed/);
    expect(linear.max).toBe("0.26");
    expect(angular.max).toBe("0.7");
    fireEvent.change(linear, { target: { value: "0.26" } });
    fireEvent.change(angular, { target: { value: "0.70" } });
    fireEvent.blur(angular);
    expect(onStage).not.toHaveBeenCalled();
    fireEvent.click(confirm());
    expect(onStage).toHaveBeenCalledExactlyOnceWith({ profile: "Custom", linear_m_s: 0.26, angular_rad_s: 0.70 });
    expect(stageStatus()).toBe("Last locally staged: Custom — 0.26 m/s, 0.70 rad/s");
  });

  it.each([
    [/Linear speed/, "0.26001"],
    [/Angular speed/, "0.70001"],
    [/Linear speed/, "-0.01"],
    [/Angular speed/, ""],
    [/Linear speed/, "1e309"],
  ])("does not stage invalid Custom input %s = %s", (name, value) => {
    const onStage = vi.fn();
    render(<AutonomousSpeedPicker onStage={onStage} />);
    fireEvent.click(radio("Custom"));
    const input = speed(name);
    fireEvent.change(input, { target: { value } });
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(confirm().disabled).toBe(true);
    fireEvent.click(confirm());
    expect(onStage).not.toHaveBeenCalled();
    expect(stageStatus()).toBe("Last locally staged: none");
  });

  it("uses the latest stricter caller limits without raising either built-in ceiling", () => {
    const onStage = vi.fn();
    const { rerender } = render(<AutonomousSpeedPicker onStage={onStage}
      limits={{ max_linear_m_s: 0.15 }} />);
    expect(screen.getByText("This preset exceeds the current picker limits.")).toBeTruthy();
    expect(confirm().disabled).toBe(true);
    fireEvent.click(radio("Slow"));
    expect(confirm().disabled).toBe(false);

    fireEvent.click(radio("Custom"));
    expect(speed(/Linear speed/).max).toBe("0.15");
    expect(speed(/Angular speed/).max).toBe("0.7");
    fireEvent.change(speed(/Linear speed/), { target: { value: "0.15" } });
    expect(confirm().disabled).toBe(false);
    rerender(<AutonomousSpeedPicker onStage={onStage}
      limits={{ max_angular_rad_s: 0.55 }} />);
    expect(speed(/Linear speed/).max).toBe("0.26");
    expect(speed(/Angular speed/).max).toBe("0.55");
    expect(confirm().disabled).toBe(true);
    expect(onStage).not.toHaveBeenCalled();
    fireEvent.change(speed(/Angular speed/), { target: { value: "0.545" } });
    fireEvent.click(confirm());
    expect(onStage).toHaveBeenCalledExactlyOnceWith({ profile: "Custom", linear_m_s: 0.15, angular_rad_s: 0.545 });
    expect(stageStatus()).toBe("Last locally staged: Custom — 0.15 m/s, 0.545 rad/s");
  });

  it.each([NaN, -0.01, 0.26001, Infinity])("fails closed for a supplied linear limit of %s", (limit) => {
    const onStage = vi.fn();
    render(<AutonomousSpeedPicker onStage={onStage} limits={{ max_linear_m_s: limit }} />);
    expect(screen.getByText("Picker limits are unavailable; staging is disabled.")).toBeTruthy();
    expect(confirm().disabled).toBe(true);
    fireEvent.click(confirm());
    expect(onStage).not.toHaveBeenCalled();
  });

  it("keeps radio groups independent across instances", () => {
    render(<><AutonomousSpeedPicker onStage={vi.fn()} /><AutonomousSpeedPicker onStage={vi.fn()} /></>);
    const [first, second] = screen.getAllByRole("group", { name: "Autonomous speed" }).map((group) => within(group));
    fireEvent.click(first.getByRole("radio", { name: "Slow" }));
    expect(first.getByRole<HTMLInputElement>("radio", { name: "Slow" }).checked).toBe(true);
    expect(second.getByRole<HTMLInputElement>("radio", { name: "Fast" }).checked).toBe(true);
  });
});
