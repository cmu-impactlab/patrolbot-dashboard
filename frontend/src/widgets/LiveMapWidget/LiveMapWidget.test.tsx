import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveMapWidget } from ".";
import { registerCommandSender, useCommandStore } from "../../stores/commandStore";
import { useTelemetryStore } from "../../stores/telemetryStore";
import { useAuthStore } from "../../stores/authStore";
import { useUiStore } from "../../stores/uiStore";

vi.mock("../../api/queries", () => ({ useMapQuery: () => ({ data: {
  map_version: 1, name: "Test", width: 100, height: 100, resolution: 0.05,
  origin: { x: 0, y: 0, yaw: 0 }, rle: [[0, 10000]],
} }) }));
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
  useTelemetryStore.setState({ wsConnected: true, connection: { state: "online" }, pose: null, poseSetThisSession: true });
  useAuthStore.setState({ user: { id: 1, username: "test", display_name: "Test", role: "operator", auth_mode: "local" } });
  useCommandStore.setState({ active: null, pickMode: "initialpose", lastResult: null });
  useUiStore.setState({ fullscreenWidget: null, followRobot: false });
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
