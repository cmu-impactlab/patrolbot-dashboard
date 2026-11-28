import { expect, test } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE, STOPPED_MOTION } from "./fixtures";

test("inline recovery leaves controls usable and waits for authoritative fault clearance", async ({ page }) => {
  const f = await dashboardFixture(page, "operator", "Operator", false, ["dock_reset"]);
  await page.goto("/?advanced=1");
  await f.wsReady;
  const fault = { ...READY_BASE_STATE, dock_state_valid: true, dock_state: "FAULT_STOPPED",
    operation_fault_reason: "rear_clearance_insufficient_for_requested_distance", operation_fault_request_id: "failed-undock" };
  f.send("telemetry.base_state", fault);
  const banner = page.getByRole("alert").filter({ hasText: "Robot operation fault" });
  await expect(banner).toBeVisible();
  const toggle = banner.getByRole("button", { name: "Details / recovery" });
  await toggle.click();
  const details = banner.getByRole("region", { name: "Recover operation fault" });
  await expect(details).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(f.commands).toHaveLength(0);
  // An existing control outside the panel must remain reachable by real clicks.
  await page.getByRole("button", { name: "Refresh parameter readback" }).click();
  await expect(banner).toBeVisible();
  await expect(details).toBeVisible();
  f.send("telemetry.motion", STOPPED_MOTION);
  const confirm = details.getByRole("button", { name: "Confirm recovery" });
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => f.commands.map(item => item.command)).toEqual(["dock_reset"]);
  await expect(details.getByRole("status")).toHaveText("Recovery succeeded: Mock command completed");
  await expect(banner).toBeVisible();
  await details.getByRole("button", { name: "Close details" }).click();
  await expect(toggle).toBeFocused();
  await expect(details).toBeHidden();
  await expect(banner).toBeVisible();
  await toggle.click();
  await expect(details).toBeVisible();
  f.send("telemetry.base_state", { ...fault, operation_fault_request_id: "next-fault" });
  await expect(details).toBeHidden();
  await toggle.click();
  await expect(details.getByRole("status")).toHaveCount(0);
  f.send("telemetry.base_state", { ...fault, dock_state: "CLEAR_CONFIRMED", operation_fault_reason: "", operation_fault_request_id: "" });
  await expect(banner).toHaveCount(0);
});
