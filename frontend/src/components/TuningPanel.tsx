import { createContext, useContext, useEffect, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { isAdministrator, useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTeleopStore } from "../stores/teleopStore";
import {
  editDisabledReason,
  editPolicyText,
  fieldBadge,
  formatBounds,
  parameterPending,
  parseNumericInput,
  BUNDLED_ENTRIES,
  REGISTRY_CAPABILITY,
  REGISTRY_GROUPS,
  REGISTRY_PROVENANCE,
  sliderRange,
  valuesEqual,
} from "../lib/tuning";
import type { NormalizedSnapshot, TuningCatalogEntry, TuningValue } from "../lib/tuning";
import { dirtyDraftIds, pendingRedeploy, saveDisabledReason, useTuningStore } from "../stores/tuningStore";
import type { SavePhase } from "../stores/tuningStore";
import { useTuningReadback } from "../lib/useTuningReadback";

const ObservedValues = createContext<{ values: Record<string, TuningValue>; fresh: boolean; ageS: number | null }>(
  { values: {}, fresh: false, ageS: null });

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
  saved: null,
  saved_pending: "Saved — waiting for safe apply",
  conflict: "Conflict — review the robot values",
  failed: "Failed",
  unknown: "Outcome unknown",
};

function StatusLine({ entry }: { entry: TuningCatalogEntry }) {
  const snapshot = useTuningStore((state) => state.snapshot);
  const phase = useTuningStore((state) => state.savePhase[entry.id] ?? "idle");
  const detail = useTuningStore((state) => state.saveDetail[entry.id] ?? null);
  const pending = parameterPending(snapshot, entry);
  const badge = fieldBadge(entry);
  const phaseText = phase === "saved_pending" ? null : PHASE_TEXT[phase];
  if (!pending && !phaseText && !detail && phase !== "unknown") return null;
  return (
    <div className="field-status">
      {pending && <span className={`badge badge-${badge}`}>
        Apply required</span>}
      {phaseText && <span className={`field-phase phase-${phase}`} role="status">{phaseText}</span>}
      {detail && <span className="field-detail">{detail}</span>}
    </div>
  );
}

function NumberField({ entry, editable, disabledReason }: {
  entry: TuningCatalogEntry; editable: boolean; disabledReason: string | null;
}) {
  const observation = useContext(ObservedValues);
  const observed = observation.values[entry.id];
  const snapshot = useTuningStore((state) => state.snapshot);
  const draft = useTuningStore((state) => state.drafts[entry.id]);
  const server = useTuningStore((state) => state.snapshot?.desired[entry.id]);
  const conflict = useTuningStore((state) => state.conflictIds.includes(entry.id));
  const setDraft = useTuningStore((state) => state.setDraft);
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
  const protectedField = !entry.accepts_edits || entry.protected || entry.not_applicable;
  const canEdit = editable && typeof server === "number";
  // Both smoother fields are `max_velocity`, so a key-only label would render
  // two identical rows. Disambiguate by the array slot the value occupies.
  const keyLabel = entry.source.key.split(".").at(-1)!.replaceAll("_", " ");
  const shortLabel = entry.source.key === "max_velocity" && entry.source.index !== null
    ? entry.source.index === 0 ? "linear max" : "angular max" : keyLabel;
  return (
    <div className={`field-row${conflict ? " field-conflict" : ""}`} data-parameter={entry.id}>
      <label className="field-label" htmlFor={id} title={entry.label}>
        <span>{entry.group === "speed_ceiling" ? entry.label.replace("Maximum ", "") : shortLabel}</span>
        <small className="field-unit">{unitText(entry)}</small>
      </label>
      <div className="field-controls">
        {canEdit || (draft !== undefined && !protectedField) ? (
          <>
            <input id={id} className="field-number" type="text" inputMode="decimal" value={value}
              disabled={!canEdit}
              aria-label={entry.label}
              aria-describedby={`${id}-status`}
              onChange={(event) => setDraft(entry.id, event.target.value)}
              onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }} />
            {range && canEdit && (
              <input className="field-slider" type="range" aria-label={`${entry.label} slider`}
                min={range.min} max={range.max} step={range.step} value={sliderValue}
                onChange={(event) => setDraft(entry.id, event.target.value)} />
            )}
          </>
        ) : (
          <span className="field-readonly-value" title={disabledReason ?? "Read-only setting"}>
            {protectedField ? "Read only" : server !== undefined ? String(server)
              : snapshot?.catalogFromServer ? "—" : `Source ${formatValue(entry.default, entry)}`}</span>
        )}
      </div>
      <div className={`field-live ${observation.fresh ? "is-fresh" : "is-stale"}`}
        title={observed === undefined ? "No parameter observation received" : String(observed)}>
        <span className="field-live-value">{formatValue(observed, entry)}</span>
        <small>{observed === undefined ? "Not received" : observation.fresh ? "Live" : "Last read"}</small>
      </div>
      <details className="field-info" onKeyDown={event => {
        if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
      }}>
        <summary aria-label={`Details for ${entry.label}`} title={entry.label}>ⓘ</summary>
        <div className="field-info-content">
          <strong>{entry.label}</strong>
          <p className="field-key">{entry.source.node} · {entry.source.key}{entry.source.index !== null ? `[${entry.source.index}]` : ""}</p>
          <p className="field-help">{formatBounds(entry)} {editPolicyText(entry)} {entry.bounds.note}</p>
          {disabledReason && !canEdit && <p>{disabledReason}</p>}
          <p>{snapshot?.desiredRevision == null ? "Initial YAML target" : "Saved target"}: {formatValue(server, entry)} {unitText(entry)}</p>
          <p>{observation.fresh ? "Live readback" : "Last-known readback"}: {formatValue(observed, entry)} {unitText(entry)}
            {observation.ageS !== null && ` · ${observation.ageS.toFixed(1)} s old`}</p>
          {entry.effective_ceiling_ref && <p>Runtime output is bounded by the active ceiling; a clamped value is not an unsaved change.</p>}
          <button className="btn" onClick={event => {
            const details = event.currentTarget.closest("details");
            if (details) { details.open = false; details.querySelector("summary")?.focus(); }
          }}>Close details</button>
        </div>
      </details>
      <div id={`${id}-status`}><StatusLine entry={entry} /></div>
    </div>
  );
}

function ParameterGroup({ group, entries, editable, disabledReason, title }: {
  group: string; entries: TuningCatalogEntry[]; editable: boolean; disabledReason: string | null;
  title?: string;
}) {
  if (entries.length === 0) return null;
  return (
    <section className={`tuning-group tuning-group-${group}`} aria-label={title ?? groupLabel(group)}>
      <h3>{title ?? groupLabel(group)} <span className="tuning-group-count">{entries.length}</span></h3>
      <div className="field-columns" aria-hidden="true"><span>Parameter</span><span>Target</span><span>Readback</span><span /></div>
      {entries.map((entry) => <NumberField key={entry.id} entry={entry}
        editable={editable && entry.accepts_edits && !entry.protected && !entry.not_applicable && (entry.type === "number" || entry.type === "integer")}
        disabledReason={disabledReason ?? "This parameter is read-only or not approved for activation."} />)}
    </section>
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

function ApplyDialog({ snapshot }: { snapshot: NormalizedSnapshot }) {
  const review = useTuningStore((state) => state.review);
  const apply = useTuningStore((state) => state.apply);
  const job = useTuningStore((state) => state.job);
  const close = useTuningStore((state) => state.closeApplyReview);
  const confirm = useTuningStore((state) => state.confirmApply);
  const queryJob = useTuningStore((state) => state.queryJob);
  const drafts = useTuningStore(state => state.drafts);
  const conflicts = useTuningStore(state => state.conflictIds);
  const save = useTuningStore(state => state.save);
  if (!review) return null;
  const plan = review.plan;
  const current = plan.operation === "restore" ? snapshot.restorePlan : snapshot.activationPlan;
  const changed = apply.phase !== "saved" && (plan.plan_hash !== current?.plan_hash || plan.desired_revision !== snapshot.desiredRevision
    || review.snapshot.desiredDigest !== snapshot.desiredDigest || plan.session_id !== snapshot.sessionId);
  const dirty = dirtyDraftIds({ drafts, snapshot }).length > 0;
  const jobId = typeof job?.job_id === "string" ? job.job_id : apply.requestId ?? undefined;
  return (
    <ConfirmDialog title={plan.operation === "restore" ? "Review last-known-good restore" : "Review saved changes"} confirmLabel="Apply the reviewed revision"
      onCancel={close} onConfirm={() => void confirm()}
      confirmDisabled={["saving", "unknown", "saved"].includes(apply.phase) || ["saving", "unknown"].includes(save.phase)
        || !snapshot.safeApply.allowed || !snapshot.activationSupported || !plan.executable || changed || dirty || conflicts.length > 0}>
      <p><strong>Reviewed revision {plan.revision}</strong> ({plan.digest}). Confirmation applies only this exact revision and scope.</p>
      <ul className="tuning-diff">{plan.changes.map(change => {
        const entry = review.snapshot.entries.find(entry => entry.id === change.id);
        return <li key={change.id}><strong>{entry?.label ?? change.id}</strong>: {String(change.before ?? "unknown")} → {String(change.after)} {entry ? unitText(entry) : ""}</li>;
      })}</ul>
      <p>{plan.execution_kind === "nav2_redeploy" ? "Whole Nav2 stack redeploy" : plan.execution_kind === "robot_docker"
        ? "Full robot Docker redeploy" : plan.execution_kind === "scoped_lifecycle"
        ? "Affected-node lifecycle reload" : "Hot parameter update (no redeploy)"}.</p>
      <p>Affected nodes: {plan.nodes.join(", ") || "none"}. Services: {plan.services.join(", ") || "none"}.</p>
      <p>{plan.disruption}</p>
      <p>{plan.seed_required ? "Localization will be invalidated. Navigation will wait for a fresh operator-supplied seed. No old pose is replayed."
        : "Localization dependencies are unaffected; existing valid localization is preserved."}</p>
      <p role="alert">Command-idle maintenance is not measured standstill. Motors may remain on. Without fresh odometry, software cannot verify physical zero motion.</p>
      {changed && <p role="alert">The saved revision or activation plan changed. Close this review and review again.</p>}
      {plan.reasons.map((reason, index) => <p key={`${reason.code}-${index}`} role="status">{reason.detail}</p>)}
      {!snapshot.safeApply.allowed && <p role="status">{snapshot.safeApply.reason}</p>}
      {apply.detail && <p className={`tuning-op detail-${apply.phase}`} role="status">{apply.detail}</p>}
      {jobId && <p className="tuning-job">Job {jobId}: {typeof job?.state === "string" ? job.state : "status unknown"}
        {" "}<button className="btn" onClick={() => void queryJob(jobId)}>Check progress</button></p>}
    </ConfirmDialog>
  );
}

/** End this tab's own manual session when settings are entered; never arm. */
function releaseManualSession() {
  const manual = useTeleopStore.getState();
  if (manual.phase !== "idle") manual.end("Tuning settings opened — manual input ended.");
}

export function TuningPanel() {
  const readback = useTuningReadback();
  const { now, observed, last, ageS } = readback;
  const baseState = useTelemetryStore((state) => state.baseState);
  const baseStateAt = useTelemetryStore((state) => state.baseStateAt);
  const robotId = useTelemetryStore(state => state.robotId);
  const snapshot = useTuningStore((state) => state.snapshot);
  const status = useTuningStore((state) => state.status);
  const load = useTuningStore((state) => state.load);
  const job = useTuningStore((state) => state.job);
  const apply = useTuningStore((state) => state.apply);
  const openApplyReview = useTuningStore((state) => state.openApplyReview);
  const saveDrafts = useTuningStore(state => state.saveDrafts);
  const save = useTuningStore(state => state.save);
  const drafts = useTuningStore(state => state.drafts);
  const conflicts = useTuningStore(state => state.conflictIds);
  const user = useAuthStore((state) => state.user);
  const telemetryConnected = useTelemetryStore((state) => state.wsConnected);
  const admin = isAdministrator(user);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      const started = performance.now();
      await load();
      if (!disposed && telemetryConnected) timer = setTimeout(() => { void refresh(); },
        Math.max(150, 1000 - (performance.now() - started)));
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [telemetryConnected, robotId, load]);

  const disabledReason = !telemetryConnected ? "The dashboard connection is offline — drafts are unsaved."
    : editDisabledReason(snapshot, admin)
      ?? (!baseState?.link_connected || baseStateAt === null || now - baseStateAt < 0
        || now - baseStateAt > 500 || !Number.isFinite(baseState.telemetry_age)
        || baseState.telemetry_age < 0 || baseState.telemetry_age > .5
        ? "The SBC link is disconnected or stale — editing is disabled." : null);
  const editable = disabledReason === null;
  const pending = pendingRedeploy(snapshot);
  const dirtyCount = dirtyDraftIds({ drafts, snapshot }).length;
  const saveReason = saveDisabledReason(useTuningStore.getState(), now);
  const busy = job !== null && typeof job.state === "string" && !["succeeded", "failed", "canceled", "done"].includes(job.state);

  const redeployReason = !admin ? "An administrator account is required."
    : !snapshot?.available ? "The robot tuning state is unknown."
    : pending.count === 0 ? "No saved changes are waiting."
    : dirtyCount > 0 || conflicts.length > 0 ? "Save or resolve your unsaved drafts before reviewing Apply."
    : ["saving", "unknown"].includes(save.phase) || ["saving", "unknown"].includes(apply.phase) ? "Resolve the in-flight or unknown request first."
    : !snapshot.activationSupported ? "Runtime apply is not commissioned on this robot. Saving targets is still available."
    : !snapshot.activationPlan ? "The robot has not provided a reviewable activation plan."
    : !snapshot.safeApply.allowed ? snapshot.safeApply.reason
    : !telemetryConnected ? "The dashboard server is offline."
    : busy ? "A deployment job is already running."
    : null;
  const canRedeploy = redeployReason === null;

  const entries = snapshot?.entries ?? BUNDLED_ENTRIES;
  const group = (name: string) => entries.filter(entry => entry.group === name);
  const protection = entries.filter(entry => ["protected_safety", "structural_read_only"].includes(entry.group));
  const liveCount = Object.keys(observed?.values ?? {}).length;
  const renderGroup = (name: string, title?: string) => <ParameterGroup key={name}
    group={name} title={title} entries={group(name)} editable={editable} disabledReason={disabledReason} />;

  return (
    <ObservedValues.Provider value={{ values: observed?.values ?? last?.values ?? {}, fresh: !!observed, ageS }}>
    <section className="tuning-panel" aria-label="Robot tuning"
      onFocusCapture={releaseManualSession} onPointerDownCapture={releaseManualSession}>
      <header className="tuning-heading">
        <div><h2>Speed and tuning</h2><p>Targets are ceilings, not motion commands. Saving does not apply them.</p></div>
        <div className={`readback-health readback-${readback.status}`} role="status">
          {observed ? `${liveCount}/${entries.length} live · ${ageS?.toFixed(1)} s`
            : last ? `Last read ${ageS?.toFixed(1) ?? "?"} s ago · not live`
              : telemetryConnected ? "Waiting for parameter readback" : "Dashboard disconnected"}
          <button className="btn" onClick={() => void load()} aria-label="Refresh parameter readback">↻ Refresh</button>
        </div>
      </header>

      {status === "loading" && !snapshot && <p role="status">Loading the robot's tuning state…</p>}
      {snapshot && !snapshot.available && (
        <p className="nav-disabled-banner" role="status">
          {snapshot.reason} {snapshot.catalogFromServer
            ? "Last-confirmed targets and last-known observations remain visible; writes are disabled."
            : "The list below is a bundled source catalogue, shown as read-only candidates — not active robot values."}
        </p>
      )}
      {snapshot?.available && !editable && <p className="tuning-readonly-banner" role="status">{disabledReason}</p>}
      {snapshot?.available && editable && <p className="tuning-writable-banner" role="status">Administrator · edits are drafts until you click Save changes. Saving never changes running parameters.</p>}
      {!snapshot?.catalogFromServer && (
        <p className="subtext">Source catalogue from {REGISTRY_PROVENANCE.baseline_commit.slice(0, 10)} ({REGISTRY_PROVENANCE.inspected}). {REGISTRY_CAPABILITY.advertised ? "" : "The runtime capability is not advertised."}</p>
      )}

      {Object.keys(drafts).length > 0 && (
        <div className="tuning-drafts">
          <p>Unsaved drafts are kept in this tab only. Reconnect never replays them.</p>
          {conflicts.length > 0 && <ul>{conflicts.map(id => <li key={id}>
            {snapshot?.entries.find(entry => entry.id === id)?.label ?? id}: saved {String(snapshot?.desired[id] ?? "unknown")}; draft {drafts[id] ?? "none"}
          </li>)}</ul>}
          <button className="btn" disabled={["saving", "unknown"].includes(save.phase)} onClick={() => useTuningStore.getState().discardAllDrafts()}>Discard unsaved drafts</button>
          {conflicts.length > 0 && <button className="btn" disabled={["saving", "unknown"].includes(save.phase)}
            onClick={() => useTuningStore.getState().rebaseDrafts()}>Keep drafts against reviewed saved values</button>}
          <button className="btn" onClick={() => void load()}>Refresh robot state</button>
        </div>
      )}

      <div className="tuning-columns">
        <div className="tuning-column">{renderGroup("amcl")}</div>
        <div className="tuning-column">
          {renderGroup("nav2_controller", "Nav2 controller")}
          {renderGroup("nav2_velocity_smoother", "Nav2 smoother")}
          {renderGroup("nav2_behavior", "Nav2 behavior")}
        </div>
        <div className="tuning-column">
          {renderGroup("speed_ceiling", "Maximum speeds")}
          <ParameterGroup group="protected" title="Safety & topology · read-only"
            entries={protection} editable={false} disabledReason="Protected setting — observation only." />
        </div>
      </div>

      <div className="tuning-sticky" aria-label="Pending deployment">
        <span>{dirtyCount} unsaved change{dirtyCount === 1 ? "" : "s"}</span>
        <button className="btn primary" disabled={!!saveReason} title={saveReason ?? undefined} onClick={() => void saveDrafts()}>Save changes</button>
        <span className="tuning-change-summary">{!snapshot?.available ? "Pending state unknown"
          : pending.count ? `${pending.count} saved change${pending.count === 1 ? "" : "s"} waiting for apply`
            : "No saved changes waiting for apply"}</span>
        <button className={`btn primary${canRedeploy ? " is-active" : ""}`} onClick={openApplyReview}
          disabled={!canRedeploy} title={redeployReason ?? undefined}>
          Apply changes{pending.count > 0 ? ` (${pending.count})` : ""}
        </button>
        {admin && snapshot?.lastKnownGoodRevision !== null && snapshot?.lastKnownGoodRevision !== undefined && (
          <button className="btn" onClick={() => useTuningStore.getState().openRestoreReview()}
            disabled={!snapshot.restorePlan || !snapshot.activationSupported || !snapshot.safeApply.allowed || busy
              || dirtyCount > 0 || conflicts.length > 0 || ["saving", "unknown"].includes(save.phase) || ["saving", "unknown"].includes(apply.phase)}
            title={!snapshot.restorePlan ? "A robot-provided exact restore plan is required." : undefined}>Restore last known good</button>
        )}
        {save.detail && <span role="status">{save.detail}</span>}
        {save.phase === "unknown" && <button className="btn" onClick={() => void useTuningStore.getState().resolveUnknownSave()}>Query saved request outcome</button>}
        {apply.phase === "unknown" && apply.requestId && <button className="btn" onClick={() => void useTuningStore.getState().queryJob(apply.requestId!)}>Query apply request outcome</button>}
        {canRedeploy && <span className="tuning-sticky-detail">Affects: {snapshot?.activationPlan?.nodes.join(", ") || pending.services.join(", ") || "no changed runtime values"}</span>}
        {redeployReason && pending.count > 0 && <span className="tuning-sticky-detail">{redeployReason}</span>}
      </div>

      {snapshot?.available && <ApplyDialog snapshot={snapshot} />}
    </section>
    </ObservedValues.Provider>
  );
}

/** Exposed for tests: whether a saved value equals the runtime read-back. */
export const tuningValuesEqual = valuesEqual;
