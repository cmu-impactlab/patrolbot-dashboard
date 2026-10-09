import { describe, expect, it } from "vitest";
import { sameMap } from "./mapContext";
import { useCommandStore, registerCommandSender } from "../stores/commandStore";
const a = { map_id: "cmuq-floor1", map_revision: "a" };
describe("map context", () => {
  it("requires both identity and revision", () => {
    expect(sameMap(a, { ...a })).toBe(true);
    expect(sameMap(a, { ...a, map_id: "cmuq-floor2" })).toBe(false);
    expect(sameMap(a, { ...a, map_revision: "old" })).toBe(false);
    expect(sameMap({}, {})).toBe(false);
  });
  it("rejects a legacy spatial request before sending", () => {
    const sent: string[] = [];
    registerCommandSender(frame => { sent.push(frame); return true; });
    useCommandStore.getState().send("set_initial_pose", { x: 1, y: 2 });
    expect(sent).toEqual([]);
    expect(useCommandStore.getState().lastResult?.outcome).toBe("rejected");
    registerCommandSender(null);
  });
});
