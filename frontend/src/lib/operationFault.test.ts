import { describe, expect, it } from "vitest";
import { operationFaultReason } from "./operationFault";
import type { BaseStateData } from "../types/protocol";

const base = (fields: Partial<BaseStateData>) => fields as BaseStateData;
describe("operation fault reason", () => {
  it("explains a controller-originated blocked turn", () => {
    expect(operationFaultReason(base({ operation_fault_reason: "turn_path_blocked" }))).toContain("half-turn did not complete");
  });
  it("keeps hardware and unknown outcome blockers above operation details", () => {
    expect(operationFaultReason(base({ estop_pressed: true, operation_fault_reason: "turn_path_blocked" }))).toContain("emergency stop");
    expect(operationFaultReason(base({ operation_outcome_unknown: true }))).toContain("unknown");
    expect(operationFaultReason(base({ fault_flags: 256 }))).toContain("256");
  });
  it("does not invent a historical fault reason", () => {
    expect(operationFaultReason(base({ dock_state: "FAULT_STOPPED" }))).toContain("unavailable");
    expect(operationFaultReason(base({ operation_fault_reason: "new_stop_code" }))).toContain("new stop code");
    expect(operationFaultReason(null)).toBeNull();
    expect(operationFaultReason(base({ dock_state: "DOCKED_CONFIRMED" }))).toBeNull();
  });
});
