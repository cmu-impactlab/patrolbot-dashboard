import { expect, test } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE, STOPPED_MOTION } from "./fixtures";

test("Undock uses fresh measured motion without a pose and expires independently", async ({ page }) => {
  const fixture = await dashboardFixture(page, "operator", "Operator", false, ["undock"], { omitPose: true });
  await page.goto("/");
  await fixture.wsReady;
  fixture.send("telemetry.base_state", {
    ...READY_BASE_STATE, charge_state: "charging", dock_state: "DOCKED_CONFIRMED",
    dock_state_valid: true, undock_profile_commissioned: true,
    motors_enabled: false, localization_recovery_required: true, localization_seed_stamp_ns: 0,
  });
  const nav = page.locator('[data-tour="widget-navControls"]');
  const undock = nav.getByRole("button", { name: "Undock", exact: true });
  const destination = nav.getByRole("button", { name: "Send Robot Here", exact: true });
  const noMotion = "Wait for fresh, valid session-bound measured-motion readings before undocking.";
  await expect(undock).toBeDisabled();
  await expect(undock).toHaveAttribute("title", noMotion);

  fixture.send("telemetry.motion", STOPPED_MOTION);
  await expect(undock).toBeEnabled();
  await expect(destination).toBeDisabled();
  await expect(nav).toContainText("The robot is recovering its location.");

  // Base and LiDAR updates keep arriving; only measured motion stops.
  await expect(undock).toHaveAttribute("title", noMotion);
  await expect(undock).toBeDisabled();
  await expect(nav.getByRole("button", { name: "Set Robot Location", exact: true })).toBeEnabled();
  expect(fixture.commands).toHaveLength(0);
});

test("Undock does not reuse a stopped reading after an odometry origin change", async ({ page }) => {
  const fixture = await dashboardFixture(page, "operator", "Operator", false, ["undock"], { omitPose: true });
  await page.goto("/");
  await fixture.wsReady;
  const docked = {
    ...READY_BASE_STATE, charge_state: "charging", dock_state: "DOCKED_CONFIRMED",
    dock_state_valid: true, undock_profile_commissioned: true, motors_enabled: false,
  };
  const newOrigin = { odom_epoch: "new-origin", odom_epoch_generation: 1 };
  const undock = page.getByRole("button", { name: "Undock", exact: true });
  fixture.send("telemetry.base_state", docked);
  await expect(undock).toBeDisabled();
  fixture.send("telemetry.motion", STOPPED_MOTION);
  await expect(undock).toBeEnabled();

  fixture.send("telemetry.base_state", { ...docked, odom_epoch_valid: false });
  await expect(undock).toBeDisabled();
  fixture.send("telemetry.base_state", { ...docked, ...newOrigin });
  fixture.send("telemetry.motion", STOPPED_MOTION);
  // Ordered WebSocket frames: this capability update proves the browser
  // processed the old motion frame before we inspect the Undock control.
  fixture.send("state.capabilities", { capabilities: ["undock", "motor_enable"] });
  await expect(page.getByText("Advanced", { exact: true })).toBeVisible();
  await page.getByText("Advanced", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Turn motors on", exact: true })).toBeVisible();
  await expect(undock).toBeDisabled();
  fixture.send("telemetry.motion", { ...STOPPED_MOTION, ...newOrigin });
  await expect(undock).toBeEnabled();
  expect(fixture.commands).toHaveLength(0);
});
