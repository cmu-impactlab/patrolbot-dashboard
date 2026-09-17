import { test, expect } from "@playwright/test";
import { dashboardFixture } from "./fixtures";

async function menu(page: import("@playwright/test").Page) {
  const trigger = page.getByRole("button", { name: "Menu", exact: true });
  if (await trigger.isVisible() && await trigger.getAttribute("aria-expanded") === "false") await trigger.click();
}
for (const role of ["observer", "operator", "administrator"]) {
  test(`all presets and themes reflow for ${role}`, async ({ page }) => {
    const fixture = await dashboardFixture(page, role);
    await page.goto("/");
    await expect(page.locator('[data-tour="widget-liveMap"]')).toBeVisible();
    for (const width of [320, 390, 480, 767, 768, 784, 1024, 1199, 1200, 1280, 1440]) {
      await page.setViewportSize({ width, height: width < 768 ? 740 : 800 });
      await menu(page);
      for (const preset of ["Operator", "Research", "Diagnostics"]) {
        await page.locator('[data-tour="layouts"]').click();
        await page.getByRole("menuitem", { name: preset, exact: true }).click();
        for (let theme = 0; theme < 2; theme++) {
          await page.getByTitle("Toggle light/dark theme").click();
          await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
          const overflow = await page.locator(".widget").evaluateAll(elements => elements.some(el => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1 || el.getBoundingClientRect().left < -1));
          expect(overflow).toBe(false);
        }
      }
    }
    expect(fixture.commands).toHaveLength(0);
  });
}

test("save failure keeps changes and retry persists phone edits without desktop drift", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await menu(page);
  await page.getByRole("button", { name: "Edit dashboard", exact: true }).click();
  fixture.setFailSave(true);
  const widget = page.locator('[data-tour="widget-robotStatus"]');
  await widget.getByTitle("Widget menu").click();
  await page.getByRole("menuitem", { name: "Taller", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("unsaved");
  fixture.setFailSave(false);
  await page.getByRole("button", { name: "Retry saving" }).click();
  await expect.poll(() => fixture.saves.length).toBe(1);
  expect(fixture.saves[0].layouts.lg.find(item => item.i === "robotStatus")?.h).toBe(9);
  expect(fixture.saves[0].layouts.xs.find(item => item.i === "robotStatus")?.h).toBe(10);
  await page.reload();
  await expect(widget).toBeVisible();
  expect(fixture.saves).toHaveLength(1);
});

test("touch preview confirms once; cancellation and multi-touch do not send", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/");
  await page.getByRole("button", { name: "Set Robot Location", exact: true }).click();
  const canvas = page.getByLabel("Live robot map");
  await expect(page.getByRole("dialog")).toBeVisible();
  const pointer = async (type: string, pointerId = 1) => {
    if (type === "pointerdown") await canvas.evaluate(el => { el.setPointerCapture = () => {}; });
    await canvas.dispatchEvent(type, { pointerId, pointerType: "touch", clientX: 140, clientY: 260, bubbles: true });
  };
  // Synthetic PointerEvents lack an active browser pointer for capture. Disable
  // capture only here; separate tap tests exercise real browser capture.
  await canvas.evaluate(el => { el.setPointerCapture = () => {}; });
  await pointer("pointerdown"); await pointer("pointercancel"); await pointer("pointerup");
  expect(fixture.commands).toHaveLength(0);
  await pointer("pointerdown"); await pointer("pointerdown", 2); await pointer("pointerup", 2); await pointer("pointerup");
  expect(fixture.commands).toHaveLength(0);
  await pointer("pointerdown"); await pointer("pointerup");
  await expect(page.getByLabel("Confirm map selection")).toBeVisible();
  expect(fixture.commands).toHaveLength(0);
  await page.getByRole("button", { name: "Set location", exact: true }).click();
  await expect.poll(() => fixture.commands.length).toBe(1);
  expect(fixture.commands[0].command).toBe("set_initial_pose");
  await page.getByTitle("Exit full screen").click();
  await page.getByRole("button", { name: "Send Robot Here", exact: true }).click();
  await pointer("pointerdown"); await pointer("pointerup");
  await page.getByRole("button", { name: "Send destination", exact: true }).click();
  await expect.poll(() => fixture.commands.length).toBe(2);
  expect(fixture.commands[1].command).toBe("navigate_to_pose");
});

test("real touch tap previews, mouse release sends, and fullscreen Stop stays reachable", async ({ page }, info) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Set Robot Location", exact: true }).click();
  const canvas = page.getByLabel("Live robot map");
  if (info.project.use.hasTouch) {
    await canvas.tap({ position: { x: 80, y: 180 } });
    await expect(page.getByLabel("Confirm map selection")).toBeVisible();
    expect(fixture.commands).toHaveLength(0);
    await page.getByRole("button", { name: "Set location", exact: true }).click();
  } else {
    await canvas.click({ position: { x: 80, y: 180 } });
  }
  await expect.poll(() => fixture.commands.length).toBe(1);
  const live = page.locator('[data-tour="widget-liveMap"]');
  if (!(await page.getByRole("dialog").count())) await live.getByTitle("Full screen",{exact:true}).click();
  await live.getByTitle("Map layers").click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("button",{name:"Stop",exact:true})).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("temporary map leaves widget selection unchanged and disconnect discards preview", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  fixture.setCurrent({ widgets: ["navControls"], layouts: { lg: [{ i: "navControls", x: 0, y: 0, w: 4, h: 10 }] } });
  await page.goto("/");
  await page.getByRole("button", { name: "Set Robot Location", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  fixture.send("state.connection", { state: "offline" });
  await expect(page.getByRole("button", { name: "Stop", exact: true }).last()).toBeDisabled();
  await page.getByTitle("Exit full screen").click();
  await expect(page.locator('[data-tour="widget-liveMap"]')).toHaveCount(0);
  expect(fixture.saves).toHaveLength(0);
  expect(fixture.commands).toHaveLength(0);
});

test("large replay supports playback, scrubbing, speed and rotation", async ({ page }) => {
  await dashboardFixture(page, "administrator", "Research", true);
  await page.goto("/?replay=1");
  await expect(page.getByLabel("Recorded robot map")).toBeVisible();
  await page.getByTitle("Play").click();
  await page.getByTitle("Pause").click();
  await page.locator('input[type="range"]').fill("100000");
  await page.locator("select").selectOption("2");
  await page.getByTitle("Jump to this moment").click();
  await expect(page.locator('input[type="range"]')).toHaveValue("120000");
  for (const size of [{width:390,height:844},{width:844,height:390},{width:1024,height:768}]) {
    await page.setViewportSize(size);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await expect(page.getByRole("button", { name: "Interact with map" })).toBeVisible();
  }
});

test("widget library, minimize, move, resize and named dashboards work with touch controls", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.setViewportSize({width:390,height:844});
  await page.goto("/"); await menu(page);
  await page.getByRole("button", {name:"Edit dashboard",exact:true}).click();
  await page.getByRole("button", {name:"Add widget",exact:true}).click();
  await page.getByRole("button", {name:"Add Bumpers",exact:true}).click();
  await page.getByRole("button", {name:"Close widget library"}).click();
  const widget = page.locator('[data-tour="widget-bumpers"]');
  for (const action of ["Move up", "Taller", "Minimize", "Expand"]) {
    await widget.getByTitle("Widget menu").click();
    await page.getByRole("menuitem", {name:action,exact:true}).click();
  }
  await menu(page);
  await page.locator('[data-tour="layouts"]').click();
  await page.getByRole("menuitem", {name:"Save current as…",exact:true}).click();
  await page.getByRole("textbox", {name:"Dashboard name"}).fill("My phone dashboard");
  await page.getByRole("button", {name:"Save",exact:true}).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator('[data-tour="layouts"]')).toContainText("My phone dashboard");
  await widget.getByTitle("Widget menu").click();
  await page.getByRole("menuitem", {name:"Remove from dashboard",exact:true}).click();
  await expect(widget).toHaveCount(0);
  await expect.poll(() => fixture.saves.at(-1)?.widgets.includes("bumpers")).toBe(false);
  await page.locator('[data-tour="layouts"]').click();
  await page.getByTitle('Delete "My phone dashboard"').click();
  await expect(page.getByRole("menuitem", {name:"My phone dashboard"})).toHaveCount(0);
});

test("recordings support channel selection, start/stop, export selection and administrator deletion", async ({ page }) => {
  await dashboardFixture(page,"administrator","Research");
  await page.goto("/");
  const recordings = page.locator('[data-tour="widget-recordings"]');
  await recordings.getByRole("textbox", {name:"Recording name"}).fill("Touch recording");
  await recordings.getByLabel("Laser scan", {exact:true}).check();
  await recordings.getByRole("button", {name:"Record",exact:true}).click();
  await expect(recordings).toContainText("Recording “Touch recording”");
  await recordings.getByRole("button", {name:"Stop",exact:true}).click();
  await expect(recordings.getByRole("button", {name:"Record",exact:true})).toBeVisible();
  await recordings.getByTitle("Download data (.zip of CSVs)").first().click();
  await page.getByRole("menuitem",{name:"Battery",exact:true}).click();
  // Native <a download> requests bypass Chromium route interception. The
  // real local-server integration test validates the download bytes.
  await expect(page.getByRole("menuitem",{name:"Download 2 CSVs (.zip)",exact:true})).toHaveAttribute("href", "/api/recordings/1/export.zip?channels=pose,event");
  await page.keyboard.press("Escape");
  await recordings.getByTitle("Delete recording").first().click();
  await expect(recordings.locator(".rec-row")).toHaveCount(1);
});

test("alerts, layers, help, login and replay errors remain reachable", async ({ page }) => {
  await dashboardFixture(page);
  await page.goto("/");
  const alerts = page.locator('[data-tour="widget-alerts"]');
  await alerts.getByRole("button", {name:/Mark.*read|Acknowledge|Mark.*seen/i}).first().click();
  await expect(alerts).toContainText(/Seen/);
  await page.locator('[data-tour="widget-liveMap"]').getByTitle("Map layers").click();
  await page.getByRole("menuitem",{name:"Laser points",exact:true}).click();
  await page.keyboard.press("Escape");
  await menu(page);
  await page.getByRole("button",{name:/guide|tour|help/i}).first().click();
  await expect(page.locator(".tour-card")).toBeVisible();
  await page.locator(".tour-close").click();
  await page.route("**/api/recordings/1",route => route.fulfill({status:500,json:{}}));
  await page.goto("/?replay=1");
  await expect(page.getByRole("link",{name:"Return to dashboard"})).toBeVisible();
  await page.route("**/auth/me",route => route.fulfill({status:401,json:{}}));
  await page.goto("/");
  await expect(page.getByRole("link",{name:"Sign in with your Andrew ID"})).toBeVisible();
});

test("review screenshots at phone, tablet and desktop widths", async ({ page }, info) => {
  await dashboardFixture(page,"operator","Diagnostics");
  await page.goto("/");
  await expect(page.locator('[data-tour="widget-systemHealth"]')).toBeVisible();
  for (const width of [320, 1024, 1440]) {
    await page.setViewportSize({width,height:900});
    await page.screenshot({path:info.outputPath(`dashboard-${width}.png`)});
  }
});

test("chart taps show readings and stale data is explained", async ({ page }, info) => {
  const fixture = await dashboardFixture(page,"operator","Research");
  await page.goto("/");
  const battery = page.locator('[data-tour="widget-battery"]');
  await expect(battery).toContainText("85%");
  const chart = battery.locator(".recharts-wrapper");
  if (info.project.use.hasTouch) await chart.tap({position:{x:100,y:40}});
  else await chart.hover({position:{x:100,y:40}});
  await expect(battery.locator(".recharts-tooltip-wrapper")).toBeVisible();
  fixture.send("server.snapshot", {connection:{state:"online"},robot_status:{status:"ready",detail:""},system_health:{overall:"healthy",subsystems:[]},map_version:1,events:[],battery:{voltage:25,percentage:85,charging:false},slice_ages_s:{battery:60}});
  await expect(battery).toContainText("too old to rely on");
});

test("native Chromium touch scroll and pinch never issue commands", async ({ page, context, browserName }) => {
  test.skip(browserName !== "chromium", "CDP touch injection is Chromium-only; pointer transition coverage runs in both engines.");
  const fixture = await dashboardFixture(page);
  await page.setViewportSize({width:390,height:844});
  await page.goto("/");
  const canvas = page.getByLabel("Live robot map");
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  const cdp = await context.newCDPSession(page);
  const x = box.x + 100, y = Math.min(600, box.y + 220);
  const scroll = () => page.locator(".dashboard-scroll").evaluate(el => el.scrollTop);
  const beforeScroll = await scroll();
  await cdp.send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x,y,id:1}]});
  for (let i=1;i<=5;i++) await cdp.send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[{x,y:y-i*20,id:1}]});
  await cdp.send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]});
  await expect.poll(scroll).toBeGreaterThan(beforeScroll);
  await page.getByRole("button",{name:"Interact with map",exact:true}).click();
  const nextBox = (await canvas.boundingBox())!;
  const centerX = nextBox.x + 150, centerY = Math.min(600, nextBox.y + 220);
  const beforePinch = await canvas.evaluate(el => (el as HTMLCanvasElement).toDataURL());
  await cdp.send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x:centerX-20,y:centerY,id:1},{x:centerX+20,y:centerY,id:2}]});
  await cdp.send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[{x:centerX-70,y:centerY,id:1},{x:centerX+70,y:centerY,id:2}]});
  await cdp.send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]});
  await expect.poll(() => canvas.evaluate(el => (el as HTMLCanvasElement).toDataURL())).not.toBe(beforePinch);
  expect(fixture.commands).toHaveLength(0);
  await page.getByRole("button",{name:"Done",exact:true}).click();
  await expect(canvas).toHaveCSS("touch-action","pan-y pinch-zoom");
});

test("charging actions retain touch confirmations and visible rejection reasons", async ({ page }) => {
  const fixture = await dashboardFixture(page);
  await page.goto("/");
  await expect(page.getByRole("button",{name:"Set Robot Location",exact:true})).toBeEnabled();
  const base = {session_generation:1,link_connected:true,telemetry_age:0.1,hardware_state_valid:true,charge_state:"not_charging",motors_enabled:false,estop_pressed:false,fault_flags:0,stall_value:0,bumpers_front:false,bumpers_rear:false,bumpers_valid:true};
  fixture.send("state.capabilities",{capabilities:["motor_enable","undock"]});
  fixture.send("telemetry.base_state",base);
  const nav = page.locator('[data-tour="widget-navControls"]');
  await nav.getByText("Advanced",{exact:true}).click();
  await nav.getByRole("button",{name:"Turn motors on",exact:true}).click();
  await expect(page.getByRole("dialog")).toContainText("Turn the motors on?");
  expect(fixture.commands).toHaveLength(0);
  await page.getByRole("button",{name:"Confirm motor enable",exact:true}).click();
  await expect.poll(() => fixture.commands.length).toBe(1);
  expect(fixture.commands[0].command).toBe("motor_enable");
  fixture.send("telemetry.base_state",{...base,charge_state:"charging",bumpers_rear:true});
  await expect(nav.getByRole("button",{name:"Undock",exact:true})).toBeDisabled();
  await expect(nav).toContainText("rear bumper is pressed");
  fixture.send("telemetry.base_state",{...base,charge_state:"charging"});
  await nav.getByRole("button",{name:"Undock",exact:true}).click();
  await expect.poll(() => fixture.commands.length).toBe(2);
  expect(fixture.commands[1].command).toBe("undock");
});


test("native touch handles reorder only the active arrangement", async ({ page, context, browserName }) => {
  test.skip(browserName !== "chromium", "Native multi-point injection requires CDP.");
  const fixture = await dashboardFixture(page);
  fixture.setCurrent({widgets:["robotStatus","battery"],layouts:{lg:[
    {i:"robotStatus",x:0,y:0,w:4,h:4,minH:3},{i:"battery",x:4,y:0,w:4,h:4,minH:3},
  ]}});
  await page.setViewportSize({width:390,height:844});
  await page.goto("/"); await menu(page);
  await page.getByRole("button",{name:"Edit dashboard",exact:true}).click();
  await page.getByRole("button",{name:"Menu",exact:true}).click();
  const handle = page.locator('[data-tour="widget-battery"] .drag-handle');
  await handle.scrollIntoViewIfNeeded();
  const box = (await handle.boundingBox())!;
  const cdp = await context.newCDPSession(page);
  const x = box.x + box.width/2, y = box.y + box.height/2;
  await cdp.send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x,y,id:1}]});
  for (let i=1;i<=10;i++) await cdp.send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[{x,y:y-i*19,id:1}]});
  await cdp.send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]});
  await expect.poll(() => fixture.saves.length).toBe(1);
  const saved = fixture.saves[0];
  expect(saved.layouts.xs.find(item => item.i === "battery")!.y).toBeLessThan(saved.layouts.xs.find(item => item.i === "robotStatus")!.y);
  expect(saved.layouts.lg.map(item => [item.i,item.x,item.y,item.h])).toEqual([["robotStatus",0,0,4],["battery",4,0,4]]);
});


test("exact container boundaries display and edit the same arrangement", async ({page}) => {
  const fixture = await dashboardFixture(page);
  await page.setViewportSize({width:1440,height:900});
  await page.goto("/");
  await page.getByRole("button",{name:"Edit dashboard",exact:true}).click();
  const widget = page.locator('[data-tour="widget-robotStatus"]');
  for (const [width, key, maximumRatio] of [[480,"sm",1],[768,"md",0.5],[1200,"lg",0.3]] as const) {
    await page.locator(".dashboard-scroll > div").evaluate((el,width) => { (el as HTMLElement).style.width = `${width}px`; },width);
    await expect.poll(async () => (await widget.boundingBox())!.width).toBeLessThan(width * maximumRatio);
    if (key === "sm") await expect.poll(async () => (await widget.boundingBox())!.width).toBeGreaterThan(width * 0.9);
    await widget.getByTitle("Widget menu").click();
    await page.getByRole("menuitem",{name:"Taller",exact:true}).click();
    await expect.poll(() => fixture.saves.at(-1)?.layouts[key].find(item => item.i === "robotStatus")?.h).toBe(10);
  }
});
