/**
 * The replay page is a whole tab of its own (?replay=<id>), deliberately
 * separate from the dashboard so a past route is never drawn on the live map.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReplayPage } from "./ReplayPage";
import { useReplayStore } from "../stores/replayStore";
import { useTelemetryStore } from "../stores/telemetryStore";

const DETAIL = {
  id: 4,
  robot_id: "patrolbot-01",
  name: "Corridor sweep",
  channels: ["pose", "battery", "event"],
  started_at: "2026-07-28 16:32:50",
  ended_at: "2026-07-28 16:33:15",
  status: "done",
  sample_count: 6,
  samples: [
    { ts: "2026-07-28T16:32:50.000Z", kind: "pose",
      data: { x: 0, y: 0, yaw: 0, linear_velocity: 0.4 } },
    { ts: "2026-07-28T16:32:52.000Z", kind: "event",
      data: { severity: "warning", title: "Bumper pressed", message: "Front bumper" } },
    { ts: "2026-07-28T16:32:54.000Z", kind: "battery",
      data: { voltage: 25.0, percentage: 77 } },
    { ts: "2026-07-28T16:33:00.000Z", kind: "pose",
      data: { x: 3, y: 4, yaw: 1.5708, linear_velocity: 0.2 } },
  ],
};

const MAP = {
  map_version: 1, name: "cmuq-floor2", resolution: 0.05,
  width: 40, height: 40, origin: { x: -1, y: -1, yaw: 0 }, rle: [[0, 1600]],
};

function mockApi(detail: unknown = DETAIL) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true,
    status: 200,
    json: async () => (String(url).includes("/api/map") ? MAP : detail),
  })));
}

function renderPage(id = 4) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReplayPage recordingId={id} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useReplayStore.getState().close();
  mockApi();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("replay page", () => {
  it("loads the recording and names it in the header and tab title", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Corridor sweep" })).toBeTruthy();
    expect(screen.getByText(/patrolbot-01/)).toBeTruthy();
    expect(document.title).toBe("Replay — Corridor sweep");
  });

  it("offers a transport for the recorded route", async () => {
    renderPage();
    const slider = await screen.findByLabelText("Replay position");
    expect(slider.getAttribute("max")).toBe("10000"); // 0 s to 10 s
    expect(screen.getByLabelText("Playback speed")).toBeTruthy();
    expect(screen.getByTitle("Back to the start")).toBeTruthy();
  });

  it("reports the state at the playhead, not the robot's current state", async () => {
    // A live robot somewhere else entirely must not leak into the replay.
    useTelemetryStore.setState({
      pose: { x: 99, y: 99, yaw: 0, linear_velocity: 9, angular_velocity: 0, localized: true },
    });
    renderPage();
    await screen.findByRole("heading", { name: "Corridor sweep" });
    await waitFor(() => expect(screen.getByText("0.00, 0.00 m")).toBeTruthy());
    expect(screen.queryByText(/99\.00/)).toBeNull();
    // 3 m across then 4 m up.
    expect(screen.getByText("5.0 m")).toBeTruthy();
  });

  it("lists the recorded alerts, with ones ahead of the playhead marked pending", async () => {
    const { container } = renderPage();
    expect(await screen.findByText("Bumper pressed")).toBeTruthy();
    useReplayStore.getState().pause();
    useReplayStore.getState().seek(0);
    await waitFor(() =>
      expect(container.querySelectorAll(".replay-event.pending").length).toBe(1));
    useReplayStore.getState().seek(9000);
    await waitFor(() =>
      expect(container.querySelectorAll(".replay-event.pending").length).toBe(0));
  });

  it("never opens a telemetry socket — this tab is history only", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Corridor sweep" });
    const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map((call) => String(call[0]));
    expect(calls).toContain("/api/recordings/4");
    expect(calls.some((url) => url.includes("/ws/"))).toBe(false);
  });

  it("explains itself when the recording captured no positions", async () => {
    mockApi({ ...DETAIL, samples: DETAIL.samples.filter((s) => s.kind !== "pose") });
    renderPage();
    expect(await screen.findByText(/no position data/)).toBeTruthy();
    // Without a route there is nothing to scrub through.
    expect(screen.queryByLabelText("Replay position")).toBeNull();
  });

  it("says so when the recording cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })));
    renderPage(999);
    expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
  });

  it("clears the replay on unmount so nothing lingers", async () => {
    const { unmount } = renderPage();
    await screen.findByRole("heading", { name: "Corridor sweep" });
    unmount();
    expect(useReplayStore.getState().recording).toBeNull();
  });
});
