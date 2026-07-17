import { STATUS_COPY } from "../lib/plainLanguage";
import { formatAge, formatSpeed } from "../lib/format";
import { useTelemetryStore } from "../stores/telemetryStore";

export function RobotStatusWidget() {
  const status = useTelemetryStore((state) => state.status);
  const pose = useTelemetryStore((state) => state.pose);
  const baseState = useTelemetryStore((state) => state.baseState);
  const lastFrameAt = useTelemetryStore((state) => state.lastFrameAt);

  const copy = STATUS_COPY[status.status];

  return (
    <div>
      <span className={`status-pill tone-${copy.tone}`} style={{ fontSize: 15, padding: "6px 14px" }}>
        <span className="dot" />
        {copy.label}
      </span>
      <p className="subtext" style={{ margin: "8px 0 12px" }}>{status.detail}</p>
      <div className="kv-list">
        <div className="kv">
          <span className="k">Motors</span>
          <span className="v">{baseState ? (baseState.motors_enabled ? "Enabled" : "Off") : "—"}</span>
        </div>
        <div className="kv">
          <span className="k">Location known</span>
          <span className="v">{pose ? (pose.localized === false ? "Uncertain" : "Yes") : "—"}</span>
        </div>
        <div className="kv">
          <span className="k">Speed</span>
          <span className="v">{formatSpeed(pose?.linear_velocity)}</span>
        </div>
        <div className="kv">
          <span className="k">Emergency stop</span>
          <span className="v">{baseState ? (baseState.estop_pressed ? "PRESSED" : "Clear") : "—"}</span>
        </div>
        <div className="kv">
          <span className="k">Charger</span>
          <span className="v">{baseState ? baseState.charge_state.replaceAll("_", " ") : "—"}</span>
        </div>
        <div className="kv">
          <span className="k">Last update</span>
          <span className="v">{formatAge(lastFrameAt)}</span>
        </div>
      </div>
    </div>
  );
}
