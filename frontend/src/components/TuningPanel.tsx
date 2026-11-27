import { useEffect } from "react";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTeleopStore } from "../stores/teleopStore";
import {
  BUNDLED_ENTRIES, REGISTRY_GROUPS, REGISTRY_PROVENANCE, formatBounds,
  type TuningCatalogEntry, type TuningValue,
} from "../lib/tuning";
import { useTuningStore } from "../stores/tuningStore";
import { useTuningReadback } from "../lib/useTuningReadback";

function formatValue(value: TuningValue | null | undefined, entry: TuningCatalogEntry): string {
  if (value == null) return "—";
  return typeof value === "number" && entry.type !== "integer"
    ? Number(value.toFixed(3)).toString()
    : String(value);
}

function ParameterRow({ entry, target, observed, fresh, ageS }: {
  entry: TuningCatalogEntry; target: TuningValue | null | undefined;
  observed: TuningValue | undefined; fresh: boolean; ageS: number | null;
}) {
  const unit = entry.unit && !["boolean", "string"].includes(entry.unit) ? entry.unit : "";
  return (
    <div className="field-row">
      <span className="field-label"><span>{entry.label}</span>{unit && <small className="field-unit">{unit}</small>}</span>
      <span className="field-readonly-value">{formatValue(target, entry)}</span>
      <div className={`field-live ${fresh ? "is-fresh" : "is-stale"}`}>
        <span className="field-live-value">{formatValue(observed, entry)}</span>
        <small>{observed === undefined ? "Not received" : fresh ? "Live" : "Last read"}</small>
      </div>
      <details className="field-info" onKeyDown={event => {
        if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
      }}>
        <summary aria-label={`Details for ${entry.label}`} title={entry.label}>ⓘ</summary>
        <div className="field-info-content">
          <strong>{entry.label}</strong>
          <p className="field-key">{entry.source.node} · {entry.source.key}{entry.source.index !== null ? `[${entry.source.index}]` : ""}</p>
          <p className="field-help">{formatBounds(entry)} {entry.bounds.note}</p>
          <p>Configured target: {formatValue(target, entry)} {unit}</p>
          <p>{fresh ? "Live readback" : "Last-known readback"}: {formatValue(observed, entry)} {unit}
            {observed !== undefined && ageS !== null && ` · ${ageS.toFixed(1)} s old`}</p>
          <button className="btn" onClick={event => {
            const details = event.currentTarget.closest("details");
            if (details) { details.open = false; details.querySelector("summary")?.focus(); }
          }}>Close details</button>
        </div>
      </details>
    </div>
  );
}

/** End this tab's own manual session when settings are entered; never arm. */
function releaseManualSession() {
  const manual = useTeleopStore.getState();
  if (manual.phase !== "idle") manual.end("Tuning settings opened — manual input ended.");
}

export function TuningPanel() {
  const { snapshot, connected, observed, last, ageS, status: readbackStatus } = useTuningReadback();
  const robotId = useTelemetryStore(state => state.robotId);
  const status = useTuningStore(state => state.status);
  const load = useTuningStore(state => state.load);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      const started = performance.now();
      await load();
      if (!disposed && connected) timer = setTimeout(() => { void refresh(); },
        Math.max(150, 1000 - (performance.now() - started)));
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [connected, robotId, load]);

  const entries = snapshot?.entries ?? BUNDLED_ENTRIES;
  const values = observed?.values ?? last?.values ?? {};
  const renderGroup = (groups: string[], title?: string) => {
    const members = entries.filter(entry => groups.includes(entry.group));
    if (!members.length) return null;
    const group = groups[0];
    const name = title ?? REGISTRY_GROUPS.find(item => item.id === group)?.label ?? group;
    return (
      <section className={`tuning-group tuning-group-${group}`} aria-label={name}>
        <h3>{name} <span className="tuning-group-count">{members.length}</span></h3>
        <div className="field-columns" aria-hidden="true"><span>Parameter</span><span>Target</span><span>Readback</span><span /></div>
        {members.map(entry => <ParameterRow key={entry.id} entry={entry}
          target={snapshot?.desired[entry.id] ?? (!snapshot?.catalogFromServer ? entry.default : undefined)}
          observed={values[entry.id]} fresh={!!observed} ageS={ageS} />)}
      </section>
    );
  };

  return (
    <section className="tuning-panel" aria-label="Robot tuning"
      onFocusCapture={releaseManualSession} onPointerDownCapture={releaseManualSession}>
      <header className="tuning-heading">
        <div><h2>Speed and tuning</h2><p>Read-only: inspect configured targets and live values.</p></div>
        <div className={`readback-health readback-${readbackStatus}`} role="status">
          {observed ? `${Object.keys(observed.values).length}/${entries.length} live · ${ageS?.toFixed(1)} s`
            : last ? `Last read ${ageS?.toFixed(1) ?? "?"} s ago · not live`
              : connected ? "Waiting for parameter readback" : "Dashboard disconnected"}
          <button className="btn" onClick={() => void load()} aria-label="Refresh parameter readback">↻ Refresh</button>
        </div>
      </header>
      {status === "loading" && !snapshot && <p role="status">Loading the robot's tuning state…</p>}
      {snapshot && !snapshot.available && <p className="nav-disabled-banner" role="status">
        {snapshot.reason} Displayed targets are not live readings.
      </p>}
      {!snapshot?.catalogFromServer && <p className="subtext">
        Bundled source catalogue from {REGISTRY_PROVENANCE.baseline_commit.slice(0, 10)} ({REGISTRY_PROVENANCE.inspected}).
      </p>}
      <div className="tuning-columns">
        <div className="tuning-column">{renderGroup(["amcl"])}</div>
        <div className="tuning-column">
          {renderGroup(["nav2_controller"], "Nav2 controller")}
          {renderGroup(["nav2_velocity_smoother"], "Nav2 smoother")}
          {renderGroup(["nav2_behavior"], "Nav2 behavior")}
        </div>
        <div className="tuning-column">
          {renderGroup(["speed_ceiling"], "Maximum speeds")}
          {renderGroup(["protected_safety", "structural_read_only"], "Safety & topology · read-only")}
        </div>
      </div>
    </section>
  );
}
