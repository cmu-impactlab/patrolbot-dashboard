import { test, expect } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE } from "./fixtures";

test("filter spread, invalid data and AMCL expiration remain honest", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Set Robot Location", exact: true })).toBeEnabled();
  const status = page.locator('[data-tour="widget-robotStatus"]');
  const pose = { x: 0, y: 0, yaw: 0, localized: true, linear_velocity: 0, angular_velocity: 0,
    planar_covariance: [0.2238, 0.2453, 0.0683], covariance_limit: 0.25,
    localization_age_s: 0, localization_max_age_s: 30 };
  fixture.send("telemetry.pose", pose);
  await expect(status).toContainText("0.495 m");
  await expect(status).toContainText("15.0°");
  await expect(status).toContainText("XY +1.9% · heading +72.7%");
  fixture.send("telemetry.pose", { ...pose, planar_covariance: [null, 0.1, 0.1] });
  await expect(status).toContainText("Unavailable: invalid covariance");
  fixture.send("telemetry.pose", { ...pose, localization_age_s: 29.5 });
  await expect(status).toContainText("Not refreshed recently");
  await expect(status.locator(".kv").filter({ hasText: "Location known" })).toContainText("Unknown");
  expect(fixture.commands).toHaveLength(0);
});

test("stale off readback retains immediate motor disable", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Set Robot Location", exact: true })).toBeEnabled();
  fixture.send("state.capabilities", { capabilities: ["motor_enable", "motor_disable"] });
  fixture.send("telemetry.base_state", { ...READY_BASE_STATE, motors_enabled: false });
  fixture.stopBaseUpdates();
  await page.clock.install();
  await page.clock.fastForward(16_000);
  const off = page.getByRole("button", { name: "Turn motors off", exact: true });
  await expect(off).toBeEnabled();
  await off.click();
  await expect.poll(() => fixture.commands.map(command => command.command)).toEqual(["motor_disable"]);
});
