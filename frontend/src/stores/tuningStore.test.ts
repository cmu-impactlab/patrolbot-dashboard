import { beforeEach, describe, expect, it, vi } from "vitest";
import { BUNDLED_ENTRIES, normalizeSnapshot } from "../lib/tuning";
import { useAuthStore } from "./authStore";
import { useTelemetryStore } from "./telemetryStore";
import { savedPhaseFor, useTuningStore } from "./tuningStore";
import { postTuningRequest, fetchTuningSnapshot } from "../api/tuning";

vi.mock("../api/tuning", async (original) => ({
  ...await original<typeof import("../api/tuning")>(),
  postTuningRequest: vi.fn(), fetchTuningSnapshot: vi.fn(), fetchTuningJob: vi.fn(),
}));
const session = "11111111-1111-4111-8111-111111111111";
function snapshot(revision = 1) {
  return normalizeSnapshot({ session_id: session, desired_revision: revision, active_revision: 1,
    last_known_good_revision: 1, active_known: true, writable: true, activation_supported: true,
    safe_apply: { allowed: true, reason: "offline fixture idle" }, pending_keys: [],
    effective_limits: { max_linear_m_s: .3, max_angular_rad_s: .7 },
    desired: { "amcl.alpha1": .05 }, active: { "amcl.alpha1": .05 },
    catalog: BUNDLED_ENTRIES.map(entry => ({ ...entry, activatable: entry.accepts_edits })) });
}
beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { id: 1, username: "admin", display_name: "Admin", role: "administrator", auth_mode: "local" } });
  useTelemetryStore.setState({ wsConnected: true });
  useTuningStore.setState({ snapshot: snapshot(), receivedAt: null, status: "ready", drafts: {}, savePhase: {}, saveDetail: {},
    saveRequestIds: {}, draftBaseRevision: 1, conflictIds: [], redeploy: { phase: "idle", detail: null, requestId: null },
    restore: { phase: "idle", detail: null, requestId: null } });
});
describe("robot-owned autosave", () => {
  it("a pending debounce cannot save old drafts after a same-session reconnect", async () => {
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    useTuningStore.getState().connectionLost();
    useTelemetryStore.setState({ wsConnected: false });
    useTelemetryStore.setState({ wsConnected: true });
    vi.mocked(fetchTuningSnapshot).mockResolvedValue(snapshot());
    await useTuningStore.getState().load();
    await useTuningStore.getState().commitGroup("amcl");
    expect(postTuningRequest).not.toHaveBeenCalled();
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(".08");
    expect(useTuningStore.getState().conflictIds).toContain("amcl.alpha1");
  });
  it("never calls unsaved YAML starting values saved or active", () => {
    const initial = { ...snapshot(), desiredRevision: null, activeRevision: null, activeKnown: false };
    expect(savedPhaseFor(initial, BUNDLED_ENTRIES.find(entry => entry.id === "amcl.alpha1")!)).toBe("idle");
  });
  it("does not invent a pending edit merely because a live readback is missing", () => {
    const entry = BUNDLED_ENTRIES.find(entry => entry.id === "amcl.alpha1")!;
    const unknown = { ...snapshot(), activeKnown: false, fieldActiveKnown: { [entry.id]: false } };
    expect(savedPhaseFor(unknown, entry)).toBe("saved");
    expect(savedPhaseFor({ ...unknown, pendingKeys: [entry.id] }, entry)).toBe("saved_pending");
    expect(savedPhaseFor(snapshot(), entry)).toBe("saved");
  });
  it("refresh never carries old saved-active labels into an unsaved YAML snapshot", async () => {
    useTuningStore.setState({ savePhase: { "amcl.alpha1": "saved" } });
    vi.mocked(fetchTuningSnapshot).mockResolvedValue({ ...snapshot(), desiredRevision: null,
      activeRevision: null, activeKnown: false, sessionId: null });
    await useTuningStore.getState().load();
    expect(useTuningStore.getState().savePhase["amcl.alpha1"]).toBe("idle");
    expect(postTuningRequest).not.toHaveBeenCalled();
  });
  it.each(["", "-", "0.", "NaN", "Infinity"])("never sends incomplete input %s", async value => {
    useTuningStore.getState().setDraft("amcl.alpha1", value);
    await useTuningStore.getState().commitGroup("amcl");
    expect(postTuningRequest).not.toHaveBeenCalled();
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(value);
  });
  it("does not replay an unknown save and keeps its draft", async () => {
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => ({ request_id: request.request_id, outcome: "unknown", detail: "timeout", snapshot: null, job: null }));
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    await useTuningStore.getState().commitGroup("amcl");
    await useTuningStore.getState().commitGroup("amcl");
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(".08");
  });
  it("uses a new idempotency key for a subsequent completed edit", async () => {
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => ({ request_id: request.request_id,
      outcome: "ok", detail: "durable", snapshot: { ...snapshot(2), desired: { "amcl.alpha1": request.changes![0].value } }, job: null }));
    for (const value of [".08", ".09"]) {
      useTuningStore.getState().setDraft("amcl.alpha1", value);
      await useTuningStore.getState().commitGroup("amcl");
    }
    const calls = vi.mocked(postTuningRequest).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][1].request_id).not.toBe(calls[1][1].request_id);
  });
  it("refresh preserves conflicted drafts without sending", async () => {
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    vi.mocked(fetchTuningSnapshot).mockResolvedValue(snapshot(3));
    await useTuningStore.getState().load();
    await useTuningStore.getState().commitGroup("amcl");
    expect(postTuningRequest).not.toHaveBeenCalled();
    expect(useTuningStore.getState().conflictIds).toContain("amcl.alpha1");
  });
  it("checks role at commit even when a caller bypasses the disabled UI", async () => {
    useAuthStore.setState({ user: { id: 2, username: "op", display_name: "Op", role: "operator", auth_mode: "local" } });
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    await useTuningStore.getState().commitGroup("amcl");
    expect(postTuningRequest).not.toHaveBeenCalled();
  });

  it("coalesces overlapping reads so a slow response cannot create a growing request queue", async () => {
    let finish!: (value: ReturnType<typeof snapshot>) => void;
    vi.mocked(fetchTuningSnapshot).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const first = useTuningStore.getState().load();
    const second = useTuningStore.getState().load();
    expect(first).toBe(second);
    expect(fetchTuningSnapshot).toHaveBeenCalledTimes(1);
    finish(snapshot());
    await Promise.all([first, second]);
    vi.mocked(fetchTuningSnapshot).mockResolvedValue(snapshot());
    await useTuningStore.getState().load();
    expect(fetchTuningSnapshot).toHaveBeenCalledTimes(2);
  });

  it("keeps last confirmed values visible but disabled without refreshing observation ages on failure", async () => {
    const prior = { ...snapshot(), catalogFromServer: true };
    useTuningStore.setState({ snapshot: prior, receivedAt: 0 });
    vi.mocked(fetchTuningSnapshot).mockResolvedValue(normalizeSnapshot(null));
    await useTuningStore.getState().load();
    const state = useTuningStore.getState();
    expect(state.snapshot?.desired).toEqual(prior.desired);
    expect(state.snapshot?.available).toBe(false);
    expect(state.snapshot?.writable).toBe(false);
    expect(state.receivedAt).toBe(0);
    expect(postTuningRequest).not.toHaveBeenCalled();
  });
});
