import { test, expect } from "@playwright/test";
import { dashboardFixture } from "./fixtures";

test("the guided tour opens from Help and from a direct link", async ({ page }) => {
  const state = await dashboardFixture(page);
  const tour = page.getByRole("dialog", { name: "Welcome to your dashboard" });
  await page.goto("/");
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(tour).toBeHidden();
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name: "Start the guided dashboard tour" }).click();
  await expect(tour).toBeVisible();
  await tour.getByRole("button", { name: "Skip tour" }).click();
  await expect(tour).toBeHidden();

  await page.goto("/?tour=1");
  await expect(tour).toBeVisible();
  await expect(page).toHaveURL("/");
  expect(state.commands).toEqual([]);
});
