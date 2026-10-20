import { describe, expect, it } from "vitest";
import { editPolicyText, formatBounds, numericStep, sliderRange } from "./tuning";
import type { TuningCatalogEntry } from "./tuning";

/**
 * Self-contained catalog entries. The registry is deliberately NOT consulted:
 * these tests describe the formatter's contract, not any particular reviewed
 * metadata, so a concurrent registry edit cannot make them lie.
 */
type EntryPatch = Omit<Partial<TuningCatalogEntry>, "bounds"> & { bounds?: Partial<TuningCatalogEntry["bounds"]> };

function entry(overrides: EntryPatch = {}): TuningCatalogEntry {
  const { bounds, ...rest } = overrides;
  return {
    id: "test.param",
    label: "Test parameter",
    group: "nav2_controller",
    type: "number",
    value_kind: "speed",
    unit: "m/s",
    source: { file: "test.yaml", node: "test_node", key: "test", index: null },
    default: 0.1,
    accepts_edits: true,
    activatable: true,
    activation_block: null,
    protected: false,
    not_applicable: false,
    mutability: "unverified",
    mutability_fallback: "restart_required",
    live_verified: false,
    restart_scopes: [],
    effective_ceiling_ref: null,
    exceeds_effective_ceiling: false,
    dependencies: [],
    side_effects: [],
    ...rest,
    bounds: { status: "candidate_unverified", lower: null, upper: null,
      lower_inclusive: false, upper_inclusive: false, note: "", ...(bounds ?? {}) },
  };
}

describe("formatBounds", () => {
  it("spells inclusive and exclusive endpoints with the exact inequality", () => {
    expect(formatBounds(entry({ bounds: { lower: 0.01, upper: 0.3, lower_inclusive: false, upper_inclusive: true } })))
      .toBe("Allowed range: 0.01 < value ≤ 0.3 m/s.");
    expect(formatBounds(entry({ bounds: { lower: 0, upper: 1, lower_inclusive: true, upper_inclusive: false } })))
      .toBe("Allowed range: 0 ≤ value < 1 m/s.");
    expect(formatBounds(entry({ bounds: { lower: 0, upper: 1, lower_inclusive: true, upper_inclusive: true } })))
      .toBe("Allowed range: 0 ≤ value ≤ 1 m/s.");
  });

  it("never invents a missing side from the current value", () => {
    expect(formatBounds(entry({ bounds: { lower: 0, lower_inclusive: false } })))
      .toBe("Allowed range: value > 0 m/s.");
    expect(formatBounds(entry({ bounds: { lower: 0, lower_inclusive: true } })))
      .toBe("Allowed range: value ≥ 0 m/s.");
    expect(formatBounds(entry({ bounds: { upper: 0.3, upper_inclusive: true } })))
      .toBe("Allowed range: value ≤ 0.3 m/s.");
    expect(formatBounds(entry({ bounds: { upper: 1, upper_inclusive: false } })))
      .toBe("Allowed range: value < 1 m/s.");
  });

  it("reports fully unbounded parameters without a fabricated 0 or 1", () => {
    expect(formatBounds(entry())).toBe("No declared parameter bounds.");
    expect(formatBounds(entry({ bounds: { status: "unknown", note: "Not applicable." } }))).toBe("No declared parameter bounds.");
  });

  it("omits the unit when the value is not a physical quantity", () => {
    expect(formatBounds(entry({ unit: "boolean", bounds: { lower: 0, upper: 1, lower_inclusive: true, upper_inclusive: true } })))
      .toBe("Allowed range: 0 ≤ value ≤ 1.");
    expect(formatBounds(entry({ unit: "string", bounds: { lower: 0, lower_inclusive: true } })))
      .toBe("Allowed range: value ≥ 0.");
  });
});

describe("editPolicyText", () => {
  it("describes the robot's policy from catalog flags only", () => {
    expect(editPolicyText(entry())).toMatch(/validates every change/);
    expect(editPolicyText(entry({ accepts_edits: false }))).toMatch(/Read-only/);
    expect(editPolicyText(entry({ accepts_edits: true, protected: true }))).toMatch(/Protected/);
    expect(editPolicyText(entry({ accepts_edits: true, protected: false, not_applicable: true }))).toMatch(/Not applicable/);
    expect(editPolicyText(entry({ accepts_edits: true, protected: false, not_applicable: false, activatable: false })))
      .toMatch(/not enabled activation/);
  });
});

describe("numericStep", () => {
  it("steps whole numbers for integer fields and hundredths otherwise", () => {
    expect(numericStep(entry({ type: "integer" }))).toBe(1);
    expect(numericStep(entry({ type: "number" }))).toBe(0.01);
  });
});

describe("sliderRange", () => {
  it("refuses a slider whenever a side is unbounded", () => {
    expect(sliderRange(entry({ bounds: { lower: null, upper: null } }))).toBeNull();
    expect(sliderRange(entry({ bounds: { lower: 0, upper: null } }))).toBeNull();
    expect(sliderRange(entry({ bounds: { lower: null, upper: 1 } }))).toBeNull();
  });

  it("keeps inclusive endpoints and pulls exclusive ones onto the step grid", () => {
    expect(sliderRange(entry({ bounds: { lower: 0, upper: 0.3, lower_inclusive: true, upper_inclusive: true } })))
      .toEqual({ min: 0, max: 0.3, step: 0.01 });
    // 0.01 exclusive: 0.01 itself is illegal, so the first legal grid point is 0.02.
    expect(sliderRange(entry({ bounds: { lower: 0.01, upper: 0.3, lower_inclusive: false, upper_inclusive: true } })))
      .toEqual({ min: 0.02, max: 0.3, step: 0.01 });
    // 0 exclusive: the first legal grid point is one step up.
    expect(sliderRange(entry({ bounds: { lower: 0, upper: 1, lower_inclusive: false, upper_inclusive: true } })))
      .toEqual({ min: 0.01, max: 1, step: 0.01 });
    // 1 exclusive: the last legal grid point is one step down.
    expect(sliderRange(entry({ bounds: { lower: 0, upper: 1, lower_inclusive: true, upper_inclusive: false } })))
      .toEqual({ min: 0, max: 0.99, step: 0.01 });
  });

  it("uses whole-number grids for integer fields", () => {
    expect(sliderRange(entry({ type: "integer", bounds: { lower: 0, upper: 10, lower_inclusive: false, upper_inclusive: true } })))
      .toEqual({ min: 1, max: 10, step: 1 });
    expect(sliderRange(entry({ type: "integer", bounds: { lower: 0, upper: 10, lower_inclusive: true, upper_inclusive: false } })))
      .toEqual({ min: 0, max: 9, step: 1 });
  });

  it("refuses a slider when no legal value remains on the step grid", () => {
    // (0.01, 0.02]: only 0.02 is representable, so the slider would be a point.
    expect(sliderRange(entry({ bounds: { lower: 0.01, upper: 0.02, lower_inclusive: false, upper_inclusive: true } }))).toBeNull();
    // (0, 0.01) exclusive both sides: no grid point survives.
    expect(sliderRange(entry({ bounds: { lower: 0, upper: 0.01, lower_inclusive: false, upper_inclusive: false } }))).toBeNull();
    // A single inclusive value is a point, not a range.
    expect(sliderRange(entry({ bounds: { lower: 0.05, upper: 0.05, lower_inclusive: true, upper_inclusive: true } }))).toBeNull();
  });
});
