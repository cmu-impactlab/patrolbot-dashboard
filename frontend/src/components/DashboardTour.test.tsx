import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore, type AuthUser } from "../stores/authStore";
import { useLayoutStore } from "../stores/layoutStore";
import { useUiStore } from "../stores/uiStore";
import { buildTourSteps, DashboardTour, type TourStep } from "./DashboardTour";

const OPERATOR: AuthUser = {
  id: 2, username: "operator", display_name: "Operator", role: "operator", auth_mode: "oidc",
};

const VISIBLE = ["liveMap", "robotStatus", "navControls", "recordings"];

function pageTargets(steps: TourStep[]) {
  const targets = [...new Set(steps.map((step) => step.target.match(/"(.+)"/)?.[1]).filter(Boolean))];
  return (
    <>
      <main data-tour="dashboard" />
      {targets.map((target) => <div key={target} data-tour={target} />)}
    </>
  );
}

beforeEach(() => {
  useUiStore.setState({ tourActive: false, tourRequest: 0 });
  useLayoutStore.setState({ widgets: VISIBLE });
  useAuthStore.setState({ user: OPERATOR });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 20, top: 30, width: 200, height: 50, right: 220, bottom: 80,
    x: 20, y: 30, toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useUiStore.setState({ tourActive: false, tourRequest: 0 });
  useLayoutStore.setState({ widgets: [] });
  useAuthStore.setState({ user: undefined });
});

describe("dynamic tour steps", () => {
  it("includes only widgets in the active layout", () => {
    const steps = buildTourSteps(["liveMap", "alerts"], OPERATOR);
    expect(steps.some((step) => step.title === "Use the Live Map")).toBe(true);
    expect(steps.some((step) => step.title === "Review alerts")).toBe(true);
    expect(steps.some((step) => step.title === "Check the battery")).toBe(false);
    expect(steps.some((step) => step.title === "Move the robot safely")).toBe(false);
  });

  it("names missing widgets and explains the exact Add widget flow", () => {
    const step = buildTourSteps(["liveMap", "robotStatus"], OPERATOR)
      .find((item) => item.title === "Add and arrange your own widgets")!;
    expect(step.description).toContain("Battery");
    expect(step.description).toContain("Navigation");
    expect(step.instruction).toMatch(/Click Edit dashboard, then Add widget.*Click Add.*Done editing/);
  });

  it("tailors Navigation and Recordings to the signed-in role", () => {
    const widgets = ["navControls", "recordings"];
    const observer = buildTourSteps(widgets, { ...OPERATOR, role: "observer" });
    expect(observer.find((step) => step.title === "Move the robot safely")?.description)
      .toContain("Observer account");
    expect(observer.find((step) => step.title === "Record and replay sessions")?.instruction)
      .toContain("cannot start or stop");

    const administrator = buildTourSteps(widgets, { ...OPERATOR, role: "administrator" });
    expect(administrator.find((step) => step.title === "Record and replay sessions")?.instruction)
      .toContain("delete recordings");
  });

  it("always includes core header, role, customization, and restart steps", () => {
    const titles = buildTourSteps([], OPERATOR).map((step) => step.title);
    expect(titles).toContain("Confirm the robot");
    expect(titles).toContain("Your access level: Operator");
    expect(titles).toContain("Add and arrange your own widgets");
    expect(titles.at(-1)).toBe("Restart this tour any time");
  });

  it("builds a new account's Operator-preset tour from those five widgets", () => {
    const operatorPreset = ["robotStatus", "navControls", "liveMap", "battery", "alerts"];
    const steps = buildTourSteps(operatorPreset, OPERATOR);
    const titles = steps.map((step) => step.title);
    expect(titles).toContain("Move the robot safely");
    expect(titles).toContain("Check the battery");
    expect(titles).not.toContain("Record and replay sessions");
    expect(steps.find((step) => step.title === "Add and arrange your own widgets")?.description)
      .toContain("Recordings");
  });
});

describe("interactive dashboard tour", () => {
  it("spotlights targets and reports the layout-specific step count", () => {
    const steps = buildTourSteps(VISIBLE, OPERATOR);
    render(<>{pageTargets(steps)}<DashboardTour /></>);
    act(() => useUiStore.getState().startTour());
    expect(screen.getByRole("heading", { name: "Welcome to your dashboard" })).toBeTruthy();
    expect(document.querySelector(".tour-spotlight")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("heading", { name: "Confirm the robot" })).toBeTruthy();
    expect(screen.getByLabelText(`Step 2 of ${steps.length}`)).toBeTruthy();
  });

  it("supports arrow-key navigation and Escape dismissal", () => {
    const steps = buildTourSteps(VISIBLE, OPERATOR);
    render(<>{pageTargets(steps)}<DashboardTour /></>);
    act(() => useUiStore.getState().startTour());
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("heading", { name: "Confirm the robot" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByRole("heading", { name: "Welcome to your dashboard" })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(useUiStore.getState().tourActive).toBe(false);
  });

  it("rebuilds when the active layout changes", () => {
    render(<>{pageTargets(buildTourSteps(VISIBLE, OPERATOR))}<DashboardTour /></>);
    act(() => useUiStore.getState().startTour());
    act(() => useLayoutStore.setState({ widgets: ["battery"] }));
    const dynamicSteps = buildTourSteps(["battery"], OPERATOR);
    expect(screen.getByLabelText(`Step 1 of ${dynamicSteps.length}`)).toBeTruthy();
    expect(dynamicSteps.some((step) => step.title === "Check the battery")).toBe(true);
    expect(dynamicSteps.some((step) => step.title === "Use the Live Map")).toBe(false);
  });

  it("places the physical emergency-stop warning before movement actions", () => {
    const navigation = buildTourSteps(["navControls"], OPERATOR)
      .find((step) => step.title === "Move the robot safely")!;
    expect(navigation.instruction).toMatch(/physical emergency stop for emergencies/i);
  });
});
