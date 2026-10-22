import { expect, test } from "@playwright/test";
import { dashboardFixture, READY_BASE_STATE } from "./fixtures";
const TELEOP_BASE = { ...READY_BASE_STATE, dock_state_valid: true, dock_state: "CLEAR_CONFIRMED" };

test("profile Advanced opens a fresh authenticated advanced tab without starting input", async ({ page, context }) => {
  await dashboardFixture(page);
  await page.goto("/");
  // isVisible() does not wait: an unmounted header is not the mobile layout.
  await expect(page.getByRole("banner")).toBeVisible();
  const profile = page.getByRole("button", { name: "User profile" });
  if (!(await profile.isVisible())) await page.getByRole("button", { name: "Menu", exact: true }).click();
  await profile.click();
  const link = page.getByRole("menuitem", { name: "Advanced", exact: true });
  await expect(link).toHaveAttribute("href", "/?advanced=1");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  // The new page's auth/API responses are isolated; no robot or server commands.
  await context.route("**/auth/me", route => route.fulfill({ json: { id: 1, username: "test", display_name: "Test", role: "operator", auth_mode: "local" } }));
  await context.routeWebSocket("**/ws/ui", ws => ws.send(JSON.stringify({ version: 1, type: "server.snapshot", robot_id: "mock", sequence: 1, timestamp: new Date().toISOString(), data: {
    connection: { state: "offline" }, robot_status: { status: "offline", detail: "Isolated test" }, system_health: { overall: "offline", subsystems: [] }, map_version: 0, events: [], capabilities: ["web_teleop_v1"],
  } })));
  const newPage = context.waitForEvent("page");
  await link.click();
  const advanced = await newPage;
  await expect(advanced).toHaveURL(/\/\?advanced=1$/);
  await expect(advanced.getByRole("heading", { name: "Advanced controls" })).toBeVisible();
  await expect(advanced.getByRole("button", { name: "Arm manual drive" })).toBeDisabled();
  await expect(advanced.getByText("Not armed", { exact: true })).toBeVisible();
  await advanced.close();
});

test("advanced tab samples pointer and focused keyboard and ends with exact zero frames", async ({ page }) => {
  // Each startup/reconnect snapshot must advertise the capability. A one-shot
  // frame can otherwise reach React StrictMode's discarded first socket.
  const fixture = await dashboardFixture(page, "operator", "Operator", false, ["web_teleop_v1"]);
  await page.goto("/?advanced=1");
  await expect(page.getByRole("heading", { name: "Advanced controls" })).toBeVisible();
  await fixture.wsReady;
  expect(fixture.commands).toHaveLength(0);
  fixture.send("state.capabilities", { capabilities: ["web_teleop_v1"] });
  fixture.send("telemetry.pose", { x: 0, y: 0, yaw: 0, localized: false, linear_velocity: 0, angular_velocity: 0 }); // No AMCL prerequisite.
  fixture.send("telemetry.base_state", TELEOP_BASE);
  const arm = page.getByRole("button", { name: "Arm manual drive" });
  await expect(arm).toBeEnabled(); await arm.click();
  const stick = page.getByRole("group", { name: "Hold joystick to drive" });
  const box = (await stick.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 10); await page.mouse.down();
  await expect.poll(() => fixture.teleopFrames.some(frame => frame.deadman && frame.linear > 0)).toBe(true);
  await page.mouse.up();
  await expect.poll(() => fixture.commands.filter(command => command.command === "teleop_stop").length).toBe(1);
  expect(fixture.teleopFrames.at(-1)).toMatchObject({ linear: 0, angular: 0, deadman: false });
  await expect(arm).toBeEnabled(); await arm.click();
  const panel = page.getByRole("region", { name: "Manual drive keyboard panel" });
  await panel.focus();
  await page.keyboard.down("ArrowLeft"); await page.keyboard.down("Space");
  await expect.poll(() => fixture.teleopFrames.some(frame => frame.deadman && frame.angular > 0)).toBe(true);
  await page.keyboard.up("Space"); await page.keyboard.up("ArrowLeft");
  await expect.poll(() => fixture.commands.filter(command => command.command === "teleop_stop").length).toBe(2);
  expect(fixture.teleopFrames.at(-1)).toMatchObject({ linear: 0, angular: 0, deadman: false });
  const starts = fixture.commands.filter(command => command.command === "teleop_start").map(command => command.command_id);
  expect(new Set(starts).size).toBe(2);
  for (const id of starts) {
    const samples = fixture.teleopFrames.filter(frame => frame.command_id === id);
    expect(samples.map(frame => frame.seq)).toEqual(samples.map((_, index) => index));
    expect(samples.every(frame => Math.abs(frame.linear) <= .3 && Math.abs(frame.angular) <= .7)).toBe(true);
  }
});

test("a second advanced tab has no inherited intent and simulated focus loss ends first-tab input", async ({ page, context }) => {
  const first = await dashboardFixture(page, "operator", "Operator", false, ["web_teleop_v1"]);
  await page.goto("/?advanced=1");
  await expect(page.getByRole("heading", { name: "Advanced controls" })).toBeVisible();
  await first.wsReady;
  first.send("state.capabilities", { capabilities: ["web_teleop_v1"] });
  first.send("telemetry.base_state", TELEOP_BASE);
  await page.getByRole("button", { name: "Arm manual drive" }).click();
  await page.getByRole("region", { name: "Manual drive keyboard panel" }).focus();
  await page.keyboard.down("w"); await page.keyboard.down("Space");
  await expect.poll(() => first.teleopFrames.some(frame => frame.deadman && frame.linear > 0)).toBe(true);
  const secondPage = await context.newPage();
  const second = await dashboardFixture(secondPage, "operator", "Operator", false, ["web_teleop_v1"]);
  await secondPage.goto("/?advanced=1"); await secondPage.bringToFront();
  await expect(secondPage.getByRole("heading", { name: "Advanced controls" })).toBeVisible();
  await second.wsReady;
  // Headless Chromium does not model OS tab activation consistently. Deliver
  // the browser focus-loss event explicitly; both pages still use real WS APIs.
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await expect.poll(() => first.teleopFrames.at(-1)?.deadman).toBe(false);
  expect(first.teleopFrames.at(-1)).toMatchObject({ linear: 0, angular: 0 });
  expect(second.commands).toHaveLength(0);
  await expect(secondPage.getByText("Not armed", { exact: true })).toBeVisible();
  second.send("state.capabilities", { capabilities: ["web_teleop_v1"] });
  second.send("telemetry.base_state", TELEOP_BASE);
  await secondPage.getByRole("button", { name: "Arm manual drive" }).click();
  await secondPage.getByRole("region", { name: "Manual drive keyboard panel" }).focus();
  await secondPage.keyboard.down("a"); await secondPage.keyboard.down("Space");
  await expect.poll(() => second.teleopFrames.some(frame => frame.deadman && frame.angular > 0)).toBe(true);
  expect(second.commands[0].command_id).not.toBe(first.commands[0].command_id);
  await secondPage.keyboard.up("Space"); await secondPage.keyboard.up("a");
  await expect.poll(() => second.teleopFrames.at(-1)?.deadman).toBe(false);
  await secondPage.close();
});
