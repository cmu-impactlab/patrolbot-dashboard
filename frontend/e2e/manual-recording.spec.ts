import { expect, test } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE } from "./fixtures";

// Intercepted API/WebSocket fixture only: no backend, robot or tuning changes.
test("recording and passive telemetry permit manual drive, but undock ownership ends it", async ({ page }) => {
  const f = await dashboardFixture(page, "operator", "Operator", false, ["web_teleop_v1"]);
  await page.goto("/?advanced=1");
  await f.wsReady;
  f.send("telemetry.base_state", { ...READY_BASE_STATE, dock_state_valid: true, dock_state: "CLEAR_CONFIRMED" });
  f.send("telemetry.robot_status", { status: "recording", detail: "Capturing telemetry" });
  const arm = page.getByRole("button", { name: "Arm manual drive" });
  await expect(arm).toBeEnabled();
  await arm.click();
  const panel = page.getByRole("region", { name: "Manual drive keyboard panel" });
  await panel.focus();
  await page.keyboard.down("w"); await page.keyboard.down("Space");
  await expect.poll(() => f.teleopFrames.some(frame => frame.deadman && frame.linear > 0)).toBe(true);
  f.send("telemetry.path", { points: [[0, 0], [1, 1]], goal: { x: 1, y: 1 } });
  const count = f.teleopFrames.length;
  await expect.poll(() => f.teleopFrames.length).toBeGreaterThan(count + 1);
  expect(f.teleopFrames.at(-1)?.deadman).toBe(true);
  f.send("telemetry.base_state", { ...READY_BASE_STATE, dock_state_valid: true, dock_state: "CLEAR_CONFIRMED", undock_active: true });
  await expect.poll(() => f.teleopFrames.at(-1)?.deadman).toBe(false);
  expect(f.commands.filter(command => command.command === "teleop_stop")).toHaveLength(1);
  await page.keyboard.up("Space"); await page.keyboard.up("w");
});
