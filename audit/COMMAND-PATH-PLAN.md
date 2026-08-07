# Command-path plan: dock/undock wiring + remaining hardening

Covers the open items in `DASHBOARD-HARDENING-TODO.md` and the work to connect
the dashboard's dock/undock button to the real robot. Spans two repositories:

- **`patrolbot-dashboard`** (this one) — server, frontend, `patrolbot_web_bridge`.
- **`patrolbot-repo`** — `patrolbot_dock_manager`, `patrolbot_interfaces`,
  `patrolbot_navigation`, `patrolbot_bridge`.

Written 2026-07-26 against `hardening/command-path`.

---

## What the robot already provides

Worth stating up front, because the dashboard was built assuming less than
this exists. From `patrolbot-repo/ros2_ws/src`:

| Interface | Type | Provided by |
|---|---|---|
| `/patrolbot/undock` | action `patrolbot_interfaces/Undock` | `patrolbot_dock_manager` |
| `/patrolbot/dock_readiness` | topic `DockReadiness` @ 2 Hz | `patrolbot_dock_manager` |
| `/patrolbot/initialize_dock_pose` | service `InitializeDockPose` | `patrolbot_dock_manager` |
| `/patrolbot/charge_release` | service `ChargeRelease` | `patrolbot_bridge` |
| `/patrolbot/motor_enable` | service `MotorEnable` | `patrolbot_bridge` |

Three properties of those contracts shape everything below:

1. **`ChargeRelease` and `MotorEnable` take a caller-generated `request_id`
   and replay the original result if the same UUID is reused.** The dashboard
   already mints a `command_id` UUID per request. Passing it straight through
   makes the whole browser → server → bridge → SBC chain idempotent, instead of
   the dashboard-side dedupe being the only replay protection.
2. **`Undock.action` returns a typed `code`** (13 values: `PRECHECK_FAILED`,
   `RELEASE_FAILED`, `LOCALIZATION_FAILED`, `SAFETY_FAULT`, …) plus
   `manual_recovery_required` and the final charge/motor/e-stop state. The
   dashboard protocol currently carries only `outcome` + free-text `detail`.
3. **`DockReadiness` already publishes every precondition** the dashboard
   re-derives in `commands/gates.py`, plus `ready_for_undock` and a
   `blocking_reason` string. The robot is the authority on its own readiness;
   the dashboard is guessing from `base_state` + `pose`.

`Undock.action` also has a **`validation_mode`** flag — a dry run that
exercises the whole precheck chain without moving. That is the commissioning
tool, and it should be reachable before any real undock is attempted.

---

## Phase 0 — one decision needed first: how does the robot dock?

**There is no dock-in path on the robot today.** `patrolbot_dock_manager`
logs "Guarded undock manager ready" and advertises undock only. Nav2's
`docking_server` is configured in `nav2_params.yaml` with a real
`PatrolBotChargingDock` plugin, but the server is **not in the lifecycle node
list** — the config itself says the plugin "remains dormant while
docking_server is disabled".

So the `Dock & Charge` half of the dashboard button has nothing to call. Three
ways forward:

| Option | Work | Notes |
|---|---|---|
| **A. Enable Nav2 `docking_server`** (recommended) | Add to lifecycle list, set the dock database/pose, commission `DockRobot` | The plugin is already written and configured. Gets the mature opennav_docking staging→approach→charge-confirm state machine, and a matching `UndockRobot` for free. |
| **B. Add a `Dock` action to `patrolbot_dock_manager`** | New action + state machine mirroring the undock guards | Full control and the same guard style as undock, but re-implements what A already has. |
| **C. Ship undock only for now** | Dashboard hides/disables `Dock & Charge` | Smallest step. The robot keeps auto-docking on low battery; the operator just can't ask for it. |

Everything below assumes **A**, and calls out where B or C changes it. Until
this is decided, Phase 1–3 are unaffected — they are undock-only and are the
critical path regardless.

---

## Phase 1 — wire undock through the bridge ✅ done 2026-07-26

Undock only; `dock` is deliberately not wired (Phase 0 undecided, and the
robot has no dock-in path). Files:
`ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/commands.py`,
`bridge_node.py`, `ws_client.py`, `config/web_bridge.yaml`,
`server/app/commands/broker.py`, `server/app/telemetry/hub.py`.

Shipped:

- `CommandExecutor` holds an `ActionClient(Undock, '/patrolbot/undock')` and
  service clients for `ChargeRelease`/`MotorEnable`, constructed only under
  `WEB_BRIDGE_ENABLE_COMMANDS=1` and only when `patrolbot_interfaces` imports.
- The dashboard's `command_id` is passed through as the services'
  `request_id`, so a replayed frame replays the robot's original result.
- `operator_authorized` is stamped **server-side** from the verified session
  in `CommandBroker`, overwriting whatever the browser sent; the bridge
  refuses an undock without it as a second line of defence.
- Undock feedback → `command.progress` (throttled 1 Hz, dock-manager `state`
  rendered in plain language); the typed result `code` → outcome + an
  operator sentence, including `manual_recovery_required`.
- `stop` cancels an in-flight undock; the undock's own terminal result
  (`CANCELED`) closes out its command.
- Capabilities are computed from **live server reachability** and re-announced
  mid-session via a fresh `robot.hello`, which the dashboard server now treats
  as an update and fans out as `state.capabilities`. So a dock manager that
  starts after the bridge un-greys the button with no reload and no reconnect.
- `undock_validation_mode` is **false** since commissioning (2026-07-26). It
  was never a dry run — it only tightens the distance/speed caps and rejects
  goals above them; the robot moves in either mode. The real kill switch is
  capability advertisement, above.

Original task list, for reference:

1. **Add a `DockCommands` helper** alongside `CommandExecutor` holding an
   `ActionClient(Undock, '/patrolbot/undock')` and service clients for
   `ChargeRelease` / `MotorEnable`. Construct it only when
   `WEB_BRIDGE_ENABLE_COMMANDS=1`, like the existing executor.
2. **Dispatch** `undock`, `charge_release`, `motor_enable` from
   `CommandExecutor.handle()`.
3. **Pass the dashboard's `command_id` as the services' `request_id`**, so a
   duplicate frame replays the robot's original result rather than re-running
   the operation.
4. **Set `operator_authorized` on the Undock goal from the verified session
   role, not from the browser payload.** The server knows the role
   (`user.can_command`); add it to the forwarded frame server-side in
   `CommandBroker.handle_browser_request` so the bridge cannot be told
   "authorized" by a crafted frame. `reverse_distance` / `reverse_speed` come
   from `web_bridge.yaml`, never from the browser — the robot bounds them
   again with its own hard caps.
5. **Map action feedback → `command.progress`**: `Undock.action`'s feedback
   carries `state`, `elapsed_seconds`, `rear_clear`, `localization_valid`.
   Throttle to ~1 Hz like `_on_feedback` does today; put `state` in `stage`.
6. **Map the result → `command.result`** including the typed `code` (Phase 3).
7. **Cancellation**: `stop` while an undock is active must call
   `cancel_goal_async()` on the undock handle, and — per the open TODO item —
   only report success on the terminal result, not on cancel acceptance.
8. **Advertise the capabilities** in `ws_client.py`'s `robot.hello`:
   add `"undock"`, `"charge_release"`, `"motor_enable"` (and `"dock"` once
   Phase 4 lands) — but only when command mode is on *and* the action server
   is reachable. The dashboard keeps the button disabled until then, so
   advertising is the deliberate commissioning switch.

**Acceptance:** with `WEB_BRIDGE_ENABLE_COMMANDS=0`, no action/service clients
are created and `undock` is declined with the existing `DISABLED_REASON`.
With it on and the dock manager stopped, the dashboard shows "the robot's
docking system is not running" rather than hanging until the 5 s ack timeout.

---

## Phase 2 — let the robot's own readiness drive the button

Today `server/app/commands/gates.py` and `frontend/src/lib/dockGates.ts`
re-derive undock preconditions from `base_state` + `pose`. `DockReadiness`
publishes the robot's own answer, including checks the dashboard cannot make
(`dock_pose_valid`, `command_path_valid`, `sonar_fresh`, `navigation_idle`).

1. Bridge: subscribe `/patrolbot/dock_readiness`, normalize, and emit a new
   `telemetry.dock_readiness` frame at the existing `slow_interval`.
2. Protocol: add `DockReadinessData` to `messages.py` + `protocol.ts` +
   a golden fixture, and carry it in `SnapshotData`.
3. Gates: when readiness is fresh, `ready_for_undock` decides and
   `blocking_reason` is the sentence shown to the operator. When it is stale
   or absent, fall back to today's local rules — never fail open.
4. Frontend: same precedence, so the tooltip/reason line quotes the robot.

**Why this order:** doing Phase 1 first means undock works with the local
gates; Phase 2 replaces a good guess with the authoritative answer and removes
the risk of the two disagreeing.

---

## Phase 3 — surface the real terminal reason

Closes the open TODO item *"Surface the exact terminal reason in the UI:
safety hold, collision stop, localization loss, controller abort, cancel,
timeout, or robot disconnect."*

1. Add an optional `code: str` and `manual_recovery_required: bool` to
   `CommandResultData` (both sides + fixture).
2. Bridge maps `Undock.action`'s `uint16 code` to a stable string
   (`precheck_failed`, `release_failed`, `localization_failed`, …) — the enum,
   not the free-text message.
3. `frontend/src/lib/plainLanguage.ts` gets a `COMMAND_FAILURE_COPY` map:
   one plain sentence per code, plus what the operator should do next.
   `manual_recovery_required` gets a persistent, dismissable banner — it is
   the one state where someone has to physically go to the robot.
4. Do the same for Nav2 goal aborts on `navigate_to_pose` so the
   short-movement investigation has real terminal reasons to read.

---

## Phase 4 — dock-in (blocked on Phase 0)

Assuming option A:

1. `patrolbot-repo`: add `docking_server` to the lifecycle node list, populate
   the dock database with the commissioned dock pose, verify
   `PatrolBotChargingDock` reports charge from typed `BaseState`.
2. Bridge: `ActionClient(DockRobot, '/dock_robot')`, dispatched from the
   `dock` command; feedback → `command.progress`; `use_dock_id` with the
   configured id so the browser never supplies a pose.
3. Advertise `"dock"` in `robot.hello`. The dashboard button lights up on its
   own — no frontend change needed; that was the point of capability gating.

If option C: drop `"dock"` from the capability list permanently and change the
frontend's off-dock branch to explain the robot docks itself on low battery.

---

## Phase 5 — remaining hardening items

Genuinely still open, from `DASHBOARD-HARDENING-TODO.md`:

1. **Motion gate for `navigate_to_pose`** (Command boundary). Dock/undock are
   gated; navigation is not. Apply the same `gates.py` treatment: fresh+valid
   base state, faults/e-stop clear, motors enabled, charging inactive,
   localization valid, nav lifecycle active. *Note:* the existing server tests
   send `navigate_to_pose` with no `base_state` at all, so this needs those
   fixtures updated — that is why it was scoped out of the dock work rather
   than bolted on.
2. **Stop/cancel completion** must depend on the terminal result and confirmed
   zero velocity. `commands.py::_stop` currently reports success when the
   cancel request is *accepted*. Wait for the terminal status, then for
   `|linear|,|angular| ≈ 0` in odom, with a timeout that reports honestly.
3. **Envelope validation on the browser→server path**: `command_id` must
   parse as a UUID, `sequence` must increase per connection, `timestamp` must
   be within a sane skew window. The robot→server path already checks
   robot identity and sequence; `ui_gateway.py` checks neither.
4. **Joystick takeover is final**: when the Pi's F710 preempts a Nav2 goal,
   the dashboard must not offer Resume. Needs a takeover signal in
   `base_state` (or a dedicated frame) and `commandStore.stoppedGoal` cleared
   on it.
5. **Bridge tests** proving command mode is off by default, that the bridge
   never creates a `/cmd_vel` publisher, and never opens an SBC connection.
   The current suite only covers `precheck` and quaternion math.
6. **Confirmation dialog + "off charge, motors live" banner** — dropped with
   the one-button design (recorded in the TODO). Revisit once operators have
   used undock for real.

Items 1–3 are server/bridge-only and can land before any robot time.

---

## Phase 6 — commissioning and rollout

Order matters; each step gates the next.

1. **Dock pose**: `initialize_dock_pose` with the robot physically on the
   charger; confirm `dock_pose_valid` and stable AMCL convergence.
2. **Readiness watch**: with command mode still **off**, watch
   `/patrolbot/dock_readiness` on the dashboard through a full charge cycle.
   Every flag should read as expected with nobody commanding anything.
3. **Validation-mode undock**: `validation_mode: true` from the dashboard —
   full precheck chain, zero motion. Fix whatever it refuses on.
4. **First real undock**: operator physically at the robot, hand on the
   e-stop, short `reverse_distance`. Verify the audit row, the typed result
   code, and that the rear bumper genuinely aborts it.
5. **Dock-in** (Phase 4) with the same treatment.
6. **Rollback**: keep the checksum-backed previous bridge build; capability
   advertisement is the kill switch — remove `"undock"` from `robot.hello`
   and every dashboard drops the control without a redeploy.

---

## Risks

- **Two repos, one contract.** `patrolbot_interfaces` changes ripple into the
  bridge. Pin the interface version in `robot.hello`'s `software_version` and
  check it server-side before advertising capabilities.
- **The dashboard's gates and the robot's gates can disagree** until Phase 2
  lands. Both fail closed, so the failure mode is a button greyed out for a
  reason the robot doesn't recognize — annoying, not dangerous.
- **`operator_authorized` is a real authorization boundary.** If it is ever
  taken from the browser payload rather than the verified session, the whole
  role model is bypassed for undock. Server-side only, with a test.
- **Undock moves the robot with a human likely nearby** — it is the one
  dashboard command whose normal case is someone standing at the charger.
  Phase 6 step 4 is not optional.
