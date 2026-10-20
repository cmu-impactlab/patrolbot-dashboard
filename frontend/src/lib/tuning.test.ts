import { describe, expect, it } from "vitest";
import {
  affectedServices,
  applyRequest,
  BADGE_LABEL,
  BUNDLED_ENTRIES,
  editDisabledReason,
  effectiveValues,
  fieldBadge,
  formatBounds,
  gaugeReading,
  normalizeSnapshot,
  outcomeLabel,
  parseNumericInput,
  sliderRange,
  parseTuningResponse,
  redeployRequest,
  registryEntry,
  restoreRequest,
  saveRequest,
  speedBand,
  statusRequest,
  unappliedRestartFields,
  validateBatch,
} from "./tuning";
import type { BatchChange, TuningCatalogEntry } from "./tuning";

const linear = registryEntry("max_linear_m_s")!;
const integerField = registryEntry("amcl.min_particles")!;
const exclusiveLow = registryEntry("amcl.pf_err")!;
// `amcl.pf_z` is a positive quantile multiplier with no published upper bound,
// so the exclusive-upper example must be the robot's `< 1.5 m` approach range.
const exclusiveHigh = registryEntry("nav2.controller.approach_velocity_scaling_dist")!;
const pfZ = registryEntry("amcl.pf_z")!;

/** A realistic server snapshot. Catalog omitted so the bundled registry is used. */
function serverSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "session-1",
    desired_revision: 5,
    active_revision: 4,
    last_known_good_revision: 3,
    active_known: true,
    writable: true,
    activation_supported: true,
    desired: { max_linear_m_s: 0.3, "amcl.alpha1": 0.2 },
    active: { max_linear_m_s: 0.25, "amcl.alpha1": 0.2 },
    effective_limits: { max_linear_m_s: 0.3, max_angular_rad_s: 0.7 },
    pending_keys: [],
    safe_apply: { allowed: false, reason: "Live activation is not enabled." },
    latest_job: null,
    digest: "0123456789abcdef",
    compatibility: "contracted",
    ...overrides,
  };
}

describe("speed band thresholds", () => {
  it("is green below half, amber below 80%, red at or above 80%", () => {
    expect(speedBand(0)).toBe("ok");
    expect(speedBand(0.4999)).toBe("ok");
    expect(speedBand(0.5)).toBe("warn");
    expect(speedBand(0.7999)).toBe("warn");
    expect(speedBand(0.8)).toBe("danger");
    expect(speedBand(2)).toBe("danger");
  });
  it("never treats missing or non-finite ratios as a real band", () => {
    expect(speedBand(null)).toBe("unknown");
    expect(speedBand(Number.NaN)).toBe("unknown");
    expect(speedBand(Number.POSITIVE_INFINITY)).toBe("unknown");
  });
});

describe("gauge readings", () => {
  it("reports signed direction and magnitude ratio for linear motion", () => {
    expect(gaugeReading(0.1, 0.3, "linear")).toMatchObject({ band: "ok", direction: "forward", overLimit: false });
    expect(gaugeReading(-0.1, 0.3, "linear")).toMatchObject({ band: "ok", direction: "reverse" });
    expect(gaugeReading(0.27, 0.3, "linear")).toMatchObject({ band: "danger" });
    expect(gaugeReading(0.2, 0.3, "linear")).toMatchObject({ band: "warn" });
  });
  it("reports angular direction and called-out stopped state", () => {
    expect(gaugeReading(0.1, 0.7, "angular").direction).toBe("left (CCW)");
    expect(gaugeReading(-0.1, 0.7, "angular").direction).toBe("right (CW)");
    expect(gaugeReading(0, 0.7, "linear")).toMatchObject({ direction: "stopped", ratio: 0, band: "ok", overLimit: false });
  });
  it("never fabricates NaN when the active cap is zero", () => {
    const over = gaugeReading(0.1, 0, "linear");
    expect(Number.isNaN(over.ratio)).toBe(false);
    expect(over.overLimit).toBe(true);
    expect(over.percent).toBeNull();
    expect(over.band).toBe("danger");
    const stopped = gaugeReading(0, 0, "linear");
    expect(stopped.ratio).toBe(0);
    expect(stopped.overLimit).toBe(false);
  });
  it("treats a missing value or cap as unknown, never zero", () => {
    const none = gaugeReading(null, 0.3, "linear");
    expect(none).toMatchObject({ band: "unknown", direction: "no data", value: null, ratio: null });
    expect(gaugeReading(0.1, null, "linear")).toMatchObject({ band: "unknown", ratio: null, cap: null });
  });
});

describe("numeric input validation", () => {
  it("rejects incomplete and non-numeric text", () => {
    for (const text of ["", "   ", "-", "0.", ".", "1e", "NaN", "Infinity", "abc", "1.2.3", "0x10", "true"]) {
      expect(parseNumericInput(text, linear).ok, `expected ${JSON.stringify(text)} to be rejected`).toBe(false);
    }
  });
  it("accepts complete finite numbers inside the bounds", () => {
    expect(parseNumericInput("0.2", linear)).toEqual({ ok: true, value: 0.2 });
    expect(parseNumericInput(" 0.3 ", linear)).toEqual({ ok: true, value: 0.3 });
    expect(parseNumericInput("1e-1", linear)).toEqual({ ok: true, value: 0.1 });
  });
  it("enforces inclusive and exclusive bounds and integer types", () => {
    expect(parseNumericInput("0.31", linear).ok).toBe(false);
    expect(parseNumericInput("-0.01", linear).ok).toBe(false);
    expect(parseNumericInput("0", exclusiveLow).ok).toBe(false); // strictly positive
    expect(parseNumericInput("1.5", exclusiveHigh).ok).toBe(false); // strictly below 1.5 m
    expect(parseNumericInput("0", pfZ).ok).toBe(false); // strictly positive quantile multiplier
    expect(parseNumericInput("2.33", pfZ)).toEqual({ ok: true, value: 2.33 }); // documented 99th percentile
    expect(parseNumericInput("100.5", integerField).ok).toBe(false);
    expect(parseNumericInput("1000", integerField)).toEqual({ ok: true, value: 1000 });
  });
});

describe("declared bounds and slider endpoints", () => {
  it("keeps exclusive endpoints off the slider grid at floating-point boundaries", () => {
    // 0.29 is itself a 0.01 grid point, so an exclusive floor must start at 0.30.
    expect(sliderRange({ ...linear, bounds: { ...linear.bounds, lower: 0.29, lower_inclusive: false, upper: 0.6, upper_inclusive: true } }))
      .toEqual({ min: 0.3, max: 0.6, step: 0.01 });
    // An exclusive ceiling at a grid point must stop one step below it.
    expect(sliderRange({ ...linear, bounds: { ...linear.bounds, lower: 0.1, lower_inclusive: true, upper: 0.29, upper_inclusive: false } }))
      .toEqual({ min: 0.1, max: 0.28, step: 0.01 });
  });

  it("shows the robot's documented entries exactly and only sliders they can support", () => {
    const approach = registryEntry("nav2.controller.approach_velocity_scaling_dist")!;
    expect(formatBounds(approach)).toBe("Allowed range: 0 ≤ value < 1.5 m.");
    expect(sliderRange(approach)).toEqual({ min: 0, max: 1.49, step: 0.01 });

    const minApproach = registryEntry("nav2.controller.min_approach_linear_velocity")!;
    expect(formatBounds(minApproach)).toBe("Allowed range: 0.01 < value ≤ 0.3 m/s.");
    expect(sliderRange(minApproach)).toEqual({ min: 0.02, max: 0.3, step: 0.01 });
  });

  it("invents no slider for an unbounded quantile multiplier", () => {
    expect(sliderRange(pfZ)).toBeNull();
    expect(formatBounds(pfZ)).toMatch(/^Allowed range: value > 0/);
  });
});

describe("batch dependency validation", () => {
  const base = effectiveValues(normalizeSnapshot(serverSnapshot()));
  it("rejects a min greater than its max partner in the same batch", () => {
    const changes: BatchChange[] = [{ id: "amcl.min_particles", value: 500 }, { id: "amcl.max_particles", value: 100 }];
    const result = validateBatch(changes, base, BUNDLED_ENTRIES);
    expect(result.ok).toBe(false);
    expect(result.ids).toContain("amcl.min_particles");
  });
  it("enforces a sum constraint", () => {
    const bad = validateBatch([{ id: "amcl.z_hit", value: 0.4 }, { id: "amcl.z_rand", value: 0.5 }], base, BUNDLED_ENTRIES);
    expect(bad.ok).toBe(false);
    const good = validateBatch([{ id: "amcl.z_hit", value: 0.4 }, { id: "amcl.z_rand", value: 0.6 }], base, BUNDLED_ENTRIES);
    expect(good.ok).toBe(true);
  });
  it("checks a single edited field against the persisted partner value", () => {
    const result = validateBatch([{ id: "nav2.controller.desired_linear_vel", value: 0.5 }], base, BUNDLED_ENTRIES);
    expect(result.ok).toBe(false);
    expect(result.reason).toMatch(/no more than/);
  });
  it("rejects the same field twice in one save", () => {
    const result = validateBatch([{ id: "amcl.alpha1", value: 0.2 }, { id: "amcl.alpha1", value: 0.3 }], base, BUNDLED_ENTRIES);
    expect(result.ok).toBe(false);
    expect(result.reason).toMatch(/Duplicate/);
  });
});

describe("snapshot normalization honesty", () => {
  it("treats a missing or malformed response as unavailable with bundled source candidates", () => {
    for (const raw of [null, undefined, "nope", 42, {}, { session_id: "" }]) {
      const snapshot = normalizeSnapshot(raw);
      expect(snapshot.available).toBe(false);
      expect(snapshot.catalogFromServer).toBe(false);
      expect(snapshot.entries.length).toBe(BUNDLED_ENTRIES.length);
      expect(snapshot.reason).toBeTruthy();
    }
  });
  it("reads a contracted server snapshot without inferring activity", () => {
    const snapshot = normalizeSnapshot(serverSnapshot());
    expect(snapshot).toMatchObject({
      available: true,
      sessionId: "session-1",
      desiredRevision: 5,
      activeRevision: 4,
      lastKnownGoodRevision: 3,
      writable: true,
      activationSupported: true,
      activeKnown: true,
      catalogFromServer: false,
    });
    expect(snapshot.effectiveLimits).toEqual({ max_linear_m_s: 0.3, max_angular_rad_s: 0.7 });
    expect(snapshot.fieldActiveKnown["max_linear_m_s"]).toBe(true);
    // No active value for this id -> must not be shown as known.
    expect(snapshot.fieldActiveKnown["nav2.behavior.max_rotational_vel"]).toBe(false);
    expect(snapshot.digest).toBe("0123456789abcdef");
  });
  it("does not claim a field is active when active_known is absent", () => {
    const snapshot = normalizeSnapshot(serverSnapshot({ active_known: false }));
    expect(snapshot.activeKnown).toBe(false);
    expect(snapshot.fieldActiveKnown["max_linear_m_s"]).toBe(false);
  });
  it("accepts the slice-1 contract-only shape", () => {
    const snapshot = normalizeSnapshot({
      kind: "tuning_state_snapshot",
      runtime: "known",
      revisions: { desired: 2, active: 1, last_known_good: 0 },
      fields: [{ id: "max_linear_m_s", desired: 0.3, active: 0.25, active_known: true, pending: false }],
    });
    expect(snapshot.available).toBe(true);
    expect(snapshot.compatibility).toBe("contract-only");
    expect(snapshot.desiredRevision).toBe(2);
    expect(snapshot.activeKnown).toBe(true);
    expect(snapshot.fieldActiveKnown["max_linear_m_s"]).toBe(true);
  });
  it("uses the server catalog and fills missing display fields from the packaged registry", () => {
    const snapshot = normalizeSnapshot(serverSnapshot({
      catalog: [{ id: "max_linear_m_s", group: "speed_ceiling", type: "number", accepts_edits: true }],
    }));
    expect(snapshot.catalogFromServer).toBe(true);
    expect(snapshot.entries).toHaveLength(1);
    expect(snapshot.entries[0].label).toBe("Maximum linear speed");
    expect(snapshot.entries[0].unit).toBe("m/s");
  });
});

describe("write gating and badges", () => {
  it("gives a precise disabled reason at each step", () => {
    expect(editDisabledReason(null, true)).toMatch(/unavailable/);
    expect(editDisabledReason(normalizeSnapshot(serverSnapshot()), false)).toMatch(/administrator/);
    expect(editDisabledReason(normalizeSnapshot(serverSnapshot({ writable: false })), true)).toMatch(/not writable/);
    // Durable desired saves do not activate a running node. Activation retains
    // its separate capability and safe-idle gates.
    expect(editDisabledReason(normalizeSnapshot(serverSnapshot({ activation_supported: false })), true)).toBeNull();
    expect(editDisabledReason(normalizeSnapshot(serverSnapshot()), true)).toBeNull();
  });
  it("never shows Live without verified activation evidence", () => {
    expect(fieldBadge(registryEntry("max_linear_m_s")!)).toBe("redeploy");
    expect(fieldBadge(registryEntry("safety_watchdog.scan_timeout")!)).toBe("read-only");
    expect(fieldBadge(registryEntry("amcl.alpha5")!)).toBe("read-only");
    const live: TuningCatalogEntry = { ...linear, activatable: true, mutability: "live", live_verified: true };
    expect(fieldBadge(live)).toBe("live");
    const unverified: TuningCatalogEntry = { ...linear, activatable: true, mutability: "live", live_verified: false };
    expect(fieldBadge(unverified)).toBe("redeploy");
    expect(BADGE_LABEL.live).toBe("Live");
  });
});

describe("pending deployment detection", () => {
  it("counts restart-required fields the robot has not activated", () => {
    const snapshot = normalizeSnapshot(serverSnapshot());
    const fields = unappliedRestartFields(snapshot);
    expect(fields.map((field) => field.id)).toContain("max_linear_m_s");
    expect(affectedServices(fields).length).toBeGreaterThan(0);
  });
  it("does not flag values that already match the active read-back", () => {
    const snapshot = normalizeSnapshot(serverSnapshot({ desired: { max_linear_m_s: 0.3, "amcl.alpha1": 0.2 }, active: { max_linear_m_s: 0.3, "amcl.alpha1": 0.2 } }));
    expect(unappliedRestartFields(snapshot).map((field) => field.id)).not.toContain("max_linear_m_s");
  });
  it("flags an explicitly pending key even before the active read-back differs", () => {
    const snapshot = normalizeSnapshot(serverSnapshot({ desired: { max_linear_m_s: 0.3, "amcl.alpha1": 0.2 }, active: { max_linear_m_s: 0.3, "amcl.alpha1": 0.2 }, pending_keys: ["amcl.alpha1"] }));
    expect(unappliedRestartFields(snapshot).map((field) => field.id)).toContain("amcl.alpha1");
  });
});

describe("canonical requests carry exact revisions", () => {
  const change: BatchChange = { id: "max_linear_m_s", value: 0.28 };
  it("builds a compare-and-swap save", () => {
    const request = saveRequest("s", 5, [change], "req-1");
    expect(request).toMatchObject({ operation: "save", base_revision: 5, request_id: "req-1",
      generation: { capability: "robot_tuning_v1", session_id: "s" }, changes: [change] });
  });
  it("carries the expected and restore revisions exactly", () => {
    expect(applyRequest("s", 5, "r").expected_revision).toBe(5);
    expect(redeployRequest("s", 5, "r").expected_revision).toBe(5);
    const restore = restoreRequest("s", 5, 3, "r");
    expect(restore).toMatchObject({ operation: "restore", expected_revision: 5, restore_revision: 3, target: "last_known_good" });
  });
  it("adds a job id only to a status query", () => {
    expect(statusRequest("s", "job-1", "r").job_id).toBe("job-1");
    expect(statusRequest("s", undefined, "r").job_id).toBeUndefined();
  });
  it("reuses a passed request id and mints a unique one otherwise", () => {
    expect(saveRequest("s", 5, [change], "keep").request_id).toBe("keep");
    expect(saveRequest("s", 5, [change]).request_id).not.toBe(saveRequest("s", 5, [change]).request_id);
  });
});

describe("response parsing", () => {
  it("maps unreadable responses to an unknown outcome", () => {
    expect(parseTuningResponse("x").outcome).toBe("unknown");
    expect(parseTuningResponse({ outcome: "weird" }).outcome).toBe("unknown");
  });
  it("unwraps a valid response snapshot", () => {
    const response = parseTuningResponse({ request_id: "r", outcome: "conflict", detail: "move on", snapshot: serverSnapshot({ desired_revision: 6 }) });
    expect(response.outcome).toBe("conflict");
    expect(response.snapshot?.desiredRevision).toBe(6);
  });
  it("labels outcomes for the field status line", () => {
    expect(outcomeLabel("ok")).toBe("Saved and active");
    expect(outcomeLabel("conflict")).toBe("Conflict");
    expect(outcomeLabel("failed")).toBe("Failed");
    expect(outcomeLabel("unknown")).toBe("Outcome unknown");
  });
});
