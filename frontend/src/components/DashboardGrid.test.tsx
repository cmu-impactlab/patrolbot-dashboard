import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DashboardGrid } from "./DashboardGrid";
import { deriveLayouts, useLayoutStore } from "../stores/layoutStore";

vi.mock("react-grid-layout", () => ({ Responsive: ({children}: {children: React.ReactNode}) => <div>{children}</div> }));
vi.mock("../widgets/registry", () => ({ WIDGET_REGISTRY: { battery: {} } }));
vi.mock("./WidgetFrame", () => ({ WidgetFrame: () => <div>Battery</div> }));
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const widgets = ["battery"];
  useLayoutStore.setState({ widgets, layouts: deriveLayouts(widgets, { lg: [{ i: "battery", x: 0, y: 0, w: 3, h: 12 }] }),
    dirty: false, revision: 0, minimized: {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it("retains unsaved changes after a failed request and saves them on retry", async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><DashboardGrid /></QueryClientProvider>);
  act(() => useLayoutStore.getState().editWidget("battery", "taller"));
  await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
  expect(useLayoutStore.getState().dirty).toBe(true);
  expect(screen.getByRole("alert").textContent).toContain("unsaved");
  fireEvent.click(screen.getByRole("button", { name: "Retry saving" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(useLayoutStore.getState().dirty).toBe(false);
});
