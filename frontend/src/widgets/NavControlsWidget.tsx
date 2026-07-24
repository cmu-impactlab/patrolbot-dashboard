import { Anchor, Crosshair, MapPin, Octagon, Play, X } from "lucide-react";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";

const OUTCOME_COPY: Record<string, string> = {
  succeeded: "Done",
  failed: "Failed",
  rejected: "Not accepted",
  canceled: "Canceled",
  timeout: "Timed out",
};

/**
 * Goal-based commands only (send-to-destination, set-location, stop). There is
 * deliberately no joystick/velocity control, and "Return to Dock" stays
 * disabled because the robot has no autonomous dock-in — enabling it would
 * teach users an interaction the robot doesn't support.
 */
export function NavControlsWidget() {
  const connection = useTelemetryStore((state) => state.connection);
  const poseSetThisSession = useTelemetryStore((state) => state.poseSetThisSession);
  const active = useCommandStore((state) => state.active);
  const lastResult = useCommandStore((state) => state.lastResult);
  const pickMode = useCommandStore((state) => state.pickMode);
  const stoppedGoal = useCommandStore((state) => state.stoppedGoal);
  const setPickMode = useCommandStore((state) => state.setPickMode);
  const stop = useCommandStore((state) => state.stop);
  const resume = useCommandStore((state) => state.resume);
  const cancel = useCommandStore((state) => state.cancel);
  const takeOver = useCommandStore((state) => state.takeOver);

  const online = connection.state === "online";
  // A rejection from the single-operator lease — offer an explicit takeover.
  const leaseBlocked =
    active === null &&
    lastResult?.outcome === "rejected" &&
    (lastResult.detail ?? "").includes("in control of the robot");
  const offerResume = stoppedGoal !== null && active === null;
  // Navigation is hard-blocked until the operator has set the robot's 2D
  // location this session, so the robot is never sent anywhere from an
  // unconfirmed pose.
  const canNavigate = online && poseSetThisSession;
  const gateHint = "Set the robot's 2D location before sending it anywhere.";

  return (
    <div>
      {!online && (
        <div className="nav-disabled-banner">
          The robot is not connected — commands are unavailable.
        </div>
      )}
      {online && !poseSetThisSession && pickMode === "none" && (
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
      {online && !active && lastResult && (
        <div className={`nav-result-banner outcome-${lastResult.outcome}`}>
          {OUTCOME_COPY[lastResult.outcome] ?? lastResult.outcome}
          {lastResult.detail ? ` — ${lastResult.detail}` : ""}
          {leaseBlocked && (
            <button
              className="btn"
              onClick={takeOver}
              title="Take control from the current operator and re-send your command"
            >
              Take over
            </button>
          )}
        </div>
      )}
      <div className="nav-buttons">
        <button
          className={`btn wide primary ${pickMode === "goal" ? "active" : ""}`}
          disabled={!canNavigate}
          onClick={() => setPickMode(pickMode === "goal" ? "none" : "goal")}
          title={canNavigate ? "Pick a destination on the map" : gateHint}
        >
          <MapPin size={15} /> Send Robot Here
        </button>
        <button
          className={`btn ${pickMode === "initialpose" ? "active" : ""}`}
          disabled={!online}
          onClick={() => setPickMode(pickMode === "initialpose" ? "none" : "initialpose")}
          title="Tell the robot where it actually is on the map"
        >
          <Crosshair size={15} /> Set Robot Location
        </button>
        <button className="btn" disabled title="This robot has no automatic docking — drive it onto the dock manually">
          <Anchor size={15} /> Return to Dock
        </button>
        {offerResume ? (
          <>
            <button
              className="btn success"
              disabled={!canNavigate}
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
              disabled={!online}
              onClick={stop}
              title="Pause the robot here; you can resume afterwards"
            >
              <Octagon size={15} /> Stop
            </button>
            <button
              className="btn"
              disabled={!online}
              onClick={cancel}
              title="Cancel the destination and stop the robot (no resume)"
            >
              <X size={15} /> Cancel
            </button>
          </>
        ) : (
          <button
            className="btn wide danger"
            disabled={!online}
            onClick={stop}
            title="Cancel navigation and stop the robot"
          >
            <Octagon size={15} /> Stop Robot
          </button>
        )}
      </div>
      <p className="subtext" style={{ marginTop: 10 }}>
        In an actual emergency always use the red physical emergency-stop button on the robot —
        never rely on a software button.
      </p>
    </div>
  );
}
