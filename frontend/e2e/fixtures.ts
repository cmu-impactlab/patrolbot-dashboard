import { type Page, type WebSocketRoute } from "@playwright/test";
import presets from "./presets.json" with { type: "json" };
import type { LayoutDoc } from "../src/stores/layoutStore";

export const READY_BASE_STATE = {
  session_generation: 1, link_connected: true, telemetry_age: 0.1, hardware_state_valid: true,
  charge_state: "not_charging", motors_enabled: true, estop_pressed: false,
  fault_flags: 0, stall_value: 0, bumpers_front: false, bumpers_rear: false, bumpers_valid: true,
  odom_epoch_valid: true, localization_recovery_required: false, localization_seed_stamp_ns: 1,
};

export async function dashboardFixture(page: Page, role = "operator", preset = "Operator", largeMap = false) {
  const commands: { command: string; command_id: string; goal: unknown }[] = [];
  const saves: LayoutDoc[] = [];
  let current: LayoutDoc = structuredClone(presets[preset as keyof typeof presets]);
  let failSave = false;
  let socket: WebSocketRoute;
  let sequence = 0;
  let baseState: unknown = { ...READY_BASE_STATE };
  let baseTimer: ReturnType<typeof setInterval> | undefined;
  page.on("close", () => clearInterval(baseTimer));
  let rows = [{ id: 1, robot_id: "mock", name: "Long recording name ".repeat(12), started_at: "2026-09-16T08:00:00Z", ended_at: "2026-09-16T08:10:00Z", status: "done", sample_count: 6000, channels: ["pose", "battery", "event"] }];
  const send = (type: string, data: unknown) => {
    if (type === "telemetry.base_state") baseState = data;
    socket.send(JSON.stringify({ version: 1, type, robot_id: "mock", sequence: ++sequence, timestamp: new Date().toISOString(), data }));
  };
  await page.route("**/auth/me", route => route.fulfill({ json: { id: 1, username: "touch-user", display_name: "Touch User", role, auth_mode: "oidc" } }));
  await page.context().route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    if (url.pathname === "/api/layouts") return route.fulfill({ json: [
      ...Object.entries(presets).map(([name, layout]) => ({ name, layout, is_preset: true, updated_at: "" })),
      { name: "current", layout: current, is_preset: false, updated_at: "" },
    ] });
    if (url.pathname.startsWith("/api/layouts/")) {
      if (failSave) return route.fulfill({ status: 503, json: {} });
      if (route.request().method() === "PUT") { current = route.request().postDataJSON(); saves.push(current); }
      return route.fulfill({ json: {} });
    }
    if (url.pathname === "/api/map") {
      const width = largeMap ? 3192 : 120, height = largeMap ? 2205 : 120;
      return route.fulfill({ json: { map_version: 1, name: "Test floor", width, height, resolution: 0.05, origin: { x: -3, y: -3, yaw: 0 }, rle: [[0, width * height]] } });
    }
    if (url.pathname === "/api/help-guide/status") return route.fulfill({ json: { should_prompt: false, current_version: 1, seen_version: 1 } });
    if (url.pathname.endsWith("export.zip")) return route.fulfill({ contentType: "application/zip", headers: { "Content-Disposition": 'attachment; filename="recording.zip"' }, body: "fixture export" });
    if (url.pathname === "/api/recordings/start") { rows = [...rows, { ...rows[0], id: 2, status: "recording", name: route.request().postDataJSON().name }]; return route.fulfill({ json: rows[1] }); }
    if (url.pathname === "/api/recordings/stop") { rows = rows.map(row => ({...row, status: "done"})); return route.fulfill({json: rows[1]}); }
    if (url.pathname === "/api/recordings/1" && route.request().method() === "DELETE") { rows = rows.filter(row => row.id !== 1); return route.fulfill({json: {}}); }
    if (url.pathname === "/api/recordings/1") return route.fulfill({ json: { ...rows[0], samples: [...Array.from({ length: 6000 }, (_, i) => ({ ts: new Date(Date.parse(rows[0].started_at) + i * 100).toISOString(), kind: "pose", data: { x: i / 100, y: Math.sin(i / 100), yaw: 0, linear_velocity: 0.1 } })), {ts:"2026-09-16T08:02:00Z",kind:"event",data:{severity:"warning",title:"Recorded alert",message:"Touch to seek to this event"}}] } });
    if (url.pathname === "/api/history/battery") return route.fulfill({json: Array.from({length:30},(_,i)=>({ts:`2026-09-16T08:${String(i).padStart(2,"0")}:00Z`,voltage:25-i/100,percentage:85,current:0,charging:0}))});
    if (url.pathname === "/api/recordings") return route.fulfill({ json: rows });
    return route.fulfill({ json: [] });
  });
  await page.routeWebSocket("**/ws/ui", ws => {
    socket = ws;
    send("server.snapshot", { connection: { state: "online" }, robot_status: { status: "ready", detail: "Ready" },
      system_health: { overall: "healthy", subsystems: [{ id: "computer", label: "Computer", level: "healthy", message: "Long network diagnostic ".repeat(15), updated_at: new Date().toISOString() }] },
      map_version: 1, pose: { x: 0, y: 0, yaw: 0, localized: true, linear_velocity: 0, angular_velocity: 0 },
      battery: { voltage: 25, percentage: 85, charging: false },
      resources: { cpu_percent: 20, memory_percent: 40, disk_percent: 30, wifi_signal_dbm: -52 },
      base_state: baseState,
      events: [{ id: 1, ts: new Date().toISOString(), severity: "warning", title: "Long alert", message: "Long message ".repeat(35) }], slice_ages_s: {pose: 0, base_state: 0, battery: 0, resources: 0},
    });
    clearInterval(baseTimer);
    baseTimer = setInterval(() => send("telemetry.base_state", baseState), 500);
    ws.onMessage(message => {
      const frame = JSON.parse(String(message));
      if (frame.type !== "command.request") return;
      commands.push(frame.data);
      send("command.ack", { command_id: frame.data.command_id, accepted: role !== "observer" });
      if (role !== "observer") setTimeout(() => send("command.result", { command_id: frame.data.command_id, outcome: "succeeded", detail: "Mock command completed" }), 20);
    });
  });
  return { commands, saves, send, stopBaseUpdates: () => clearInterval(baseTimer), setFailSave: (value: boolean) => { failSave = value; }, setCurrent: (doc: LayoutDoc) => { current = doc; } };
}
