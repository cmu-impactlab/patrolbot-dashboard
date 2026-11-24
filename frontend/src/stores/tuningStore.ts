import { create } from "zustand";
import { fetchTuningSnapshot } from "../api/tuning";
import type { NormalizedSnapshot } from "../lib/tuning";
import { useTelemetryStore } from "./telemetryStore";

interface TuningState {
  status: "idle" | "loading" | "ready" | "unavailable";
  snapshot: NormalizedSnapshot | null;
  receivedAt: number | null;
  load: () => Promise<void>;
  connectionLost: () => void;
}

export const useTuningStore = create<TuningState>((set, get) => {
  const pendingLoads = new Map<string, Promise<void>>();
  const robotId = () => useTelemetryStore.getState().robotId;
  let observedRobot: string | null = null;
  let connectionGeneration = 0;

  return {
    status: "idle",
    snapshot: null,
    receivedAt: null,
    load: () => {
      const robot = robotId();
      if (observedRobot !== robot) {
        observedRobot = robot;
        connectionGeneration++;
        pendingLoads.clear();
        set({ snapshot: null, receivedAt: null });
      }
      const generation = connectionGeneration;
      const pending = pendingLoads.get(robot);
      if (pending) return pending;
      const load = async () => {
        set({ status: "loading" });
        const snapshot = await fetchTuningSnapshot(robot);
        if (robot !== robotId() || generation !== connectionGeneration) return;
        const previous = get().snapshot;
        if (!snapshot.available && previous?.catalogFromServer) {
          // A failed poll does not renew the age of the last observation.
          set({ snapshot: { ...previous, available: false, reason: snapshot.reason }, status: "unavailable" });
          return;
        }
        set({
          snapshot,
          receivedAt: performance.now(),
          status: snapshot.available ? "ready" : "unavailable",
        });
      };
      const operation = load().finally(() => {
        if (pendingLoads.get(robot) === operation) pendingLoads.delete(robot);
      });
      pendingLoads.set(robot, operation);
      return operation;
    },
    connectionLost: () => {
      connectionGeneration++;
      pendingLoads.clear();
      const snapshot = get().snapshot;
      set({ snapshot: snapshot ? { ...snapshot, available: false, reason: "Dashboard disconnected." } : null,
        status: "unavailable" });
    },
  };
});
