import { afterEach, describe, expect, it, vi } from "vitest";
import { freshReadback, saveRequest } from "../lib/tuning";
import { postTuningRequest } from "./tuning";

const session = "11111111-1111-4111-8111-111111111111";
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe("mutation transport never renews old observations or replays requests", () => {
  it("charges the whole response round-trip against the unchanged three-second live boundary", async () => {
    let time = 0;
    vi.spyOn(performance, "now").mockImplementation(() => time);
    const request = saveRequest(session, 1, [{ id: "amcl.alpha1", value: .06 }]);
    vi.stubGlobal("fetch", vi.fn(async () => {
      time = 4000;
      return Response.json({ request_id: request.request_id, outcome: "ok", detail: "saved", job: null,
        snapshot: { session_id: session, desired_revision: 2, writable: true, active_known: false,
          readback: { source: "ros_parameters", age_s: .1, hardware_session_generation: 1,
            values: { max_linear_m_s: .3, max_angular_rad_s: .7 }, limits: { max_linear_m_s: .3, max_angular_rad_s: .7 } } } });
    }));
    const response = await postTuningRequest("fixture", request);
    expect(response.snapshot?.readback?.ageS).toBe(4.1);
    expect(freshReadback(response.snapshot, 4000, 4000, 1)).toBeNull();
  });
  it("aborts a lost response once without retrying or changing its request identity", async () => {
    vi.useFakeTimers();
    const request = saveRequest(session, 1, [{ id: "amcl.alpha1", value: .06 }]);
    const fetch = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    vi.stubGlobal("fetch", fetch);
    const pending = expect(postTuningRequest("fixture", request)).rejects.toThrow("Aborted");
    await vi.advanceTimersByTimeAsync(35_001);
    await pending;
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetch.mock.calls[0][1].body)).request_id).toBe(request.request_id);
  });
});
