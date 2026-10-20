import { describe, expect, it } from "vitest";
import { parseNumericInput, registryEntry } from "./tuning";

describe("documented Nav2 and robot input boundaries", () => {
  it.each([
    ["amcl.sigma_hit", "0", "0.2"],
    ["nav2.controller.min_approach_linear_velocity", "0.01", "0.08"],
    ["nav2.controller.approach_velocity_scaling_dist", "1.5", "0.6"],
    ["amcl.pf_z", "0", "2.33"],
    ["amcl.min_particles", "0", "1000"],
    ["amcl.max_beams", "1", "60"],
    ["max_linear_m_s", "0.31", "0.30"],
    ["max_angular_rad_s", "0.71", "0.70"],
  ])("%s refuses invalid values without replacing the YAML starting value", (id, bad, good) => {
    const entry = registryEntry(id)!;
    expect(parseNumericInput(bad, entry).ok).toBe(false);
    expect(parseNumericInput(good, entry).ok).toBe(true);
  });
});
