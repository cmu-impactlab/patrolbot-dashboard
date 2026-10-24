import { create } from "zustand";
import { fetchTuningJob, fetchTuningSnapshot, postTuningRequest, TuningHttpError } from "../api/tuning";
import { applyRequest, effectiveValues, newRequestId, parameterPending, parseNumericInput,
  restoreRequest, saveRequest, validateBatch } from "../lib/tuning";
import type { ActivationPlan, BatchChange, NormalizedSnapshot, TuningCatalogEntry, TuningJob, TuningResponse } from "../lib/tuning";
import { useTelemetryStore } from "./telemetryStore";
import { isAdministrator, useAuthStore } from "./authStore";

export type SavePhase = "idle" | "saving" | "saved" | "saved_pending" | "conflict" | "failed" | "unknown";
export interface OperationState { phase: SavePhase; detail: string | null; requestId: string | null }
interface SaveIntent {
  requestId: string;
  robotId: string;
  sessionId: string;
  actorId: number;
  baseRevision: number;
  changes: BatchChange[];
  versions: Record<string, number>;
}
interface ApplyReview { robotId: string; snapshot: NormalizedSnapshot; plan: ActivationPlan }
export interface TuningState {
  status: "idle" | "loading" | "ready" | "unavailable";
  snapshot: NormalizedSnapshot | null;
  receivedAt: number | null;
  loadReason: string | null;
  drafts: Record<string, string>;
  draftVersions: Record<string, number>;
  draftBaseRevision: number | null;
  draftSession: string | null;
  draftRobot: string | null;
  conflictIds: string[];
  savePhase: Record<string, SavePhase>;
  saveDetail: Record<string, string | null>;
  save: OperationState;
  saveIntent: SaveIntent | null;
  review: ApplyReview | null;
  apply: OperationState;
  applyIntent: ApplyReview | null;
  job: TuningJob | null;
  load: () => Promise<void>;
  setDraft: (id: string, text: string) => void;
  clearDraft: (id: string) => void;
  discardAllDrafts: () => void;
  rebaseDrafts: () => void;
  connectionLost: () => void;
  saveDrafts: () => Promise<void>;
  resolveUnknownSave: () => Promise<void>;
  openApplyReview: () => void;
  openRestoreReview: () => void;
  closeApplyReview: () => void;
  confirmApply: () => Promise<void>;
  queryJob: (id: string) => Promise<void>;
}

const IDLE: OperationState = { phase: "idle", detail: null, requestId: null };
const robotId = () => useTelemetryStore.getState().robotId;
const unresolved = (phase: SavePhase) => phase === "saving" || phase === "unknown";
function fields<T>(current: Record<string, T>, ids: string[], value: T): Record<string, T> {
  return { ...current, ...Object.fromEntries(ids.map(id => [id, value])) };
}
export function dirtyDraftIds(state: Pick<TuningState, "drafts" | "snapshot">): string[] {
  return Object.keys(state.drafts).filter(id => {
    const entry = state.snapshot?.entries.find(value => value.id === id);
    const parsed = entry && parseNumericInput(state.drafts[id], entry);
    return !parsed?.ok || parsed.value !== state.snapshot?.desired[id];
  }).sort();
}
export function savedPhaseFor(snapshot: NormalizedSnapshot | null, entry: TuningCatalogEntry): SavePhase {
  if (!snapshot?.available || snapshot.desiredRevision === null) return "idle";
  return parameterPending(snapshot, entry) ? "saved_pending" : "saved";
}
/** Save admission is deliberately independent of motors, odometry and navigation. */
export function saveDisabledReason(state: TuningState, now = performance.now()): string | null {
  const telemetry = useTelemetryStore.getState();
  const base = telemetry.baseState;
  const elapsed = telemetry.baseStateAt === null ? Infinity : (now - telemetry.baseStateAt) / 1000;
  if (!isAdministrator(useAuthStore.getState().user)) return "An administrator account is required.";
  if (!telemetry.wsConnected) return "The dashboard connection is offline.";
  if (!state.snapshot?.available || !state.snapshot.writable || !state.snapshot.sessionId || state.snapshot.desiredRevision === null)
    return state.snapshot?.writeDisabledReason ?? "The robot tuning store is not writable.";
  if (!base?.link_connected || !Number.isFinite(elapsed) || elapsed < 0 || elapsed > .5
    || !Number.isFinite(base.telemetry_age) || base.telemetry_age < 0 || base.telemetry_age + elapsed > .5)
    return "The SBC link is disconnected or stale.";
  if (unresolved(state.save.phase) || unresolved(state.apply.phase)) return "Resolve the in-flight or unknown request first.";
  if (state.conflictIds.length) return "Review the changed robot values before saving your drafts.";
  if (!dirtyDraftIds(state).length) return "No unsaved changes.";
  if (state.draftBaseRevision !== state.snapshot.desiredRevision || state.draftSession !== state.snapshot.sessionId
    || state.draftRobot !== robotId()) return "The draft's revision, robot or session changed. Review it first.";
  return null;
}
function reviewChanged(review: ApplyReview, snapshot: NormalizedSnapshot | null): boolean {
  const plan = review.plan.operation === "restore" ? snapshot?.restorePlan : snapshot?.activationPlan;
  return review.robotId !== robotId() || !snapshot?.available || !plan
    || snapshot.sessionId !== review.snapshot.sessionId || snapshot.desiredRevision !== review.plan.desired_revision
    || snapshot.desiredDigest !== review.snapshot.desiredDigest || plan.plan_hash !== review.plan.plan_hash;
}
function applyBlocked(state: TuningState): boolean {
  return !isAdministrator(useAuthStore.getState().user) || !useTelemetryStore.getState().wsConnected
    || !state.snapshot?.available || !state.snapshot.activationSupported || !state.snapshot.safeApply.allowed
    || dirtyDraftIds(state).length > 0 || state.conflictIds.length > 0
    || unresolved(state.save.phase) || unresolved(state.apply.phase)
    || !!state.job && !["succeeded", "failed", "canceled", "done"].includes(String(state.job.state));
}

export const useTuningStore = create<TuningState>((set, get) => {
  const pendingLoads = new Map<string, Promise<void>>();
  let mutationGeneration = 0;
  const markSave = (intent: SaveIntent, phase: SavePhase, detail: string | null) => set({
    save: { phase, detail, requestId: intent.requestId },
    savePhase: fields(get().savePhase, intent.changes.map(change => change.id), phase),
    saveDetail: fields(get().saveDetail, intent.changes.map(change => change.id), detail),
  });
  const confirmSave = (intent: SaveIntent, snapshot: NormalizedSnapshot | null, revision?: number) => {
    const confirmed = snapshot?.available && snapshot.sessionId === intent.sessionId
      && snapshot.desiredRevision !== null && snapshot.desiredRevision > intent.baseRevision
      && (revision === undefined || snapshot.desiredRevision === revision)
      && intent.changes.every(change => snapshot.desired[change.id] === change.value);
    if (intent.robotId !== robotId() || useAuthStore.getState().user?.id !== intent.actorId || !confirmed) return false;
    const current = get();
    // A newer observation cannot be overwritten with an earlier save response.
    if (current.snapshot?.sessionId !== intent.sessionId || (current.snapshot.desiredRevision ?? 0) > snapshot.desiredRevision!) return false;
    const drafts = { ...current.drafts };
    const acknowledged: string[] = [];
    for (const change of intent.changes) {
      if (current.draftVersions[change.id] === intent.versions[change.id]) {
        delete drafts[change.id];
        acknowledged.push(change.id);
      }
    }
    const remainingConflicts = current.conflictIds.filter(id => !intent.changes.some(change => change.id === id));
    mutationGeneration++;
    set({ snapshot, receivedAt: performance.now(), status: "ready", loadReason: null, drafts,
      draftBaseRevision: snapshot.desiredRevision, draftSession: snapshot.sessionId, draftRobot: intent.robotId,
      conflictIds: remainingConflicts, saveIntent: null });
    const pending = intent.changes.some(change => {
      const entry = snapshot.entries.find(entry => entry.id === change.id);
      return entry && parameterPending(snapshot, entry);
    });
    markSave(intent, pending ? "saved_pending" : "saved", "Saved targets only; running parameters were not changed.");
    // A later edit is still a draft, even if the submitted version was confirmed.
    const phases = { ...get().savePhase };
    for (const change of intent.changes) if (!acknowledged.includes(change.id)) phases[change.id] = "idle";
    set({ savePhase: phases });
    return true;
  };
  return {
    status: "idle", snapshot: null, receivedAt: null, loadReason: null, drafts: {}, draftVersions: {},
    draftBaseRevision: null, draftSession: null, draftRobot: null, conflictIds: [], savePhase: {}, saveDetail: {},
    save: IDLE, saveIntent: null, review: null, apply: IDLE, applyIntent: null, job: null,
    load: () => {
      const requestedRobot = robotId();
      const pending = pendingLoads.get(requestedRobot);
      if (pending) return pending;
      const started = performance.now();
      const generation = mutationGeneration;
      const fetch = async () => {
        set({ status: "loading" });
        const snapshot = await fetchTuningSnapshot(requestedRobot);
        if (requestedRobot !== robotId() || generation !== mutationGeneration
          || (get().receivedAt !== null && get().receivedAt! > started)) return;
        const previous = get().snapshot;
        if (!snapshot.available && previous?.catalogFromServer) {
          set({ snapshot: { ...previous, available: false, writable: false, activationSupported: false,
            safeApply: { allowed: false, reason: snapshot.reason ?? "Readback unavailable." }, reason: snapshot.reason },
            status: "unavailable", loadReason: snapshot.reason });
          return;
        }
        const current = get();
        const dirty = dirtyDraftIds(current);
        const moved = dirty.length > 0 && (snapshot.desiredRevision !== current.draftBaseRevision
          || snapshot.sessionId !== current.draftSession || requestedRobot !== current.draftRobot);
        const phases = { ...current.savePhase };
        for (const [id, phase] of Object.entries(phases)) {
          if (phase !== "saved" && phase !== "saved_pending") continue;
          const entry = snapshot.entries.find(value => value.id === id);
          phases[id] = entry ? savedPhaseFor(snapshot, entry) : "idle";
        }
        set({ snapshot, receivedAt: performance.now(), status: snapshot.available ? "ready" : "unavailable",
          loadReason: snapshot.available ? null : snapshot.reason,
          ...(dirty.length ? {} : { draftBaseRevision: snapshot.desiredRevision, draftSession: snapshot.sessionId, draftRobot: requestedRobot }),
          conflictIds: moved ? [...new Set([...current.conflictIds, ...dirty])] : current.conflictIds,
          savePhase: moved ? fields(phases, dirty, "conflict") : phases,
          saveDetail: moved ? fields(current.saveDetail, dirty, "The saved revision, robot or session changed. Review or discard your drafts.") : current.saveDetail,
          job: snapshot.latestJob ?? current.job });
      };
      const operation = fetch().finally(() => { if (pendingLoads.get(requestedRobot) === operation) pendingLoads.delete(requestedRobot); });
      pendingLoads.set(requestedRobot, operation);
      return operation;
    },
    setDraft: (id, text) => {
      const current = get();
      const clean = !dirtyDraftIds(current).length && !current.conflictIds.length && !unresolved(current.save.phase);
      set({ drafts: { ...current.drafts, [id]: text }, draftVersions: { ...current.draftVersions, [id]: (current.draftVersions[id] ?? 0) + 1 },
        savePhase: { ...current.savePhase, [id]: "idle" }, saveDetail: { ...current.saveDetail, [id]: null },
        ...(clean ? { draftBaseRevision: current.snapshot?.desiredRevision ?? null,
          draftSession: current.snapshot?.sessionId ?? null, draftRobot: robotId() } : {}) });
    },
    clearDraft: id => {
      if (unresolved(get().save.phase)) return;
      const drafts = { ...get().drafts }; delete drafts[id];
      set({ drafts, conflictIds: get().conflictIds.filter(value => value !== id) });
    },
    discardAllDrafts: () => {
      if (unresolved(get().save.phase)) return;
      set({ drafts: {}, conflictIds: [], savePhase: {}, saveDetail: {}, save: IDLE, saveIntent: null });
    },
    rebaseDrafts: () => {
      const state = get();
      // Explicitly reviewed reconnects may rebase onto the new session, but
      // drafts are never transferred to another robot or sent by this action.
      if (unresolved(state.save.phase) || !state.snapshot?.available || state.draftRobot !== robotId()
        || !state.snapshot.sessionId) return;
      set({ draftBaseRevision: state.snapshot.desiredRevision, draftSession: state.snapshot.sessionId,
        conflictIds: [], save: IDLE,
        savePhase: {}, saveDetail: {}, saveIntent: null });
    },
    connectionLost: () => {
      const ids = dirtyDraftIds(get());
      if (ids.length) set({ conflictIds: [...new Set([...get().conflictIds, ...ids])] });
    },
    saveDrafts: async () => {
      if (saveDisabledReason(get())) return;
      const state = get();
      const snapshot = state.snapshot!;
      const ids = dirtyDraftIds(state);
      const changes: BatchChange[] = [];
      for (const id of ids) {
        const entry = snapshot.entries.find(value => value.id === id);
        const parsed = entry && parseNumericInput(state.drafts[id], entry);
        if (!entry?.accepts_edits || entry.protected || entry.not_applicable || !parsed?.ok) {
          const detail = parsed?.reason ?? "This field is not approved for editing.";
          set({ save: { phase: "failed", detail, requestId: null }, savePhase: fields(state.savePhase, [id], "failed"), saveDetail: fields(state.saveDetail, [id], detail) });
          return;
        }
        changes.push({ id, value: parsed.value! });
      }
      const validation = validateBatch(changes, effectiveValues(snapshot), snapshot.entries);
      if (!validation.ok) {
        set({ save: { phase: "failed", detail: validation.reason, requestId: null }, savePhase: fields(state.savePhase, validation.ids, "failed"), saveDetail: fields(state.saveDetail, validation.ids, validation.reason) });
        return;
      }
      const intent: SaveIntent = { requestId: newRequestId(), robotId: robotId(), sessionId: snapshot.sessionId!,
        actorId: useAuthStore.getState().user!.id, baseRevision: snapshot.desiredRevision!, changes,
        versions: Object.fromEntries(ids.map(id => [id, state.draftVersions[id]])) };
      set({ saveIntent: intent });
      markSave(intent, "saving", null);
      try {
        const response = await postTuningRequest(intent.robotId, saveRequest(intent.sessionId, intent.baseRevision, changes, intent.requestId));
        if (response.request_id !== intent.requestId) { markSave(intent, "unknown", "The robot did not confirm this request. Query its status; do not retry."); return; }
        if (response.outcome === "ok") {
          if (!confirmSave(intent, response.snapshot)) markSave(intent, "unknown", "Exact saved batch/revision was not confirmed. Query the request; do not retry.");
        } else if (response.outcome === "unknown") markSave(intent, "unknown", response.detail || "Outcome unknown — not retried.");
        else {
          const conflict = response.outcome === "conflict" || get().snapshot?.desiredRevision !== intent.baseRevision;
          if (intent.robotId === robotId() && response.snapshot?.sessionId === intent.sessionId
            && (response.snapshot.desiredRevision ?? -1) >= (get().snapshot?.desiredRevision ?? -1)) {
            mutationGeneration++;
            set({ snapshot: response.snapshot, receivedAt: performance.now() });
          }
          markSave(intent, conflict ? "conflict" : "failed", response.detail || "The robot refused this batch.");
          set({ saveIntent: null, ...(conflict ? { conflictIds: [...new Set([...get().conflictIds, ...ids])] } : {}) });
        }
      } catch (error) {
        // HTTP errors alone do not prove that a relayed mutation never ran.
        markSave(intent, "unknown", `${error instanceof TuningHttpError ? error.reason ?? `HTTP ${error.status}` : "Server connection lost."} Outcome unknown — query this request, never retry blindly.`);
      }
    },
    resolveUnknownSave: async () => {
      const intent = get().saveIntent;
      if (!intent || get().save.phase !== "unknown" || intent.robotId !== robotId()) return;
      try {
        const record = await fetchTuningJob(intent.robotId, intent.requestId);
        if (record.request_id !== intent.requestId || record.state !== "done" || record.outcome === "unknown") return;
        await get().load();
        if (record.outcome === "ok" && typeof record.revision === "number"
          && confirmSave(intent, get().snapshot, record.revision)) return;
        markSave(intent, "conflict", "The request resolved, but current targets differ. Review the saved values and your drafts.");
        set({ saveIntent: null, conflictIds: [...new Set([...get().conflictIds, ...intent.changes.map(change => change.id)])] });
      } catch { /* Status failure never permits a resend or discarding the intent. */ }
    },
    openApplyReview: () => {
      const state = get();
      const plan = state.snapshot?.activationPlan;
      if (applyBlocked(state) || !plan || plan.revision !== state.snapshot?.desiredRevision
        || plan.operation !== "apply" || plan.digest !== state.snapshot.desiredDigest || plan.session_id !== state.snapshot.sessionId) return;
      set({ review: structuredClone({ robotId: robotId(), snapshot: state.snapshot, plan }), apply: IDLE });
    },
    openRestoreReview: () => {
      const state = get();
      const plan = state.snapshot?.restorePlan;
      if (applyBlocked(state) || !plan || plan.operation !== "restore" || plan.revision !== state.snapshot?.lastKnownGoodRevision
        || plan.desired_revision !== state.snapshot.desiredRevision || plan.session_id !== state.snapshot.sessionId) return;
      set({ review: structuredClone({ robotId: robotId(), snapshot: state.snapshot, plan }), apply: IDLE });
    },
    closeApplyReview: () => set({ review: null }),
    confirmApply: async () => {
      const state = get();
      const review = state.review;
      if (!review || applyBlocked(state) || state.apply.phase === "saved") return;
      if (reviewChanged(review, state.snapshot)) {
        set({ apply: { phase: "conflict", detail: "The reviewed revision, session or activation scope changed. Review again.", requestId: null } });
        return;
      }
      if (!review.plan.executable) return;
      const requestId = newRequestId();
      set({ apply: { phase: "saving", detail: null, requestId }, applyIntent: review });
      try {
        const response: TuningResponse = await postTuningRequest(review.robotId, {
          ...(review.plan.operation === "restore"
            ? restoreRequest(review.plan.session_id, review.plan.desired_revision, review.plan.revision, requestId)
            : applyRequest(review.plan.session_id, review.plan.revision, requestId)),
          review: { digest: review.plan.digest, plan_hash: review.plan.plan_hash } });
        const job = response.job;
        const verified = response.request_id === requestId && response.outcome === "ok"
          && job?.state === "succeeded" && job.revision === review.plan.revision
          && job.digest === review.plan.digest && job.plan_hash === review.plan.plan_hash
          && response.snapshot?.committedActiveRevision === review.plan.revision;
        const matched = response.request_id === requestId && review.robotId === robotId();
        if (matched && response.snapshot) mutationGeneration++;
        set({ ...(matched && response.snapshot ? { snapshot: response.snapshot, receivedAt: performance.now() } : {}),
          ...(matched && job ? { job } : {}), apply: { requestId,
            phase: verified && matched ? "saved" : matched && response.outcome === "conflict" ? "conflict"
              : matched && response.outcome === "refused" ? "failed" : "unknown",
            detail: verified && matched ? response.detail : matched && ["refused", "conflict"].includes(response.outcome)
              ? response.detail : `${matched && response.detail ? response.detail + " " : ""}Verified completion of the exact reviewed job was not confirmed. Query its status; do not retry.` } });
      } catch {
        set({ apply: { phase: "unknown", detail: "Apply outcome unknown. Query this request; do not retry or redeploy.", requestId } });
      }
    },
    queryJob: async id => {
      try {
        const requestedRobot = robotId();
        const state = get();
        const record = await fetchTuningJob(requestedRobot, id);
        const intent = state.applyIntent;
        const binding = state.apply.phase === "unknown" && state.apply.requestId && id !== state.apply.requestId
          ? await fetchTuningJob(requestedRobot, state.apply.requestId) : record;
        const job = typeof binding.job_id === "string" && binding.request_id === state.apply.requestId
          ? await fetchTuningJob(requestedRobot, binding.job_id) : record;
        if (requestedRobot !== robotId()) return;
        set({ job });
        if (get().apply.phase !== "unknown" || !intent || intent.robotId !== requestedRobot) return;
        const bound = binding.request_id === state.apply.requestId && binding.job_id === job.job_id;
        if (!bound || job.state !== "succeeded" || job.revision !== intent.plan.revision
          || job.digest !== intent.plan.digest || job.plan_hash !== intent.plan.plan_hash) return;
        await get().load();
        if (get().snapshot?.committedActiveRevision === intent.plan.revision)
          set({ apply: { ...get().apply, phase: "saved", detail: "Exact reviewed job verified; navigation readiness is separate." } });
      } catch { /* Preserve unknown; status never repeats an operation. */ }
    },
  };
});

export function pendingRedeploy(snapshot: NormalizedSnapshot | null) {
  const fields = snapshot?.entries.filter(entry => parameterPending(snapshot, entry)) ?? [];
  return { fields, count: fields.length, services: snapshot?.activationPlan?.services ?? [] };
}
