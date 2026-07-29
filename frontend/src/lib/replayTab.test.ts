import { afterEach, describe, expect, it, vi } from "vitest";
import { replayIdFromUrl, replayUrl } from "./replayTab";

function at(url: string) {
  window.history.replaceState({}, "", url);
}

afterEach(() => at("/"));

describe("replay tab routing", () => {
  it("the dashboard is the default", () => {
    at("/");
    expect(replayIdFromUrl()).toBeNull();
    at("/?panel=map");
    expect(replayIdFromUrl()).toBeNull();
  });

  it("?replay=<id> selects a recording", () => {
    at("/?replay=42");
    expect(replayIdFromUrl()).toBe(42);
  });

  it("rejects anything that is not a positive recording id", () => {
    for (const raw of ["0", "-3", "abc", "1.5", "", "1;drop"]) {
      at(`/?replay=${encodeURIComponent(raw)}`);
      expect(replayIdFromUrl()).toBeNull();
    }
  });

  it("builds the link against the current path, not the site root", () => {
    // The dashboard can be served from a sub-path; the replay link must follow.
    at("/dashboard/");
    expect(replayUrl(7)).toBe("/dashboard/?replay=7");
  });

  it("round-trips through the URL it generates", () => {
    at(replayUrl(19));
    expect(replayIdFromUrl()).toBe(19);
  });

  it("opens a named tab per recording so repeat clicks reuse it", async () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const { openReplayTab } = await import("./replayTab");
    at("/");
    openReplayTab(5);
    openReplayTab(5);
    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[0]).toEqual(["/?replay=5", "patrolbot-replay-5"]);
    expect(open.mock.calls[1][1]).toBe(open.mock.calls[0][1]);
    vi.unstubAllGlobals();
  });
});
