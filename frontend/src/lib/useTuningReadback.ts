import { useEffect, useState } from "react";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTuningStore } from "../stores/tuningStore";
import { freshReadback } from "./tuning";

/** Expired observations can remain visible as last-known data, never live caps.
 * Neither an unrelated heartbeat nor a failed HTTP poll renews their age. */
export function useTuningReadback() {
  const [tick, setTick] = useState(() => performance.now());
  useEffect(() => {
    const timer = setInterval(() => setTick(performance.now()), 250);
    return () => clearInterval(timer);
  }, []);
  const snapshot = useTuningStore(state => state.snapshot);
  const receivedAt = useTuningStore(state => state.receivedAt);
  const connected = useTelemetryStore(state => state.wsConnected);
  const generation = useTelemetryStore(state => state.baseState?.session_generation ?? null);
  const now = Math.max(tick, performance.now());
  const observed = connected && snapshot?.available
    ? freshReadback(snapshot, receivedAt, now, generation) : null;
  const last = snapshot?.readback?.generation === generation ? snapshot.readback : null;
  const localAge = receivedAt === null ? null : (now - receivedAt) / 1000;
  const ageS = last && localAge !== null && Number.isFinite(localAge) && localAge >= 0
    ? last.ageS + localAge : null;
  return { snapshot, now, receivedAt, connected, observed, last, ageS,
    status: !connected ? "offline" : observed ? "fresh" : last ? "stale" : "unavailable" } as const;
}
