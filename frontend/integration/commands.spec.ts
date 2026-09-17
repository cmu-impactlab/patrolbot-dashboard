import { test, expect } from "@playwright/test";

test("touch location, destination, Stop, Resume and Cancel reach the mock through server gates", async ({ page, request }) => {
  await expect.poll(async () => (await (await request.get("/api/health")).json()).robot_connected).toBe(true);
  await request.put("/api/help-guide/status");
  const commands: string[] = [];
  page.on("websocket", socket => socket.on("framesent", event => {
    const frame = JSON.parse(String(event.payload));
    if (frame.type === "command.request") commands.push(frame.data.command);
  }));
  await page.goto("/");
  await page.getByRole("button", {name: "Set Robot Location", exact: true}).click();
  const map = page.getByLabel("Live robot map");
  await map.tap({position: {x: 100, y: 250}});
  expect(commands).toHaveLength(0);
  await page.getByRole("button", {name: "Set location", exact: true}).click();
  await expect(page.getByRole("status")).toContainText("Robot location updated");
  await page.getByTitle("Exit full screen").click();
  await page.getByRole("button", {name: "Send Robot Here", exact: true}).click();
  await map.tap({position: {x: 220, y: 360}});
  await page.getByRole("button", {name: "Send destination", exact: true}).click();
  await expect(page.getByRole("status")).toContainText(/Command in progress|succeeded/);
  await page.getByRole("dialog").getByRole("button", {name: "Stop", exact: true}).click();
  await expect(page.getByRole("status")).toContainText("The robot has stopped");
  await page.getByTitle("Exit full screen").click();
  await page.getByRole("button", {name: "Resume", exact: true}).click();
  await page.getByRole("button", {name: "Cancel", exact: true}).click();
  expect(commands).toEqual(["set_initial_pose", "navigate_to_pose", "stop", "navigate_to_pose", "stop"]);
});

test("recording controls produce a real ZIP download and open an isolated replay", async ({ page, context, request }) => {
  await request.put("/api/help-guide/status");
  await page.goto("/");
  await page.getByRole("button",{name:"Menu",exact:true}).click();
  await page.locator('[data-tour="layouts"]').click();
  await page.getByRole("menuitem",{name:"Research",exact:true}).click();
  const widget = page.locator('[data-tour="widget-recordings"]');
  await widget.getByRole("textbox",{name:"Recording name"}).fill("Mobile integration recording");
  await widget.getByRole("button",{name:"Record",exact:true}).click();
  await expect(widget.getByRole("button",{name:"Stop",exact:true})).toBeVisible();
  await expect.poll(async () => {
    const recordings = await (await request.get("/api/recordings")).json();
    return recordings.find((row: {status:string}) => row.status === "recording")?.sample_count ?? 0;
  },{timeout:15000}).toBeGreaterThan(0);
  await widget.getByRole("button",{name:"Stop",exact:true}).click();
  await widget.getByTitle("Download data (.zip of CSVs)").click();
  const downloading = page.waitForEvent("download");
  await page.getByRole("menuitem",{name:"Download 3 CSVs (.zip)",exact:true}).click();
  const download = await downloading;
  expect(await download.failure()).toBeNull();
  const {readFile} = await import("node:fs/promises");
  const zip = await readFile((await download.path())!);
  expect(zip.subarray(0,2).toString()).toBe("PK");
  const opening = context.waitForEvent("page");
  await widget.getByTitle("Replay in a new tab").click();
  const replay = await opening;
  await expect(replay.getByLabel("Recorded robot map")).toBeVisible();
  await expect(replay.getByRole("link",{name:"Return to dashboard"})).toBeVisible();
  await replay.close();
});
