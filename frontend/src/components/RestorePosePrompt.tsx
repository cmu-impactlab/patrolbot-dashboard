import * as Dialog from "@radix-ui/react-dialog";
import { Crosshair, MapPin } from "lucide-react";
import { useEffect, useState } from "react";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";

/**
 * When the robot reconnects and no 2D location has been set this session, offer
 * to resume from the last-known pose the server saved when it went offline. The
 * dialog itself is the confirmation — nothing is sent to the robot until the
 * operator explicitly clicks a button.
 */
export function RestorePosePrompt() {
  const connection = useTelemetryStore((state) => state.connection);
  const lastKnownPose = useTelemetryStore((state) => state.lastKnownPose);
  const poseSetThisSession = useTelemetryStore((state) => state.poseSetThisSession);
  const send = useCommandStore((state) => state.send);
  const setPickMode = useCommandStore((state) => state.setPickMode);

  const online = connection.state === "online";
  const [dismissed, setDismissed] = useState(false);

  // A dismissal only lasts for the current connection; a fresh session should
  // ask again.
  useEffect(() => {
    if (!online) setDismissed(false);
  }, [online]);

  const open = online && !poseSetThisSession && lastKnownPose != null && !dismissed;

  const restore = () => {
    if (lastKnownPose) send("set_initial_pose", lastKnownPose);
    setDismissed(true);
  };
  const setNew = () => {
    setPickMode("initialpose");
    setDismissed(true);
  };

  const heading =
    lastKnownPose?.yaw != null
      ? `${Math.round((lastKnownPose.yaw * 180) / Math.PI)}°`
      : "unknown";

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next) setDismissed(true); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content" style={{ maxWidth: 400 }}>
          <Dialog.Title asChild>
            <h2>Continue from where you left off?</h2>
          </Dialog.Title>
          <p className="subtext" style={{ marginTop: 0 }}>
            The robot's last-known location was saved when it disconnected. You
            can restore it, or set a new location on the map. The robot won't be
            sent anywhere until its location is set.
          </p>
          {lastKnownPose && (
            <div className="restore-pose-facts">
              <span>x&nbsp;{lastKnownPose.x.toFixed(2)} m</span>
              <span>y&nbsp;{lastKnownPose.y.toFixed(2)} m</span>
              <span>heading&nbsp;{heading}</span>
            </div>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14, flexWrap: "wrap" }}>
            <button className="btn" onClick={() => setDismissed(true)}>Not now</button>
            <button className="btn" onClick={setNew}>
              <Crosshair size={14} /> Set a new location
            </button>
            <button className="btn primary" onClick={restore}>
              <MapPin size={14} /> Restore this location
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
