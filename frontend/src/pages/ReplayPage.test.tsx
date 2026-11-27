/**
 * The replay page is a whole tab of its own (?replay=<id>), deliberately
 * separate from the dashboard so a past route is never drawn on the live map.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
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
      data: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 0, y: 0, yaw: 0, linear_velocity: 0.4 } },
    { ts: "2026-07-28T16:32:52.000Z", kind: "event",
      data: { severity: "warning", title: "Bumper pressed", message: "Front bumper" } },
    { ts: "2026-07-28T16:32:54.000Z", kind: "battery",
      data: { voltage: 25.0, percentage: 77 } },
    { ts: "2026-07-28T16:33:00.000Z", kind: "pose",
      data: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 3, y: 4, yaw: 1.5708, linear_velocity: 0.2 } },
  ],
};

const MAP = {
  map_id: "cmuq-floor2", map_revision: "test-revision",
  map_version: 1, name: "Floor 2", resolution: 0.05,
  width: 40, height: 40, origin: { x: -1, y: -1, yaw: 0 }, rle: [[0, 1600]],
};
const OTHER_MAP = { ...MAP, map_id: "cmuq-floor1", map_revision: "floor1-revision", name: "Floor 1" };

function mockApi(detail: unknown = DETAIL, maps: Record<string, typeof MAP> = { [MAP.map_id]: MAP }) {
  const fetch = vi.fn(async (url: string) => {
    const data = url === "/api/map" ? MAP : url.startsWith("/api/maps/")
      ? maps[url.slice("/api/maps/".length)] : detail;
    return { ok: data != null, status: data == null ? 404 : 200, json: async () => data };
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
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
    const help = screen.getByRole("link", { name: "Open the guided dashboard tour in a new tab" });
    expect(help.getAttribute("href")).toBe("/?tour=1");
    expect(help.getAttribute("target")).toBe("_blank");
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
      pose: { map_id: "cmuq-floor2", map_revision: "test-revision", x: 99, y: 99, yaw: 0, linear_velocity: 9, angular_velocity: 0, localized: true },
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

  it("loads the recorded floor, switches at a recorded map change, and seeks back", async () => {
    const samples = DETAIL.samples.map((sample, index) => index === 0
      ? { ...sample, data: { ...sample.data, map_id: OTHER_MAP.map_id, map_revision: OTHER_MAP.map_revision } }
      : sample);
    const fetch = mockApi({ ...DETAIL, samples }, { [MAP.map_id]: MAP, [OTHER_MAP.map_id]: OTHER_MAP });
    renderPage();
    expect(await screen.findByText("Floor 1 · 0.05 m/cell")).toBeTruthy();
    act(() => {
      const replay = useReplayStore.getState();
      replay.pause();
      replay.seek(10000);
    });
    expect(await screen.findByText("Floor 2 · 0.05 m/cell")).toBeTruthy();
    act(() => useReplayStore.getState().seek(0));
    expect(await screen.findByText("Floor 1 · 0.05 m/cell")).toBeTruthy();
    expect(fetch.mock.calls.map(([url]) => url)).not.toContain("/api/map");
  });

  it("rejects a different revision even when the floor ID still matches", async () => {
    mockApi(DETAIL, { [MAP.map_id]: { ...MAP, map_revision: "new-revision" } });
    renderPage();
    expect(await screen.findByText(/Recorded map.*unavailable/)).toBeTruthy();
    expect(screen.queryByLabelText("Recorded robot map")).toBeNull();
    expect(screen.getByLabelText("Replay position")).toBeTruthy();
  });

  it("switches between available and unavailable revisions of the same floor", async () => {
    const samples = DETAIL.samples.map((sample, index) => index === 3
      ? { ...sample, data: { ...sample.data, map_revision: "different-revision" } }
      : sample);
    mockApi({ ...DETAIL, samples });
    renderPage();
    expect(await screen.findByText("Floor 2 · 0.05 m/cell")).toBeTruthy();
    act(() => {
      const replay = useReplayStore.getState();
      replay.pause();
      replay.seek(10000);
    });
    expect(await screen.findByText(/Recorded map.*unavailable/)).toBeTruthy();
    expect(screen.queryByLabelText("Recorded robot map")).toBeNull();
    act(() => useReplayStore.getState().seek(0));
    expect(await screen.findByText("Floor 2 · 0.05 m/cell")).toBeTruthy();
  });

  it("does not fall back to the live map when the recorded floor is missing", async () => {
    const fetch = mockApi(DETAIL, {});
    renderPage();
    expect(await screen.findByText(/Recorded map.*unavailable/)).toBeTruthy();
    expect(screen.queryByLabelText("Recorded robot map")).toBeNull();
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/recordings/4", "/api/maps/cmuq-floor2"]);
  });

  it.each([
    { map_id: undefined, map_revision: undefined },
    { map_revision: undefined },
  ])("keeps playback available without guessing missing map metadata (%j)", async incomplete => {
    const samples = DETAIL.samples.map(sample => ({ ...sample, data: {
      ...sample.data, ...incomplete,
    } }));
    const fetch = mockApi({ ...DETAIL, samples });
    renderPage();
    expect(await screen.findByText(/No map information was recorded/)).toBeTruthy();
    expect(screen.queryByLabelText("Recorded robot map")).toBeNull();
    expect(screen.getByLabelText("Replay position")).toBeTruthy();
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/recordings/4"]);
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
