import { expect, test } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE } from "./fixtures";

test("advanced tuning shows measured odom without AMCL and never arms from settings", async ({ page }) => {
  const fixture = await dashboardFixture(page, "operator");
  await page.route("**/api/robots/*/tuning", route => route.fulfill({ status: 503,
    json: { detail: "Robot tuning is unavailable in this isolated fixture." } }));
  await page.goto("/?advanced=1");
  await fixture.wsReady;
  await expect(page.getByRole("heading", { name: "Speed and tuning" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Redeploy with new params/ })).toBeDisabled();
  expect(fixture.commands).toHaveLength(0);
  fixture.send("telemetry.base_state", { ...READY_BASE_STATE, dock_state_valid: true, dock_state: "CLEAR_CONFIRMED" });
  fixture.send("telemetry.motion", { linear_velocity: -.12, angular_velocity: .2, source: "raw_odom",
    sample_age_s: .01, receipt_age_s: .01, session_generation: 1,
    odom_epoch: "isolated-epoch", odom_epoch_generation: 1 });
  await expect(page.getByRole("meter", { name: "Measured linear speed" })).toContainText("reverse");
  await expect(page.getByRole("meter", { name: "Measured angular speed" })).toContainText("left");
  await expect(page.getByRole("meter", { name: "Measured linear speed" })).toContainText("active cap unknown");
  await page.getByText("AMCL localization", { exact: false }).first().click();
  expect(fixture.commands).toHaveLength(0);
  expect(fixture.teleopFrames).toHaveLength(0);
  await expect(page.getByRole("meter", { name: "Measured linear speed" })).toContainText("No fresh speed data", { timeout: 4000 });
  expect(await page.locator("body").evaluate(node => node.scrollWidth <= innerWidth + 1)).toBe(true);
});
