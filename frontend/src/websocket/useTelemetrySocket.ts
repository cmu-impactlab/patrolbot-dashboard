import { useEffect } from "react";
import { useTelemetryStore } from "../stores/telemetryStore";
import { DashboardSocket, telemetryUrl } from "./client";

export function useTelemetrySocket(): void {
  useEffect(() => {
    const store = useTelemetryStore.getState();
    const socket = new DashboardSocket(telemetryUrl(), {
      onFrame: (frame) => useTelemetryStore.getState().handleFrame(frame),
      onOpen: () => store.setWsConnected(true),
      onClose: () => useTelemetryStore.getState().setWsConnected(false),
    });
    socket.connect();
    return () => socket.close();
  }, []);
}
