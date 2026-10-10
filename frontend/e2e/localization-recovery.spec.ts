import { test, expect } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE } from "./fixtures";

test("recovery blocks override, preserves pose control and recovers without reload", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  const goal = page.getByRole("button", { name: "Send Robot Here", exact: true });
  const seed = page.getByRole("button", { name: "Set Robot Location", exact: true });
  await expect(seed).toBeEnabled();
  await page.getByText("Advanced", { exact: true }).click();
  await page.getByRole("checkbox", { name: "Send a destination even if the robot does not know where it is" }).check();
  await expect(goal).toBeEnabled();
  fixture.send("telemetry.base_state", { ...READY_BASE_STATE, localization_recovery_required: true });
  await expect(goal).toBeDisabled();
  await expect(seed).toBeEnabled();
  await expect(page.getByText("The robot is recovering its location. Wait before sending a destination.", { exact: true })).toBeVisible();
  fixture.send("telemetry.base_state", READY_BASE_STATE);
  await expect(goal).toBeEnabled();
  fixture.stopBaseUpdates();
  await expect(goal).toBeDisabled({ timeout: 6000 });
  await expect(seed).toBeEnabled();
  expect(fixture.commands).toHaveLength(0);
});

test("legacy base telemetry cannot enable destinations through override", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  await page.getByText("Advanced", { exact: true }).click();
  await page.getByRole("checkbox", { name: "Send a destination even if the robot does not know where it is" }).check();
  const { odom_epoch_valid, localization_recovery_required, localization_seed_stamp_ns, ...legacy } = READY_BASE_STATE;
  void odom_epoch_valid; void localization_recovery_required; void localization_seed_stamp_ns;
  fixture.send("telemetry.base_state", legacy);
  await expect(page.getByRole("button", { name: "Send Robot Here", exact: true })).toBeDisabled();
  expect(fixture.commands).toHaveLength(0);
});

test("a destination preview cannot outlive localization recovery", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  await page.getByText("Advanced", { exact: true }).click();
  await page.getByRole("checkbox", { name: "Send a destination even if the robot does not know where it is" }).check();
  await page.getByRole("button", { name: "Send Robot Here", exact: true }).click();
  const canvas = page.getByLabel("Live robot map");
  await canvas.evaluate(el => { el.setPointerCapture = () => {}; });
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("Map is not visible");
  const pointer = { pointerId: 1, pointerType: "touch", clientX: bounds.x + 80, clientY: bounds.y + 80, bubbles: true };
  await canvas.dispatchEvent("pointerdown", pointer);
  await canvas.dispatchEvent("pointerup", pointer);
  await expect(page.getByLabel("Confirm map selection")).toBeVisible();
  fixture.send("telemetry.base_state", { ...READY_BASE_STATE, localization_recovery_required: true });
  // Fullscreen phone maps hide background controls from accessibility queries.
  await expect(page.locator("button").filter({ hasText: "Send Robot Here" })).toBeDisabled();
  await page.getByRole("button", { name: "Send destination", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "recovering its location" })).toBeVisible();
  expect(fixture.commands).toHaveLength(0);
});
