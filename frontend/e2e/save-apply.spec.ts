import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/** Dedicated loopback preview intercepts every API/socket call in-page. These
 * scenarios never run against the dashboard server, a robot, or native ROS. */
interface Traffic { tuningSaves: unknown[]; tuningApplies: unknown[]; commands: unknown[]; teleop: unknown[] }
async function traffic(page: Page): Promise<Traffic> {
  return page.evaluate(() => (window as unknown as { __advancedPreview: Traffic }).__advancedPreview);
}
async function open(page: Page, scenario: string) {
  await page.goto(`/advanced-preview.html?advanced=1&scenario=${scenario}`);
  await expect(page.getByRole("heading", { name: "Speed and tuning" })).toBeVisible();
  await expect(page.getByLabel("Maximum linear speed", { exact: true })).toBeVisible();
}
async function assertNoRobotTraffic(page: Page) {
  const recorded = await traffic(page);
  expect(recorded.commands).toHaveLength(0);
  expect(recorded.teleop).toHaveLength(0);
  expect(await page.locator("body").evaluate(node => node.scrollWidth <= innerWidth + 1)).toBe(true);
}

test("editing, Enter, blur, slider keyboard/release and timers are draft-only; explicit Save sends one batch", async ({ page }) => {
  const external: string[] = [];
  page.on("request", request => { if (request.method() === "POST" || /\/api\/robots\//.test(request.url())) external.push(request.url()); });
  await open(page, "pending");
  const field = page.getByLabel("Maximum linear speed", { exact: true });
  await field.fill("0.26");
  await field.press("Enter");
  await field.press("Tab");
  const slider = page.getByRole("slider", { name: "Maximum linear speed slider", exact: true });
  await slider.focus(); await page.keyboard.press("ArrowLeft");
  await slider.dispatchEvent("pointerup");
  await slider.dispatchEvent("pointercancel");
  await page.waitForTimeout(550); // Prove the removed 400 ms autosave cannot fire.
  expect((await traffic(page)).tuningSaves).toHaveLength(0);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect.poll(async () => (await traffic(page)).tuningSaves.length).toBe(1);
  await expect(page.getByText("0 unsaved changes", { exact: true })).toBeVisible();
  expect((await traffic(page)).tuningApplies).toHaveLength(0);
  expect(external).toEqual([]);
  await assertNoRobotTraffic(page);
});

for (const scenario of ["scoped", "scoped-motors-on", "docker"]) {
  test(`${scenario}: review binds scope and seed impact; motors/odom/localization do not gate synthetic command-idle apply`, async ({ page }) => {
    await open(page, scenario);
    await page.getByRole("button", { name: /^Apply changes/ }).click();
    const dialog = page.getByRole("dialog", { name: "Review saved changes" });
    await expect(dialog).toContainText("Reviewed revision 2");
    await expect(dialog).toContainText("not measured standstill");
    await expect(dialog).toContainText("Motors may remain on");
    await expect(dialog).toContainText(scenario === "docker" ? "Whole Nav2 stack redeploy" : "Hot parameter update (no redeploy)");
    if (scenario === "docker") await expect(dialog).toContainText("core bridge, bringup, SBC and dashboard stay up");
    await expect(dialog).toContainText(scenario === "docker" ? "fresh operator-supplied seed" : "existing valid localization is preserved");
    expect((await traffic(page)).tuningApplies).toHaveLength(0);
    await dialog.getByRole("button", { name: "Apply the reviewed revision" }).click();
    await expect(dialog).toContainText("SIMULATION: parameters verified");
    expect((await traffic(page)).tuningApplies).toHaveLength(1);
    await expect(dialog.getByRole("button", { name: "Apply the reviewed revision" })).toBeDisabled();
    await assertNoRobotTraffic(page);
  });
}

test("unknown Save preserves draft/identity, blocks replay and resolves by status query only", async ({ page }) => {
  await open(page, "unknown-save");
  const input = page.getByLabel("Maximum linear speed", { exact: true });
  await input.fill("0.25");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  const query = page.getByRole("button", { name: "Query saved request outcome" });
  await expect(query).toBeVisible();
  await expect(input).toHaveValue("0.25");
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  await query.click();
  await expect(page.getByText("0 unsaved changes", { exact: true })).toBeVisible();
  expect((await traffic(page)).tuningSaves).toHaveLength(1);
  await assertNoRobotTraffic(page);
});

test("refused Save keeps drafts and never activates or retries", async ({ page }) => {
  await open(page, "refused-save");
  const input = page.getByLabel("Maximum linear speed", { exact: true });
  await input.fill("0.25");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("SIMULATION: store refused the batch; drafts retained.", { exact: true }).first()).toBeVisible();
  await expect(input).toHaveValue("0.25");
  expect((await traffic(page)).tuningSaves).toHaveLength(1);
  expect((await traffic(page)).tuningApplies).toHaveLength(0);
  await assertNoRobotTraffic(page);
});

test("unknown Apply retains its hold and disables repeat confirmation or fallback", async ({ page }) => {
  await open(page, "unknown-apply");
  await page.getByRole("button", { name: /^Apply changes/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Apply the reviewed revision" }).click();
  await expect(dialog.getByText(/Verified completion.*not confirmed/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Apply the reviewed revision" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Check progress" }).click();
  expect((await traffic(page)).tuningApplies).toHaveLength(1);
  await assertNoRobotTraffic(page);
});

test("pre-mutation Apply refusal is reported without claiming verification or automatic retry", async ({ page }) => {
  await open(page, "refused-apply");
  await page.getByRole("button", { name: /^Apply changes/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Apply the reviewed revision" }).click();
  await expect(dialog).toContainText("physical release evidence changed; no mutation and no orphan hold");
  expect((await traffic(page)).tuningApplies).toHaveLength(1);
  await assertNoRobotTraffic(page);
});
