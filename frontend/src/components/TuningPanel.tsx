import { createContext, useContext, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { isAdministrator, useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTeleopStore } from "../stores/teleopStore";
import {
  BADGE_LABEL,
  editDisabledReason,
  editPolicyText,
  fieldBadge,
  formatBounds,
  freshReadback,
  parseNumericInput,
  REGISTRY_CAPABILITY,
  REGISTRY_GROUPS,
  REGISTRY_PROVENANCE,
  sliderRange,
  valuesEqual,
} from "../lib/tuning";
import type { NormalizedSnapshot, TuningCatalogEntry, TuningValue } from "../lib/tuning";
import { pendingRedeploy, useTuningStore } from "../stores/tuningStore";
import type { SavePhase } from "../stores/tuningStore";
import { SpeedGauges } from "./SpeedGauges";

const SHOWN_GROUPS = ["amcl", "nav2_controller", "nav2_velocity_smoother", "nav2_behavior", "structural_read_only"];
const ObservedValues = createContext<Record<string, TuningValue>>({});

function groupLabel(id: string): string {
  return REGISTRY_GROUPS.find((group) => group.id === id)?.label ?? id;
}

function formatValue(value: TuningValue | null | undefined, entry: TuningCatalogEntry): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    return entry.type === "integer" ? String(value) : Number(value.toFixed(3)).toString();
  }
  return String(value);
}

function unitText(entry: TuningCatalogEntry): string {
  return entry.unit && entry.unit !== "boolean" && entry.unit !== "string" ? entry.unit : "";
}

const PHASE_TEXT: Record<SavePhase, string | null> = {
  idle: null,
  saving: "Saving…",
  saved: "Saved and active",
  saved_pending: "Saved — waiting for safe apply",
  conflict: "Conflict — review the robot values",
  failed: "Failed",
  unknown: "Outcome unknown",
};

function StatusLine({ entry }: { entry: TuningCatalogEntry }) {
  const observed = useContext(ObservedValues)[entry.id];
  const snapshot = useTuningStore((state) => state.snapshot);
  const phase = useTuningStore((state) => state.savePhase[entry.id] ?? "idle");
  const detail = useTuningStore((state) => state.saveDetail[entry.id] ?? null);
  const desired = snapshot?.desired[entry.id];
  const active = snapshot?.active[entry.id];
  const activeKnown = snapshot?.fieldActiveKnown[entry.id] ?? false;
  const pending = snapshot?.pendingKeys.includes(entry.id) ?? false;
  const badge = fieldBadge(entry);
  const showDesired = snapshot?.available && desired !== undefined;
  // Without a saved revision the value is only the starting point the robot
  // reported; calling it "Saved" would claim a durable state that is unknown.
  const desiredIsSaved = snapshot?.desiredRevision !== null && snapshot?.desiredRevision !== undefined;
  const phaseText = phase === "saved_pending" && badge === "redeploy"
    ? "Saved — redeploy required" : PHASE_TEXT[phase];
  return (
    <div className="field-status">
      <span className={`badge badge-${badge}`}>{BADGE_LABEL[badge]}</span>
      {showDesired && <span className="field-saved">{desiredIsSaved ? "Saved" : "Initial YAML value"} {formatValue(desired, entry)} {unitText(entry)}</span>}
      {observed !== undefined && <span className="field-observed">Live readback {formatValue(observed, entry)} {unitText(entry)}</span>}
      {snapshot?.available && activeKnown && <span className="field-active">Verified revision value {formatValue(active, entry)} {unitText(entry)}</span>}
      {snapshot?.available && observed === undefined && <span className="field-active field-unknown">Live readback unavailable</span>}
      {pending && <span className="field-pending">Pending activation</span>}
      {phaseText && <span className={`field-phase phase-${phase}`} role="status">{phaseText}</span>}
      {detail && <span className="field-detail">{detail}</span>}
      {phase === "unknown" && <button className="btn" onClick={() => void useTuningStore.getState().resolveUnknownSave(entry.group)}>Query saved request outcome</button>}
    </div>
  );
}

function NumberField({ entry, editable, disabledReason }: {
  entry: TuningCatalogEntry; editable: boolean; disabledReason: string | null;
}) {
  const observed = useContext(ObservedValues)[entry.id];
  const snapshot = useTuningStore((state) => state.snapshot);
  const draft = useTuningStore((state) => state.drafts[entry.id]);
  const server = useTuningStore((state) => state.snapshot?.desired[entry.id]);
  const conflict = useTuningStore((state) => state.conflictIds.includes(entry.id));
  const setDraft = useTuningStore((state) => state.setDraft);
  const commitGroup = useTuningStore((state) => state.commitGroup);
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dragging = useRef(false);
  // The editable fallback is the robot's exact reported number, never the
  // rounded display text: rounding here would silently swap an in-progress
  // edit's starting point (0.12345 would read back as 0.123).
  const fallback = typeof server === "number" ? String(server) : "";
  // The text field mirrors exactly what the operator typed (a draft) or the
  // robot's reported value. It is never clamped, rounded or replaced with a
  // bound; only the store's validation decides whether a draft may be sent.
  const value = draft ?? fallback;
  // A slider is offered only when the declared bounds can honestly support one.
  const range = sliderRange(entry);
  // An invalid/incomplete draft stays in the text field; the slider reflects the
  // last honest numeric value instead of a clamped replacement.
  const parsedDraft = draft !== undefined ? parseNumericInput(draft, entry) : null;
  const sliderValue = parsedDraft?.ok ? parsedDraft.value!
    : typeof server === "number" ? server : range?.min ?? 0;
  const id = `tuning-${entry.id.replace(/\W/g, "-")}`;
  const commit = () => commitGroup(entry.group);
  const schedule = () => {
    clearTimeout(debounce.current);
    debounce.current = setTimeout(commit, 400);
  };
  useEffect(() => () => clearTimeout(debounce.current), []);
  const numericKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") { event.preventDefault(); clearTimeout(debounce.current); commit(); }
  };
  const sliderRelease = (event: ReactPointerEvent<HTMLInputElement>) => {
    dragging.current = false;
    clearTimeout(debounce.current);
    if (event.type === "pointerup") commit();
  };
  return (
    <div className={`field-row${conflict ? " field-conflict" : ""}`}>
      <label className="field-label" htmlFor={id}>{entry.label}</label>
      <p className="field-key">{entry.source.node} · {entry.source.key}{entry.source.index !== null ? `[${entry.source.index}]` : ""}</p>
      <p className="field-help">
        {formatBounds(entry)} {editPolicyText(entry)}
        {entry.bounds.note ? ` ${entry.bounds.note}` : ""}
        {entry.effective_ceiling_ref && " Bounded by the active speed ceiling."}
      </p>
      <div className="field-controls">
        {editable ? (
          <>
            <input id={id} className="field-number" type="text" inputMode="decimal" value={value}
              aria-describedby={`${id}-status`}
              onChange={(event) => setDraft(entry.id, event.target.value)}
              onBlur={() => { clearTimeout(debounce.current); commit(); }}
              onKeyDown={numericKey} />
            {range && (
              <input className="field-slider" type="range" aria-label={`${entry.label} slider`}
                min={range.min} max={range.max} step={range.step} value={sliderValue}
                onPointerDown={() => { dragging.current = true; clearTimeout(debounce.current); }}
                onChange={(event) => { setDraft(entry.id, event.target.value); if (!dragging.current) schedule(); }}
                onPointerUp={sliderRelease} onPointerCancel={sliderRelease}
                onKeyUp={(event) => { if (event.key.startsWith("Arrow") || event.key === "Home" || event.key === "End") schedule(); }} />
            )}
            <span className="field-unit">{unitText(entry)}</span>
          </>
        ) : (
          <span className="field-readonly-value">{observed !== undefined
            ? `${formatValue(observed, entry)} ${unitText(entry)} (live readback)`
            : snapshot?.available ? "— (live readback unavailable)"
              : `${formatValue(entry.default, entry)} ${unitText(entry)} (YAML starting value; not live readback)`}</span>
        )}
      </div>
      {!editable && disabledReason && <p className="field-disabled-reason">{disabledReason}</p>}
      <div id={`${id}-status`}><StatusLine entry={entry} /></div>
    </div>
  );
}

function CollapsibleGroup({ group, entries, editable, disabledReason }: {
  group: string; entries: TuningCatalogEntry[]; editable: boolean; disabledReason: string | null;
}) {
  const editableEntries = entries.filter((entry) => entry.accepts_edits && !entry.not_applicable);
  return (
    <details className="tuning-group">
      <summary>{groupLabel(group)} <span className="tuning-group-count">{editableEntries.length} parameter{editableEntries.length === 1 ? "" : "s"}</span></summary>
      {entries.map((entry) => <NumberField key={entry.id} entry={entry}
        editable={editable && entry.accepts_edits && !entry.protected && !entry.not_applicable && (entry.type === "number" || entry.type === "integer")}
        disabledReason={disabledReason ?? "This parameter is read-only or not approved for activation."} />)}
    </details>
  );
}

function ConfirmDialog({ title, children, confirmLabel, onConfirm, onCancel, confirmDisabled }: {
  title: string; children: ReactNode; confirmLabel: string;
  onConfirm: () => void; onCancel: () => void; confirmDisabled?: boolean;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { confirmRef.current?.focus(); }, []);
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <Dialog.Portal><Dialog.Overlay className="tuning-dialog-backdrop">
      <Dialog.Content className="tuning-dialog" aria-describedby={undefined}>
        <Dialog.Title>{title}</Dialog.Title>
        {children}
        <div className="tuning-dialog-actions">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button ref={confirmRef} className="btn danger" disabled={confirmDisabled} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </Dialog.Content>
      </Dialog.Overlay></Dialog.Portal>
    </Dialog.Root>
  );
}

function DiffList({ entries, snapshot }: { entries: TuningCatalogEntry[]; snapshot: NormalizedSnapshot }) {
  return (
    <ul className="tuning-diff">
      {entries.map((entry) => (
        <li key={entry.id}>
          <strong>{entry.label}</strong>: {formatValue(snapshot.desired[entry.id], entry)} {unitText(entry)}
          {" "}(active {snapshot.fieldActiveKnown[entry.id] ? formatValue(snapshot.active[entry.id], entry) : "unknown"})
        </li>
      ))}
    </ul>
  );
}

function RedeployDialog({ snapshot }: { snapshot: NormalizedSnapshot }) {
  const { fields, count, services } = pendingRedeploy(snapshot);
  const open = useTuningStore((state) => state.redeployOpen);
  const redeploy = useTuningStore((state) => state.redeploy);
  const reviewedRevision = useTuningStore((state) => state.reviewedRevision);
  const reviewedDigest = useTuningStore((state) => state.reviewedDigest);
  const job = useTuningStore((state) => state.job);
  const closeRedeploy = useTuningStore((state) => state.closeRedeploy);
  const confirmRedeploy = useTuningStore((state) => state.confirmRedeploy);
  const queryJob = useTuningStore((state) => state.queryJob);
  if (!open) return null;
  const jobId = typeof job?.job_id === "string" ? job.job_id : typeof job?.id === "string" ? job.id : undefined;
  return (
    <ConfirmDialog title="Redeploy with new params" confirmLabel="Deploy the reviewed revision"
      onCancel={closeRedeploy} onConfirm={() => void confirmRedeploy()}
      confirmDisabled={redeploy.phase === "saving" || !snapshot.safeApply.allowed
        || !snapshot.activationSupported || reviewedRevision !== snapshot.desiredRevision}>
      <p>This activates the saved parameters and reloads the affected robot scope. It does not rebuild images or restart the whole system.</p>
      <p><strong>Reviewed revision {reviewedRevision ?? "unknown"}</strong>{reviewedDigest ? ` (${reviewedDigest.slice(0, 12)}…)` : ""}. The exact reviewed revision is deployed, never “latest”.</p>
      <DiffList entries={fields} snapshot={snapshot} />
      <p>{count} change{count === 1 ? "" : "s"} affect: {services.length ? services.join(", ") : "no services"}.</p>
      <p role="alert">Motion may be interrupted while the affected services reload. Only deploy while the robot is stationary and idle.</p>
      {!snapshot.safeApply.allowed && <p role="status">{snapshot.safeApply.reason}</p>}
      {redeploy.detail && <p className={`tuning-op detail-${redeploy.phase}`} role="status">{redeploy.detail}</p>}
      {jobId && <p className="tuning-job">Job {jobId}: {typeof job?.state === "string" ? job.state : "status unknown"}
        {" "}<button className="btn" onClick={() => void queryJob(jobId)}>Check progress</button></p>}
    </ConfirmDialog>
  );
}

function RestoreDialog({ snapshot }: { snapshot: NormalizedSnapshot }) {
  const open = useTuningStore((state) => state.restoreOpen);
  const restore = useTuningStore((state) => state.restore);
  const closeRestore = useTuningStore((state) => state.closeRestore);
  const confirmRestore = useTuningStore((state) => state.confirmRestore);
  const target = useTuningStore((state) => state.restoreTarget);
  if (!open) return null;
  return (
    <ConfirmDialog title="Restore last known good" confirmLabel={`Restore revision ${target ?? "?"}`}
      onCancel={closeRestore} onConfirm={() => void confirmRestore()}
      confirmDisabled={restore.phase === "saving" || target === null}>
      <p>Restoring is a new audited activation of the last known good revision; it never deletes history.</p>
      <p>Current saved revision {snapshot.desiredRevision ?? "unknown"} → restore to <strong>revision {target ?? "unknown"}</strong> (exact revision, not “latest”).</p>
      <p role="alert">Restoring reloads the affected services and may interrupt motion. Only restore while stationary.</p>
      {restore.detail && <p className={`tuning-op detail-${restore.phase}`} role="status">{restore.detail}</p>}
    </ConfirmDialog>
  );
}

/** End this tab's own manual session when settings are entered; never arm. */
function releaseManualSession() {
  const manual = useTeleopStore.getState();
  if (manual.phase !== "idle") manual.end("Tuning settings opened — manual input ended.");
}

export function TuningPanel() {
  const [tick, setTick] = useState(() => performance.now());
  useEffect(() => {
    const timer = setInterval(() => setTick(performance.now()), 500);
    return () => clearInterval(timer);
  }, []);
  const now = Math.max(tick, performance.now());
  const baseState = useTelemetryStore((state) => state.baseState);
  const baseStateAt = useTelemetryStore((state) => state.baseStateAt);
  const snapshot = useTuningStore((state) => state.snapshot);
  const status = useTuningStore((state) => state.status);
  const load = useTuningStore((state) => state.load);
  const job = useTuningStore((state) => state.job);
  const apply = useTuningStore((state) => state.apply);
  const applyLive = useTuningStore((state) => state.applyLive);
  const openRedeploy = useTuningStore((state) => state.openRedeploy);
  const openRestore = useTuningStore((state) => state.openRestore);
  const user = useAuthStore((state) => state.user);
  const telemetryConnected = useTelemetryStore((state) => state.wsConnected);
  const admin = isAdministrator(user);
  const wasConnected = useRef(telemetryConnected);
  const receivedAt = useTuningStore((state) => state.receivedAt);
  const observed = telemetryConnected
    ? freshReadback(snapshot, receivedAt, now, baseState?.session_generation ?? null) : null;
  const limits = observed?.limits ?? null;
  const limitsAt = observed && receivedAt !== null ? receivedAt - observed.ageS * 1000 : null;

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    // Reconnect fetches the robot's state; it never replays drafts or jobs.
    if (telemetryConnected && !wasConnected.current) void load();
    wasConnected.current = telemetryConnected;
  }, [telemetryConnected, load]);
  useEffect(() => {
    if (!telemetryConnected) return;
    const timer = setInterval(() => { void load(); }, 2000);
    return () => clearInterval(timer);
  }, [telemetryConnected, load]);

  const disabledReason = !telemetryConnected ? "The dashboard connection is offline — drafts are unsaved."
    : editDisabledReason(snapshot, admin)
      ?? (!baseState?.link_connected || baseStateAt === null || now - baseStateAt < 0
        || now - baseStateAt > 500 || !Number.isFinite(baseState.telemetry_age)
        || baseState.telemetry_age < 0 || baseState.telemetry_age > .5
        ? "The SBC link is disconnected or stale — editing is disabled." : null);
  const editable = disabledReason === null;
  const pending = pendingRedeploy(snapshot);
  const livePending = snapshot?.entries.filter((entry) => fieldBadge(entry) === "live"
    && snapshot.pendingKeys.includes(entry.id)) ?? [];
  const busy = job !== null && typeof job.state === "string" && !["succeeded", "failed", "canceled", "done"].includes(job.state);

  const redeployReason = !admin ? "An administrator account is required."
    : !snapshot?.available ? "The robot tuning state is unknown."
    : !snapshot.activationSupported ? "The robot has not enabled tuning activation."
    : !snapshot.safeApply.allowed ? snapshot.safeApply.reason
    : pending.count === 0 ? "No saved restart-required changes are waiting."
    : !telemetryConnected ? "The dashboard server is offline."
    : busy ? "A deployment job is already running."
    : null;
  const canRedeploy = redeployReason === null;

  const ceilingEntries = snapshot?.entries.filter((entry) => entry.group === "speed_ceiling") ?? [];
  const groups = SHOWN_GROUPS
    .map((group) => ({ group, entries: snapshot?.entries.filter((entry) => entry.group === group) ?? [] }))
    .filter(({ entries }) => entries.length > 0);

  return (
    <ObservedValues.Provider value={observed?.values ?? {}}>
    <section className="tuning-panel" aria-label="Robot tuning"
      onFocusCapture={releaseManualSession} onPointerDownCapture={releaseManualSession}>
      <h2>Speed and tuning</h2>
      <p className="subtext">Speed settings are ceilings, not commanded speeds. Nav2 keeps its normal curvature, obstacle and approach slowdowns.</p>

      {status === "loading" && !snapshot && <p role="status">Loading the robot's tuning state…</p>}
      {snapshot && !snapshot.available && (
        <p className="nav-disabled-banner" role="status">
          {snapshot.reason} The list below is a bundled source catalogue, shown as read-only candidates — not active robot values.
        </p>
      )}
      {snapshot?.available && !editable && <p className="tuning-readonly-banner" role="status">{disabledReason}</p>}
      {snapshot?.available && editable && <p className="tuning-writable-banner" role="status">Administrator: complete valid changes save to the robot’s own configuration.</p>}
      {!snapshot?.catalogFromServer && (
        <p className="subtext">Source catalogue from {REGISTRY_PROVENANCE.baseline_commit.slice(0, 10)} ({REGISTRY_PROVENANCE.inspected}). {REGISTRY_CAPABILITY.advertised ? "" : "The runtime capability is not advertised."}</p>
      )}

      <SpeedGauges limits={telemetryConnected ? limits : null} limitsAt={limitsAt} />

      {Object.keys(useTuningStore.getState().drafts).length > 0 && (
        <div className="tuning-drafts">
          <p>Unsaved drafts are kept in this tab only. Reconnect never replays them.</p>
          <button className="btn" onClick={() => useTuningStore.getState().discardAllDrafts()}>Discard unsaved drafts</button>
          <button className="btn" onClick={() => void load()}>Refresh robot state</button>
        </div>
      )}

      {ceilingEntries.length > 0 && (
        <section className="tuning-ceilings" aria-label="Maximum speed ceilings">
          <h3>Maximum speeds</h3>
          {ceilingEntries.map((entry) => <NumberField key={entry.id} entry={entry}
            editable={editable && entry.accepts_edits && !entry.protected}
            disabledReason={disabledReason ?? "The usable speed range is not commissioned."} />)}
        </section>
      )}

      {groups.map(({ group, entries }) => (
        <CollapsibleGroup key={group} group={group} entries={entries} editable={editable} disabledReason={disabledReason} />
      ))}

      {livePending.length > 0 && (
        <div className="tuning-live-apply">
          {snapshot?.safeApply.allowed
            ? <button className="btn" onClick={() => void applyLive()} disabled={apply.phase === "saving"}>Apply live changes</button>
            : <button className="btn" disabled title={snapshot?.safeApply.reason}>Apply live changes</button>}
          <span>{snapshot?.safeApply.allowed ? "Live-capable saved changes are ready to apply." : `Live apply unavailable: ${snapshot?.safeApply.reason}`}</span>
        </div>
      )}

      <div className="tuning-sticky" aria-label="Pending deployment">
        <button className={`btn primary${canRedeploy ? " is-active" : ""}`} onClick={openRedeploy}
          disabled={!canRedeploy} title={redeployReason ?? undefined}>
          Redeploy with new params{pending.count > 0 ? ` (${pending.count})` : ""}
        </button>
        {canRedeploy && <span className="tuning-sticky-detail">Affects: {pending.services.join(", ")}</span>}
        {redeployReason && <span className="tuning-sticky-detail">{redeployReason}</span>}
        {admin && snapshot?.lastKnownGoodRevision !== null && snapshot?.lastKnownGoodRevision !== undefined && (
          <button className="btn" onClick={openRestore} disabled={!!disabledReason || busy || !snapshot.safeApply.allowed}>Restore last known good</button>
        )}
      </div>

      {snapshot?.available && <RedeployDialog snapshot={snapshot} />}
      {snapshot?.available && <RestoreDialog snapshot={snapshot} />}
    </section>
    </ObservedValues.Provider>
  );
}

/** Exposed for tests: whether a saved value equals the runtime read-back. */
export const tuningValuesEqual = valuesEqual;
