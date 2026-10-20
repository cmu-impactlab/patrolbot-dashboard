# Advanced tuning — slice 1 offline source inventory

Status: **offline, source-only inventory. Not commissioned. Nothing here is a
live value.** Written 2026-10-17 for the `feat/advanced-tuning-contract` slice
on the dashboard. No robot, SBC, SSH, ROS or deployment contact was made.

> **Current-runtime status (2026-10-18).** The 2026-10-17 source inventory below
> is the tracked-state record and is unchanged. An **uncommitted** working-tree
> runtime slice now exists on this branch (server API/broker, robot bridge
> adapter, frontend panel and measured-motion telemetry). It is not committed,
> merged, deployed or commissioned and adds no live value. See
> `docs/advanced-tuning-runtime-verification.md`. Where the text below is
> directly superseded by that slice it is labelled inline, not rewritten.

## 0. Method and honesty rules

- Every value below is read from tracked source at the stated line. A source
  default is **not** an installed or running value.
- Baselines inspected:
  - dashboard `cmu-impactlab/patrolbot-dashboard` @ `28af8dc` (branch
    `feat/advanced-tuning-contract`).
  - robot `cmu-impactlab/patrolbot` @ `37a4c1a` (branch
    `feat/advanced-tuning-contract`), read-only.
- **Installed/live values are UNKNOWN** and stay UNKNOWN until a separately
  authorized read-only deployment inventory runs. Do not quote these source
  values as "the robot's current limits".
- Container **image references** such as `:latest` or `patrolbot-web-bridge:dev`
  are mutable tags, not immutable pins; only a recorded image digest is
  evidence. The `patrolbot-state` volume is different: it is persistent mutable
  *state* (maps, config), not a package or image pin at all. Any unverified node
  mutability stays **restart-required / disabled**.
- The dashboard web bridge's `config/web_bridge.yaml` is **not read on the
  robot**: the file's own comment (`ros2_ws/.../config/web_bridge.yaml:48-52`)
  states the deployed container starts `bridge_node.py` with inline `-p`
  overrides and no `--params-file`, so the `declare_parameters` defaults are
  expected to run. Treat that as a **source expectation, not a live
  measurement**: the actual inline `-p` values and any environment overrides
  (`WEB_BRIDGE_*`) are not verified offline, so the live effective values remain
  **UNKNOWN**.

**Path convention.** `hardware/...`, `docker/...` and full
`ros2_ws/src/patrolbot_bridge/...` paths are relative to the **robot** repository
root (`cmu-impactlab/patrolbot`). Shortened `patrolbot_bridge/...`,
`patrolbot-launch/...` and `patrolbot_navigation/...` paths mean packages under
that repository's `ros2_ws/src/`; bare filenames refer to the named package.
Bare `docker-compose.yml` references mean the robot's `docker/docker-compose.yml`.
Paths prefixed `server/`, `frontend/`, `mock-robot/`, `infrastructure/` and
`ros2_ws/src/patrolbot_web_bridge/` are in the **dashboard** repository
(`cmu-impactlab/patrolbot-dashboard`). `contracts/web-teleop.json` is identical
in both repositories. The web bridge tree exists only in the dashboard repo;
the core bridge tree exists only in the robot repo.

## 1. Node / fully-qualified-name expectations

Names are what the launchers set; FQNs are the expected graph names. Treat the
FQN column as *expected*, to be confirmed read-only on the robot later.

| Node (FQN expected) | Package / executable | Launched by | Purpose |
|---|---|---|---|
| `/teleop_velocity_smoother` | `nav2_velocity_smoother` / `velocity_smoother` (lifecycle) | `patrolbot-launch/launch/smoother.xml:6` | FINAL smoother; sole `/cmd_vel` publisher |
| `/cmd_vel_mux` | `twist_mux` / `twist_mux` | `patrolbot-launch/launch/mobile_base.xml:7` | sole `/cmd_vel_out` publisher |
| `/lifecycle_manager_script` | `patrolbot-launch` / `lifecycle_mgr.py` | `smoother.xml:15` | keeps the final smoother ACTIVE, self-heals respawns (`lifecycle_mgr.py:18-70`) |
| `/nav2_container` | `rclcpp_components` / `component_container_isolated` | `patrolbot_navigation/launch/bringup.launch.py:53-59` | single composed Nav2 process |
| `/map_server`, `/amcl` | `nav2_map_server`, `nav2_amcl` | `patrolbot_localization_launch.py:187-223` | localization; lifecycle manager `lifecycle_manager_localization` |
| `/controller_server` (+ RPP `FollowPath`) | `nav2_controller` | `patrolbot_navigation_launch.py:245-252` | Nav2 controller |
| `/velocity_smoother` | `nav2_velocity_smoother` | `patrolbot_navigation_launch.py:296-303` | Nav2-internal smoother (NOT the final one) |
| `/collision_monitor`, `/behavior_server`, `/planner_server`, `/bt_navigator`, `/smoother_server`, `/route_server`, `/waypoint_follower` | `nav2_*` | `patrolbot_navigation_launch.py:245-318` | Nav2 stack |
| `/nav_command_mux` | `twist_mux` | `patrolbot_navigation_launch.py:343-352` | arbitrates Nav2 producers before the Nav2 smoother |
| `/patrolbot_joy_teleop` | `patrolbot_navigation` / `patrolbot_joy_teleop.py` | `bringup.launch.py:137-140` | F710 proportional drive |
| `/joy_node` | `joy` / `joy_node` | `bringup.launch.py:117-131` | F710 input |
| `/patrolbot_safety_watchdog` | `patrolbot_navigation` | `bringup.launch.py:155-158` | priority-10 zero hold on stale sensors |
| `/patrolbot_controller_feedback`, `/patrolbot_dock_manager`, `/laser_static_tf` | various | `bringup.launch.py:143-176` | feedback, guarded undock, laser TF |
| `/patrolbot_bridge` | `patrolbot_bridge` / `bridge_node` | `docker/docker-compose.yml:60-67` | core Pi bridge: `/odom`, `/scan`, `/sonar`, `/battery`, `/diagnostics`, `/patrolbot/base_state`; subscribes `/cmd_vel` |
| `/patrolbot_web_bridge` | `patrolbot_web_bridge` / `bridge_node.py` | `docker-compose.yml:99-110` (opt-in profile) | dashboard web telemetry + web teleop adapter |

**Name caveat.** `patrolbot_joy_teleop.py:72` constructs the node as
`p3dxJoyTeleop`, while the launch file passes `name='patrolbot_joy_teleop'`
(`bringup.launch.py:138`). Through this launcher the FQN is expected to be
`/patrolbot_joy_teleop`, but a differently launched instance would be
`/p3dxJoyTeleop`. Confirm the actual FQN read-only before a registry entry names
it; do not assume.

## 2. Package / source versions: known vs UNKNOWN

Known source declarations (not proofs of what is installed):

| Package | Source version | File |
|---|---|---|
| `patrolbot_interfaces` | 0.1.0 | `ros2_ws/src/patrolbot_interfaces/package.xml` |
| `patrolbot_navigation` | 0.0.1 | `.../patrolbot_navigation/package.xml` |
| `patrolbot_bridge` | 0.0.0 | `.../patrolbot_bridge/package.xml` |
| `patrolbot-launch` | 0.0.0 | `.../patrolbot-launch/package.xml` |
| `patrolbot-dashboard-server` | 0.1.0 | `server/pyproject.toml:3` |
| `patrolbot-mock-robot` | 0.1.0 | `mock-robot/pyproject.toml` |

Committed evidence (dashboard only):

- Last dashboard deployment record
  `infrastructure/releases/deployed-20261017-web-teleop.json`: `source_sha`
  `62f7326…`, server `image` digest `sha256:97d5b1e9…`, `physical_acceptance:
  false`, `sbc`: "Operator-reported down".
- Pi web-bridge rollout by source sync `web-teleop-20261017-r1`, file manifest
  sha256 `910b0384…`, claimed byte-identical to this repo's
  `ros2_ws/src/patrolbot_web_bridge` tree.

**UNKNOWN (must not be asserted):** installed ROS 2 distro and Nav2 package
versions (apt/pinned/deb), built image digests on the Pi, the live running node
versions, SBC firmware/protocol build, any host `docker` image tag resolution,
and whether every live service equals branch `feat/advanced-tuning-contract`.
The Dockerfile targets `ROS_DISTRO: jazzy` (`docker-compose.yml:154`) but that is
a build argument, not an installed-version record.

## 3. Ordinary-drive consumer graph to the final smoother / core / SBC

Arbitration (from source):

```
F710  --/input/joy----(priority 8)--.
Web   --/input/teleop-(priority 7)--+--> cmd_vel_mux --/cmd_vel_out-->
Keyboard(web, same /input/teleop)   |     (priority 9 undock_turn,
                                    |      10 safety_controller)
Nav2 producers --nav_command_mux--> velocity_smoother --/cmd_vel_smoothed-->
                  (controller 5,                 collision_monitor -->
                   behavior 7, docking 9)        /input/navi -(priority 5)-> cmd_vel_mux
                                                                                |
                                                                                v
                                              teleop_velocity_smoother --/cmd_vel-->
                                              patrolbot_bridge --> SBC DRIVE (classifyDrive)
```

- Mux priorities and timeouts: `patrolbot-launch/param/defaults/mux.yaml:5-37`;
  mirrored in `contracts/web-teleop.json:18-25`. Safety 10 > undock-turn 9 >
  F710 8 > web 7 > switch 6 > Nav2 5.
- Nav2 producer mux `nav_command_mux`: controller 5, behavior 7, docking 9
  (`nav2_params.yaml:471-486`), remapped to `cmd_vel_nav`
  (`patrolbot_navigation_launch.py:343-352`).
- Collision monitor reads `cmd_vel_smoothed` and writes `input/navi`
  (`nav2_params.yaml:497-501`).
- Final smoother remaps: subscribe `/cmd_vel_out`, publish `cmd_vel`
  (`smoother.xml:11-12`); it bounds **both** web/F710 manual and Nav2 output.
- Core bridge subscribes `/cmd_vel` and forwards to the SBC
  (`ros2_ws/src/patrolbot_bridge/patrolbot_bridge/bridge_node.py:108`).
- SBC `classifyDrive` accepts ordinary DRIVE only within `|linear| <= 0.30`,
  `|angular| <= 1.20` (`hardware/patrolbot_hw_server/protocol.cpp:265`).
  **1.20 is not the commissioned angular cap**; the final/web cap is 0.70.
- Manual bypass of *localization only* needs fresh deadman evidence
  (`/patrolbot/manual_active` or `/patrolbot/web_teleop_active`);
  `manual_active.py:26-65` still refuses on undock, LiDAR, odom origin,
  raw odom, and map/mutation ownership.

Keyboard drive is not a separate ROS channel: it is the same web
`/input/teleop` path with digital input (plan §2, `ManualDrive.tsx`).

## 4. Source-effective limits vs upstream mismatches

All values are source configuration, not live readback.

| Producer / bound | Value (source) | Reference |
|---|---|---|
| FINAL smoother linear | ±0.30 m/s | `smoother.yaml:11-12` |
| FINAL smoother angular | ±0.70 rad/s | `smoother.yaml:11-12` |
| FINAL smoother accel / decel | [1.0, 0, 1.5] / [-1.4, 0, -2.0] | `smoother.yaml:18-19` |
| SBC ordinary DRIVE accept | \|lin\|≤0.30, \|ang\|≤1.20 | `protocol.cpp:265` |
| Web wire envelope | ±0.30 / ±0.70 | `contracts/web-teleop.json:30-33`; `server/app/protocol/messages.py:264-265` |
| F710 default / caps | max_lin 0.30, max_ang 0.7, lin_cap 0.30, **ang_cap 3.0** | `patrolbot_joy_teleop.py:78-83` |
| F710 minima / steps | min_lin 0.05, min_ang 0.1; steps 0.05 / 0.1 | `patrolbot_joy_teleop.py:80-85` |
| F710 ramp | accel_lin 0.7 m/s², accel_ang 2.0 rad/s², 30 Hz | `patrolbot_joy_teleop.py:87-89` |
| Nav2 internal smoother | ±0.26 / ±1.0 rad/s | `nav2_params.yaml:365-366` |
| RPP cruise / turn | desired 0.22; rotate 0.9 rad/s; ang accel 3.2 | `nav2_params.yaml:153-165` |
| RPP approach / regulated min | 0.08 / 0.10 m/s | `nav2_params.yaml:168-173` |
| `behavior_server.max_rotational_vel` | 0.7 rad/s | `nav2_params.yaml:92` |
| Collision-monitor forward/reverse polygon | linear ≤0.26 | `nav2_params.yaml:541,549,576,582` |
| AMCL motion model | alpha1..4 = 0.05, alpha5 = 0.0 | `nav2_params.yaml:8-14` |
| AMCL particles / update | 1000–4000; update_min_d/a 0.10 | `nav2_params.yaml:37-38,57-58` |
| AMCL laser model | `likelihood_field_prob`, max_beams 60, z_hit 0.5, z_rand 0.5 | `nav2_params.yaml:29-32,59-67` |

Explicit mismatches to map, not silently reconcile:

1. **RPP `rotate_to_heading_angular_vel` 0.9 and Nav2 smoother 1.0 exceed the
   final 0.70 cap.** The final smoother clamps them, so the source settings are
   inconsistent upstream of the commissioned cap. Any generated tuning group
   must keep source targets ≤ the global cap, and must not treat
   `rotate_to_heading_angular_vel` as a cap on curved-path output.
2. **F710 `max_angular_cap = 3.0`** (`patrolbot_joy_teleop.py:83`) is a
   source-declared producer ceiling above both the final smoother (0.70) and
   the SBC hard accept (1.20). Because the final smoother clamps the final
   `/cmd_vel` at 0.70 today, this is **not** a bypass of the commissioned final
   output right now. It is a producer-level mismatch and a latent hazard: if the
   downstream clamp were ever relaxed or removed, a retained 3.0 trim could
   command up to that value. Any activation must clamp cached trims to the
   active policy rather than preserve them.
3. **SBC accepts 1.20 rad/s** while the commissioned cap is 0.70; the panel
   envelope must not exceed 0.30 / 0.70 (plan §7.1).
4. **AMCL config inconsistencies in source:** `alpha5`, `z_max`, `z_short` are
   zeroed as unused by `DifferentialMotionModel`/`likelihood_field_prob`, while
   `lambda_short` (also unused) is left at 0.10 (`nav2_params.yaml:12-13,
   60-67`). Show unused fields read-only/not-applicable; do not "fix" them
   through the panel.

## 5. Cached F710 trims

- Declared parameters are read once into `self.max_lin`, `self.max_ang`,
  `self.lin_cap`, `self.ang_cap`, `self.min_lin`, `self.min_ang`
  (`patrolbot_joy_teleop.py:91-97`). Declaring a parameter does not prove a
  later change updates the running controller.
- A/Y and X/B are **edge-triggered in-memory trims** only
  (`patrolbot_joy_teleop.py:325-336`): they mutate `max_lin`/`max_ang`, are
  clamped to `[min_*, *_cap]`, and are **not** persisted to ROS settings or
  disk. They reset on restart and are not durable settings.
- Consequence for tuning: cached trims must be clamped on any activation so
  `min_linear`/`min_angular` cannot push a lowered ceiling back up (plan §7.3);
  the source-declared `max_angular_cap = 3.0` is exactly that producer-level
  hazard to clamp — it is not a current bypass of the final smoother's 0.70 cap,
  but it must not survive an activation as a higher producer ceiling.

## 6. Web protocol (already deployed; do not re-implement)

- Browser→server frames: `command.request` for `teleop_start`/`teleop_stop`
  (UUID `command_id`), then `teleop.velocity` `{command_id, seq, linear,
  angular, deadman}` at ~12.5 Hz (`frontend/src/lib/teleop.ts:12`;
  `server/app/protocol/messages.py:245-276`).
- Server-side admission (`server/app/commands/teleop.py`): identity + lease +
  session generation binding (`bound`, lines 73-84), monotonic `seq`
  (107-111), timestamp freshness (-0.05…0.3 s, 112-120), per-second frame cap
  (131-134), rate-limit/lease/rejection in `broker.py:159-189`, and
  duplicate protection via an in-memory LRU plus a DB lookup
  (`broker.py:182-189`).
- Robot side (`ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/teleop.py`):
  canonical UUID check (39-45), closed-ID replay barrier sized 4096 (98-102),
  strict frame key/shape/range checks (146-157), 300 ms zero watchdog from
  `contracts/web-teleop.json:28`, and a half-second zero-drain start barrier
  across `/cmd_vel_out` and `/cmd_vel` before ACK (434-442).
- Evidence: `/patrolbot/web_teleop_active` Bool, published false before a
  stop and true only after its Twist (`teleop.py:268-276`).
- Manual start is gated on the `web_teleop_v1` capability on both the server
  (`gates.py:392-426`) and the browser (`teleop.ts:20`).

## 7. Final output owners

- `/cmd_vel_out` — sole publisher `cmd_vel_mux`. `/cmd_vel` — sole publisher
  `teleop_velocity_smoother`. The web bridge refuses ambiguity by checking the
  single expected endpoint on each topic (`teleop.py:291-334`).
- `/cmd_vel` is consumed by `patrolbot_bridge` (`bridge_node.py:108`) and sent
  to the SBC, the final physical arbiter (`classifyDrive`).
- **No new velocity publisher may be added.** Tuning generators must feed the
  existing param sets, never publish Twist (plan §3.4).

## 8. Raw-odom telemetry gap and current lifecycle owner

- The core bridge publishes genuine `/odom` with measured covariance
  (`patrolbot_bridge/bridge_node.py:98`; `config/bridge_params.yaml`).
- The dashboard web bridge subscribes `/odom`
  (`web_bridge.yaml:12`; `bridge_node.py:222`) and calls
  `_maybe_send_pose()` on each odom message (`bridge_node.py:245-249`).
- `_maybe_send_pose()` emits `telemetry.pose` only when
  `normalize_pose(...)` produced a payload **and** the AMCL pose is on the
  active map (`bridge_node.py:256-274`). So odom twist rides inside a
  pose frame that requires valid AMCL/active-map context.
- When unlocalized — the exact condition under which manual drive is allowed —
  no pose frame is sent, so **there is no measured-speed telemetry**. There is
  no standalone bounded `telemetry.motion`/odom-twist slice. `_raw_odom_age()`
  (`bridge_node.py:508-526`) computes an age only for `base_state`'s
  `raw_odom_age_s`; it does not stream twist.
- Therefore a gauge that reads "measured speed" cannot be built on existing
  protocol without adding the slice-3 `telemetry.motion` (plan §7.7). A
  commanded value is not a measured value.
- Odom-origin lifecycle owner: the core `patrolbot_bridge` via
  `OdomEpochGuard` (`patrolbot_bridge/odom_epoch.py:41-112`) and
  `epoch_runtime.py`. `BaseState.msg` carries `odom_epoch`,
  `odom_epoch_generation`, `odom_epoch_valid`, distinct from the transport
  `session_generation`. Dashboard-side cache:
  `_latest_odom`/`_latest_odom_mono`/`_latest_odom_at` (`bridge_node.py:158-160`).

> **Superseded by the working tree (2026-10-18).** The "no standalone
> `telemetry.motion` slice" statement above described tracked `main` when this
> inventory was written. An **uncommitted** runtime slice now adds a bounded,
> AMCL-independent measured-`/odom` `telemetry.motion` frame plus speed gauges
> (`normalizers.py`, `shared/schemas/protocol.md`, `frontend/src/components/SpeedGauges.tsx`).
> It is not committed, merged, deployed or commissioned, so the tracked-state
> record and the UNKNOWN items in §11 still stand. See
> `docs/advanced-tuning-runtime-verification.md`.

## 9. Persistence pattern, first-run import, commissioning lower bounds

- Existing atomic-state precedent: `ActiveMapStore`
  (`patrolbot_bridge/map_catalog.py:109-156`) writes a same-directory temp file,
  `fsync`s it, `os.replace`s, then `fsync`s the directory. It validates
  `schema_version`, treats only a missing file in an available durable dir as
  first install, and refuses on any read/write error.
- State dir default `/var/lib/patrolbot` (`bringup.launch.py:25`;
  `map_runtime.py:54`). Volume mounts today: bridge RW
  (`docker-compose.yml:69`), navigation RO (`:168`), **bringup none**
  (`:44-46`). A tuning store therefore needs new least-privilege mounts; the
  exact volumes are unverified and must not be assumed.
- Conservative first-run / import (plan §5): import observed approved settings
  **without raising any effective or source-specific ceiling**; a missing file
  on a clean install may initialize reviewed defaults, but a missing/corrupt
  previously-initialized config must fail closed/read-only and retain last-good,
  never reset to faster defaults. Pending restart-required edits stay pending
  across reboot until an activation revision is explicitly promoted.
- Unresolved commissioning lower bounds: source minima exist (`min_linear`
  0.05, `min_angular` 0.1 in `patrolbot_joy_teleop.py:80,82`; RPP
  `min_approach_linear_velocity` 0.08 and regulated min 0.10 in
  `nav2_params.yaml:168-173`), but no measured hardware deadband or performance
  floor is established. Do **not** invent a commissioned lower bound; leave it
  UNKNOWN until measured.

## 10. Mutability posture

- Source does not prove live mutability. AMCL and Nav2 nodes cache values and
  reinitialize internals; `read_only=false` is not evidence. Every initial
  editable candidate (plan §8) defaults to **restart-required / disabled**
  until an isolated native-node test proves callback, readback and effect.
- Container image tags are not pins; installed versions are UNKNOWN; therefore
  any group whose correctness depends on a Nav2/AMCL package version must stay
  disabled/restart-required rather than hot-applied.

## 11. Open items (still UNKNOWN)

- True live FQN of the joystick teleop node and every installed package version.
- Whether the live Pi web bridge tree equals this branch.
- Actual live effective limits (they may not equal source).
- Which volume/mount layout a tuning store will use on `bringup`/`navigation`.
- Whether the SBC is currently reachable at all (operator-reported down).
