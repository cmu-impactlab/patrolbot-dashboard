import { create } from "zustand";
import {
  AUTONOMOUS_SPEED_CAPABILITY, AUTONOMOUS_SPEED_MAX_ANGULAR, AUTONOMOUS_SPEED_MAX_LINEAR,
  AUTONOMOUS_SPEED_REQUEST,
} from "../types/protocol";
import type { AutonomousSpeedReceiptData, AutonomousSpeedRequestData, AutonomousSpeedSelection } from "../types/protocol";
import { canCommand, useAuthStore } from "./authStore";
import { nextCommandSequence, sendCommandFrame } from "./commandStore";
import { useTelemetryStore } from "./telemetryStore";

const RECEIPT_WINDOW_MS = 6_000;
const CONFIRMATION_WINDOW_MS = 5_000;
const uuid4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
type Binding = Readonly<{ robotId: string; session: string }>;

type Result = { phase: "idle" | "unknown" }
  | { phase: "pending"; binding: Binding; request: AutonomousSpeedRequestData; deadline: number }
  | { phase: "applied"; binding: Binding; selection: AutonomousSpeedSelection };

export function autonomousSpeedBinding(state: ReturnType<typeof useTelemetryStore.getState>): Binding | null {
  const binding = state.autonomousSpeedBinding;
  return state.wsConnected && state.connection.state === "online"
    && state.capabilities.includes(AUTONOMOUS_SPEED_CAPABILITY)
    && binding && binding.robotId === state.robotId && uuid4.test(binding.session) ? binding : null;
}

function sameBinding(left: Binding, right: Binding | null): boolean {
  return left.robotId === right?.robotId && left.session === right.session;
}

function matches(left: AutonomousSpeedSelection, right?: AutonomousSpeedSelection | null): boolean {
  return left.profile === right?.profile && left.linear_m_s === right.linear_m_s
    && left.angular_rad_s === right.angular_rad_s;
}

function valid(selection: AutonomousSpeedSelection): boolean {
  const { profile, linear_m_s, angular_rad_s } = selection;
  return Number.isFinite(linear_m_s) && linear_m_s >= 0 && linear_m_s <= AUTONOMOUS_SPEED_MAX_LINEAR
    && Number.isFinite(angular_rad_s) && angular_rad_s >= 0 && angular_rad_s <= AUTONOMOUS_SPEED_MAX_ANGULAR
    && (profile === "Custom" || (profile === "Slow" && linear_m_s === .12 && angular_rad_s === .40)
      || (profile === "Fast" && linear_m_s === .22 && angular_rad_s === .70));
}

export const useAutonomousSpeedStore = create<{
  result: Result;
  send: (binding: Binding, selection: AutonomousSpeedSelection) => void;
  receipt: (robotId: string, data: AutonomousSpeedReceiptData) => void;
  reconcile: () => void;
}>((set, get) => {
  function expire(result: Result, delay: number) {
    setTimeout(() => {
      if (get().result === result) set({ result: { phase: "unknown" } });
    }, delay);
  }
  return {
    result: { phase: "idle" },
    send(binding, selection) {
      if (!canCommand(useAuthStore.getState().user) || !valid(selection)
          || !sameBinding(binding, autonomousSpeedBinding(useTelemetryStore.getState()))
          || get().result.phase === "pending") return;
      const request: AutonomousSpeedRequestData = {
        command_id: crypto.randomUUID(), session_id: binding.session,
        selection: { profile: selection.profile, linear_m_s: selection.linear_m_s, angular_rad_s: selection.angular_rad_s },
      };
      const result: Result = { phase: "pending", binding, request, deadline: performance.now() + RECEIPT_WINDOW_MS };
      set({ result });
      try {
        if (sendCommandFrame(JSON.stringify({
          version: 1, type: AUTONOMOUS_SPEED_REQUEST, robot_id: binding.robotId,
          sequence: nextCommandSequence(), timestamp: new Date().toISOString(), data: request,
        }))) {
          expire(result, RECEIPT_WINDOW_MS);
          return;
        }
      } catch { /* A failed socket send is not a readback. */ }
      set({ result: { phase: "unknown" } });
    },
    receipt(robotId, data) {
      const pending = get().result;
      if (pending.phase !== "pending" || robotId !== pending.binding.robotId
          || pending.request.command_id !== data.command_id) return;
      if (data.outcome !== "applied" || !matches(pending.request.selection, data.readback)
          || !sameBinding(pending.binding, autonomousSpeedBinding(useTelemetryStore.getState()))
          || performance.now() >= pending.deadline) {
        set({ result: { phase: "unknown" } });
        return;
      }
      const result: Result = { phase: "applied", binding: pending.binding, selection: pending.request.selection };
      set({ result });
      expire(result, CONFIRMATION_WINDOW_MS);
    },
    reconcile() {
      const result = get().result;
      if ((result.phase === "pending" || result.phase === "applied")
          && !sameBinding(result.binding, autonomousSpeedBinding(useTelemetryStore.getState()))) {
        set({ result: { phase: "unknown" } });
      }
    },
  };
});
