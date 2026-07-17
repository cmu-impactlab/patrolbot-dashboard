import { create } from "zustand";
import { useTelemetryStore } from "./telemetryStore";
import type {
  CommandAckData,
  CommandOutcome,
  CommandProgressData,
  CommandResultData,
  CommandType,
  GoalData,
} from "../types/protocol";

/** Map-click modes armed from the Navigation widget. */
export type PickMode = "none" | "goal" | "initialpose";

export interface ActiveCommand {
  commandId: string;
  command: CommandType;
  phase: "sending" | "running";
  stage: string | null;
  distanceRemaining: number | null;
}

export interface CommandResultInfo {
  command: CommandType;
  outcome: CommandOutcome;
  detail: string | null;
  at: number;
}

interface CommandState {
  active: ActiveCommand | null;
  lastResult: CommandResultInfo | null;
  pickMode: PickMode;
  /** Where the robot was heading when the operator pressed Stop; offering
   *  "Resume" re-sends this destination. */
  stoppedGoal: GoalData | null;

  setPickMode: (mode: PickMode) => void;
  send: (command: CommandType, goal?: GoalData) => void;
  stop: () => void;
  resume: () => void;
  handleAck: (data: CommandAckData) => void;
  handleProgress: (data: CommandProgressData) => void;
  handleResult: (data: CommandResultData) => void;
}

// The live socket registers its sender here; null while disconnected.
let sendFrame: ((frame: string) => boolean) | null = null;
let sequence = 0;

export function registerCommandSender(sender: ((frame: string) => boolean) | null): void {
  sendFrame = sender;
}

export const useCommandStore = create<CommandState>((set, get) => ({
  active: null,
  lastResult: null,
  pickMode: "none",
  stoppedGoal: null,

  setPickMode: (mode) => set({ pickMode: mode }),

  stop: () => {
    // Remember the destination in effect right now so Resume can restore it.
    const goal = useTelemetryStore.getState().path?.goal ?? null;
    set({ stoppedGoal: goal ?? get().stoppedGoal });
    get().send("stop");
  },

  resume: () => {
    const goal = get().stoppedGoal;
    if (!goal) return;
    set({ stoppedGoal: null });
    get().send("navigate_to_pose", goal);
  },

  send: (command, goal) => {
    // A fresh destination invalidates any pending Resume offer.
    if (command === "navigate_to_pose" && get().stoppedGoal && goal !== undefined) {
      set({ stoppedGoal: null });
    }
    const commandId = crypto.randomUUID();
    const frame = JSON.stringify({
      version: 1,
      type: "command.request",
      robot_id: useTelemetryStore.getState().robotId,
      sequence: ++sequence,
      timestamp: new Date().toISOString(),
      data: { command_id: commandId, command, goal: goal ?? null },
    });
    if (!sendFrame?.(frame)) {
      set({
        pickMode: "none",
        lastResult: {
          command, outcome: "failed", at: Date.now(),
          detail: "Not connected to the dashboard server.",
        },
      });
      return;
    }
    set({
      pickMode: "none",
      active: { commandId, command, phase: "sending", stage: null, distanceRemaining: null },
    });
  },

  handleAck: (data) => {
    const active = get().active;
    if (data.accepted) {
      if (active?.commandId === data.command_id) {
        set({ active: { ...active, phase: "running" } });
      }
      return;
    }
    // Rejections may target a command this tab never sent (another operator's,
    // or a synthesized server rejection) — always surface the reason.
    set({
      active: active?.commandId === data.command_id ? null : active,
      lastResult: {
        command: active?.command ?? "navigate_to_pose",
        outcome: "rejected",
        detail: data.reason ?? "The command was not accepted.",
        at: Date.now(),
      },
    });
  },

  handleProgress: (data) => {
    const active = get().active;
    if (active?.commandId !== data.command_id) return;
    set({
      active: {
        ...active,
        stage: data.detail ?? data.stage,
        distanceRemaining: data.distance_remaining ?? null,
      },
    });
  },

  handleResult: (data) => {
    const active = get().active;
    if (active?.commandId !== data.command_id) return;
    set({
      active: null,
      lastResult: {
        command: active.command,
        outcome: data.outcome,
        detail: data.detail ?? null,
        at: Date.now(),
      },
    });
  },
}));
