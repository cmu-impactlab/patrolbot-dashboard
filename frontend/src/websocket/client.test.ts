import { afterEach, expect, it, vi } from "vitest";
import { DashboardSocket } from "./client";

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 1;
  onopen = () => {};
  onclose = () => {};
  onmessage = (_message: { data: string }) => {};
  onerror = () => {};
  constructor() { FakeSocket.instances.push(this); }
  send() {}
  close() {} // The browser delivers close asynchronously, after replacement.
}
afterEach(() => { vi.unstubAllGlobals(); FakeSocket.instances = []; });
it("ignores callbacks from a retired socket after a replacement connects", () => {
  vi.stubGlobal("WebSocket", FakeSocket);
  const callbacks = { onOpen: vi.fn(), onClose: vi.fn(), onFrame: vi.fn() };
  const old = new DashboardSocket("ws://test", callbacks);
  old.connect(); old.close();
  const replacement = new DashboardSocket("ws://test", callbacks);
  replacement.connect();
  FakeSocket.instances[1].onopen();
  FakeSocket.instances[0].onclose();
  FakeSocket.instances[0].onmessage({ data: '{"version":1,"type":"server.snapshot"}' });
  expect(callbacks.onOpen).toHaveBeenCalledTimes(1);
  expect(callbacks.onClose).not.toHaveBeenCalled();
  expect(callbacks.onFrame).not.toHaveBeenCalled();
  replacement.close();
});
