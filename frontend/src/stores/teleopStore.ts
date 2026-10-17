import { create } from "zustand";
import { manualDriveReason, TELEOP_LIMITS, TELEOP_TICK_MS } from "../lib/teleop";
import type { CommandAckData, CommandResultData, TeleopVelocityData } from "../types/protocol";
import { useAuthStore } from "./authStore";
import { nextCommandSequence, sendCommandFrame, useCommandStore } from "./commandStore";
import { useTelemetryStore } from "./telemetryStore";

type Phase = "idle" | "armed" | "pending" | "active" | "stopping";
interface Session {
  id: string;
  generation: number;
  robotGeneration: number;
  robotId: string;
  seq: number;
  stopId: string | null;
  lateClosed: boolean;
  accepted: boolean;
  startTerminal: boolean;
  stopIds: Set<string>;
  pendingStops: Set<string>;
}
interface TeleopState {
  phase: Phase;
  externalBusy: boolean;
  generation: number;
  session: Session | null;
  linear: number;
  angular: number;
  deadman: boolean;
  detail: string | null;
  takeover: boolean;
  arm: (takeover?: boolean) => void;
  input: (linear: number, angular: number, deadman: boolean, takeover?: boolean) => void;
  end: (detail?: string) => void;
  handleAck: (data: CommandAckData) => boolean;
  handleResult: (data: CommandResultData) => boolean;
}

let timer: ReturnType<typeof setInterval> | undefined;
let unsubscribe: (() => void)[] = [];
// Keep IDs/sequence numbers through release and reconnect, so late replies can
// only close an old start, never activate it or reuse its sample sequence.
const sessions = new Map<string, Session>();
function envelope(type: string, data: unknown, robotId = useTelemetryStore.getState().robotId): boolean {
  return sendCommandFrame(JSON.stringify({ version: 1, type, robot_id: robotId,
    sequence: nextCommandSequence(), timestamp: new Date().toISOString(), data }));
}
function sample(session: Session, deadman: boolean, linear = 0, angular = 0): boolean {
  const data: TeleopVelocityData = { command_id: session.id, seq: session.seq++, deadman,
    linear: deadman ? linear : 0, angular: deadman ? angular : 0 };
  return envelope("teleop.velocity", data, session.robotId);
}
function stop(session: Session): void {
  session.stopId = crypto.randomUUID();
  session.stopIds.add(session.stopId);
  session.pendingStops.add(session.stopId);
  envelope("command.request", { command_id: session.stopId, command: "teleop_stop", goal: null }, session.robotId);
}
function finishIfResolved(session: Session): void {
  if (useTeleopStore.getState().session === session && session.startTerminal && session.pendingStops.size === 0) {
    useTeleopStore.setState({ phase: "idle", session: null });
  }
}
function clearWatch(): void {
  clearInterval(timer);
  timer = undefined;
  unsubscribe.splice(0).forEach(fn => fn());
}
function watch(): void {
  clearWatch();
  const operatorId = useAuthStore.getState().user?.id;
  const check = () => {
    const state = useTeleopStore.getState();
    if (!["armed", "pending", "active"].includes(state.phase)) return;
    const reason = manualDriveReason();
    const t = useTelemetryStore.getState();
    if (reason || operatorId !== useAuthStore.getState().user?.id || (state.session && (state.session.robotGeneration !== t.baseState?.session_generation || state.session.robotId !== t.robotId))) {
      state.end(reason ?? "Robot session changed — manual input ended.");
    }
  };
  unsubscribe = [useTelemetryStore.subscribe(check), useAuthStore.subscribe(check), useCommandStore.subscribe(check)];
  timer = setInterval(() => {
    check();
    const state = useTeleopStore.getState();
    if (state.phase === "active" && state.deadman && state.session) {
      if (!sample(state.session, true, state.linear, state.angular)) state.end("Connection lost — manual input ended.");
    }
  }, TELEOP_TICK_MS);
}
function bounded(value: number, limits: { min: number; max: number }): number {
  return Number.isFinite(value) ? Math.max(limits.min, Math.min(limits.max, value)) : 0;
}

export const useTeleopStore = create<TeleopState>((set, get) => ({
  phase: "idle", externalBusy: false, generation: 0, session: null, linear: 0, angular: 0, deadman: false, detail: null, takeover: false,
  arm: (takeover = false) => {
    if (get().phase !== "idle") return;
    const reason = manualDriveReason();
    if (reason) { set({ detail: reason }); return; }
    useCommandStore.setState({ stoppedGoal: null, allowUnlocalized: false, pickMode: "none", lastAttempt: null });
    set({ phase: "armed", generation: get().generation + 1, detail: null, takeover });
    watch();
  },
  input: (linear, angular, deadman, takeover = false) => {
    if (!deadman) { get().end(); return; }
    if (!["armed", "pending", "active"].includes(get().phase)) return;
    const reason = manualDriveReason();
    if (reason) { get().end(reason); return; }
    set({ linear: bounded(linear, TELEOP_LIMITS.linear_m_s), angular: bounded(angular, TELEOP_LIMITS.angular_rad_s), deadman: true });
    if (get().phase !== "armed") return;
    const t = useTelemetryStore.getState();
    const session: Session = { id: crypto.randomUUID(), generation: get().generation,
      robotGeneration: t.baseState!.session_generation, robotId: t.robotId, seq: 0, stopId: null, lateClosed: false, accepted: false,
      startTerminal: false, stopIds: new Set(), pendingStops: new Set() };
    sessions.set(session.id, session);
    set({ session, phase: "pending" });
    if (!envelope("command.request", { command_id: session.id, command: "teleop_start", goal: null, takeover: takeover || get().takeover }, session.robotId)) {
      get().end("Not connected to the dashboard server.");
    }
  },
  end: (detail = "Manual input ended — zero requested. This is not confirmation the robot stopped.") => {
    const state = get();
    if (state.phase === "idle" || state.phase === "stopping") return;
    clearWatch();
    // Invalidate the generation before any later ACK or next sampling tick.
    set({ phase: state.session ? "stopping" : "idle", generation: state.generation + 1,
      deadman: false, linear: 0, angular: 0, detail });
    if (state.session) {
      sample(state.session, false);
      stop(state.session);
    }
  },
  handleAck: data => {
    const session = sessions.get(data.command_id);
    if (!session) {
      const stopping = [...sessions.values()].find(s => s.stopIds.has(data.command_id));
      if (!stopping) return false;
      if (!data.accepted) {
        stopping.pendingStops.delete(data.command_id);
        if (get().session === stopping) set({ detail: data.reason ?? "Manual stop was not accepted. Waiting for the original robot result." });
        finishIfResolved(stopping);
      }
      return true;
    }
    const state = get();
    if (data.accepted) {
      if (session.accepted) return true;
      session.accepted = true;
      if (state.session === session && state.phase === "pending" && state.generation === session.generation && state.deadman && !manualDriveReason()) {
        set({ phase: "active", detail: "Robot accepted manual drive — hold to send input." });
      } else if (state.session === session && state.phase === "pending") {
        session.lateClosed = true;
        get().end(manualDriveReason() ?? "Manual input ended before acceptance.");
      } else if (!(state.session === session && state.phase === "active") && !session.lateClosed) {
        session.lateClosed = true;
        if (state.phase === "idle" && state.session === null) {
          set({ phase: "stopping", session, generation: state.generation + 1,
            deadman: false, linear: 0, angular: 0, detail: "Late start acceptance — zero requested; waiting for robot closure." });
        }
        sample(session, false);
        stop(session); // accepted after release: close the now-accepted start.
      }
    } else if (state.session === session) {
      // Definitive refusal is not an owned session. Do not issue a global
      // teleop_stop here: it could stop the other operator whose lease refused
      // this start. A release already in flight must still resolve its stops.
      clearWatch();
      session.startTerminal = true;
      set({ phase: "stopping", generation: state.generation + 1, deadman: false, linear: 0, angular: 0,
        detail: data.reason ?? "Manual drive was not accepted. Arm again to retry." });
      sample(session, false);
      finishIfResolved(session);
    }
    return true;
  },
  handleResult: data => {
    const session = sessions.get(data.command_id) ?? [...sessions.values()].find(s => s.stopIds.has(data.command_id));
    if (!session) return false;
    if (get().session === session) {
      get().end(data.detail ?? "Manual session ended. Arm again to retry.");
      if (data.outcome === "timeout") {
        set({ detail: data.detail ?? "Manual command outcome is unknown — waiting for the robot result. Do not retry." });
        return true;
      }
      set({ detail: data.detail ?? get().detail ?? "Manual session ended. Arm again to retry." });
    }
    if (data.outcome === "timeout") return true;
    if (data.command_id === session.id) session.startTerminal = true;
    else session.pendingStops.delete(data.command_id);
    // Retain unknown ownership until BOTH the original robot session and all
    // requested stops have terminal answers, regardless of result order.
    finishIfResolved(session);
    return true;
  },
}));
