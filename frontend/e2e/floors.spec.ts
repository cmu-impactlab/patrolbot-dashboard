import { test, expect } from "@playwright/test";
import { dashboardFixture } from "./fixtures";

test("floor browsing is view-only and active floor stays independent", async ({ page }) => {
  const state = await dashboardFixture(page);
  await page.goto("/");
  const floors = page.getByRole("group", { name: "Viewed floor" });
  await expect(floors).toBeVisible();
  const activeFloor = floors.getByRole("button", { name: "Floor 2 ● Active" });
  await expect(activeFloor).toHaveAttribute("aria-pressed", "true");
  const lockedFloor = floors.getByRole("button", { name: "Floor 3 — Map unavailable" });
  await expect(lockedFloor).toBeVisible();
  await expect(lockedFloor).toBeDisabled();
  await expect(lockedFloor).toHaveAttribute("aria-pressed", "false");
  await floors.getByRole("button", { name: "Floor 1" }).click();
  await expect(floors.getByRole("button", { name: "Floor 1" })).toHaveAttribute("aria-pressed", "true");
  await expect(activeFloor).toHaveAttribute("aria-pressed", "false");
  await page.reload();
  await expect(activeFloor).toHaveAttribute("aria-pressed", "true");
  expect(state.commands).toEqual([]);
});

test("map control icons are centered and the buttons stay inside the map", async ({ page }) => {
  const state = await dashboardFixture(page);
  state.setCurrent({ widgets: ["liveMap"], layouts: { lg: [{ i: "liveMap", x: 0, y: 0, w: 6, h: 6 }] } });
  await page.goto("/");
  const widget = page.locator('[data-tour="widget-liveMap"]');

  async function checkControls() {
    const map = widget.getByLabel("Live robot map");
    await expect(map).toBeVisible();
    const bounds = (await map.boundingBox())!;
    for (const label of ["Zoom in", "Zoom out", "Fit map", "Follow robot"]) {
      const button = widget.getByTitle(label, { exact: true });
      await expect(button).toBeVisible();
      const frame = (await button.boundingBox())!;
      const icon = (await button.locator("svg").boundingBox())!;
      expect(frame.width).toBe(44);
      expect(frame.height).toBe(44);
      expect(frame.x + frame.width).toBeCloseTo(bounds.x + bounds.width - 8);
      expect(frame.y).toBeGreaterThanOrEqual(bounds.y + 4);
      expect(frame.y + frame.height).toBeLessThanOrEqual(bounds.y + bounds.height - 4);
      expect(icon.x + icon.width / 2).toBeCloseTo(frame.x + frame.width / 2);
      expect(icon.y + icon.height / 2).toBeCloseTo(frame.y + frame.height / 2);
    }
  }

  await checkControls();
  await widget.getByTitle("Full screen", { exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await checkControls();
  expect(state.commands).toEqual([]);
});
