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
          case "state.connection":
            // A (re)connecting robot may be a different robot with the same
            // map_version — refetch rather than trust the cached map.
            if (frame.data.state === "online") {
              queryClient.invalidateQueries({ queryKey: ["map"] });
            }
            useTelemetryStore.getState().handleFrame(frame);
            break;
          default:
            useTelemetryStore.getState().handleFrame(frame);
        }
      },
      onOpen: () => store.setWsConnected(true),
      onClose: () => useTelemetryStore.getState().setWsConnected(false),
    });
    socket.connect();
    registerCommandSender((frame) => socket.send(frame));
    return () => {
      registerCommandSender(null);
      socket.close();
    };
  }, [queryClient]);
}
