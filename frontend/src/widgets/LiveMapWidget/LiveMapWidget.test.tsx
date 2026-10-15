import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveMapWidget, poseNeedsUncertaintyRing } from ".";
import { registerCommandSender, useCommandStore } from "../../stores/commandStore";
import { useTelemetryStore } from "../../stores/telemetryStore";
import { useAuthStore } from "../../stores/authStore";
import type { BaseStateData } from "../../types/protocol";
import { useUiStore } from "../../stores/uiStore";

vi.mock("../../api/queries", () => ({ useMapQuery: (_version: number, id: string) => ({ data: {
  map_id: id, map_revision: id === "cmuq-floor1" ? "test-first" : "test-revision", map_version: 1, name: "Test", width: 100, height: 100, resolution: 0.05,
  origin: { x: -5, y: 7, yaw: 0 }, rle: [[0, 10000]],
} }), useMapsQuery: () => ({ data: [{ map_id: "cmuq-floor1", map_revision: "test-first", name: "Floor 1" }, { map_id: "cmuq-floor2", map_revision: "test-revision", name: "Floor 2" }] }) }));
vi.mock("./bitmap", () => ({ buildMapBitmap: () => document.createElement("canvas") }));
let paint: FrameRequestCallback;
const sender = vi.fn((_frame: string) => true);
beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { paint = callback; return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  class TestPointer extends MouseEvent {
    pointerId: number; pointerType: string;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init); this.pointerId = init.pointerId ?? 1; this.pointerType = init.pointerType ?? "touch";
    }
  }
  vi.stubGlobal("PointerEvent", TestPointer);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => new Proxy({}, {
    get: () => vi.fn(), set: () => true,
  }) as CanvasRenderingContext2D);
  Element.prototype.setPointerCapture = vi.fn();
  sender.mockClear(); registerCommandSender(sender);
  useTelemetryStore.setState({ wsConnected: true, connection: { state: "online" }, pose: null, poseSetThisSession: true, baseStateAt: performance.now(),
    baseState: { lidar_state: "READY", lidar_scan_age: .1, link_connected:true, hardware_state_valid:true, telemetry_age:.1 } as BaseStateData });
  useAuthStore.setState({ user: { id: 1, username: "test", display_name: "Test", role: "operator", auth_mode: "local" } });
  useCommandStore.setState({ active: null, lidarPending: null, lidarRecoveryRequired: false, lidarStateAfter: null, pickMode: "initialpose", lastResult: null });
  useUiStore.setState({ fullscreenWidget: null, followRobot: false });
});

describe("localization uncertainty display", () => {
  it("follows the bridge localized verdict rather than covariance trace", () => {
    expect(poseNeedsUncertaintyRing({ localized: true, covariance_trace: 0.5374 })).toBe(false);
    expect(poseNeedsUncertaintyRing({ localized: false, covariance_trace: 0.02 })).toBe(true);
    expect(poseNeedsUncertaintyRing({ covariance_trace: 0.9 })).toBe(false);
  });
});

describe("planned path rendering", () => {
  it.each(["current", "previous", "untagged"])("draws only valid current paths (%s)", command => {
    const lineTo = vi.fn();
    let canvas: HTMLCanvasElement;
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(() => new Proxy({}, {
      get: (_target, key) => key === "canvas" ? canvas : key === "lineTo" ? lineTo : vi.fn(),
      set: () => true,
    }) as CanvasRenderingContext2D);
    useCommandStore.setState({ pickMode: "none", active: { commandId: "current",
      command: "navigate_to_pose", phase: "running", stage: null, distanceRemaining: null } });
    useTelemetryStore.setState({ pose: null, lastKnownPose: null, lidar: null, trajectory: [],
      path: { map_id: "cmuq-floor2", map_revision: "test-revision", points: [[-4, 8], [-3, 9]],
        command_id: command, path_valid: command !== "untagged" } });
    const view = render(<LiveMapWidget />);
    canvas = view.container.querySelector("canvas")!;
    Object.defineProperties(canvas, { clientWidth: { value: 400 }, clientHeight: { value: 400 } });
    act(() => paint(0));
    expect(lineTo).toHaveBeenCalledTimes(command === "current" ? 1 : 0);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); registerCommandSender(null); });
function mount() {
  const { container } = render(<LiveMapWidget />);
  const canvas = container.querySelector("canvas")!;
  Object.defineProperties(canvas, { clientWidth: { value: 400 }, clientHeight: { value: 400 } });
  act(() => paint(0));
  return canvas;
}
const down = (canvas: Element, id = 1, pointerType = "touch") => fireEvent.pointerDown(canvas, { pointerId: id, pointerType, clientX: 150, clientY: 150 });
const up = (canvas: Element, id = 1, pointerType = "touch") => fireEvent.pointerUp(canvas, { pointerId: id, pointerType, clientX: 150, clientY: 150 });
describe("live map command boundary", () => {
  it("sends an explicit measured profile with world coordinates, image Y and heading", () => {
    const canvas = mount(); down(canvas); up(canvas);
    fireEvent.click(screen.getByLabelText(/Measured position and heading/));
    fireEvent.change(screen.getByLabelText("Heading"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Set location" }));
    const data = JSON.parse(sender.mock.calls[0][0]).data;
    expect(data.seed_profile).toBe("measured");
    expect(data.goal).toMatchObject({ map_id: "cmuq-floor2", map_revision: "test-revision", x: -3.19, y: 10.19 });
    expect(data.goal.yaw).toBeCloseTo(Math.PI / 2);
  });
  it("keeps the coarse profile omitted and resets measured intent after a send", () => {
    const canvas = mount(); down(canvas); up(canvas);
    fireEvent.click(screen.getByRole("button", { name: "Set location" }));
    expect(JSON.parse(sender.mock.calls[0][0]).data.seed_profile).toBeUndefined();
  });
  it("supports measured mouse selection and preserves screen-up heading", () => {
    const canvas = mount();
    fireEvent.click(screen.getByLabelText(/Measured position and heading/));
    down(canvas, 1, "mouse");
    fireEvent.pointerMove(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 100 });
    fireEvent.pointerUp(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 100 });
    const data = JSON.parse(sender.mock.calls[0][0]).data;
    expect(data.seed_profile).toBe("measured");
    expect(data.goal.yaw).toBeCloseTo(Math.PI / 2, 3);
  });
  it.each(["down", "up"])("Ctrl on pointer %s sends the measured profile without clicking the checkbox", modifier => {
    const canvas = mount();
    fireEvent.pointerDown(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 150, ctrlKey: modifier === "down" });
    fireEvent.pointerMove(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 100 });
    fireEvent.pointerUp(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 100, ctrlKey: modifier === "up" });
    const data = JSON.parse(sender.mock.calls[0][0]).data;
    expect(data.seed_profile).toBe("measured");
    expect(data.goal).toMatchObject({ map_id: "cmuq-floor2", x: -3.19, y: 10.19 });
    expect(data.goal.yaw).toBeCloseTo(Math.PI / 2, 3);
    act(() => { useCommandStore.setState({ active: null, pickMode: "initialpose" }); });
    down(canvas, 2, "mouse"); up(canvas, 2, "mouse");
    expect(JSON.parse(sender.mock.calls[1][0]).data.seed_profile).toBeUndefined();
  });
  it("does not add a measured profile to Ctrl-selected destinations", () => {
    useTelemetryStore.setState({ pose: { frame_id: "map", x: 0, y: 0, yaw: 0,
      linear_velocity: 0, angular_velocity: 0, localized: true }, poseReceivedAt: performance.now() });
    act(() => useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState,
      map_id: "cmuq-floor2", map_revision: "test-revision", odom_epoch_valid: true,
      localization_recovery_required: false, localization_seed_stamp_ns: 1 } as BaseStateData }));
    act(() => useCommandStore.setState({ pickMode: "goal" }));
    const canvas = mount();
    fireEvent.pointerDown(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 150, ctrlKey: true });
    fireEvent.pointerUp(canvas, { pointerId: 1, pointerType: "mouse", clientX: 150, clientY: 150, ctrlKey: true });
    expect(JSON.parse(sender.mock.calls[0][0]).data.seed_profile).toBeUndefined();
  });
  it("disables confirmation and explains when LiDAR turns off during a preview", () => {
    const canvas = mount(); down(canvas); up(canvas);
    act(() => useTelemetryStore.setState({ baseState: { lidar_state: "OFF", lidar_scan_age: -1 } as BaseStateData }));
    const confirm = screen.getByRole("button", { name: "Set location" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText(/LiDAR is off/)).toBeTruthy();
    fireEvent.click(confirm);
    expect(sender).not.toHaveBeenCalled();
  });
  it("previews a touch tap and sends only after explicit confirmation", () => {
    const canvas = mount(); down(canvas); up(canvas);
    expect(sender).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Confirm map selection")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Set location" }));
    expect(sender).toHaveBeenCalledTimes(1);
    expect(JSON.parse(sender.mock.calls[0][0] as string).data.goal.yaw).toBeNull();
  });
  it("mouse release sends immediately on a touch-capable screen", () => {
    const canvas = mount(); down(canvas, 1, "mouse"); up(canvas, 1, "mouse");
    expect(sender).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
  });
  it.each(["pointerCancel", "lostPointerCapture"] as const)("%s never sends or leaves a preview", event => {
    const canvas = mount(); down(canvas);
    fireEvent[event](canvas, { pointerId: 1, pointerType: "touch" }); up(canvas);
    expect(sender).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
  });
  it("second-finger pan/zoom cancels selection through both releases", () => {
    const canvas = mount(); down(canvas); down(canvas, 2);
    fireEvent.pointerMove(canvas, { pointerId: 2, pointerType: "touch", clientX: 220, clientY: 150 });
    up(canvas, 2); up(canvas);
    expect(sender).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
  });
  it("normal lost capture after release preserves the touch preview", () => {
    const canvas = mount(); down(canvas); up(canvas);
    fireEvent.lostPointerCapture(canvas, { pointerId: 1, pointerType: "touch" });
    expect(screen.getByLabelText("Confirm map selection")).toBeTruthy();
  });
  it("rechecks current command eligibility on confirmation", () => {
    const canvas = mount(); down(canvas); up(canvas);
    act(() => useCommandStore.setState({ active: { commandId: "new", command: "undock", phase: "running", stage: null, distanceRemaining: null } }));
    fireEvent.click(screen.getByRole("button", { name: "Set location" }));
    expect(sender).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("Wait for the current command");
  });
  it("disconnect clears a pending preview", () => {
    const canvas = mount(); down(canvas); up(canvas);
    act(() => useTelemetryStore.setState({ wsConnected: false }));
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
    expect(sender).not.toHaveBeenCalled();
  });
  it("embedded touch scrolling never selects before interaction mode", () => {
    useCommandStore.setState({pickMode: "none"});
    const canvas = mount(); down(canvas); up(canvas);
    expect(canvas.style.touchAction).toBe("pan-y pinch-zoom");
    expect(sender).not.toHaveBeenCalled();
  });
  it("clears the preview when the page backgrounds", () => {
    const canvas = mount(); down(canvas); up(canvas);
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
    expect(useCommandStore.getState().pickMode).toBe("none");
    expect(sender).not.toHaveBeenCalled();
  });
  it("clears a preview when the map changes", () => {
    const canvas = mount(); down(canvas); up(canvas);
    act(() => useTelemetryStore.setState({ mapVersion: useTelemetryStore.getState().mapVersion + 1 }));
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
    expect(sender).not.toHaveBeenCalled();
  });
  it("clears a preview after the account loses command permission", () => {
    const canvas = mount(); down(canvas); up(canvas);
    act(() => useAuthStore.setState({user: {...useAuthStore.getState().user!, role: "observer"}}));
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
    expect(sender).not.toHaveBeenCalled();
  });
  it("sends the adjusted heading only once", () => {
    const canvas = mount(); down(canvas); up(canvas);
    fireEvent.change(screen.getByLabelText("Heading"), {target: {value: "90"}});
    const confirm = screen.getByRole("button", {name: "Set location"});
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(sender).toHaveBeenCalledTimes(1);
    expect(JSON.parse(sender.mock.calls[0][0]).data.goal.yaw).toBeCloseTo(Math.PI / 2);
  });

});


describe("floor browsing", () => {
  it("browses without commanding and cancels a pending selection", () => {
    const canvas = mount(); down(canvas); up(canvas);
    expect(screen.getByLabelText("Confirm map selection")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Floor 1" }));
    expect(sender).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Confirm map selection")).toBeNull();
    expect(useCommandStore.getState().pickMode).toBe("none");
    expect(screen.getByRole("button", { name: "Floor 1" }).getAttribute("aria-pressed")).toBe("true");
  });
  it("keeps the active floor independent from viewing and labels disconnects stale", () => {
    useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, map_id: "cmuq-floor2", map_revision: "test-revision" } });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Floor 1" }));
    expect(screen.getByRole("button", { name: "Floor 2 ● Active" }).getAttribute("aria-pressed")).toBe("false");
    act(() => useTelemetryStore.setState({ wsConnected: false }));
    expect(screen.getByRole("button", { name: "Floor 2 ● Last known (stale)" })).toBeTruthy();
    expect(sender).not.toHaveBeenCalled();
  });
  it("targets the viewed floor when setting a new location", () => {
    const canvas = mount();
    fireEvent.click(screen.getByRole("button", { name: "Floor 1" }));
    act(() => useCommandStore.getState().setPickMode("initialpose"));
    act(() => paint(0)); down(canvas, 1, "mouse"); up(canvas, 1, "mouse");
    const goal = JSON.parse(sender.mock.calls[0][0]).data.goal;
    expect(goal.map_id).toBe("cmuq-floor1");
    expect(goal.map_revision).toBe("test-first");
  });
});
