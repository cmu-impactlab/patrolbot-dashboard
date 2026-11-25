import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { AUTONOMOUS_SPEED_CAPABILITY, AUTONOMOUS_SPEED_RECEIPT, AUTONOMOUS_SPEED_REQUEST,
  type AutonomousSpeedRequestData, type AutonomousSpeedSelection } from "../src/types/protocol";
import { READY_BASE_STATE } from "./fixtures";

/** Opt-in: the real dashboard server is used; this peer has no ROS or motor connection. */
const ROBOT = "bounded-nav2-loopback";
const DEFAULT = { profile: "Fast", linear_m_s: .22, angular_rad_s: .70 } as const;
const SLOW = { profile: "Slow", linear_m_s: .12, angular_rad_s: .40 } as const;
const token = process.env.PATROLBOT_NAV_LOOPBACK_TOKEN;
const backend = process.env.PATROLBOT_DEV_BACKEND;
test.skip(!token || !backend, "requires the isolated loopback launcher");

type Frame = { type: string; data: Record<string, unknown> };
function openSocket(path: string, dispatch: (frame: Frame) => void) {
  if (!backend || new URL(backend).hostname !== "127.0.0.1") throw new Error("loopback only");
  const socket = new WebSocket(new URL(path, backend.replace(/^http:/, "ws:")));
  let seq = 0;
  const ready = new Promise<void>((resolve, reject) => {
    socket.addEventListener("open", () => resolve(), { once: true });
    socket.addEventListener("error", () => reject(new Error("loopback socket could not open")), { once: true });
  });
  socket.addEventListener("message", event => dispatch(JSON.parse(String(event.data))));
  return {
    ready, close: () => socket.close(),
    send(type: string, data: unknown) {
      socket.send(JSON.stringify({ version: 1, robot_id: ROBOT, sequence: seq++,
        timestamp: new Date().toISOString(), type, data }));
    },
  };
}

function simulatedNav2() {
  let selection: AutonomousSpeedSelection = DEFAULT;
  let goalId: string | null = null;
  let hold = false;
  const forwarded: Frame[] = [];
  const caps: (AutonomousSpeedRequestData & { operator_authorized?: boolean })[] = [];
  const transport = openSocket(`/ws/robot?token=${encodeURIComponent(token!)}`, frame => {
    if (frame.type === "server.hello_ack") return;
    forwarded.push(frame);
    if (frame.type === AUTONOMOUS_SPEED_REQUEST) {
      const request = frame.data as unknown as typeof caps[number];
      caps.push(request);
      const { linear_m_s: linear, angular_rad_s: angular } = request.selection;
      const allowed = request.operator_authorized === true && linear >= 0 && linear <= .26
        && angular >= 0 && angular <= .70;
      if (allowed) selection = request.selection;
      // A cap request cannot change the model's goal or hold state.
      transport.send(AUTONOMOUS_SPEED_RECEIPT, {
        command_id: request.command_id, outcome: allowed ? "applied" : "unknown",
        readback: allowed ? selection : null,
      });
    } else if (frame.type === "command.request" && frame.data.command === "navigate_to_pose") {
      goalId = String(frame.data.command_id);
      transport.send("command.ack", { command_id: goalId, accepted: true });
    }
  });
  return {
    ...transport, caps, forwarded,
    announce: () => transport.send("robot.hello", {
      protocol_version: 1, capabilities: [AUTONOMOUS_SPEED_CAPABILITY, "map_context_v1"],
      map_version: 0, software_version: "bounded-nav2-loopback-fixture",
    }),
    cap: () => selection,
    output: () => goalId && !hold ? Math.min(.60, .26, selection.linear_m_s) : 0,
    stopForFault: () => { hold = true; },
    finishGoal: () => {
      if (!goalId) throw new Error("No simulated goal");
      transport.send("command.result", { command_id: goalId, outcome: "canceled" });
      goalId = null;
    },
  };
}

test("real picker and broker preserve a bounded simulated Nav2 session", async ({ page, request }) => {
  const peer = simulatedNav2();
  const uiFrames: Frame[] = [];
  let commandClient: ReturnType<typeof openSocket> | undefined;
  let replacement: ReturnType<typeof simulatedNav2> | undefined;
  try {
    await peer.ready;
    peer.announce();
    await page.goto("/?advanced=1");
    const panel = page.getByRole("region", { name: "Autonomous speed profiles" });
    const apply = panel.getByRole("button", { name: "Request autonomous caps" });
    await expect(panel.getByRole("radio", { name: "Really Fast" })).toBeDisabled();
    await panel.getByRole("radio", { name: "Slow", exact: true }).check();
    await apply.click();
    await expect(panel.getByRole("status")).toContainText("Slow profile ceiling");
    expect(peer.cap()).toEqual(SLOW);
    expect(peer.output()).toBe(0);
    expect(peer.forwarded.map(frame => frame.type)).toEqual([AUTONOMOUS_SPEED_REQUEST]);

    // Enter a real *broker* goal using the separate loopback command fixture.
    // This is synthetic map/pose input, not a command to any physical robot.
    const mapResponse = await request.get("/api/map");
    expect(mapResponse.ok()).toBe(true);
    const { map_id, map_revision, origin } = await mapResponse.json();
    const pose = { map_id, map_revision, x: origin.x + .5 * Math.cos(origin.yaw) - .5 * Math.sin(origin.yaw),
      y: origin.y + .5 * Math.sin(origin.yaw) + .5 * Math.cos(origin.yaw), yaw: origin.yaw };
    const syntheticBase = { ...READY_BASE_STATE, map_id, map_revision, dock_state: "CLEAR_CONFIRMED", dock_state_valid: true };
    peer.send("telemetry.base_state", syntheticBase);
    peer.send("telemetry.pose", { ...pose, localized: true, linear_velocity: 0, angular_velocity: 0 });
    commandClient = openSocket("/ws/ui", frame => uiFrames.push(frame));
    await commandClient.ready;
    await expect.poll(() => uiFrames.some(f => f.type === "server.snapshot" && Boolean(f.data.pose))).toBe(true);
    const commandId = randomUUID();
    commandClient.send("command.request", { command_id: commandId, command: "navigate_to_pose", goal: pose });
    await expect.poll(() => (uiFrames.find(f => f.type === "command.ack" && f.data.command_id === commandId)?.data))
      .toMatchObject({ accepted: true });
    expect(peer.output()).toBe(.12);

    await panel.getByRole("radio", { name: "Custom" }).check();
    const custom = panel.getByRole("spinbutton", { name: "Linear speed (m/s)" });
    await custom.fill("1.80");
    await expect(apply).toBeDisabled();
    await custom.fill("0.26");
    await apply.click();
    await expect(panel.getByRole("status")).toHaveText("Confirmed autonomous ceiling: unknown.");
    expect(peer.caps).toHaveLength(1); // Existing server intentionally refuses a busy goal.
    expect(peer.output()).toBe(.12);

    peer.stopForFault();
    peer.finishGoal();
    await expect.poll(() => uiFrames.some(f => f.type === "command.result" && f.data.command_id === commandId)).toBe(true);
    await apply.click();
    await expect(panel.getByRole("status")).toContainText("Custom profile ceiling — 0.26 m/s");
    expect(peer.cap().linear_m_s).toBe(.26);
    expect(peer.output()).toBe(0); // Selecting after cancellation did not restart the goal.

    const oldSession = peer.caps.at(-1)!.session_id;
    peer.close();
    await expect(apply).not.toBeVisible();
    await expect(panel.getByRole("status")).toHaveText("Confirmed autonomous ceiling: unknown.");
    replacement = simulatedNav2();
    await replacement.ready;
    replacement.announce();
    await expect(panel.getByRole("radio", { name: "Fast", exact: true })).toBeChecked();
    await expect(panel.getByRole("status")).toHaveText("Confirmed autonomous ceiling: unknown.");
    expect(replacement.cap()).toEqual(DEFAULT);
    expect(replacement.output()).toBe(0);
    expect(replacement.forwarded).toEqual([]);
    await apply.click();
    await expect(panel.getByRole("status")).toContainText("Fast profile ceiling");
    expect(replacement.caps[0].session_id).not.toBe(oldSession);
    expect(replacement.output()).toBe(0);
  } finally {
    commandClient?.close();
    replacement?.close();
    peer.close();
  }
});
