import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { normalizeSnapshot } from "../lib/tuning";
import type { NormalizedSnapshot, TuningCatalogEntry } from "../lib/tuning";
import { useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTuningStore } from "../stores/tuningStore";
import { fetchTuningSnapshot, postTuningRequest } from "../api/tuning";
import { TuningPanel } from "./TuningPanel";

// The panel polls on mount; the unit tests only inspect rendering, so the
// transport is mocked and the initial GET is left pending.
vi.mock("../api/tuning", async (original) => ({
  ...await original<typeof import("../api/tuning")>(),
  postTuningRequest: vi.fn(), fetchTuningSnapshot: vi.fn(), fetchTuningJob: vi.fn(),
}));

const SESSION = "11111111-1111-4111-8111-111111111111";

/**
 * A fully self-contained catalog entry. The shared registry is deliberately not
 * read: these tests pin the panel's rendering contract, not reviewed metadata
 * that a concurrent registry edit may still be changing.
 */
type EntryPatch = Omit<Partial<TuningCatalogEntry>, "bounds"> & { bounds?: Partial<TuningCatalogEntry["bounds"]> };

function entry(overrides: EntryPatch & Pick<TuningCatalogEntry, "id" | "label" | "group">): TuningCatalogEntry {
  const { bounds, ...rest } = overrides;
  return {
    type: "number", value_kind: "speed", unit: "m/s",
    source: { file: "test.yaml", node: "test_node", key: overrides.id, index: null },
    default: 0.1, accepts_edits: true, activatable: true, activation_block: null,
    protected: false, not_applicable: false, mutability: "unverified",
    mutability_fallback: "restart_required", live_verified: false, restart_scopes: [],
    effective_ceiling_ref: null, exceeds_effective_ceiling: false,
    dependencies: [], side_effects: [],
    ...rest,
    bounds: { status: "candidate_unverified", lower: null, upper: null,
      lower_inclusive: false, upper_inclusive: false, note: "", ...(bounds ?? {}) },
  };
}

// The two requested examples: min_approach with an exclusive 0.01 floor and
// sigma with an exclusive 0 floor. Bounds are supplied here, not read from the
// registry, so the assertions cannot be invalidated by a registry edit.
const MIN_APPROACH = entry({
  id: "nav2.controller.min_approach_linear_velocity",
  label: "Min approach linear velocity",
  group: "nav2_controller",
  bounds: { lower: 0.01, lower_inclusive: false, upper: 0.3, upper_inclusive: true,
    note: "Nav2 Jazzy requires this value to be strictly above 0.01 m/s." },
});
const SIGMA = entry({
  id: "amcl.sigma_hit",
  label: "AMCL laser hit Gaussian sigma",
  group: "amcl",
  bounds: { lower: 0, lower_inclusive: false, upper: 1, upper_inclusive: true,
    note: "A positive Gaussian standard deviation; the upper limit 1 is robot policy." },
});

function snapshot(entries: TuningCatalogEntry[], desired: Record<string, number>,
  desiredRevision: number | null = 7): NormalizedSnapshot {
  return normalizeSnapshot({
    session_id: SESSION,
    desired_revision: desiredRevision,
    active_revision: desiredRevision,
    committed_active_revision: desiredRevision,
    last_known_good_revision: desiredRevision,
    active_known: false,
    writable: true,
    activation_supported: true,
    safe_apply: { allowed: false, reason: "unit fixture" },
    desired, active: {}, pending_keys: [],
    catalog: entries,
  });
}

function ready(snap: NormalizedSnapshot, drafts: Record<string, string> = {}) {
  useTuningStore.setState({
    snapshot: snap, status: "ready", drafts, savePhase: {}, saveDetail: {},
    receivedAt: performance.now(),
    saveRequestIds: {}, draftBaseRevision: 1, conflictIds: [], job: null,
    redeploy: { phase: "idle", detail: null, requestId: null },
    restore: { phase: "idle", detail: null, requestId: null },
  });
}

const inputFor = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchTuningSnapshot).mockImplementation(() => new Promise<NormalizedSnapshot>(() => {}));
  useAuthStore.setState({ user: { id: 1, username: "admin", display_name: "Admin", role: "administrator", auth_mode: "local" } });
  useTelemetryStore.setState({ wsConnected: true, robotId: "test-robot", baseStateAt: performance.now(),
    baseState: { session_generation: 1, link_connected: true, telemetry_age: 0,
      motors_enabled: false, localization_ready: false } as never });
});

afterEach(() => { cleanup(); });

describe("declared bounds are never fabricated", () => {
  it("explains why a pending activation is disabled while manual input owns motion", () => {
    const snap = snapshot([MIN_APPROACH], { [MIN_APPROACH.id]: .08 });
    snap.pendingKeys = [MIN_APPROACH.id];
    snap.safeApply = { allowed: false, reason: "Physical deadman is held." };
    ready(snap);
    render(<TuningPanel />);
    const button = screen.getByRole("button", { name: /Apply saved changes/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe("Physical deadman is held.");
    expect(postTuningRequest).not.toHaveBeenCalled();
  });
  it("shows no range and no slider when a parameter declares no bounds", () => {
    const unbounded = entry({ id: "test.unbounded", label: "Unbounded parameter", group: "nav2_controller" });
    ready(snapshot([unbounded], { "test.unbounded": 5 }));
    render(<TuningPanel />);
    expect(screen.getByText(/No declared parameter bounds\./)).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    expect(inputFor("Unbounded parameter").value).toBe("5");
    expect(document.body.textContent).not.toMatch(/Range /);
  });

  it("does not invent an upper bound from the saved value when only a lower bound exists", () => {
    const lowerOnly = entry({ id: "test.lower_only", label: "Lower only", group: "nav2_controller",
      bounds: { lower: 0, lower_inclusive: true } });
    ready(snapshot([lowerOnly], { "test.lower_only": 5 }));
    render(<TuningPanel />);
    expect(screen.getByText(/Allowed range: value ≥ 0 m\/s\./)).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    // The old code would have offered a 0–10 slider from server*2.
    expect(document.body.textContent).not.toMatch(/Range 0/);
  });
});

describe("allowed range text is exact", () => {
  it("renders an exclusive 0.01 floor as a strict inequality and starts the slider above it", () => {
    ready(snapshot([MIN_APPROACH], { [MIN_APPROACH.id]: 0.08 }));
    render(<TuningPanel />);
    expect(screen.getByText(/Allowed range: 0\.01 < value ≤ 0\.3 m\/s\./)).toBeTruthy();
    expect(screen.getByText(/Nav2 Jazzy requires this value/)).toBeTruthy();
    expect(screen.getByText(/validates every change/)).toBeTruthy();
    const slider = screen.getByRole("slider", { name: "Min approach linear velocity slider" });
    expect(slider.getAttribute("min")).toBe("0.02");
    expect(slider.getAttribute("max")).toBe("0.3");
    expect(slider.getAttribute("step")).toBe("0.01");
    expect(inputFor("Min approach linear velocity").value).toBe("0.08");
  });

  it("keeps an exclusive 0 floor off the slider's minimum", () => {
    ready(snapshot([SIGMA], { [SIGMA.id]: 0.2 }));
    render(<TuningPanel />);
    expect(screen.getByText(/Allowed range: 0 < value ≤ 1 m\/s\./)).toBeTruthy();
    const slider = screen.getByRole("slider", { name: "AMCL laser hit Gaussian sigma slider" });
    expect(slider.getAttribute("min")).toBe("0.01");
    expect(slider.getAttribute("max")).toBe("1");
  });

  it("pulls an exclusive upper bound one step below the boundary", () => {
    const upperExclusive = entry({ id: "test.upper_excl", label: "Upper exclusive", group: "amcl",
      bounds: { lower: 0, lower_inclusive: true, upper: 1, upper_inclusive: false } });
    ready(snapshot([upperExclusive], { "test.upper_excl": 0.5 }));
    render(<TuningPanel />);
    expect(screen.getByText(/Allowed range: 0 ≤ value < 1 m\/s\./)).toBeTruthy();
    expect(screen.getByRole("slider", { name: "Upper exclusive slider" }).getAttribute("max")).toBe("0.99");
  });

  it("offers no slider when the declared interval has no representable interior", () => {
    const tight = entry({ id: "test.tight", label: "Tight interval", group: "amcl",
      bounds: { lower: 0.01, lower_inclusive: false, upper: 0.02, upper_inclusive: true } });
    ready(snapshot([tight], { "test.tight": 0.02 }));
    render(<TuningPanel />);
    expect(screen.getByText(/Allowed range: 0\.01 < value ≤ 0\.02 m\/s\./)).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    expect(inputFor("Tight interval")).toBeTruthy();
  });

  it("never renders a literal undefined when a note is absent", () => {
    const noNote = entry({ id: "test.no_note", label: "No note", group: "amcl",
      bounds: { lower: 0, lower_inclusive: true, upper: 1, upper_inclusive: true, note: "" } });
    ready(snapshot([noNote], {}));
    render(<TuningPanel />);
    expect(screen.getByText(/Allowed range: 0 ≤ value ≤ 1 m\/s\./)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/undefined/);
  });
});

describe("the numeric text field is never clamped or replaced", () => {
  it("keeps every typed value verbatim, including incomplete and out-of-bounds text", () => {
    ready(snapshot([MIN_APPROACH], { [MIN_APPROACH.id]: 0.08 }));
    render(<TuningPanel />);
    const input = inputFor("Min approach linear velocity");
    for (const text of ["0.01", "", "abc", "2.", "0.001"]) {
      fireEvent.change(input, { target: { value: text } });
      expect(input.value).toBe(text);
      expect(useTuningStore.getState().drafts[MIN_APPROACH.id]).toBe(text);
    }
    expect(postTuningRequest).not.toHaveBeenCalled();
  });

  it("prefers an existing draft over the saved value and autosaves nothing on mount", () => {
    ready(snapshot([MIN_APPROACH], { [MIN_APPROACH.id]: 0.08 }), { [MIN_APPROACH.id]: "0.09" });
    render(<TuningPanel />);
    expect(inputFor("Min approach linear velocity").value).toBe("0.09");
    expect(useTuningStore.getState().drafts[MIN_APPROACH.id]).toBe("0.09");
    expect(postTuningRequest).not.toHaveBeenCalled();
  });

  it("shows the robot's exact reported number, not a rounded display value", () => {
    const precise = entry({ id: "test.precise", label: "Precise parameter", group: "nav2_controller",
      bounds: { lower: 0, lower_inclusive: true, upper: 1, upper_inclusive: true } });
    ready(snapshot([precise], { "test.precise": 0.12345 }));
    render(<TuningPanel />);
    // The editable field preserves the raw number so an edit starts from the
    // real value; only the human-readable status line may round for display.
    expect(inputFor("Precise parameter").value).toBe("0.12345");
    expect(screen.getByText(/Saved target: 0\.123 m\/s/)).toBeTruthy();
    expect(postTuningRequest).not.toHaveBeenCalled();
  });
});

describe("a pending save stays scoped to the field's own group", () => {
  it("submits only the edited group when the slider debounce fires", async () => {
    vi.useFakeTimers();
    try {
      useTelemetryStore.setState({ baseStateAt: performance.now() });
      const drafts = { [MIN_APPROACH.id]: "0.08", [SIGMA.id]: "0.3" };
      ready(snapshot([MIN_APPROACH, SIGMA], { [MIN_APPROACH.id]: 0.08, [SIGMA.id]: 0.3 }), drafts);
      render(<TuningPanel />);
      const slider = screen.getByRole("slider", { name: "Min approach linear velocity slider" });
      fireEvent.change(slider, { target: { value: "0.12" } });
      await act(async () => { vi.advanceTimersByTime(400); });
      const calls = vi.mocked(postTuningRequest).mock.calls;
      expect(calls).toHaveLength(1);
      // `amcl`'s draft must not be swept into the `nav2_controller` save.
      expect(calls[0][1].changes!.map((change) => change.id)).toEqual([MIN_APPROACH.id]);
      expect(useTuningStore.getState().drafts[SIGMA.id]).toBe("0.3");
      expect(useTuningStore.getState().drafts[MIN_APPROACH.id]).toBe("0.12");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("saved versus starting value labelling", () => {
  it("shows Saved only when the robot reports a saved revision", () => {
    ready(snapshot([MIN_APPROACH], { [MIN_APPROACH.id]: 0.08 }, 7));
    render(<TuningPanel />);
    expect(screen.getByText(/Saved target: 0\.08/)).toBeTruthy();
    expect(screen.queryByText(/Initial YAML value 0\.08/)).toBeNull();
  });

  it("shows the initial YAML value, not Saved, when no saved revision is reported", () => {
    ready(snapshot([MIN_APPROACH], { [MIN_APPROACH.id]: 0.08 }, null));
    render(<TuningPanel />);
    expect(screen.getByText(/Initial YAML target: 0\.08/)).toBeTruthy();
    expect(screen.queryByText(/^Saved target:/)).toBeNull();
  });
});

describe("read-only fallback", () => {
  it("shows live values before commissioning without calling them saved or enabling writes", () => {
    const snap = normalizeSnapshot({ session_id: null, desired_revision: null,
      active_known: false, writable: false, activation_supported: false,
      safe_apply: { allowed: false, reason: "Robot tuning has not been initialized." },
      catalog: [SIGMA], desired: { [SIGMA.id]: .2 }, active: {}, pending_keys: [],
      readback: { source: "ros_parameters", age_s: 0, hardware_session_generation: 1,
        values: { [SIGMA.id]: .17 }, limits: null } });
    ready(snap);
    render(<TuningPanel />);
    expect(screen.getByText("0.17").closest(".field-live")?.className).toContain("is-fresh");
    expect(screen.getByText(/Live readback: 0\.17/)).toBeTruthy();
    expect(screen.queryByText(/bundled source catalogue/)).toBeNull();
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("keeps editing independent of localization and motors but blocks a lost SBC link", () => {
    ready(snapshot([SIGMA], { [SIGMA.id]: .2 }));
    render(<TuningPanel />);
    expect(screen.getByLabelText(SIGMA.label).tagName).toBe("INPUT");
    act(() => useTelemetryStore.setState({ baseState: {
      session_generation: 1, link_connected: false, telemetry_age: 0 } as never }));
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.getAllByText(/SBC link is disconnected or stale/).length).toBeGreaterThan(0);
  });
  it("does not substitute YAML for unavailable live readback", () => {
    const readOnly = entry({ id: "test.readonly", label: "Read-only parameter", group: "amcl",
      accepts_edits: false, activatable: false, default: 0.2 });
    ready(snapshot([readOnly], {}));
    render(<TuningPanel />);
    expect(screen.getByText("Not received").closest(".field-live")?.textContent).toContain("—");
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByText("0.2")).toBeNull();
    expect(document.body.textContent).not.toMatch(/source candidate/);
  });
});
