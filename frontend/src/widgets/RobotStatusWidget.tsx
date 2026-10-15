import { lidarHardwareReady, useLidarReadyReason } from "../lib/lidar";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { factsFrom } from "../lib/dockGates";
import { LOCALIZATION_RECOVERY_COPY, STATUS_COPY } from "../lib/plainLanguage";
import { formatAge, formatSpeed } from "../lib/format";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useIsFresh } from "../lib/freshness";
import { localizationUncertainty } from "../lib/localizationUncertainty";

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
  const poseReceivedAt = useTelemetryStore((state) => state.poseReceivedAt);
  const poseFresh = useIsFresh(poseReceivedAt);
  const amclReceivedAt = pose?.localization_age_s != null && Number.isFinite(pose.localization_age_s)
    ? poseReceivedAt - pose.localization_age_s * 1000 : null;
  const amclFresh = useIsFresh(amclReceivedAt, (pose?.localization_max_age_s ?? 0) * 1000);
  const uncertainty = localizationUncertainty(pose, poseFresh && amclFresh ? poseReceivedAt : null);
  const base = baseFresh ? baseState : null;

  const active = useCommandStore(state => state.active);
  const lastResult = useCommandStore(state => state.lastResult);
  const send = useCommandStore(state => state.send);
  const connection = useTelemetryStore(state => state.connection);
  const capabilities = useTelemetryStore(state => state.capabilities);
  const wsConnected = useTelemetryStore(state => state.wsConnected);
  const mayCommand = canCommand(useAuthStore(state => state.user));
  const scanReason = useLidarReadyReason(baseState, baseStateAt);
  const powerFresh = lidarHardwareReady(baseState, baseStateAt);
  const facts = factsFrom(connection.state, powerFresh ? base : null, null, capabilities);
  const busy = useCommandStore(state => state.lidarPending !== null);
  const lidarStateAfter = useCommandStore(state => state.lidarStateAfter);
  const waitingForState = lidarStateAfter !== null && (baseStateAt === null || baseStateAt <= lidarStateAfter);
  const powerReason = !mayCommand ? "Your account has read-only access."
    : !wsConnected || !facts.online ? "The robot is not connected right now."
    : !powerFresh || !facts.linkConnected || !facts.hardwareStateValid ? "LiDAR power state is stale or unavailable."
    : !base?.lidar_power_supported || !capabilities.includes("set_lidar_power") ? "LiDAR power control is not commissioned on this robot."
    : !["OFF", "READY", "FAULT"].includes(base.lidar_state ?? "UNKNOWN") ? "LiDAR is starting or its power state is unknown."
    : waitingForState ? "Waiting for fresh LiDAR state after the power command."
    : active || busy || facts.undockActive ? "Wait for the current command to finish before changing LiDAR power."
    : facts.faultFlags ? "The drive base is reporting a fault. Clear it on the robot first." : null;
  const target = base?.lidar_state !== "READY";
  const copy = STATUS_COPY[status.status];

  return (
    <div>
      <span className={`status-pill tone-${copy.tone}`} style={{ fontSize: 15, padding: "6px 14px" }}>
        <span className="dot" />
        {copy.label}
      </span>
      <p className="subtext" style={{ margin: "8px 0 12px" }}>{status.detail}</p>
      {base?.localization_recovery_required && LOCALIZATION_RECOVERY_COPY[base.localization_recovery_stage ?? ""] && (
        <p role="status">{LOCALIZATION_RECOVERY_COPY[base.localization_recovery_stage ?? ""]}</p>
      )}
      <div className="kv-list">
        <div className="kv"><span className="k">LiDAR</span><span className="v">
          {!powerFresh ? "Unknown" : base?.lidar_state === "OFF" ? "Commanded off" : base?.lidar_state === "READY" && scanReason ? "Stale scans" : base?.lidar_state ?? "UNKNOWN"}
        </span></div>
        <div className="kv">
          <span className="k">Motors</span>
          <span className="v">{base ? (base.motors_enabled ? "Enabled" : "Off") : "Unknown"}</span>
        </div>
        <div className="kv">
          <span className="k">Location known</span>
          <span className="v">{poseFresh && (pose?.localization_age_s == null || amclFresh) && pose ? (pose.localized === false ? "Uncertain" : "Yes") : "Unknown"}</span>
        </div>
        {uncertainty.state === "usable" ? <>
          <div className="kv">
            <span className="k">Largest map-axis spread (1σ)</span>
            <span className="v">{uncertainty.positionSpreadM.toFixed(3)} m</span>
          </div>
          <div className="kv">
            <span className="k">Heading spread (1σ)</span>
            <span className="v">{uncertainty.headingSpreadDeg.toFixed(1)}°</span>
          </div>
          <div className="kv">
            <span className="k">Dashboard variance margin</span>
            <span className="v">{uncertainty.positionMarginPercent !== null && uncertainty.headingMarginPercent !== null
              ? `XY ${uncertainty.positionMarginPercent >= 0 ? "+" : ""}${uncertainty.positionMarginPercent.toFixed(1)}% · heading ${uncertainty.headingMarginPercent >= 0 ? "+" : ""}${uncertainty.headingMarginPercent.toFixed(1)}%`
              : "Unavailable: threshold not usable"}</span>
          </div>
        </> : <div className="kv">
          <span className="k">Filter estimate</span><span className="v">{uncertainty.detail}</span>
        </div>}
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
      <p className="subtext">Filter estimate, not measured pose accuracy. Positive margin does not mean navigation is ready.
        {uncertainty.state === "usable" && uncertainty.varianceLimit !== null
          && ` Dashboard bridge limits: XY ${uncertainty.varianceLimit} m² · heading ${uncertainty.varianceLimit} rad².`}
        {" "}Refresh age is time since the bridge received a location estimate; docking has a separate freshness check.
      </p>
      <button className="btn" disabled={powerReason !== null || busy} aria-busy={busy}
        title={powerReason ?? "Change LiDAR power"}
        onClick={() => { if (!powerReason) send("set_lidar_power", undefined, false, target); }}>
        {busy ? "Changing LiDAR power…" : target ? "Turn LiDAR on" : "Turn LiDAR off"}
      </button>
      {powerReason && <p className="subtext">{powerReason}</p>}
      {lastResult?.command === "set_lidar_power" && <p role="status">{lastResult.detail ?? lastResult.outcome}</p>}
    </div>
  );
}
