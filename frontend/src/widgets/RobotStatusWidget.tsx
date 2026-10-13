import { lidarHardwareReady, useLidarReadyReason } from "../lib/lidar";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { factsFrom } from "../lib/dockGates";
import { LOCALIZATION_RECOVERY_COPY, STATUS_COPY } from "../lib/plainLanguage";
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
