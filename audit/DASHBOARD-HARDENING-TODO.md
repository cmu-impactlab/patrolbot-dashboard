# PatrolBot Dashboard Hardening Backlog

Status: deferred from the Pi/SBC joystick and Nav2 command-path work.

**Sequenced plan for what is left, including the dock/undock wiring:
[`docs/COMMAND-PATH-PLAN.md`](docs/COMMAND-PATH-PLAN.md).** This file stays the
list of requirements; that one is the order to do them in.

Dashboard source lives in the separate `patrolbot-dashboard` repository. This
file records the work to review there; it does not authorize dashboard
deployment or robot motion.

## Command boundary

- Keep continuous velocity control out of the dashboard. The physical Logitech
  F710 on the Pi remains the commissioned manual-control channel.
- Keep the ROS bridge outbound-only and loopback-discovered. Never expose DDS,
  `/cmd_vel`, `/initialpose`, or Nav2 actions directly to the LAN or VPN.
- Separate telemetry permission from command permission. Authenticated viewers
  must remain read-only; only an explicitly authorized operator role may send
  navigation, stop, or pose commands.
- Add a single-operator command lease. A second browser may observe but cannot
  race, replace, resume, or cancel the active operator's command without an
  explicit takeover recorded in the audit log.
- Reject motion commands unless the robot connection and base state are fresh,
  hardware state is valid, faults and E-stop are clear, motors are enabled,
  charging is inactive, localization is valid, and required navigation
  lifecycle nodes are active.
- Treat joystick/manual takeover on the Pi as final: a canceled Nav2 goal must
  not be automatically resumed by the dashboard.

## Authentication and transport

- Make production startup fail closed unless OIDC, a non-default session
  secret, a non-empty username allowlist, TLS, and a non-development robot
  credential are configured.
- Enforce command authorization server-side from the verified session role;
  hiding buttons in the frontend is not authorization.
- Validate browser WebSocket `Origin` against an explicit allowlist and reject
  cross-origin command sockets.
- Set session and OIDC cookies `Secure`, `HttpOnly`, and an appropriate
  `SameSite` policy in TLS deployments; rotate the session after login.
- Move the robot credential out of the WebSocket query string so reverse-proxy
  access logs cannot capture it. Use an authorization header or a first-frame
  challenge with constant-time comparison and a documented rotation process.
- Add strict request-size, frame-rate, and per-user command-rate limits at both
  nginx and the FastAPI gateway.

## Protocol and auditability

- Validate command UUIDs, envelope sequence monotonicity, robot identity,
  timestamps, finite numeric coordinates, and configured map bounds.
- Record requesting user, session, source IP, robot session generation,
  command payload, acknowledgment, result, rejection reason, and takeover
  events in the command audit.
- Persist duplicate protection across server restarts for the audit retention
  window instead of relying only on an in-memory 512-entry cache.
- Define reconnection behavior explicitly: browser or robot reconnects must
  never replay a goal, pose, resume, or stop request.
- Make stop/cancel completion depend on Nav2's terminal result and confirmed
  zero robot velocity, not only acceptance of a cancel request.

## Navigation short-movement investigation

- Reproduce the reported “moves briefly, then stops” symptom while correlating
  the dashboard command lifecycle with Nav2 action status and the Pi's bounded
  command-path diagnostic capture.
- If the same goal fails when sent directly on the Pi, fix the Pi/Nav2/SBC
  blocker in the core repository. If direct Nav2 succeeds, fix the dashboard
  broker/bridge lifecycle without changing collision or watchdog thresholds.
- Surface the exact terminal reason in the UI: safety hold, collision stop,
  localization loss, controller abort, cancel, timeout, or robot disconnect.

## Charging, motor power, and undock controls

**Design decision (2026-07-26, Yousef):** the dashboard exposes **one** dock
control, in the Navigation widget — `Undock` (red) while the robot is on its
charger, `Dock & Charge` when it is not. `dock` and `undock` are single
operator actions: the robot releases its own charger and powers its own motors
as part of executing them. This supersedes the three-step
release → enable → undock sequence this section originally called for; the
concern behind that requirement (no unauditable “unlock wheels” command) is met
by keeping the steps as separate audited commands on the robot side rather than
by making the operator click three buttons.

Done in the dashboard — the toggle in
`frontend/src/widgets/NavControlsWidget.tsx`, the shared gate rules
(`server/app/commands/gates.py` + `frontend/src/lib/dockGates.ts`) and the
`charge_release` / `motor_enable` / `dock` / `undock` protocol commands:

- [x] `charge_release` and `motor_enable` exist as distinct, separately
  audited commands with their own interlocks — charge release is zero-motion
  and leaves the motors off; motor enable refuses while the charger is
  engaged. Nothing is labeled “unlock wheels”. They are reachable from an
  **Advanced** disclosure inside the Navigation widget, collapsed by default,
  for driving the sequence a step at a time when diagnosing which step refuses
  or recovering a robot left half-way. Motor enable needs a second deliberate
  press ("Confirm"), which restores the confirmation the one-button design
  dropped for the operation that actually makes the robot drivable.
- [x] The dock control sends its command with a fresh UUID; accepted/result
  state, progress and the rejection reason surface in the widget's existing
  banners, and the full identity (user, IP, session generation, payload,
  outcome) lands in `command_audit`.
- [x] The control is disabled — with the reason shown under the buttons —
  unless hardware telemetry is fresh and valid, faults and E-stop are clear
  and the robot is stationary; undock additionally needs a clear rear bumper,
  dock needs valid localization. Every rule is enforced server-side in
  `commands/gates.py` regardless of what the browser believed.
- [x] Dock/undock are offered only to a robot that advertises the capability
  in `robot.hello`; an uncommissioned base shows the control greyed out with
  “not commissioned on this robot yet”. Undock is an action with progress and
  a Stop control, never browser-published velocity.
- [x] A dock or undock clears any pending destination, so no prior goal is
  replayed afterwards.
- [ ] Not carried over from the original plan: the persistent “off charge with
  motors enabled” banner. Dropped with the one-button design; revisit if
  operators want it. (The confirmation step came back for motor enable under
  Advanced.)

Remaining before this is used on the real robot:

- [x] Wire `undock`, `charge_release` and `motor_enable` through
  `patrolbot_web_bridge` to the dock manager's `/patrolbot/undock` action and
  the two drive-base services, with capabilities advertised from live server
  reachability. Done 2026-07-26 — see Phase 1 in
  [`docs/COMMAND-PATH-PLAN.md`](docs/COMMAND-PATH-PLAN.md).
- `dock` is **not** wired: the robot has no dock-in path yet
  (`patrolbot_dock_manager` is undock-only and Nav2's `docking_server` is
  configured but dormant). The dashboard button stays greyed with "not
  commissioned on this robot yet" until that is decided — Phase 0 of the plan.
- Commission dock pose, localization at the dock, rear clearance, bumper
  behavior and physical acceptance before turning `undock_validation_mode`
  off. It ships **true** (dry run), so the first deployment cannot move.

## Verification and rollout

- Add server tests for roles, Origin rejection, rate limits, reconnect/replay,
  duplicate persistence, stale telemetry, invalid robot state, and complete
  audit identity.
- Add bridge tests proving command mode is off by default and that the bridge
  never publishes `/cmd_vel` or opens an SBC connection.
- [x] Make `make test` deterministic. External-service tests are already
  split behind the `external` marker; the remaining nondeterminism was
  `server/.env` (a developer's real OIDC config) leaking into the suite
  through `Settings`, which failed 33 tests on a configured machine and none
  on a fresh clone. `server/tests/conftest.py` now isolates the suite from
  both that file and any exported `PATROLBOT_*` variable.
- Deploy behind an explicit command-mode flag, validate telemetry-only behavior
  first, then commission pose and goal commands with an operator physically at
  the robot. Keep a checksum-backed rollback version.
