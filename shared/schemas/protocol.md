# PatrolBot Dashboard WebSocket Protocol — version 1

Every WebSocket frame is a JSON **envelope**:

```json
{
  "version": 1,
  "type": "telemetry.pose",
  "robot_id": "patrolbot-01",
  "sequence": 4711,
  "timestamp": "2026-07-17T18:00:00.000Z",
  "data": { }
}
```

- `version` — protocol version, currently `1`. Receivers must reject other versions.
- `type` — message type (below).
- `robot_id` — stable robot identifier.
- `sequence` — per-connection monotonically increasing integer.
- `timestamp` — ISO-8601 UTC with milliseconds.
- `data` — type-specific payload.

Authoritative payload definitions: `server/app/protocol/messages.py` (Pydantic).
Frontend mirror: `frontend/src/types/protocol.ts`.
Golden examples: `shared/schemas/fixtures/*.json` — validated by both pytest
and vitest; edit fixtures together with both definitions.

## Robot → Server  (`/ws/robot?token=…`)

| Type | Rate | Payload summary |
|------|------|-----------------|
| `robot.hello` | on connect (first frame) | `protocol_version`, `capabilities[]`, `map_version`, `software_version` |
| `telemetry.heartbeat` | 1 Hz | `uptime_s` |
| `telemetry.pose` | 10 Hz | `frame_id`, `x`, `y`, `yaw`, `linear_velocity`, `angular_velocity`, `covariance_trace?`, `planar_covariance?`, `covariance_limit?`, `localization_age_s?`, `localization_max_age_s?`, `localized` |
| `telemetry.lidar` | 5 Hz | `angle_min`, `angle_increment`, `ranges[]` (≤360, metres, `null` = no return) |
| `telemetry.path` | on change / 2 Hz | `frame_id`, `points[[x,y],…]`, `goal?{x,y,yaw}` |
| `telemetry.battery` | 1 Hz | `voltage`, `current?`, `percentage?`, `charging` |
| `telemetry.base_state` | 10 Hz | drive-base state plus the SBC dock observer (`dock_state`, validity, active phase, rear-clearance and commissioning fields); valid `CLEAR_CONFIRMED` is authoritative over a stale raw charge level |
| `telemetry.diagnostics` | 1 Hz | `items[{name, level, message, values?}]`, level ∈ OK/WARN/ERROR/STALE |
| `telemetry.resources` | 1 Hz | `cpu_percent`, `memory_percent`, `cpu_temp_c?`, `disk_percent`, `wifi_signal_dbm?` |
| `telemetry.map` | on server request + on change | `map_version`, `name`, `resolution`, `width`, `height`, `origin{x,y,yaw}`, `rle[[value,count],…]` |

Server replies to `robot.hello` with `server.hello_ack` (`data.want_map: bool`).
The robot sends `telemetry.map` when `want_map` is true or the map changes.

`telemetry.pose.planar_covariance` optionally carries unrounded `[xx, yy, yaw_yaw]`:
the position entries are m² and the heading entry is rad². A non-finite entry is
`null`; absent/null arrays mean no filter covariance is available. Zero or
negative entries are degenerate. The dashboard displays the largest map-axis
spread `sqrt(max(xx, yy))` in metres and heading spread `sqrt(yaw_yaw)` in degrees
(both 1σ), never a measured error radius, probability, or pose accuracy.
`covariance_limit` is the effective dashboard bridge threshold, applied
separately to each variance. The signed dashboard variance margins are
`100 * (1 - max(xx, yy) / covariance_limit)` and
`100 * (1 - yaw_yaw / covariance_limit)`; over-limit values stay negative.
Missing or invalid thresholds make the margins unavailable.

`localization_age_s` is time since the bridge last received an AMCL estimate;
`localization_max_age_s` is its current dashboard freshness threshold. Browsers
add elapsed monotonic receipt time, including a snapshot's existing slice age.
The display suppresses numeric estimates when the source is odometry, covariance
is invalid/degenerate, the refresh age is unknown/invalid/stale, or receipt age
exceeds the existing 15-second display policy. Dock Manager's separate 0.5-second
freshness policy and the authoritative `localized` verdict are unchanged.

## Server → Browser  (`/ws/ui`)

All robot telemetry types are re-broadcast unchanged, plus:

| Type | When | Payload summary |
|------|------|-----------------|
| `server.snapshot` | on browser connect | full current state (no map data — fetch `GET /api/map`) |
| `state.connection` | on change | `state` ∈ `online` / `stale` / `offline`, `last_seen` |
| `state.robot_status` | on change | `status` ∈ ready/navigating/recording/docked/charging/paused/needs_attention/offline, `detail` |
| `state.system_health` | on change | `overall`, `subsystems[{id, label, level, message, action?, updated_at}]` |
| `state.capabilities` | on robot connect | `capabilities[]` copied from `robot.hello` — the dashboard offers undock only to a robot that claims it |
| `event.append` | as they occur | `id`, `ts`, `severity` ∈ info/warning/critical, `title`, `message` |

Connection staleness (server-side, from heartbeat age): `<3 s` online,
`3–10 s` stale, `>10 s` (or socket closed) offline.

## Commands (Phase 3)

Browser → server → robot; responses flow back along the same path. Every
frame carries a `command_id` (UUID minted by the browser) for correlation.

| type | direction | data |
|---|---|---|
| `command.request` | browser→robot | `command_id`, `command` ∈ navigate_to_pose/set_initial_pose/stop/charge_release/motor_enable/motor_disable/undock/software_reset/set_lidar_power/teleop_start/teleop_stop, `enabled?` (strict boolean for LiDAR power), `goal?` `{x, y, yaw?}`, `takeover?` (claim the single-operator lease from the current holder) |
| `teleop.velocity` | browser→robot | `command_id`, `seq`, `linear`, `angular`, `deadman` — deadman teleop samples inside an open session; see below |
| `command.ack` | robot→browser | `command_id`, `accepted`, `reason?` |
| `command.progress` | robot→browser | `command_id`, `stage`, `detail?`, `distance_remaining?` |
| `command.result` | robot→browser | `command_id`, `outcome` ∈ succeeded/failed/rejected/canceled/timeout, `detail?` |

Server-side broker rules:

- A request is **rejected by the server** (synthesized `command.ack`
  `accepted=false`) when the robot is not online, the payload is invalid,
  `goal` is missing for a command that needs one, or the `command_id` was
  already seen (duplicate protection).
- Every request is written to the `command_audit` table before forwarding.
- If no `command.ack` arrives within 5 s, or no `command.result` within 120 s,
  the server synthesizes `command.result` `outcome=timeout` and closes out the
  command. Late robot replies for a closed command are dropped. Manual drive,
  reset and LiDAR-power uncertainty instead retain the command barrier until
  an actual robot terminal result or an explicitly authorized software reset.
- A new `navigate_to_pose` while one is active implicitly replaces it
  (Nav2 preemption semantics); `stop` cancels any active navigation.
- The one velocity path is the **dashboard deadman teleop** (below): bounded,
  leased, and published through the existing final mux/manual safety path.
  It joins after Nav2's collision monitor, not through it. Everything else is goal-based navigation by design — `undock`
  included: it is an action the robot executes, never browser-published
  velocity. The physical e-stop is the only emergency stop, and the F710
  joystick on the robot always outranks this channel.
- `navigate_to_pose`, `charge_release` and `undock` are
  additionally gated on live hardware telemetry
  (`server/app/commands/gates.py`) before they are forwarded: readings must be
  fresh and valid, faults and E-stop clear, and `undock` requires the robot to
  claim the capability in `robot.hello`. `undock` is a **single operator
  action**: the commissioned action atomically releases charging, powers the
  motors, backs clear, restores localization, turns away from the dock, and
  stops; the dashboard only sends `undock` and renders its progress/result. It
  also needs a valid commissioned dock observer and a clear rear bumper.
  `navigate_to_pose` needs a fresh pose the robot reports as localized, live
  motors, and a robot that is not on its charger — driving off the dock is
  undock's job.

  **There is no `dock` command.** The robot has no automatic dock-in path (its
  ROS graph offers `/patrolbot/undock` and `/patrolbot/hardware_undock` and
  nothing to drive back onto the charger, verified 2026-08-08), so it is
  driven onto its charger by hand. The command, its gate, its UI control and
  the mock's implementation of it were removed rather than left advertising a
  capability nothing implements.
  `charge_release` remains a robot-side and diagnostic command (zero-motion and
  motor-disabled); the dashboard UI does not send it. `motor_enable` is an
  explicit, confirmed dashboard motor-power request. The browser and server
  require connection, capability and operator authorization; the robot's typed
  service enforces physical enable interlocks. `motor_disable` remains available
  during motion or recovery and cancels outstanding movement.
- The bridge executes commands only when `WEB_BRIDGE_ENABLE_COMMANDS=1`;
  otherwise it acks `accepted=false` with an explanatory reason.

### Dashboard deadman teleop

The joystick and keyboard in the separate Advanced browser tab use a leased,
audited session, not a stream of anonymous commands. Opening a tab never
starts a session or enables motors:

- `command.request` `teleop_start` (UUID, audited, lease-checked, rate-limited
  like every command) opens a session once the robot acks it. `teleop_stop`
  closes it; it bypasses the lease and rate limit so a release is never
  blocked — any operator who may command can end manual drive. Both are
  forwarded to the robot, which acks/answers like any command.
- Admission requires `web_teleop_v1`, an online robot, fresh valid connected
  base state, fresh raw odometry (`raw_odom_age_s`) and LiDAR, a valid odometry
  origin (`odom_epoch_valid`), motors already on, no faults/E-stop, valid clear
  front/rear bumpers, confirmed
  `CLEAR_CONFIRMED` dock clearance, and no active undock or owning command.
  Each acquisition age plus server receipt age must be ≤0.5 s. Missing fields
  fail closed. No map, localization, saved pose or commissioned undock profile
  is required; software tests may supply explicit isolated fake evidence, never
  production readiness. SBC-down production remains blocked.
- `teleop.velocity` (`command_id` = the start UUID, monotonic `seq`,
  `linear`/`angular` in m/s and rad/s, `deadman`) flows at ~10–15 Hz while
  the deadman is held. The server forwards it only for an open session owned
  by the exact sending browser connection, bound to the exact robot socket and
  connection/base generation. Another tab, even of the same user and knowing
  the UUID, cannot inject input or start a physical operation. The server drops
  replayed or reordered `seq` values, and caps
  the stream at `PATROLBOT_TELEOP_FRAMES_PER_S` (default 30/s, separate from
  the discrete command rate limit).
- Velocity bounds are the robot's own smoother ceiling (±0.3 m/s /
  ±0.7 rad/s), pinned in `contracts/web-teleop.json` and compared byte for
  byte in CI — never a browser choice. Server bounds, non-finite values,
  unknown fields and malformed types are rejected, not silently clamped.
- Envelope timestamps must include a timezone and be no older than 300 ms or
  more than 50 ms in the future against aware UTC. Sequence is consumed only
  after identity, freshness and rate admission. No nonzero send queue is kept;
  robot sends are bounded to 100 ms (configurable, never above 300 ms). Reconnect
  never replays input. `deadman=false` must contain zero velocities; a valid
  owner release bypasses stream rate, ack and closing barriers, not identity,
  lease, timestamp or sequence checks.
- The server ends the session and sends an explicit zero (`deadman=false`)
  plus an audited `teleop_stop` on lease takeover, browser disconnect, robot
  disconnect/stale state, capability/hardware loss, or producer stream silence
  of 300 ms. `PATROLBOT_TELEOP_SESSION_TTL_S` defaults to 20 s as an outer
  permission cap without frames; it never extends the stricter 300 ms stream
  timeout (a smaller setting shortens it). Pending starts are zero-only on the
  robot while its single adapter drains prior autonomous output; they use the
  existing 5 s acknowledgement deadline, not the active-stream timeout. The
  300 ms producer clock starts at robot acceptance. The Pi web bridge independently
  zeroes within 300 ms of the last frame regardless and stops
  refreshing the `web_teleop_active` evidence the robot bridge requires for
  manual `DRIVE`. Nothing resumes automatically.
- At the mux the channel is `input/teleop` priority 7 — strictly below the
  F710 (`input/joy`, 8), `undock_turn` (9) and the safety watchdog (10).
  This input joins the **final mux after** Nav2 collision-monitor output
  `/input/navi`, then uses the final smoother → bridge → SBC hardware guards.
  LiDAR freshness is required, but collision-monitor coverage of web input,
  reactive laser obstacle stopping and physical collision acceptance are not
  proven or claimed; physical acceptance remains pending.
  It outranks Nav2 (5), but **start rejects active navigation/map/undock
  operations**: Stop navigation first and wait for its terminal result. There
  is no old navigation to resume on release. F710 activity ends the Pi web
  session permanently; its terminal result requires a fresh human start.
- `stop`, `teleop_stop` and `motor_disable` bypass lease/rate barriers for any
  authorized operator, including another tab, never observers. Reset also
  safely revokes manual input. Other physical operations revoke/end manual
  input and are rejected until its outcome resolves; nothing auto-resumes.
  Start acceptance arriving after cancellation is relayed as refused to the
  UI and cannot arm any browser. Pending/unknown ownership is retained until
  the original robot terminal result and pending stop results resolve, or an
  explicitly authorized reset. A zero sent, timeout or publication failure is
  not proof of physical stopping.
- Only `teleop_start`/`teleop_stop` are audited; per-frame samples are not —
  at 10–15 Hz they would drown the command audit trail.

## Transport rules

- Robots authenticate with `?token=` (shared secret, env-configured).
- A second robot connection with the same `robot_id` supersedes the first.
- Browser fan-out is lossy for high-rate telemetry (drop-oldest per client);
  `telemetry.map`, snapshots, events and state changes are never dropped.
- Occupancy grid is RLE-encoded `[value, count]` pairs over row-major int8
  cells (`-1` unknown, `0` free, `100` occupied), row 0 = map origin row,
  ROS convention (+y up — renderers must flip).

## Map context (map_context_v1)

Spatial payloads and command goals carry `map_id` and `map_revision`. Missing
context remains readable for legacy recordings but is explicitly rejected for
spatial commands. `/api/maps` lists local catalog metadata; `/api/maps/{map_id}`
returns occupancy data. Browsing never commands the robot. `set_initial_pose`
uses LocalizeOnMap; map activation and localization completion are separate.
BaseState map context is confirmed robot state; it becomes stale on disconnect.
Navigation, restoration and retries must retain and revalidate their original
map context. Map switching invalidates selections and cross-floor overlays.

`command.cancel` is a server-to-robot frame carrying the original `command_id`.
Operator browser disconnect, server timeout or shutdown cancels its active map
transaction; cancellation retains the robot-side hold. Browser gateways never
accept this internal message as authorization.

### Emergency software reset

`command.request.command = "software_reset"` uses a UUID, the authenticated
server's `operator_authorized` stamp, confirmation and durable command audit.
It accepts no shell, host, goal, service or reset-scope arguments. Reset bypasses
ordinary operator leases, rate limits and unresolved LiDAR commands; authorization,
UUID dedupe and host single-flight protection remain mandatory.

Neither admission nor the host restart depends on dock contact, pose,
localization, motion telemetry, idle navigation or ROS health. The host stops
old Pi command publishers, restarts the fixed SBC hardware-server service and
existing Pi core/web-bridge containers, retaining images and configuration.
Later operations are attempted even if an earlier one fails.

An accepted acknowledgement means the host durably recorded the request.
`succeeded` means restart commands completed; it does not certify robot readiness.
The host reads no robot preflight or postflight and performs no automatic retry.
An ambiguous result is retained as `timeout`/`uncertain`; ordinary commands remain
held, but a newly confirmed UUID may request another reset. An actually running
reset rejects a second request. Old destinations and commands are not resumed.

Normal navigation authorization and fresh-localization requirements are separate
from reset completion. Reset issues no pose, goal, velocity, motor-enable,
charge-release or undock request, and does not reboot either OS. The current
transport still needs a live web bridge; resetting that failed process itself
requires an independent host management channel.


LiDAR admission and power control:

- BaseState adds `lidar_state` (`UNKNOWN`, `OFF`, `STARTING`, `READY`, `FAULT`),
  `lidar_power_supported` (commissioned power control), and `lidar_scan_age`
  (seconds since measured acquisition; -1 means unavailable). Missing fields fail
  closed for commands while older recordings remain readable.
- `OFF` means commanded off, without electrical power readback. Scan readiness
  is independent of power commissioning. READY requires scan acquisition age plus
  downstream receipt age no greater than 0.5 seconds. Both scan and power
  admission also require connected, valid drive-base telemetry with acquisition
  age plus receipt age no greater than 0.5 seconds.
- `set_lidar_power` carries an explicit boolean `enabled`; it uses the existing
  command permission, rate limit, operator lease and audit lifecycle, then the
  typed `/patrolbot/set_lidar_power` service with the command UUID as request_id.
- Power requires fresh hardware data, commissioned support, an idle stationary
  robot and a known OFF/READY/FAULT state. ON does not require localization.
- Goal, pose/map-localization, motor-enable, charger-release and undock commands
  refuse without fresh READY scans, including localization overrides. Stop and
  map cancellation remain available. A power request also blocks these commands
  while pending; ambiguous timeouts/disconnects keep the barrier until a definitive
  robot response. A definitive typed timeout allows a new deliberate power request
  but keeps spatial commands held until an explicit successful recovery and fresh
  READY scans. Every terminal power response requires a subsequently received
  BaseState before another command, so cached READY cannot bridge a transition.
  Power changes clear saved destinations and require a new pose
  before navigation; ON never resumes an old goal.

### Measured Set location profile

`command.request` may carry top-level `seed_profile: "measured"` only for
`set_initial_pose`. The operator explicitly selects this after physically
measuring position and heading. The web adapter sets `LocalizeOnMap.measured_pose`
and diagonal covariance x/y `0.0009`, yaw `0.00030461742`. Omission preserves
the coarse covariance `0.25`, `0.25`, `0.0685`. Map coordinates and yaw remain
in the map frame; the profile changes no coordinate conversion or safety gate.
The command audit records the requested profile alongside the goal.

### Motor power requests

`motor_disable` requests the typed robot service and cancels outstanding movement;
it stays available to authorized operators during motion, faults, LiDAR recovery,
and reset uncertainty. It clears the dashboard's saved destination and override.
Motor enable remains an explicit confirmed request; the robot owns physical
interlocks and refuses re-enabling while prior movement cancellation is unfinished.
Neither motor command sends a velocity or goal, or establishes localization.
Service results retain request correlation; disable success also requires a
motors-disabled readback. These repairs were recovered from the saved deployment
source and web-bridge image, not inferred from mock behavior.
