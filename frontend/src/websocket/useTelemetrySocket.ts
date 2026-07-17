import { useEffect } from "react";
import { registerCommandSender, useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { DashboardSocket, telemetryUrl } from "./client";

export function useTelemetrySocket(): void {
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
  }, []);
}
