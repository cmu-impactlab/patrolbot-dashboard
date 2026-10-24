/** Dedicated loopback-only Vite preview. Never loaded by the production entry.
 * All API/socket operations terminate in this module; there is NO passthrough. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { BUNDLED_ENTRIES } from "../src/lib/tuning";
import type { TuningValue } from "../src/lib/tuning";
import runtimeContract from "../../contracts/robot-tuning-runtime.json";
import "../src/themes/tokens.css";
import "../src/themes/app.css";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

if (!import.meta.env.DEV || !["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) {
  throw new Error("The synthetic preview is development-only and loopback-only.");
}
const scenario = new URLSearchParams(location.search).get("scenario") ?? "pending";
const unactivated = scenario === "unactivated";
const applyScenario = ["scoped", "scoped-motors-on", "docker", "refused-apply", "unknown-apply"].includes(scenario);
const session = "11111111-1111-4111-8111-111111111111";
const committed = Object.fromEntries(BUNDLED_ENTRIES.filter(e => e.default !== null).map(e => [e.id, e.default]));
const initial = { ...committed };
const desired = { ...committed };
if (scenario === "pending" || applyScenario) desired.max_linear_m_s = .27;
let revision = scenario === "pending" || applyScenario ? 2 : 1;
let activeRevision = 1;
const observed: Record<string, TuningValue> = { ...committed,
  "nav2.controller.rotate_to_heading_angular_vel": .7,
  "nav2.velocity_smoother.max_velocity_theta": .7 };
const traffic = { tuningSaves: [] as unknown[], tuningApplies: [] as unknown[], commands: [] as unknown[], teleop: [] as unknown[] };
const records = new Map<string, Record<string, unknown>>();
let latestJob: Record<string, unknown> | null = null;
const digestFor = (number: number) => "sha256:" + number.toString(16).padStart(64, "0");
Object.assign(window, { __advancedPreview: traffic });
const base = { session_generation: 1, link_connected: scenario !== "offline", telemetry_age: .01,
  hardware_state_valid: true, odom_epoch: "local-simulation", odom_epoch_generation: 1,
  odom_epoch_valid: true, motors_enabled: scenario === "scoped-motors-on", estop_pressed: false, fault_flags: 0,
  stall_value: 0, bumpers_valid: true, bumpers_front: false, bumpers_rear: false,
  dock_state: applyScenario ? "CLEAR_CONFIRMED" : "DOCKED_CONFIRMED", dock_state_valid: true, charge_state: applyScenario ? 0 : 2,
  lidar_state: "READY", lidar_scan_age: .01, raw_odom_age_s: applyScenario ? null : .01,
  localization_recovery_required: true, localization_seed_stamp_ns: 0 };
const motion = { linear_velocity: 0, angular_velocity: 0, source: "raw_odom", sample_age_s: .01,
  receipt_age_s: .01, session_generation: 1, odom_epoch: "local-simulation", odom_epoch_generation: 1 };

function activationPlan(operation: "apply" | "restore" = "apply") {
  const docker = scenario === "docker";
  const targets = operation === "restore" ? initial : desired;
  return { version: 1, operation, desired_revision: revision, revision: operation === "restore" ? 1 : revision,
    digest: digestFor(operation === "restore" ? 1 : revision), base_revision: activeRevision, base_digest: digestFor(activeRevision),
    session_id: session, hardware_generation: digestFor(1), plan_hash: digestFor(100 + revision + activeRevision * 10 + (docker ? 10000 : 0) + (operation === "restore" ? 1000 : 0)),
    strategy: "hot_or_nav2_redeploy", image_rebuild: false,
    execution_kind: docker ? "nav2_redeploy" : "scoped_parameters",
    nodes: docker ? runtimeContract.nav2_redeploy.nodes
      : ["teleop_velocity_smoother", "controller_server", "velocity_smoother", "patrolbot_joy_teleop"],
    services: docker ? ["navigation"] : [], seed_required: docker,
    disruption: docker ? "SIMULATION: the complete navigation container restarts. The core bridge, bringup, SBC and dashboard stay up. No image rebuild or host reset."
      : "SIMULATION: hot-update the listed consumers without redeployment. Changed core/web policies refresh while input is blocked. No AMCL or map reload.",
    executable: true, reasons: [], changes: Object.keys(targets).filter(id => targets[id] !== committed[id])
      .map(id => ({ id, before: committed[id], after: targets[id] })) };
}
function snapshot() {
  const limits = { max_linear_m_s: Number(observed.max_linear_m_s), max_angular_rad_s: Number(observed.max_angular_rad_s) };
  return { session_id: session, desired_revision: revision, active_revision: unactivated ? null : activeRevision,
    committed_active_revision: unactivated ? null : activeRevision, committed_active: unactivated ? null : committed,
    last_known_good_revision: unactivated ? null : 1,
    active_known: !unactivated, writable: scenario !== "offline", activation_supported: applyScenario,
    activation_plan: applyScenario ? activationPlan() : null, restore_plan: applyScenario ? activationPlan("restore") : null,
    write_disabled_reason: scenario === "offline" ? "The SBC link is disconnected." : null,
    desired, active: unactivated ? {} : observed,
    pending_keys: unactivated ? Object.keys(desired) : Object.keys(desired).filter(id => desired[id] !== committed[id]),
    effective_limits: limits,
    safe_apply: { allowed: applyScenario && latestJob?.state !== "unknown", reason: applyScenario
      ? "Synthetic command-idle evidence (no raw odometry/localization prerequisite)." : "Local preview: runtime apply is not commissioned." },
    readback: { source: "ros_parameters", age_s: scenario === "stale" ? 2.8 : .15,
      hardware_session_generation: 1, values: observed,
      limits },
    catalog: BUNDLED_ENTRIES, compatibility: "LOCAL SYNTHETIC PREVIEW", latest_job: latestJob,
    digest: digestFor(activeRevision), desired_digest: digestFor(revision) };
}
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(input instanceof Request ? input.url : String(input), location.origin);
  if (url.origin !== location.origin) throw new Error("Preview blocks all external API traffic.");
  if (url.pathname === "/auth/me") return Response.json({ id: 1, username: "local-preview",
    display_name: "Local simulation", role: "administrator", auth_mode: "local" });
  if (/^\/api\/robots\/[^/]+\/tuning\/jobs\//.test(url.pathname)) {
    const record = records.get(decodeURIComponent(url.pathname.split("/").at(-1)!));
    return record ? Response.json(record) : Response.json({ detail: "No such synthetic request/job." }, { status: 404 });
  }
  if (/^\/api\/robots\/[^/]+\/tuning$/.test(url.pathname)) {
    await new Promise(resolve => setTimeout(resolve, ["slow", "stale"].includes(scenario) ? 700 : 40));
    if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (init?.method === "POST") {
      const request = JSON.parse(String(init.body));
      if (["apply", "restore"].includes(request.operation) && applyScenario) {
        traffic.tuningApplies.push(request);
        const plan = activationPlan(request.operation);
        if (request.expected_revision !== revision || request.review?.digest !== plan.digest || request.review?.plan_hash !== plan.plan_hash)
          return Response.json({ request_id: request.request_id, outcome: "conflict", detail: "Synthetic plan changed; review again.", snapshot: snapshot(), job: null });
        if (scenario === "refused-apply") return Response.json({ request_id: request.request_id, outcome: "refused",
          detail: "SIMULATION: physical release evidence changed; no mutation and no orphan hold.", snapshot: snapshot(), job: null });
        const unknown = scenario === "unknown-apply";
        const jobId = crypto.randomUUID();
        latestJob = { job_id: jobId, state: unknown ? "unknown" : "succeeded", revision: plan.revision,
          digest: plan.digest, plan_hash: plan.plan_hash, detail: unknown ? "SIMULATION: mutation outcome unknown; durable hold retained. No retry/fallback."
            : plan.seed_required ? "SIMULATION: parameters verified; navigation awaiting operator seed."
              : "SIMULATION: parameters verified; valid localization preserved. No input or goal resumes." };
        records.set(jobId, latestJob);
        records.set(request.request_id, { request_id: request.request_id, state: "done", outcome: unknown ? "unknown" : "ok", job_id: jobId, revision: plan.revision });
        if (!unknown) {
          const targets = request.operation === "restore" ? initial : desired;
          Object.assign(committed, targets);
          Object.assign(observed, targets);
          for (const entry of BUNDLED_ENTRIES) {
            if (entry.value_kind === "speed") observed[entry.id] = Math.min(Number(targets[entry.id]), Number(targets.max_linear_m_s));
            if (entry.value_kind === "angular_speed") observed[entry.id] = Math.min(Number(targets[entry.id]), Number(targets.max_angular_rad_s));
          }
          activeRevision = plan.revision;
        }
        return Response.json({ request_id: request.request_id, outcome: unknown ? "unknown" : "ok", detail: latestJob.detail, snapshot: snapshot(), job: latestJob });
      }
      if (request.operation !== "save") return Response.json({ detail: "Preview never contacts or activates robot nodes." }, { status: 403 });
      traffic.tuningSaves.push(request);
      if (scenario === "offline") return Response.json({ detail: "SBC disconnected (simulation)." }, { status: 409 });
      if (request.base_revision !== revision) return Response.json({ request_id: request.request_id,
        outcome: "conflict", detail: "Synthetic revision changed", snapshot: snapshot(), job: null });
      if (scenario === "refused-save") return Response.json({ request_id: request.request_id, outcome: "refused",
        detail: "SIMULATION: store refused the batch; drafts retained.", snapshot: snapshot(), job: null });
      for (const change of request.changes) desired[change.id] = change.value;
      revision++;
      records.set(request.request_id, { request_id: request.request_id, state: "done", outcome: "ok", revision });
      return Response.json({ request_id: request.request_id, outcome: scenario === "unknown-save" ? "unknown" : "ok",
        detail: scenario === "unknown-save" ? "SIMULATION: save response lost. Query the original request; never replay."
          : "Saved locally, not to a robot", snapshot: scenario === "unknown-save" ? null : snapshot(), job: null });
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
        system_health: null, map_version: 1, events: [], base_state: base, ...(applyScenario ? {} : { motion }),
        slice_ages_s: { base_state: 0, motion: 0 }, capabilities: ["web_teleop_v1", "robot_tuning_read_v1", "robot_tuning_write_v1"] });
      this.timer = setInterval(() => {
        this.frame("telemetry.base_state", base);
        if (scenario !== "offline" && !applyScenario) this.frame("telemetry.motion", motion);
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
          <option value="unactivated">Never activated store</option><option value="slow">Slow readback</option><option value="stale">Expired readback</option><option value="offline">SBC disconnected</option>
          <option value="scoped">Scoped apply · no odometry, motors off</option><option value="scoped-motors-on">Scoped apply · no odometry, motors on</option>
          <option value="docker">Whole Nav2 redeploy</option><option value="refused-save">Save refused</option><option value="unknown-save">Save outcome unknown</option>
          <option value="refused-apply">Apply refused before mutation</option><option value="unknown-apply">Apply unknown · hold retained</option></select></label>
      </div>
      <App />
    </div>
  </QueryClientProvider>);
