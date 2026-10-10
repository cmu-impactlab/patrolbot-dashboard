import { sameMap } from "../lib/mapContext";
import { localizationRecoveryReason } from "../lib/localizationRecovery";
import { create } from "zustand";
import { useTelemetryStore } from "./telemetryStore";
import type {
  CommandAckData,
  CommandOutcome,
  CommandProgressData,
  CommandResultData,
  CommandType,
  GoalData,
  MapContext,
} from "../types/protocol";

/** Map-click modes armed from the Navigation widget. */
export type PickMode = "none" | "goal" | "initialpose";

export interface ActiveCommand {
  commandId: string;
  command: CommandType;
  phase: "sending" | "running";
  stage: string | null;
  distanceRemaining: number | null;
  goal?: GoalData;
}

export interface CommandResultInfo {
  /** The request UUID this outcome belongs to — shown verbatim next to the
   *  charging/dock controls so a result can be matched to the audit log. */
  commandId: string | null;
  command: CommandType;
  outcome: CommandOutcome;
  detail: string | null;
  at: number;
}

/** Commands that hand the robot the ability to move, or take it away. A
 *  pending destination must not survive one — requiring a new, explicit
 *  operator action afterwards is the whole point. */
const CLEARS_PENDING_GOAL: CommandType[] = ["charge_release", "motor_enable", "undock", "software_reset"];

interface CommandState {
  active: ActiveCommand | null;
  lastResult: CommandResultInfo | null;
  pickMode: PickMode;
  /** Where the robot was heading when the operator pressed Stop; offering
   *  "Resume" re-sends this destination. */
  stoppedGoal: GoalData | null;
  /** The most recent command intent, remembered so "Take over" can re-send it
   *  with the takeover flag after a single-operator-lease rejection. */
  lastAttempt: { command: CommandType; goal?: GoalData; activeMap?: MapContext } | null;
  /** Operator override, armed under Advanced: send the next destination even
   *  though the robot reports it does not know where it is. Cleared as soon as
   *  it is used, so it can never be left switched on. */
  allowUnlocalized: boolean;

  setPickMode: (mode: PickMode) => void;
  setAllowUnlocalized: (allow: boolean) => void;
  resetOverrides: () => void;
  connectionLost: () => void;
  send: (command: CommandType, goal?: GoalData, takeover?: boolean) => void;
  stop: () => void;
  resume: () => void;
  cancel: () => void;
  takeOver: () => void;
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
  lastAttempt: null,
  allowUnlocalized: false,

  setPickMode: (mode) => set({ pickMode: mode }),

  setAllowUnlocalized: (allow) => set({ allowUnlocalized: allow }),

  // A reconnect is treated elsewhere as possibly a different robot, so an
  // override armed against the old session must not carry over to it. Called
  // from the socket layer on every (re)connect.
  resetOverrides: () => set({ allowUnlocalized: false }),

  connectionLost: () => {
    const active = get().active;
    if (active?.command === "software_reset") {
      set({ active: { ...active, phase: "running",
        stage: "Connection lost — reset outcome is unknown. Waiting for the supervisor result; do not retry." } });
    }
  },

  stop: () => {
    // Remember the destination in effect right now so Resume can restore it.
    const active = get().active;
    const telemetry = useTelemetryStore.getState();
    // Path telemetry can lag the acknowledged command. Keep the destination
    // actually sent, even when Stop arrives before the first path update.
    const candidate = active?.command === "navigate_to_pose"
      ? active.goal ?? telemetry.path?.goal : telemetry.path?.goal;
    const goal = candidate && sameMap(candidate, telemetry.baseState) ? candidate : null;
    set({ stoppedGoal: goal ?? get().stoppedGoal });
    get().send("stop");
  },

  resume: () => {
    const goal = get().stoppedGoal;
    if (!goal) return;
    get().send("navigate_to_pose", goal);
  },

  cancel: () => {
    // Abandon the destination. If the robot is still driving, halt it; unlike
    // Stop, this does not remember the goal, so no Resume is offered.
    const wasActive = get().active !== null;
    set({ stoppedGoal: null });
    if (wasActive) get().send("stop");
  },

  takeOver: () => {
    const attempt = get().lastAttempt;
    if (!attempt) return;
    if (attempt.goal && !sameMap(attempt.activeMap, useTelemetryStore.getState().baseState)) {
      set({ lastAttempt: null, pickMode: "none", lastResult: { commandId: null,
        command: attempt.command, outcome: "rejected", at: Date.now(),
        detail: "The robot map changed. Select a new position before taking over." } });
      return;
    }
    get().send(attempt.command, attempt.goal, true);
  },

  send: (command, goal, takeover = false) => {
    if ((command === "navigate_to_pose" || command === "set_initial_pose") &&
        (!goal?.map_id || !goal.map_revision ||
         (command === "navigate_to_pose" && !sameMap(goal, useTelemetryStore.getState().baseState)))) {
      set({ pickMode: "none", lastResult: { commandId: crypto.randomUUID(), command,
        outcome: "rejected", at: Date.now(), detail: "Map context is missing or differs from the robot. Select a new position." } });
      return;
    }
    if (command === "software_reset") {
      useTelemetryStore.setState({ poseSetThisSession: false });
      set({ allowUnlocalized: false, pickMode: "none" });
    }
    if (command === "navigate_to_pose") {
      const telemetry = useTelemetryStore.getState();
      const reason = localizationRecoveryReason(telemetry.baseState, telemetry.baseStateAt);
      if (reason) {
        set({ pickMode: "none", lastResult: {
          commandId: crypto.randomUUID(), command, outcome: "rejected", at: Date.now(), detail: reason,
        } });
        return;
      }
    }
    // A fresh destination invalidates any pending Resume offer.
    if (command === "navigate_to_pose" && get().stoppedGoal && goal !== undefined) {
      set({ stoppedGoal: null });
    }
    // So does anything that changes whether the robot can move at all: after
    // a charge release, motor enable or undock the operator starts from a
    // clean slate rather than being offered a stale destination to resume.
    if (CLEARS_PENDING_GOAL.includes(command)) {
      set({ stoppedGoal: null });
    }
    // Remember the intent so a lease rejection can be retried as a takeover.
    set({ lastAttempt: { command, goal, activeMap: useTelemetryStore.getState().baseState ?? undefined } });
    // Read the override once and disarm it. A takeover re-send of the same
    // destination therefore has to be armed again deliberately, which is the
    // point: the operator re-confirms driving on a fix the robot distrusts.
    const allowUnlocalized = command === "navigate_to_pose" && get().allowUnlocalized;
    const commandId = crypto.randomUUID();
    const frame = JSON.stringify({
      version: 1,
      type: "command.request",
      robot_id: useTelemetryStore.getState().robotId,
      sequence: ++sequence,
      timestamp: new Date().toISOString(),
      data: {
        command_id: commandId, command, goal: goal ?? null, takeover,
        allow_unlocalized: allowUnlocalized,
      },
    });
    if (!sendFrame?.(frame)) {
      set({
        pickMode: "none",
        lastResult: {
          commandId, command, outcome: "failed", at: Date.now(),
          detail: "Not connected to the dashboard server.",
        },
      });
      return;
    }
    // Disarm only now the frame is actually away. Clearing it before the send
    // would lose an override the operator legitimately armed if the socket
    // dropped the frame, and silently demand they arm it again.
    set({
      pickMode: "none",
      allowUnlocalized: allowUnlocalized ? false : get().allowUnlocalized,
      active: { commandId, command, phase: "sending", stage: null, distanceRemaining: null, goal },
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
        commandId: data.command_id,
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
    if (active?.commandId !== data.command_id) {
      const previous = get().lastResult;
      // A durable supervisor can resolve a reset after the dashboard has
      // already surfaced an uncertain timeout. Accept that replay by ID.
      if (previous?.commandId === data.command_id) {
        set({ lastResult: { ...previous, outcome: data.outcome,
          detail: data.detail ?? null, at: Date.now() } });
      }
      return;
    }
    // A successful "set location" satisfies the navigation gate for this session.
    if (active.command === "set_initial_pose" && data.outcome === "succeeded") {
      useTelemetryStore.getState().markPoseSet();
    }
    set({
      active: null,
      lastResult: {
        commandId: data.command_id,
        command: active.command,
        outcome: data.outcome,
        detail: data.detail ?? null,
        at: Date.now(),
      },
    });
  },
}));
