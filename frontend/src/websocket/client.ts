import type { AnyFrame } from "../types/protocol";

export interface SocketCallbacks {
  onFrame: (frame: AnyFrame) => void;
  onOpen: () => void;
  onClose: () => void;
}

/** WebSocket wrapper with jittered exponential-backoff reconnect. */
export class DashboardSocket {
  private socket: WebSocket | null = null;
  private retry = 0;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private url: string, private callbacks: SocketCallbacks) {}

  connect(): void {
    this.closed = false;
    this.open();
  }

  private open(): void {
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.retry = 0;
      this.callbacks.onOpen();
    };
    socket.onmessage = (message) => {
      try {
        const frame = JSON.parse(message.data) as AnyFrame;
        if (frame.version !== 1) {
          console.warn("protocol version mismatch:", frame.version);
          return;
        }
        this.callbacks.onFrame(frame);
      } catch (error) {
        console.warn("bad frame", error);
      }
    };
    socket.onclose = () => {
      this.callbacks.onClose();
      if (this.closed) return;
      const delay = Math.min(10_000, 500 * 2 ** this.retry) * (1 + Math.random() * 0.3);
      this.retry += 1;
      this.timer = setTimeout(() => this.open(), delay);
    };
    socket.onerror = () => socket.close();
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close();
  }
}

export function telemetryUrl(): string {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${location.host}/ws/ui`;
}
