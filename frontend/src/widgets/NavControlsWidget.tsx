import { useLidarReadyReason } from "../lib/lidar";
import { localizationRecoveryReason, RECOVERY_RECEIPT_MAX_MS } from "../lib/localizationRecovery";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowUpFromDot, Crosshair, MapPin, Octagon, Play, Power, X,
} from "lucide-react";
import { useState } from "react";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import {
  factsFrom, motorEnableReason, motorDisableReason, showsUndock, softwareResetReason, undockReason,
} from "../lib/dockGates";
import { useIsFresh } from "../lib/freshness";

const OUTCOME_COPY: Record<string, string> = {
  succeeded: "Done",
  failed: "Failed",
  rejected: "Not accepted",
  canceled: "Canceled",
  timeout: "Timed out",
};

/**
 * Navigation plus explicitly gated hardware actions. There is deliberately no
 * joystick/velocity control — undock is an action the robot executes rather
 * than velocity published from a browser, and explicit motor power changes
 * drive power without sending motion.
 *
 * There is no Dock control to pair with Undock: the robot has no automatic
 * dock-in path, so it is driven onto its charger by hand. Undock appears only
 * when the robot is on the dock, and is absent rather than greyed out
 * otherwise — a permanently disabled button reads as something broken.
 */
export function NavControlsWidget() {
  const mayCommand = canCommand(useAuthStore(state => state.user));
  const wsConnected = useTelemetryStore(state => state.wsConnected);
  const [motorEnableConfirmOpen, setMotorEnableConfirmOpen] = useState(false);
  const [softwareResetConfirmOpen, setSoftwareResetConfirmOpen] = useState(false);
  const connection = useTelemetryStore((state) => state.connection);
  const poseSetThisSession = useTelemetryStore((state) => state.poseSetThisSession);
  const baseState = useTelemetryStore((state) => state.baseState);
  const pose = useTelemetryStore((state) => state.pose);
  const capabilities = useTelemetryStore((state) => state.capabilities);
  const send = useCommandStore((state) => state.send);
  const active = useCommandStore((state) => state.active);
  const lastResult = useCommandStore((state) => state.lastResult);
  const pickMode = useCommandStore((state) => state.pickMode);
  const stoppedGoal = useCommandStore((state) => state.stoppedGoal);
  const setPickMode = useCommandStore((state) => state.setPickMode);
  const allowUnlocalized = useCommandStore((state) => state.allowUnlocalized);
  const setAllowUnlocalized = useCommandStore((state) => state.setAllowUnlocalized);
  const stop = useCommandStore((state) => state.stop);
  const resume = useCommandStore((state) => state.resume);
  const cancel = useCommandStore((state) => state.cancel);
  const takeOver = useCommandStore((state) => state.takeOver);

  const online = wsConnected && connection.state === "online";
  // A rejection from the single-operator lease — offer an explicit takeover.
  const leaseBlocked =
    active === null &&
    lastResult?.outcome === "rejected" &&
    (lastResult.detail ?? "").includes("in control of the robot");
  const offerResume = stoppedGoal !== null && active === null;
  // Navigation is hard-blocked until the operator has set the robot's 2D
  // location this session, so the robot is never sent anywhere from an
  // unconfirmed pose.
  const recoveryAt = useTelemetryStore(state => state.baseStateAt);
  const recoveryFresh = useIsFresh(recoveryAt, RECOVERY_RECEIPT_MAX_MS);
  const recoveryReason = localizationRecoveryReason(recoveryFresh ? baseState : null, recoveryAt);
  const lidarPending = useCommandStore(state => state.lidarPending);
  const lidarRecoveryRequired = useCommandStore(state => state.lidarRecoveryRequired);
  const lidarStateAfter = useCommandStore(state => state.lidarStateAfter);
  const waitingForState = lidarStateAfter !== null && (recoveryAt === null || recoveryAt <= lidarStateAfter);
  const lidarReason = useLidarReadyReason(baseState, recoveryAt) ?? (lidarPending !== null ? "LiDAR power is pending — wait for its result." : lidarRecoveryRequired ? "LiDAR power recovery is required. Make a new explicit power request first." : waitingForState ? "Waiting for fresh LiDAR state after the power command." : null);
  const canNavigate = online && lidarReason === null && recoveryReason === null && (poseSetThisSession || allowUnlocalized);
  const gateHint = lidarReason ?? recoveryReason ?? "Set the robot's 2D location before sending it anywhere.";

  // Undock is offered only when the robot is on its charger; there is no Dock
  // control, because the robot has no automatic dock-in path — it is driven
  // onto the charger by hand. The disabled reason is the same sentence the
  // server would reject with, so the UI never silently disagrees with the
  // robot.
  // Stale slices are handed to the gate mirror as absent, which is how the
  // server's own gates read them: they judge receipt age, so a browser working
  // from a frozen frame would otherwise offer a control the server refuses.
  const baseFresh = useIsFresh(useTelemetryStore((state) => state.baseStateAt));
  const poseFresh = useIsFresh(useTelemetryStore((state) => state.poseReceivedAt));
  const facts = factsFrom(connection.state, baseFresh ? baseState : null,
    poseFresh ? pose : null, capabilities,
    active?.command === "navigate_to_pose");
  const onDock = showsUndock(facts);
  const undockBlockedReason = lidarReason ?? undockReason(facts);
  const undockBusy = active?.command === "undock" || facts.undockActive;
  const motorsEnabled = baseState?.motors_enabled === true;
  const motorEnableBusy = active?.command === "motor_enable";
  const motorDisableBusy = active?.command === "motor_disable";
  const motorEnableUiReason = !wsConnected ? "The robot is not connected right now." : motorEnableReason(facts);
  const motorDisableUiReason = !wsConnected ? "The robot is not connected right now." : motorDisableReason(facts);
  const motorUiReason = motorsEnabled ? motorDisableUiReason : motorEnableUiReason;
  const softwareResetBlockedReason = softwareResetReason(facts);
  const softwareResetUiReason = softwareResetBlockedReason ?? (active?.command === "software_reset"
    ? "Wait for the current command to finish before resetting software."
    : null);

  return (
    <div>
      {!mayCommand && <div className="nav-disabled-banner">Your account has read-only access. An operator account is required for robot commands.</div>}
      {!online && (
        <div className="nav-disabled-banner">
          The robot is not connected — commands are unavailable.
        </div>
      )}
      {online && !canNavigate && pickMode === "none" && (
        <div className="nav-gate-banner">
          <Crosshair size={13} /> {gateHint}
        </div>
      )}
      {online && pickMode !== "none" && (
        <div className="nav-pick-banner">
          {pickMode === "goal"
            ? "Press on the Live Map where the robot should go, and drag to choose which way it should face."
            : "Press on the robot's true position on the Live Map, and drag toward where it is facing."}
          <button className="btn" onClick={() => setPickMode("none")} title="Cancel">
            <X size={13} /> Cancel
          </button>
        </div>
      )}
      {online && active && (
        <div className="nav-active-banner">
          <span className="spinner" />
          {active.phase === "sending"
            ? "Sending command…"
            : active.stage ?? "The robot accepted the command."}
          {active.distanceRemaining != null && ` — ${active.distanceRemaining.toFixed(1)} m left`}
        </div>
      )}
      {active?.command === "software_reset" && !online && (
        <div className="nav-active-banner" role="status">
          {active.stage ?? "Connection lost — reset outcome is unknown. Waiting for the supervisor result; do not retry."}
        </div>
      )}
      {online && !active && lastResult && (
        <div className={`nav-result-banner outcome-${lastResult.outcome}`}>
          {OUTCOME_COPY[lastResult.outcome] ?? lastResult.outcome}
          {lastResult.detail ? ` — ${lastResult.detail}` : ""}
          {leaseBlocked && (
            <button
              className="btn"
              disabled={!mayCommand || !online} onClick={takeOver}
              title="Take control from the current operator and re-send your command"
            >
              Take over
            </button>
          )}
        </div>
      )}
      <details className="nav-advanced">
        <summary>Advanced</summary>
        <label className="nav-advanced-row">
          <input
            type="checkbox"
            checked={allowUnlocalized}
            onChange={(event) => setAllowUnlocalized(event.target.checked)}
          />
          <span>
            Send a destination even if the robot does not know where it is
          </span>
        </label>
        <p className="nav-advanced-note">
          The robot will plan from a position it does not trust, so it may take
          a wrong route or collide. RViz has always allowed this — Nav2 has no
          localization check of its own — so this only gives the dashboard the
          same reach. It waives that one question and nothing else: a robot
          that has stopped reporting its position, has its motors off, a fault
          raised or the e-stop pressed is still refused. Clears itself after
          one destination, and on reconnect.
        </p>
      </details>
      <div className="nav-advanced-motor">
        <button
          className={`btn ${motorsEnabled ? "danger" : "success"}`}
          disabled={!mayCommand || motorUiReason !== null || (motorsEnabled ? motorDisableBusy : motorEnableBusy)}
          onClick={() => motorsEnabled ? send("motor_disable") : setMotorEnableConfirmOpen(true)}
          title={motorUiReason ?? (motorsEnabled ? "Disable drive power" : "Request drive power without commanding movement")}
        >
          <Power size={15} />
          {motorsEnabled ? (motorDisableBusy ? "Turning motors off…" : "Turn motors off") : (motorEnableBusy ? "Turning motors on…" : "Turn motors on")}
        </button>
        {!motorsEnabled && <button
          className="btn danger"
          disabled={!mayCommand || motorDisableUiReason !== null || motorDisableBusy}
          onClick={() => send("motor_disable")}
          title={motorDisableUiReason ?? "Disable drive power"}
        >
          <Power size={15} /> {motorDisableBusy ? "Turning motors off…" : "Turn motors off"}
        </button>}
        <p className="nav-advanced-note">
          Motor power is separate from movement and localization. The robot checks physical safety before enabling; turning motors off remains available during movement and faults.
        </p>
        {motorUiReason && <p className="nav-advanced-note nav-motor-reason">Motor power — {motorUiReason}</p>}
      </div>
      <div className="nav-advanced-motor">
        <button
          className="btn danger"
          disabled={!mayCommand || softwareResetUiReason !== null}
          onClick={() => setSoftwareResetConfirmOpen(true)}
          title={softwareResetUiReason ?? "Restart robot software from a clean state"}
        >
          <Power size={15} />
          {active?.command === "software_reset" ? "Reset pending…" : "Restart robot software"}
        </button>
        <p className="nav-advanced-note">
          Restarts robot software from a clean state without dock, pose or telemetry checks.
          The dashboard will disconnect. Navigation readiness is checked separately.
        </p>
        {softwareResetUiReason && (
          <p className="nav-advanced-note nav-motor-reason">Software reset — {softwareResetUiReason}</p>
        )}
      </div>
      <Dialog.Root open={motorEnableConfirmOpen} onOpenChange={setMotorEnableConfirmOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" style={{ maxWidth: 420 }}>
            <Dialog.Title asChild>
              <h2>Turn the motors on?</h2>
            </Dialog.Title>
            <Dialog.Description className="subtext">
              This enables drive power but does not command movement. Confirm
              that the area around the robot is clear before continuing.
            </Dialog.Description>
            {motorEnableUiReason && (
              <p className="nav-advanced-note nav-motor-reason">
                Motor enable — {motorEnableUiReason}
              </p>
            )}
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button className="btn">Cancel</button>
              </Dialog.Close>
              <button
                className="btn danger"
                disabled={!mayCommand || (motorEnableUiReason !== null || motorEnableBusy)}
                onClick={() => {
                  const live = useTelemetryStore.getState();
                  if (!canCommand(useAuthStore.getState().user) || !live.wsConnected || live.baseState?.motors_enabled === true ||
                      useCommandStore.getState().active?.command === "motor_enable" ||
                      motorEnableReason(factsFrom(live.connection.state, null, null, live.capabilities)) !== null) return;
                  setMotorEnableConfirmOpen(false);
                  send("motor_enable");
                }}
                title={motorEnableUiReason ?? "Confirm motor enable"}
              >
                <Power size={15} /> Confirm motor enable
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root open={softwareResetConfirmOpen} onOpenChange={setSoftwareResetConfirmOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" style={{ maxWidth: 440 }}>
            <Dialog.Title asChild><h2>Restart robot software?</h2></Dialog.Title>
            <Dialog.Description className="subtext">
              This stops the old robot software and restarts it without requiring dock contact,
              a pose or working telemetry. Neither computer is rebooted. Old commands will not resume.
              The dashboard will disconnect. The result reports restart operations, not navigation readiness.
              An unknown result is never retried automatically; another reset requires a new confirmation.
            </Dialog.Description>
            {softwareResetUiReason && (
              <p className="nav-advanced-note nav-motor-reason">Software reset — {softwareResetUiReason}</p>
            )}
            <div className="dialog-actions">
              <Dialog.Close asChild><button className="btn">Cancel</button></Dialog.Close>
              <button
                className="btn danger"
                disabled={!mayCommand || softwareResetUiReason !== null}
                onClick={() => {
                  const live = useTelemetryStore.getState();
                  const command = useCommandStore.getState().active;
                  const liveFacts = factsFrom(live.connection.state, null, null, live.capabilities);
                  if (!canCommand(useAuthStore.getState().user) || !live.wsConnected ||
                      command?.command === "software_reset" || softwareResetReason(liveFacts) !== null) return;
                  setSoftwareResetConfirmOpen(false);
                  send("software_reset");
                }}
              >
                <Power size={15} /> Confirm software reset
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <div className="nav-buttons">
        <button
          className={`btn wide primary ${pickMode === "goal" ? "active" : ""}`}
          disabled={!mayCommand || (!canNavigate)}
          onClick={() => setPickMode(pickMode === "goal" ? "none" : "goal")}
          title={canNavigate ? "Pick a destination on the map" : gateHint}
        >
          <MapPin size={15} /> Send Robot Here
        </button>
        <button
          className={`btn ${pickMode === "initialpose" ? "active" : ""}`}
          disabled={!mayCommand || !online || lidarReason !== null}
          onClick={() => setPickMode(pickMode === "initialpose" ? "none" : "initialpose")}
          title={lidarReason ?? "Tell the robot where it actually is on the map"}
        >
          <Crosshair size={15} /> Set Robot Location
        </button>
        {onDock && (
          <button
            className="btn danger"
            disabled={!mayCommand || (undockBlockedReason !== null || undockBusy)}
            onClick={() => send("undock")}
            title={undockBlockedReason
              ?? "Move the robot clear of its charging dock and turn it around"}
          >
            <ArrowUpFromDot size={15} />
            {facts.undockActive ? "Undocking…" : "Undock"}
          </button>
        )}
        {offerResume ? (
          <>
            <button
              className="btn success"
              disabled={!mayCommand || (!canNavigate)}
              onClick={resume}
              title={canNavigate ? "Send the robot back to the destination it was stopped on" : gateHint}
            >
              <Play size={15} /> Resume
            </button>
            <button
              className="btn"
              onClick={cancel}
              title="Discard the paused destination — the robot stays put"
            >
              <X size={15} /> Cancel
            </button>
          </>
        ) : active ? (
          <>
            <button
              className="btn danger"
              disabled={!mayCommand || (!online)}
              onClick={stop}
              title="Pause the robot here; you can resume afterwards"
            >
              <Octagon size={15} /> Stop
            </button>
            <button
              className="btn"
              disabled={!mayCommand || (!online)}
              onClick={cancel}
              title="Cancel the destination and stop the robot (no resume)"
            >
              <X size={15} /> Cancel
            </button>
          </>
        ) : (
          <button
            className="btn wide danger"
            disabled={!mayCommand || (!online)}
            onClick={stop}
            title="Cancel navigation and stop the robot"
          >
            <Octagon size={15} /> Stop Robot
          </button>
        )}
      </div>
      {online && onDock && !undockBusy && undockBlockedReason && (
        <p className="subtext nav-dock-reason">Undock — {undockBlockedReason}</p>
      )}
      <p className="subtext" style={{ marginTop: 10 }}>
        In an actual emergency always use the red physical emergency-stop button on the robot —
        never rely on a software button.
      </p>
    </div>
  );
}
