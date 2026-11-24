import { beforeEach, expect, it, vi } from "vitest";
import { fetchTuningSnapshot } from "../api/tuning";
import { normalizeSnapshot } from "../lib/tuning";
import { useTelemetryStore } from "./telemetryStore";
import { useTuningStore } from "./tuningStore";

vi.mock("../api/tuning", () => ({ fetchTuningSnapshot: vi.fn() }));
const snapshot = { ...normalizeSnapshot(null), available: true, catalogFromServer: true, desired: { "amcl.alpha1": .05 } };

beforeEach(() => {
  useTelemetryStore.setState({ robotId: "test-robot" });
  useTuningStore.getState().connectionLost();
  useTuningStore.setState({ snapshot: null, receivedAt: null, status: "idle" });
  vi.clearAllMocks();
});

it("coalesces overlapping reads", async () => {
  vi.mocked(fetchTuningSnapshot).mockResolvedValue(snapshot);
  const first = useTuningStore.getState().load();
  const second = useTuningStore.getState().load();
  expect(first).toBe(second);
  await first;
  expect(fetchTuningSnapshot).toHaveBeenCalledTimes(1);
});

it("keeps last values visible without renewing their age after a failed read", async () => {
  vi.mocked(fetchTuningSnapshot).mockResolvedValueOnce(snapshot).mockResolvedValueOnce(normalizeSnapshot(null));
  const store = useTuningStore.getState;
  await store().load();
  const receivedAt = store().receivedAt;
  await store().load();
  expect(store().snapshot?.desired).toEqual(snapshot.desired);
  expect(store().snapshot?.available).toBe(false);
  expect(store().receivedAt).toBe(receivedAt);
});

it("discards an in-flight read across disconnect without reusing it", async () => {
  let finish!: (value: typeof snapshot) => void;
  vi.mocked(fetchTuningSnapshot).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = useTuningStore.getState;
  const old = store().load();
  store().connectionLost();
  vi.mocked(fetchTuningSnapshot).mockResolvedValueOnce(normalizeSnapshot(null));
  await store().load();
  finish(snapshot);
  await old;
  expect(store().snapshot?.available).toBe(false);
  expect(fetchTuningSnapshot).toHaveBeenCalledTimes(2);
});

it("does not show one robot's readback while loading another robot", async () => {
  vi.mocked(fetchTuningSnapshot).mockResolvedValueOnce(snapshot).mockResolvedValueOnce(normalizeSnapshot(null));
  const store = useTuningStore.getState;
  await store().load();
  useTelemetryStore.setState({ robotId: "second-robot" });
  const next = store().load();
  expect(store().snapshot).toBeNull();
  await next;
  expect(fetchTuningSnapshot).toHaveBeenLastCalledWith("second-robot");
});
