import { useCommandStore } from "../stores/commandStore";

/** Keep the outcome visible while a phone's navigation widget is behind the map. */
export function MapCommandFeedback() {
  const active = useCommandStore(state => state.active);
  const result = useCommandStore(state => state.lastResult);
  if (!active && !result) return null;
  return <div className="map-command-result" role="status">
    {active ? <><strong>{active.phase === "sending" ? "Sending…" : "Command in progress"}</strong>{active.stage && <div>{active.stage}</div>}
      {active.distanceRemaining != null && <div>{active.distanceRemaining.toFixed(1)} m remaining</div>}</>
      : <><strong>{result!.outcome}</strong><div>{result!.detail}</div></>}
  </div>;
}
