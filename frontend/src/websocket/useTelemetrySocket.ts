import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { registerCommandSender, useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { DashboardSocket, telemetryUrl } from "./client";

export function useTelemetrySocket(): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    const store = useTelemetryStore.getState();
    const socket = new DashboardSocket(telemetryUrl(), {
      onFrame: (frame) => {
        const commands = useCommandStore.getState();
        switch (frame.type) {
          case "command.ack":
            commands.handleAck(frame.data);
            break;
          case "command.progress":
            commands.handleProgress(frame.data);
            break;
          case "command.result":
            commands.handleResult(frame.data);
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
        useCommandStore.getState().setPickMode("none");
        useTelemetryStore.getState().setWsConnected(false);
      },
    });
    socket.connect();
    registerCommandSender((frame) => socket.send(frame));
    return () => {
      registerCommandSender(null);
      socket.close();
      store.setWsConnected(false);
    };
  }, [queryClient]);
}
