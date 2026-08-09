import { STATUS_COPY } from "../lib/plainLanguage";
import { formatAge, formatSpeed } from "../lib/format";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useIsFresh } from "../lib/freshness";

export function RobotStatusWidget() {
  const status = useTelemetryStore((state) => state.status);
  const pose = useTelemetryStore((state) => state.pose);
  const baseState = useTelemetryStore((state) => state.baseState);
  const lastFrameAt = useTelemetryStore((state) => state.lastFrameAt);
  const baseStateAt = useTelemetryStore((state) => state.baseStateAt);
  // Everything below sourced from the drive base is only worth showing while
  // the drive base is still reporting. With it switched off, the Pi keeps the
  // socket alive and these read "Enabled" and "Clear" from whatever it last
  // said — for days, in the case that prompted this.
  const baseFresh = useIsFresh(baseStateAt);
  const poseFresh = useIsFresh(useTelemetryStore((state) => state.poseReceivedAt));
  const base = baseFresh ? baseState : null;

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
          <span className="v">{base ? (base.motors_enabled ? "Enabled" : "Off") : "Unknown"}</span>
        </div>
        <div className="kv">
          <span className="k">Location known</span>
          <span className="v">{poseFresh && pose ? (pose.localized === false ? "Uncertain" : "Yes") : "Unknown"}</span>
        </div>
        <div className="kv">
          <span className="k">Speed</span>
          <span className="v">
            {poseFresh && pose ? formatSpeed(pose.linear_velocity) : "Unknown"}
          </span>
        </div>
        <div className="kv">
          <span className="k">Emergency stop</span>
          <span className="v">{base ? (base.estop_pressed ? "PRESSED" : "Clear") : "Unknown"}</span>
        </div>
        <div className="kv">
          <span className="k">Charger</span>
          <span className="v">{base ? base.charge_state.replaceAll("_", " ") : "Unknown"}</span>
        </div>
        <div className="kv">
          <span className="k">Last update</span>
          <span className="v">{formatAge(lastFrameAt)}</span>
        </div>
      </div>
    </div>
  );
}
