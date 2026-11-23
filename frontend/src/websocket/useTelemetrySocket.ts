import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { AutonomousSpeedReceiptData } from "../types/protocol";
import { registerCommandSender, useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTuningStore } from "../stores/tuningStore";
import { useTeleopStore } from "../stores/teleopStore";
import { DashboardSocket, telemetryUrl } from "./client";
import { startTeleopTabSync } from "./teleopTabSync";

interface SpeedCapsHandlers {
  receipt: (robotId: string, data: AutonomousSpeedReceiptData) => void;
  reconcile: () => void;
}

export function useTelemetrySocket(speedCaps?: SpeedCapsHandlers): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    const store = useTelemetryStore.getState();
    const stopSpeedReconcile = speedCaps && useTelemetryStore.subscribe(speedCaps.reconcile);
    const stopTabSync = startTeleopTabSync();
    const socket = new DashboardSocket(telemetryUrl(), {
      onFrame: (frame) => {
        const commands = useCommandStore.getState();
        switch (frame.type) {
          case "command.autonomous_speed_caps_receipt":
            speedCaps?.receipt(frame.robot_id, frame.data);
            break;
          case "command.ack":
            if (!useTeleopStore.getState().handleAck(frame.data)) commands.handleAck(frame.data);
            break;
          case "command.progress":
            commands.handleProgress(frame.data);
            break;
          case "command.result":
            if (!useTeleopStore.getState().handleResult(frame.data)) commands.handleResult(frame.data);
            break;
          case "server.snapshot":
            commands.setPickMode("none");
            useTelemetryStore.getState().handleFrame(frame);
            break;
          case "state.connection":
            // A (re)connecting robot may be a different robot with the same
            // map_version — refetch rather than trust the cached map.
            commands.setPickMode("none");
            if (frame.data.state === "online") {
              queryClient.invalidateQueries({ queryKey: ["map"] });
            }
            useTelemetryStore.getState().handleFrame(frame);
            break;
          default:
            useTelemetryStore.getState().handleFrame(frame);
        }
      },
      onOpen: () => {
        store.setWsConnected(true);
        // A reconnect may be a different robot or a different session, so an
        // armed safety override must not survive it — especially since the
        // Advanced disclosure it was armed in is collapsed by default.
        useCommandStore.getState().resetOverrides();
      },
      onClose: () => {
        useTuningStore.getState().connectionLost();
        useTeleopStore.getState().end("Connection lost — manual input ended.");
        useCommandStore.getState().connectionLost();
        useCommandStore.getState().setPickMode("none");
        useTelemetryStore.getState().setWsConnected(false);
      },
    });
    socket.connect();
    registerCommandSender((frame) => socket.send(frame));
    return () => {
      useTeleopStore.getState().end();
      stopTabSync();
      registerCommandSender(null);
      socket.close();
      store.setWsConnected(false);
      stopSpeedReconcile?.();
    };
  }, [queryClient, speedCaps]);
}
