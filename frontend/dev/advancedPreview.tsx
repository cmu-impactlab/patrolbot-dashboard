/** Dedicated loopback-only Vite preview. Never loaded by the production entry.
 * All API/socket operations terminate in this module; there is NO passthrough. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { BUNDLED_ENTRIES } from "../src/lib/tuning";
import type { TuningValue } from "../src/lib/tuning";
import "../src/themes/tokens.css";
import "../src/themes/app.css";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

if (!import.meta.env.DEV || !["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) {
  throw new Error("The synthetic preview is development-only and loopback-only.");
}
const scenario = new URLSearchParams(location.search).get("scenario") ?? "pending";
const unactivated = scenario === "unactivated";
const session = "11111111-1111-4111-8111-111111111111";
const committed = Object.fromEntries(BUNDLED_ENTRIES.filter(e => e.default !== null).map(e => [e.id, e.default]));
const desired = { ...committed };
if (scenario === "pending") desired.max_linear_m_s = .27;
let revision = scenario === "pending" ? 2 : 1;
const observed: Record<string, TuningValue> = { ...committed,
  "nav2.controller.rotate_to_heading_angular_vel": .7,
  "nav2.velocity_smoother.max_velocity_theta": .7 };
const traffic = { tuningSaves: [] as unknown[], commands: [] as unknown[], teleop: [] as unknown[] };
Object.assign(window, { __advancedPreview: traffic });
const base = { session_generation: 1, link_connected: scenario !== "offline", telemetry_age: .01,
  hardware_state_valid: true, odom_epoch: "local-simulation", odom_epoch_generation: 1,
  odom_epoch_valid: true, motors_enabled: false, estop_pressed: false, fault_flags: 0,
  stall_value: 0, bumpers_valid: true, bumpers_front: false, bumpers_rear: false,
  dock_state: "DOCKED_CONFIRMED", dock_state_valid: true, charge_state: "charging",
  lidar_state: "READY", lidar_scan_age: .01, raw_odom_age_s: .01,
  localization_recovery_required: true, localization_seed_stamp_ns: 0 };
const motion = { linear_velocity: 0, angular_velocity: 0, source: "raw_odom", sample_age_s: .01,
  receipt_age_s: .01, session_generation: 1, odom_epoch: "local-simulation", odom_epoch_generation: 1 };

function snapshot() {
  return { session_id: session, desired_revision: revision, active_revision: unactivated ? null : 1,
    committed_active_revision: unactivated ? null : 1, committed_active: unactivated ? null : committed,
    last_known_good_revision: unactivated ? null : 1,
    active_known: !unactivated, writable: scenario !== "offline", activation_supported: false,
    write_disabled_reason: scenario === "offline" ? "The SBC link is disconnected." : null,
    desired, active: unactivated ? {} : observed,
    pending_keys: unactivated ? Object.keys(desired) : Object.keys(desired).filter(id => desired[id] !== committed[id]),
    effective_limits: { max_linear_m_s: .3, max_angular_rad_s: .7 },
    safe_apply: { allowed: false, reason: "Local preview: runtime apply is not commissioned." },
    readback: { source: "ros_parameters", age_s: scenario === "stale" ? 2.8 : .15,
      hardware_session_generation: 1, values: observed,
      limits: { max_linear_m_s: .3, max_angular_rad_s: .7 } },
    catalog: BUNDLED_ENTRIES, compatibility: "LOCAL SYNTHETIC PREVIEW", latest_job: null,
    digest: "a".repeat(64), desired_digest: "b".repeat(64) };
}
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(input instanceof Request ? input.url : String(input), location.origin);
  if (url.origin !== location.origin) throw new Error("Preview blocks all external API traffic.");
  if (url.pathname === "/auth/me") return Response.json({ id: 1, username: "local-preview",
    display_name: "Local simulation", role: "administrator", auth_mode: "local" });
  if (/^\/api\/robots\/[^/]+\/tuning$/.test(url.pathname)) {
    await new Promise(resolve => setTimeout(resolve, ["slow", "stale"].includes(scenario) ? 700 : 40));
    if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (init?.method === "POST") {
      const request = JSON.parse(String(init.body));
      if (request.operation !== "save") return Response.json({ detail: "Preview never activates nodes." }, { status: 403 });
      traffic.tuningSaves.push(request);
      if (scenario === "offline") return Response.json({ detail: "SBC disconnected (simulation)." }, { status: 409 });
      if (request.base_revision !== revision) return Response.json({ request_id: request.request_id,
        outcome: "conflict", detail: "Synthetic revision changed", snapshot: snapshot(), job: null });
      for (const change of request.changes) desired[change.id] = change.value;
      revision++;
      return Response.json({ request_id: request.request_id, outcome: "ok", detail: "Saved locally, not to a robot",
        snapshot: snapshot(), job: null });
    }
    return Response.json(snapshot());
  }
  throw new Error("Unexpected API path blocked by local preview: " + url.pathname);
}) as typeof window.fetch;

class PreviewSocket {
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private sequence = 0;
  private timer: ReturnType<typeof setInterval> | undefined;
  constructor(url: string) {
    const parsed = new URL(url);
    if (parsed.host !== location.host || parsed.pathname !== "/ws/ui") throw new Error("External WebSocket blocked.");
    setTimeout(() => {
      if (this.readyState === 3) return;
      this.readyState = 1;
      this.onopen?.();
      this.frame("server.snapshot", { connection: { state: "online" },
        robot_status: { status: "idle", detail: "Local simulation — no robot connection" },
        system_health: null, map_version: 1, events: [], base_state: base, motion,
        slice_ages_s: { base_state: 0, motion: 0 }, capabilities: ["web_teleop_v1", "robot_tuning_read_v1", "robot_tuning_write_v1"] });
      this.timer = setInterval(() => {
        this.frame("telemetry.base_state", base);
        if (scenario !== "offline") this.frame("telemetry.motion", motion);
      }, 100);
    }, 10);
  }
  frame(type: string, data: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ version: 1, type,
      robot_id: "local-preview", sequence: ++this.sequence, timestamp: new Date().toISOString(), data }) }));
  }
  send(raw: string) {
    const frame = JSON.parse(raw);
    if (frame.type === "teleop.velocity") traffic.teleop.push(frame);
    else if (frame.type === "command.request") {
      traffic.commands.push(frame);
      this.frame("command.ack", { command_id: frame.data.command_id, accepted: false,
        reason: "Local preview never executes robot commands." });
    }
  }
  close() { clearInterval(this.timer); this.readyState = 3; this.onclose?.(); }
}
window.WebSocket = PreviewSocket as unknown as typeof WebSocket;

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={new QueryClient()}>
    <div className="local-preview-shell">
      <div className="local-preview-banner"><strong>LOCAL SIMULATION · no robot connection</strong>
        <label>Scenario <select value={scenario} onChange={event => {
          const url = new URL(location.href); url.searchParams.set("scenario", event.target.value); location.href = url.href;
        }}><option value="unchanged">Unchanged targets</option><option value="pending">One pending target</option>
          <option value="unactivated">Never activated store</option><option value="slow">Slow readback</option><option value="stale">Expired readback</option><option value="offline">SBC disconnected</option></select></label>
      </div>
      <App />
    </div>
  </QueryClientProvider>);
