import { beforeEach, describe, expect, it, vi } from "vitest";
import { BUNDLED_ENTRIES, normalizeSnapshot } from "../lib/tuning";
import type { ActivationPlan, NormalizedSnapshot } from "../lib/tuning";
import { useAuthStore } from "./authStore";
import { useTelemetryStore } from "./telemetryStore";
import { dirtyDraftIds, savedPhaseFor, useTuningStore } from "./tuningStore";
import { postTuningRequest, fetchTuningSnapshot, fetchTuningJob } from "../api/tuning";

vi.mock("../api/tuning", async original => ({ ...await original<typeof import("../api/tuning")>(),
  postTuningRequest: vi.fn(), fetchTuningSnapshot: vi.fn(), fetchTuningJob: vi.fn() }));
const session = "11111111-1111-4111-8111-111111111111";
const digest = "sha256:" + "a".repeat(64);
function snapshot(revision = 1): NormalizedSnapshot {
  return normalizeSnapshot({ session_id: session, desired_revision: revision, active_revision: 1,
    last_known_good_revision: 1, active_known: true, writable: true, activation_supported: true,
    safe_apply: { allowed: true, reason: "offline fixture command idle" }, pending_keys: [],
    effective_limits: { max_linear_m_s: .3, max_angular_rad_s: .7 }, desired_digest: digest,
    desired: Object.fromEntries(BUNDLED_ENTRIES.map(entry => [entry.id, entry.default])),
    active: { "amcl.alpha1": .05 },
    catalog: BUNDLED_ENTRIES.map(entry => ({ ...entry, activatable: entry.accepts_edits })) });
}
function plan(snap: NormalizedSnapshot): ActivationPlan {
  return { version: 1, operation: "apply", desired_revision: snap.desiredRevision!, revision: snap.desiredRevision!, digest, base_revision: 1, base_digest: digest,
    session_id: session, hardware_generation: "test-generation", plan_hash: "sha256:" + "b".repeat(64),
    execution_kind: "scoped_lifecycle", nodes: ["amcl"], services: [], seed_required: true,
    disruption: "AMCL reset", executable: true, reasons: [], changes: [{ id: "amcl.alpha1", before: .05, after: .08 }] };
}
function freshLink() {
  useTelemetryStore.setState({ wsConnected: true, robotId: "test-robot", baseStateAt: performance.now(),
    baseState: { session_generation: 1, link_connected: true, telemetry_age: 0, motors_enabled: false,
      localization_ready: false } as never });
}
beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { id: 1, username: "admin", display_name: "Admin", role: "administrator", auth_mode: "local" } });
  freshLink();
  useTuningStore.setState({ ...useTuningStore.getInitialState(), snapshot: snapshot(), status: "ready" });
});
describe("explicit batch Save", () => {
  it("editing, refresh and reconnect never save, and reconnect retains conflicted drafts", async () => {
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    useTuningStore.getState().connectionLost();
    vi.mocked(fetchTuningSnapshot).mockResolvedValue(snapshot());
    await useTuningStore.getState().load();
    await useTuningStore.getState().saveDrafts();
    expect(postTuningRequest).not.toHaveBeenCalled();
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(".08");
    expect(useTuningStore.getState().conflictIds).toContain("amcl.alpha1");
  });
  it("does not call unsaved YAML starting values saved or active", () => {
    const initial = { ...snapshot(), desiredRevision: null, activeRevision: null, activeKnown: false };
    expect(savedPhaseFor(initial, BUNDLED_ENTRIES[0])).toBe("idle");
  });
  it("does not invent pending changes from missing observations", () => {
    const entry = BUNDLED_ENTRIES.find(entry => entry.id === "amcl.alpha1")!;
    expect(savedPhaseFor({ ...snapshot(), activeKnown: false, active: {} }, entry)).toBe("saved");
  });
  it.each(["", "-", "0.", "NaN", "Infinity"])("keeps invalid input %s and sends nothing", async text => {
    useTuningStore.getState().setDraft("amcl.alpha1", text);
    await useTuningStore.getState().saveDrafts();
    expect(postTuningRequest).not.toHaveBeenCalled();
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(text);
  });
  it.each([false, true])("saves all dirty groups in one CAS batch without odom/localization, motors=%s", async motors => {
    useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, motors_enabled: motors } });
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => ({ request_id: request.request_id,
      outcome: "ok", detail: "durable", snapshot: { ...snapshot(2), desired: { ...snapshot().desired,
        ...Object.fromEntries(request.changes!.map(change => [change.id, change.value])) } }, job: null }));
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    useTuningStore.getState().setDraft("nav2.controller.lookahead_dist", ".5");
    await useTuningStore.getState().saveDrafts();
    const request = vi.mocked(postTuningRequest).mock.calls[0][1];
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
    expect(request).toMatchObject({ operation: "save", base_revision: 1, generation: { session_id: session },
      changes: [{ id: "amcl.alpha1", value: .08 }, { id: "nav2.controller.lookahead_dist", value: .5 }] });
    expect(useTuningStore.getState().drafts).toEqual({});
    expect(useTuningStore.getState().snapshot?.committedActiveRevision).toBe(1);
  });
  it("validates coupled AMCL edits atomically across all drafts", async () => {
    useTuningStore.getState().setDraft("amcl.z_hit", ".6");
    await useTuningStore.getState().saveDrafts();
    expect(postTuningRequest).not.toHaveBeenCalled();
    useTuningStore.getState().setDraft("amcl.z_rand", ".4");
    await useTuningStore.getState().saveDrafts();
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postTuningRequest).mock.calls[0][1].changes).toHaveLength(2);
  });
  it.each(["operator", "link-lost", "stale-link", "stale-telemetry", "offline"])("refuses %s even through direct store calls", async condition => {
    if (condition === "operator") useAuthStore.setState({ user: { ...useAuthStore.getState().user!, role: "operator" } });
    if (condition === "link-lost") useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, link_connected: false } });
    if (condition === "stale-link") useTelemetryStore.setState({ baseStateAt: performance.now() - 501 });
    if (condition === "stale-telemetry") useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, telemetry_age: .6 } });
    if (condition === "offline") useTelemetryStore.setState({ wsConnected: false });
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    await useTuningStore.getState().saveDrafts();
    expect(postTuningRequest).not.toHaveBeenCalled();
  });
  it("single-flights clicks and preserves a newer in-flight edit", async () => {
    let finish!: (response: Awaited<ReturnType<typeof postTuningRequest>>) => void;
    vi.mocked(postTuningRequest).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    const first = useTuningStore.getState().saveDrafts();
    await useTuningStore.getState().saveDrafts();
    useTuningStore.getState().setDraft("amcl.alpha1", ".09");
    const request = vi.mocked(postTuningRequest).mock.calls[0][1];
    finish({ request_id: request.request_id, outcome: "ok", detail: "saved", snapshot: {
      ...snapshot(2), desired: { ...snapshot().desired, "amcl.alpha1": .08 } }, job: null });
    await first;
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(".09");
    expect(dirtyDraftIds(useTuningStore.getState())).toEqual(["amcl.alpha1"]);
    expect(useTuningStore.getState().draftBaseRevision).toBe(2);
  });
  it("retains unknown intent even when edited or discarded; never reuses it for a new batch", async () => {
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => ({ request_id: request.request_id,
      outcome: "unknown", detail: "timeout", snapshot: null, job: null }));
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    await useTuningStore.getState().saveDrafts();
    const intent = useTuningStore.getState().saveIntent;
    useTuningStore.getState().setDraft("amcl.alpha1", ".09");
    useTuningStore.getState().discardAllDrafts();
    await useTuningStore.getState().saveDrafts();
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
    expect(useTuningStore.getState().saveIntent).toBe(intent);
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(".09");
  });
  it("queries the original unknown batch, preserving newer drafts after confirmation", async () => {
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => ({ request_id: request.request_id,
      outcome: "unknown", detail: "timeout", snapshot: null, job: null }));
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    await useTuningStore.getState().saveDrafts();
    const intent = useTuningStore.getState().saveIntent!;
    useTuningStore.getState().setDraft("amcl.alpha1", ".09");
    vi.mocked(fetchTuningJob).mockResolvedValue({ request_id: intent.requestId, state: "done", outcome: "ok", revision: 2 });
    vi.mocked(fetchTuningSnapshot).mockResolvedValue({ ...snapshot(2), desired: { ...snapshot().desired, "amcl.alpha1": .08 } });
    await useTuningStore.getState().resolveUnknownSave();
    expect(useTuningStore.getState().saveIntent).toBeNull();
    expect(useTuningStore.getState().drafts["amcl.alpha1"]).toBe(".09");
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
  });
  it("does not overwrite newer observed revisions with a delayed save response", async () => {
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => {
      useTuningStore.setState({ snapshot: snapshot(3) });
      return { request_id: request.request_id, outcome: "ok", detail: "saved", snapshot: {
        ...snapshot(2), desired: { "amcl.alpha1": .08 } }, job: null };
    });
    useTuningStore.getState().setDraft("amcl.alpha1", ".08");
    await useTuningStore.getState().saveDrafts();
    expect(useTuningStore.getState().snapshot?.desiredRevision).toBe(3);
    expect(useTuningStore.getState().save.phase).toBe("unknown");
  });
  it("coalesces read requests and keeps last-known observations disabled without renewing their age", async () => {
    let finish!: (snapshot: NormalizedSnapshot) => void;
    useTuningStore.setState({ receivedAt: 0 });
    vi.mocked(fetchTuningSnapshot).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const first = useTuningStore.getState().load();
    const second = useTuningStore.getState().load();
    expect(first).toBe(second);
    finish(normalizeSnapshot(null));
    await first;
    expect(fetchTuningSnapshot).toHaveBeenCalledTimes(1);
    expect(useTuningStore.getState().snapshot?.available).toBe(false);
    expect(useTuningStore.getState().receivedAt).toBe(0);
  });
});
describe("exact Apply review", () => {
  function pending() {
    const snap = snapshot(2); snap.activationPlan = plan(snap); snap.pendingKeys = ["amcl.alpha1"];
    useTuningStore.setState({ snapshot: snap }); return snap;
  }
  it("freezes the review, makes no request on opening, and refuses changed revisions/plans", async () => {
    const snap = pending();
    useTuningStore.getState().openApplyReview();
    expect(postTuningRequest).not.toHaveBeenCalled();
    useTuningStore.setState({ snapshot: { ...snap, desiredRevision: 3 } });
    await useTuningStore.getState().confirmApply();
    expect(postTuningRequest).not.toHaveBeenCalled();
    expect(useTuningStore.getState().review?.plan.revision).toBe(2);
    expect(useTuningStore.getState().apply.phase).toBe("conflict");
  });
  it("requires all drafts resolved before opening or confirming review", async () => {
    pending();
    useTuningStore.getState().openApplyReview();
    useTuningStore.getState().setDraft("amcl.alpha2", ".1");
    await useTuningStore.getState().confirmApply();
    expect(postTuningRequest).not.toHaveBeenCalled();
  });
  it("does not treat HTTP success as exact-job verification or retry unknown apply", async () => {
    pending(); useTuningStore.getState().openApplyReview();
    vi.mocked(postTuningRequest).mockImplementation(async (_robot, request) => ({ request_id: request.request_id,
      outcome: "ok", detail: "HTTP only", snapshot: snapshot(2), job: null }));
    await useTuningStore.getState().confirmApply();
    await useTuningStore.getState().confirmApply();
    expect(postTuningRequest).toHaveBeenCalledTimes(1);
    expect(useTuningStore.getState().apply.phase).toBe("unknown");
    expect(vi.mocked(postTuningRequest).mock.calls[0][1]).toMatchObject({ operation: "apply", expected_revision: 2,
      review: { digest, plan_hash: "sha256:" + "b".repeat(64) } });
  });
});
