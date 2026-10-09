import { test, expect } from "@playwright/test";
import { dashboardFixture } from "./fixtures";

test("floor browsing is view-only and active floor stays independent", async ({ page }) => {
  const state = await dashboardFixture(page);
  await page.goto("/");
  const floors = page.getByRole("group", { name: "Viewed floor" });
  await expect(floors).toBeVisible();
  const lockedFloor = floors.getByRole("button", { name: "Floor 3 — Map unavailable" });
  await expect(lockedFloor).toBeVisible();
  await expect(lockedFloor).toBeDisabled();
  await expect(lockedFloor).toHaveAttribute("aria-pressed", "false");
  await floors.getByRole("button", { name: "Floor 1" }).click();
  await expect(floors.getByRole("button", { name: "Floor 1" })).toHaveAttribute("aria-pressed", "true");
  await expect(floors.getByRole("button", { name: "Floor 2 ● Active" })).toHaveAttribute("aria-pressed", "false");
  expect(state.commands).toEqual([]);
});
