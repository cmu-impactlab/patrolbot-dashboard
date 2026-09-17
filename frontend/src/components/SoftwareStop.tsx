import { canCommand, useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";

export function SoftwareStop() {
  const user = useAuthStore(state => state.user);
  const online = useTelemetryStore(state => state.wsConnected && state.connection.state === "online");
  const stop = useCommandStore(state => state.stop);
  const reason = !canCommand(user) ? "Observer: controls unavailable" : !online ? "Robot disconnected" : null;
  return <div className="software-stop">
    <button className="btn danger" disabled={!!reason} onClick={stop}>Stop</button>
    {reason && <small>{reason}</small>}
  </div>;
}
