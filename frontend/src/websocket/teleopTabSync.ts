import { useCommandStore } from "../stores/commandStore";
import { useTeleopStore } from "../stores/teleopStore";

/** Advisory UI coordination only. Never share lease IDs, input, auth or old
 * commands; every tab still has its own socket and server-side admission. */
export function startTeleopTabSync(): () => void {
  if (typeof window.BroadcastChannel !== "function") return () => {};
  const channel = new window.BroadcastChannel("patrolbot.manual-drive-ui.v1");
  const source = crypto.randomUUID();
  const peers = new Set<string>();
  let busy = useTeleopStore.getState().phase !== "idle";
  const publish = () => channel.postMessage({ kind: "state", source, busy });
  channel.onmessage = event => {
    const message = event.data as { kind?: string; source?: string; busy?: boolean } | null;
    if (!message || typeof message.source !== "string" || message.source === source) return;
    if (message.kind === "query") { publish(); return; }
    if (message.kind !== "state" && message.kind !== "close") return;
    if (message.kind === "state" && message.busy === true) {
      peers.add(message.source);
      useCommandStore.setState({ stoppedGoal: null, allowUnlocalized: false, pickMode: "none", lastAttempt: null });
    } else peers.delete(message.source);
    useTeleopStore.setState({ externalBusy: peers.size > 0 });
  };
  const unsubscribe = useTeleopStore.subscribe(state => {
    const nextBusy = state.phase !== "idle";
    if (nextBusy !== busy) { busy = nextBusy; publish(); }
  });
  channel.postMessage({ kind: "query", source });
  publish();
  return () => {
    unsubscribe();
    channel.postMessage({ kind: "close", source });
    channel.close();
    useTeleopStore.setState({ externalBusy: false });
  };
}
