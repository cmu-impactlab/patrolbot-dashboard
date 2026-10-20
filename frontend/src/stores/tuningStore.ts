import { create } from "zustand";
import { fetchTuningJob, fetchTuningSnapshot, postTuningRequest, TuningHttpError } from "../api/tuning";
import {
  affectedServices,
  applyRequest,
  effectiveValues,
  newRequestId,
  parseNumericInput,
  redeployRequest,
  registryEntry,
  restoreRequest,
  saveRequest,
  unappliedRestartFields,
  validateBatch,
} from "../lib/tuning";
import type {
  BatchChange,
  CanonicalRequest,
  NormalizedSnapshot,
  TuningCatalogEntry,
  TuningJob,
  TuningResponse,
} from "../lib/tuning";
import { useTelemetryStore } from "./telemetryStore";
import { isAdministrator, useAuthStore } from "./authStore";

export type SavePhase = "idle" | "saving" | "saved" | "saved_pending" | "conflict" | "failed" | "unknown";

export interface OperationState {
  phase: SavePhase;
  detail: string | null;
  requestId: string | null;
}

export interface TuningState {
  status: "idle" | "loading" | "ready" | "unavailable";
  snapshot: NormalizedSnapshot | null;
  receivedAt: number | null;
  loadReason: string | null;
  drafts: Record<string, string>;
  savePhase: Record<string, SavePhase>;
  saveDetail: Record<string, string | null>;
  /** Idempotency keys, keyed by edit group, so a repeated click reuses one UUID. */
  saveRequestIds: Record<string, string>;
  /** Revision the current drafts were typed against; a change forces explicit resolve. */
  draftBaseRevision: number | null;
  conflictIds: string[];
  /** Immutable revision captured when the deploy confirmation was opened. */
  reviewedRevision: number | null;
  reviewedDigest: string | null;
  redeployOpen: boolean;
  redeploy: OperationState;
  job: TuningJob | null;
  apply: OperationState;
  restoreOpen: boolean;
  restore: OperationState;
  restoreTarget: number | null;
  restoreBase: number | null;
  restoreSession: string | null;

  load: () => Promise<void>;
  setDraft: (id: string, text: string) => void;
  clearDraft: (id: string) => void;
  discardAllDrafts: () => void;
  connectionLost: () => void;
  commitGroup: (group: string) => Promise<void>;
  applyLive: () => Promise<void>;
  openRedeploy: () => void;
  closeRedeploy: () => void;
  confirmRedeploy: () => Promise<void>;
  queryJob: (jobId: string) => Promise<void>;
  resolveUnknownSave: (group: string) => Promise<void>;
  openRestore: () => void;
  closeRestore: () => void;
  confirmRestore: () => Promise<void>;
}

const IDLE: OperationState = { phase: "idle", detail: null, requestId: null };
const robotId = () => useTelemetryStore.getState().robotId;

function withPhase(current: Record<string, SavePhase>, ids: string[], phase: SavePhase): Record<string, SavePhase> {
  const next = { ...current };
  for (const id of ids) next[id] = phase;
  return next;
}

function withDetail(current: Record<string, string | null>, ids: string[], detail: string | null): Record<string, string | null> {
  const next = { ...current };
  for (const id of ids) next[id] = detail;
  return next;
}

/** Human-facing status for a saved value, using only server-reported state. */
export function savedPhaseFor(snapshot: NormalizedSnapshot | null, entry: TuningCatalogEntry): SavePhase {
  if (!snapshot?.available || snapshot.desiredRevision === null) return "idle";
  const verifiedRevision = snapshot.activeRevision === snapshot.desiredRevision;
  if (snapshot.pendingKeys.includes(entry.id) || !snapshot.fieldActiveKnown[entry.id]
      || (!verifiedRevision && snapshot.active[entry.id] !== snapshot.desired[entry.id])) return "saved_pending";
  return "saved";
}

export const useTuningStore = create<TuningState>((set, get) => {
  /** Apply a mutation response: merge the new snapshot and update field phases. */
  const absorb = (response: TuningResponse, ids: string[]) => {
    const snapshot = response.snapshot ?? get().snapshot;
    const detail = response.detail || null;
    if (response.outcome === "ok") {
      // A durable save is only "saved" once the robot returns a snapshot.
      const phase: SavePhase = snapshot && ids.every((id) => snapshot.fieldActiveKnown[id]
        && snapshot.desired[id] === snapshot.active[id] && !snapshot.pendingKeys.includes(id)) ? "saved" : "saved_pending";
      set({
        snapshot,
        receivedAt: performance.now(),
        loadReason: null,
        status: snapshot?.available ? "ready" : get().status,
        savePhase: withPhase(get().savePhase, ids, response.snapshot ? phase : "unknown"),
        saveDetail: withDetail(get().saveDetail, ids, response.snapshot ? detail : "Saved, but the robot returned no snapshot — status unknown."),
      });
      if (response.snapshot) {
        const drafts = { ...get().drafts };
        for (const id of ids) delete drafts[id];
        set({ drafts, draftBaseRevision: snapshot?.desiredRevision ?? get().draftBaseRevision });
        set({ saveRequestIds: {} });
      }
      return;
    }
    if (response.outcome === "conflict") {
      set({
        snapshot,
        savePhase: withPhase(get().savePhase, ids, "conflict"),
        saveDetail: withDetail(get().saveDetail, ids, detail ?? "Another change landed first. Review the robot values and reapply."),
        conflictIds: [...new Set([...get().conflictIds, ...ids])],
      });
      return;
    }
    if (response.outcome === "unknown") {
      set({
        snapshot,
        savePhase: withPhase(get().savePhase, ids, "unknown"),
        saveDetail: withDetail(get().saveDetail, ids, detail ?? "Outcome unknown — the request was not retried. Reconnect to query its status."),
      });
      return;
    }
    set({
      snapshot: response.snapshot ?? get().snapshot,
      savePhase: withPhase(get().savePhase, ids, "failed"),
      saveDetail: withDetail(get().saveDetail, ids, detail ?? "The robot refused or failed this change."),
    });
  };

  const fail = (ids: string[], detail: string) => {
    set({
      savePhase: withPhase(get().savePhase, ids, "unknown"),
      saveDetail: withDetail(get().saveDetail, ids, detail),
    });
  };

  const post = async (request: CanonicalRequest, ids: string[]) => {
    try {
      const response = await postTuningRequest(robotId(), request);
      if (response.request_id !== request.request_id || (response.outcome === "ok" &&
        (!response.snapshot?.available || request.changes?.some(change => response.snapshot?.desired[change.id] !== change.value)))) {
        fail(ids, "The robot did not confirm this exact request and desired values — outcome unknown.");
        return;
      }
      absorb(response, ids);
    } catch (error) {
      if (error instanceof TuningHttpError) {
        const detail = error.reason ?? `The tuning service rejected the request (HTTP ${error.status}).`;
        // A 4xx is a definite refusal; a 5xx/503 is an unknown/unsafe state.
        if (error.status >= 400 && error.status < 500) {
          set({ savePhase: withPhase(get().savePhase, ids, "failed"), saveDetail: withDetail(get().saveDetail, ids, detail) });
        } else {
          fail(ids, `${detail} Outcome unknown — the request was not retried.`);
        }
      } else {
        fail(ids, "Could not reach the dashboard server — outcome unknown. Do not retry blindly.");
      }
    }
  };

  return {
    status: "idle",
    snapshot: null,
    receivedAt: null,
    loadReason: null,
    drafts: {},
    savePhase: {},
    saveDetail: {},
    saveRequestIds: {},
    draftBaseRevision: null,
    conflictIds: [],
    reviewedRevision: null,
    reviewedDigest: null,
    redeployOpen: false,
    redeploy: IDLE,
    job: null,
    apply: IDLE,
    restoreOpen: false,
    restore: IDLE,
    restoreTarget: null,
    restoreBase: null,
    restoreSession: null,

    load: async () => {
      const requestedRobot = robotId();
      const requestStarted = performance.now();
      set({ status: "loading" });
      const snapshot = await fetchTuningSnapshot(requestedRobot);
      if (requestedRobot !== robotId() || (get().receivedAt !== null && get().receivedAt! > requestStarted)) return;
      const drafts = get().drafts;
      const dirty = Object.keys(drafts);
      // Reconnect must not silently replay or discard drafts. If the saved
      // revision moved underneath the drafts, force explicit resolution.
      const base = get().draftBaseRevision;
      const moved = dirty.length > 0 && ((base !== null && snapshot.desiredRevision !== null && snapshot.desiredRevision !== base)
        || (get().snapshot?.sessionId && snapshot.sessionId !== get().snapshot?.sessionId));
      const phases = { ...get().savePhase };
      for (const [id, phase] of Object.entries(phases)) {
        if (phase !== "saved" && phase !== "saved_pending") continue;
        const entry = snapshot.entries.find(candidate => candidate.id === id) ?? registryEntry(id);
        phases[id] = entry ? savedPhaseFor(snapshot, entry) : "idle";
      }
      set({
        snapshot,
        receivedAt: performance.now(),
        status: snapshot.available ? "ready" : "unavailable",
        loadReason: snapshot.available ? null : snapshot.reason,
        draftBaseRevision: base === null ? snapshot.desiredRevision : base,
        conflictIds: moved ? [...new Set([...get().conflictIds, ...dirty])] : get().conflictIds,
        savePhase: moved ? withPhase(phases, dirty, "conflict") : phases,
        saveDetail: moved ? withDetail(get().saveDetail, dirty, "The robot's saved revision changed while you were editing. Review and reapply, or discard your draft.") : get().saveDetail,
        job: snapshot.latestJob ?? get().job,
      });
    },

    setDraft: (id, text) => {
      const drafts = { ...get().drafts };
      drafts[id] = text;
      set({ drafts, savePhase: { ...get().savePhase, [id]: "idle" } });
    },

    clearDraft: (id) => {
      const drafts = { ...get().drafts };
      delete drafts[id];
      set({ drafts, conflictIds: get().conflictIds.filter((value) => value !== id) });
    },

    discardAllDrafts: () => set({ drafts: {}, conflictIds: [], savePhase: {}, saveDetail: {} }),

    connectionLost: () => {
      const ids = Object.keys(get().drafts).filter(id =>
        !["unknown", "saving"].includes(get().savePhase[id]));
      if (!ids.length) return;
      set({ conflictIds: [...new Set([...get().conflictIds, ...ids])],
        savePhase: withPhase(get().savePhase, ids, "conflict"),
        saveDetail: withDetail(get().saveDetail, ids,
          "Connection changed before this draft was saved. Review the robot values and explicitly reapply; reconnect never saves drafts.") });
    },

    commitGroup: async (group) => {
      const snapshot = get().snapshot;
      if (!isAdministrator(useAuthStore.getState().user) || !useTelemetryStore.getState().wsConnected
        || !snapshot?.writable) return;
      const groupEntries = snapshot?.entries ?? [];
      const ids = Object.keys(get().drafts).filter((id) =>
        (groupEntries.find((entry) => entry.id === id) ?? registryEntry(id))?.group === group);
      if (!snapshot?.available || !snapshot.sessionId || ids.length === 0) return;
      if (ids.some((id) => get().conflictIds.includes(id) || ["unknown", "saving"].includes(get().savePhase[id]))) return;
      const entries = snapshot.entries;
      const changes: BatchChange[] = [];
      let invalid: { id: string; reason: string } | null = null;
      for (const id of ids) {
        const entry = entries.find((candidate) => candidate.id === id) ?? registryEntry(id);
        if (!entry) { invalid = { id, reason: "Unknown field." }; break; }
        if (!entry.accepts_edits || entry.protected || entry.not_applicable) {
          invalid = { id, reason: "This field is not approved for editing." }; break;
        }
        const parsed = parseNumericInput(get().drafts[id], entry);
        if (!parsed.ok) { invalid = { id, reason: parsed.reason ?? "Invalid value." }; break; }
        changes.push({ id, value: parsed.value! });
      }
      if (invalid) {
        // A batch is validated as a whole; nothing is sent.
        set({ savePhase: withPhase(get().savePhase, [invalid.id], "failed"), saveDetail: withDetail(get().saveDetail, [invalid.id], invalid.reason) });
        return;
      }
      const validation = validateBatch(changes, effectiveValues(snapshot), entries);
      if (!validation.ok) {
        set({
          savePhase: withPhase(get().savePhase, validation.ids, "failed"),
          saveDetail: withDetail(get().saveDetail, validation.ids, validation.reason),
        });
        return;
      }
      const baseRevision = snapshot.desiredRevision;
      if (baseRevision === null) {
        set({ savePhase: withPhase(get().savePhase, ids, "failed"),
          saveDetail: withDetail(get().saveDetail, ids, "The robot did not report a saved revision; refusing a compare-and-swap write.") });
        return;
      }
      const requestId = get().saveRequestIds[group] ?? newRequestId();
      set({
        saveRequestIds: { ...get().saveRequestIds, [group]: requestId },
        draftBaseRevision: baseRevision,
        savePhase: withPhase(get().savePhase, ids, "saving"),
        saveDetail: withDetail(get().saveDetail, ids, null),
      });
      await post(saveRequest(snapshot.sessionId, baseRevision, changes, requestId), ids);
    },

    applyLive: async () => {
      const snapshot = get().snapshot;
      if (!snapshot?.available || !snapshot.sessionId || snapshot.desiredRevision === null) return;
      if (!isAdministrator(useAuthStore.getState().user) || !useTelemetryStore.getState().wsConnected
        || !snapshot.activationSupported || !snapshot.safeApply.allowed
        || ["saving", "unknown"].includes(get().apply.phase)) return;
      set({ apply: { phase: "saving", detail: null, requestId: null } });
      try {
        const response = await postTuningRequest(robotId(), applyRequest(snapshot.sessionId, snapshot.desiredRevision));
        set({
          snapshot: response.snapshot ?? snapshot,
          apply: { phase: response.outcome === "ok" ? "saved" : response.outcome === "conflict" ? "conflict" : response.outcome === "unknown" ? "unknown" : "failed",
            detail: response.detail || null, requestId: response.request_id },
          job: response.job ?? get().job,
        });
      } catch (error) {
        const detail = error instanceof TuningHttpError ? (error.reason ?? `HTTP ${error.status}`) : "Could not reach the dashboard server.";
        set({ apply: { phase: "unknown", detail: `${detail} Outcome unknown — not retried.`, requestId: null } });
      }
    },

    openRedeploy: () => set({
      redeployOpen: true,
      reviewedRevision: get().snapshot?.desiredRevision ?? null,
      reviewedDigest: get().snapshot?.desiredDigest ?? null,
      redeploy: get().reviewedRevision !== get().snapshot?.desiredRevision
        && get().redeploy.phase !== "unknown" ? { ...IDLE } : get().redeploy,
    }),

    closeRedeploy: () => set({ redeployOpen: false }),

    confirmRedeploy: async () => {
      const snapshot = get().snapshot;
      const reviewed = get().reviewedRevision;
      if (!snapshot?.available || !snapshot.sessionId || reviewed === null) return;
      if (!isAdministrator(useAuthStore.getState().user) || !useTelemetryStore.getState().wsConnected
        || !snapshot.activationSupported || !snapshot.safeApply.allowed
        || ["saving", "unknown", "saved"].includes(get().redeploy.phase)) return;
      // The reviewed revision is immutable: if it moved, do not deploy "latest".
      if (snapshot.desiredRevision !== reviewed || (get().reviewedDigest !== null && snapshot.desiredDigest !== get().reviewedDigest)) {
        set({ redeploy: { phase: "conflict", detail: "The saved revision changed after review — review the new revision before deploying.", requestId: get().redeploy.requestId } });
        return;
      }
      const requestId = get().redeploy.requestId ?? newRequestId();
      set({ redeploy: { phase: "saving", detail: null, requestId } });
      try {
        const response = await postTuningRequest(robotId(), redeployRequest(snapshot.sessionId, reviewed, requestId));
        const job = response.job ?? response.snapshot?.latestJob ?? get().job;
        set({
          snapshot: response.snapshot ?? snapshot,
          job,
          redeploy: {
            phase: response.outcome === "ok" ? "saved" : response.outcome === "conflict" ? "conflict" : response.outcome === "unknown" ? "unknown" : "failed",
            detail: response.detail || null,
            requestId,
          },
        });
      } catch (error) {
        const detail = error instanceof TuningHttpError ? (error.reason ?? `HTTP ${error.status}`) : "Could not reach the dashboard server.";
        set({ redeploy: { phase: "unknown", detail: `${detail} Outcome unknown — query the same job; do not retry.`, requestId } });
      }
    },

    queryJob: async (jobId) => {
      try {
        const job = await fetchTuningJob(robotId(), jobId);
        set({ job });
      } catch (error) {
        set({ redeploy: { ...get().redeploy, detail: error instanceof TuningHttpError ? error.reason ?? `HTTP ${error.status}` : "Could not reach the dashboard server." } });
      }
    },

    resolveUnknownSave: async group => {
      const id = get().saveRequestIds[group];
      if (!id) return;
      try {
        const record = await fetchTuningJob(robotId(), id);
        if (record.state !== "done" || record.outcome === "unknown") return;
        await get().load();
        const ids = Object.keys(get().drafts).filter(key => registryEntry(key)?.group === group);
        const snapshot = get().snapshot;
        const confirmed = record.outcome === "ok" && snapshot?.available && ids.every(key => {
          const parsed = parseNumericInput(get().drafts[key], registryEntry(key)!);
          return parsed.ok && snapshot.desired[key] === parsed.value;
        });
        if (confirmed) {
          const drafts = { ...get().drafts };
          for (const key of ids) delete drafts[key];
          set({ drafts, savePhase: withPhase(get().savePhase, ids, "saved_pending"), saveRequestIds: {} });
        } else {
          set({ savePhase: withPhase(get().savePhase, ids, "conflict"), conflictIds: [...new Set([...get().conflictIds, ...ids])] });
        }
      } catch { /* Keep unknown; query failure is never permission to replay. */ }
    },

    openRestore: () => set({ restoreOpen: true,
      restoreTarget: get().snapshot?.lastKnownGoodRevision ?? null,
      restoreBase: get().snapshot?.desiredRevision ?? null,
      restoreSession: get().snapshot?.sessionId ?? null,
      restore: { ...get().restore } }),
    closeRestore: () => set({ restoreOpen: false }),

    confirmRestore: async () => {
      const snapshot = get().snapshot;
      const target = get().restoreTarget;
      if (!snapshot?.available || !snapshot.sessionId || snapshot.desiredRevision === null || target === null) return;
      if (!isAdministrator(useAuthStore.getState().user) || !useTelemetryStore.getState().wsConnected
        || !snapshot.activationSupported || !snapshot.safeApply.allowed
        || ["saving", "unknown", "saved"].includes(get().restore.phase)) return;
      if (snapshot.sessionId !== get().restoreSession || snapshot.desiredRevision !== get().restoreBase
        || snapshot.lastKnownGoodRevision !== target) {
        set({ restore: { ...get().restore, phase: "conflict", detail: "The reviewed restore state changed; review it again." } });
        return;
      }
      const requestId = get().restore.requestId ?? newRequestId();
      set({ restore: { phase: "saving", detail: null, requestId } });
      try {
        const response = await postTuningRequest(robotId(), restoreRequest(snapshot.sessionId, snapshot.desiredRevision, target, requestId));
        set({
          snapshot: response.snapshot ?? snapshot,
          job: response.job ?? get().job,
          restore: {
            phase: response.outcome === "ok" ? "saved" : response.outcome === "conflict" ? "conflict" : response.outcome === "unknown" ? "unknown" : "failed",
            detail: response.detail || null,
            requestId,
          },
        });
      } catch (error) {
        const detail = error instanceof TuningHttpError ? (error.reason ?? `HTTP ${error.status}`) : "Could not reach the dashboard server.";
        set({ restore: { phase: "unknown", detail: `${detail} Outcome unknown — not retried.`, requestId } });
      }
    },
  };
});

/** Derived helper used by both the panel and the sticky action bar. */
export function pendingRedeploy(snapshot: NormalizedSnapshot | null) {
  const fields = snapshot ? unappliedRestartFields(snapshot) : [];
  return { fields, count: fields.length, services: affectedServices(fields) };
}
