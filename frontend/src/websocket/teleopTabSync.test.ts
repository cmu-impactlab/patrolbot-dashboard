import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startTeleopTabSync } from "./teleopTabSync";
import { useCommandStore, registerCommandSender } from "../stores/commandStore";
import { useTeleopStore } from "../stores/teleopStore";

class FakeChannel {
  static current: FakeChannel;
  messages: unknown[] = [];
  onmessage: ((event: { data: unknown }) => void) | null = null;
  closed = false;
  constructor(_name: string) { FakeChannel.current = this; }
  postMessage(data: unknown) { this.messages.push(data); }
  close() { this.closed = true; }
  receive(data: unknown) { this.onmessage?.({ data }); }
}
let close: (() => void) | null = null;
beforeEach(() => {
  vi.stubGlobal("BroadcastChannel", FakeChannel);
  useTeleopStore.setState({ phase: "idle", externalBusy: false, session: null });
  useCommandStore.setState({ active: null, stoppedGoal: { x: 1, y: 2 }, allowUnlocalized: true, pickMode: "goal" });
});
afterEach(() => { close?.(); close = null; registerCommandSender(null); useTeleopStore.setState({ phase: "idle", externalBusy: false, session: null }); vi.unstubAllGlobals(); });

it("clears old navigation intent and gates conflicting commands when another tab is busy", () => {
  close = startTeleopTabSync();
  FakeChannel.current.receive({ kind: "state", source: "other-tab", busy: true });
  expect(useCommandStore.getState()).toMatchObject({ stoppedGoal: null, allowUnlocalized: false, pickMode: "none", lastAttempt: null });
  expect(useTeleopStore.getState().externalBusy).toBe(true);
  const send = vi.fn(() => true); registerCommandSender(send);
  useCommandStore.getState().send("navigate_to_pose", { x: 1, y: 2 });
  expect(send).not.toHaveBeenCalled();
  FakeChannel.current.receive({ kind: "state", source: "other-tab", busy: false });
  expect(useTeleopStore.getState().externalBusy).toBe(false);
  expect(useCommandStore.getState().stoppedGoal).toBeNull();
});
it("shares only advisory booleans, never session UUIDs, motion, auth or cached commands", () => {
  close = startTeleopTabSync();
  useTeleopStore.setState({ phase: "armed" });
  FakeChannel.current.receive({ kind: "query", source: "new-tab" });
  const states = FakeChannel.current.messages as { kind: string; source: string; busy?: boolean }[];
  expect(states.at(-1)?.busy).toBe(true);
  for (const message of states) expect(Object.keys(message).sort()).toEqual(message.kind === "state" ? ["busy", "kind", "source"] : ["kind", "source"]);
});
it("removes only a closed peer and does not clear this tab's own pending owner state", () => {
  close = startTeleopTabSync();
  useTeleopStore.setState({ phase: "pending" });
  FakeChannel.current.receive({ kind: "state", source: "other-tab", busy: true });
  FakeChannel.current.receive({ kind: "close", source: "other-tab" });
  expect(useTeleopStore.getState()).toMatchObject({ phase: "pending", externalBusy: false });
});
