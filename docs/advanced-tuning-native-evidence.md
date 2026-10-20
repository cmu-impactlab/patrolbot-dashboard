# Advanced tuning — isolated native evidence (slices 1–2 native lab)

Date: 2026-10-17. Scope: isolated, hardware-free native lab for the advanced
tuning feature. This document records machine-checkable native evidence for the
FINAL `teleop_velocity_smoother` behavior and the curated AMCL / Nav2-RPP startup
configuration. It is **image-internal evidence, not live-Pi evidence**, and it is
**not physical acceptance**. No robot, SBC, SSH, deployment, DDS-wide graph or
motion command was involved.

Delivered files (all new; no existing teleop-lab, core, contract, frontend,
server or bridge file was modified):

- `infrastructure/tuning-lab/Dockerfile`, `run_host.sh`, `README.md`
- `scripts/tuning_lab.py`, `scripts/run_tuning_lab.sh`,
  `scripts/check_tuning_receipt.py`
- `scripts/tests/test_tuning_lab_tools.py`
- this document

## 1. Safety posture

- Every native node runs only inside a local Docker container started with
  `--network none`, no host network, no hardware mounts, and an overridden
  entrypoint (`/opt/dashboard/scripts/run_tuning_lab.sh`) — no production
  bring-up and no inherited SBC connection.
- `tuning_lab.py` asserts isolation as its **first statement, before any `rclpy`
  import or publisher**: `PATROLBOT_ISOLATED_TEST=1`, `ROS_DOMAIN_ID=188`,
  `ROS_LOCALHOST_ONLY=1`, and `/sys/class/net` containing **only `lo`**. A
  non-isolated run refuses with an explicit reason. The same guard is unit-tested
  (`test_assert_isolated_raises_before_any_ros_import`).
- Synthetic `Twist` messages are published only inside the container. They are
  never hardware inputs and never motion commands. No motors, dock, undock,
  goal, pose, lifecycle activation of a real stack, or mutability flag was
  touched. `physical_acceptance` is hard-coded `false` in the receipt.

## 2. Reproduce

```sh
# Build offline (COPY-only layer on the cached patched-Nav2 image) and run isolated.
BASE_REF=patrolbot:joystick-handshake-diagnostic-20261015-r3 \
EVIDENCE_DIR=/tmp/opencode/tuning-evidence \
bash infrastructure/tuning-lab/run_host.sh

# Checker self-test (rejects fabricated/stale receipts).
python3 scripts/check_tuning_receipt.py --self-test

# Unit tests (pure stdlib/pytest; no ROS, no network).
python3 -m pytest scripts/tests/test_tuning_lab_tools.py -q
```

Evidence written to `$EVIDENCE_DIR`: `receipt.json`, `native.log`,
`amcl_startup.yaml`, `controller_startup.yaml`.

Example verified run (image IDs change on rebuild; regenerate per run):

- lab image `patrolbot-dashboard:tuning-lab` id
  `sha256:7291a6eeb40b97c3d467826bcd52538f848850ffcd9fa3484f2c6abf77542dc4`
- base image `patrolbot:joystick-handshake-diagnostic-20261015-r3` id
  `sha256:441470f84264595217dcc839ac59934975dc030c2e8c3831683638c8d3d43ca6`
- `generated_at` `2026-10-17T20:38:43Z`, `result: pass`, 15/15 checks.

## 3. Install provenance (image-internal; NOT the robot's installed version)

| Item | Value (from the exercised image) |
| --- | --- |
| Base OS | Ubuntu 24.04.4 LTS, ROS 2 Jazzy |
| apt `ros-jazzy-nav2-*` (base layer) | `1.3.13-1noble.20260921…` — **shadowed** by the source overlay for the tested packages |
| Source overlay `package.xml` version (`nav2_amcl`, `nav2_velocity_smoother`, `nav2_controller`, `nav2_regulated_pure_pursuit_controller`) | `1.3.12` |
| Core pin (`docker/Dockerfile.nav2-fixes`) upstream archive | tag `1.3.12`, sha256 `bcdf575c86250d63007595c992b9c5785c3ec907e897b79abfb4fc7eacbd1129` |
| Local patch set | 17 patches, `/opt/patrolbot-nav2-patches`, manifest sha256 `dd61f97fffa8c49063b9357ccc5f89d69a556ee5e06e09b66cb0396b05d4f376  -` |
| Source tree digest file | `0f077c31da9221f09aea39da25c1f52b65303e38db0994f033faa734a6b3b578` |
| Patrolbot smoother YAML sha256 | `deef967e197037669d14ab145860ca4024ac0671c969b5538658ed96a5b87736` |
| Patrolbot `nav2_params.yaml` sha256 | `cf3e58eddd96e181a8148ad17d5418560a2a28d3bd89efb2c5e8e9a1808e26d5` |

Resolved overlay prefixes and hashed libraries used by the proofs:

```
nav2_amcl                -> /opt/patrolbot_nav2/install/nav2_amcl
nav2_velocity_smoother   -> /opt/patrolbot_nav2/install/nav2_velocity_smoother
nav2_controller          -> /opt/patrolbot_nav2/install/nav2_controller
nav2_regulated_pure_pursuit_controller -> .../install/nav2_regulated_pure_pursuit_controller
libamcl_core.so                          f2395af9cc58ea2724f278028feebaeee8ebefaccf912d2a5b3a6e7e5a79b99e
libvelocity_smoother_core.so             3f7dd5fc5a016c01dbf3dd5fa70f31e4fac2097399ee002654d845f566eab57a
libcontroller_server_core.so             377b7feb76e1131f70805950df79508bf19ef32181023c191df6070b68c18c11
libnav2_regulated_pure_pursuit_controller.so fc33250b18e86e500d9446adda7afc649e0c63401e28a4a28e32514e88d6451c
nav2_regulated_pure_pursuit_controller.xml    6d903da447c019f3edee595cef9d9cdb6d6d1c331ef01e5765ecdb88d7b1709b
```

The `v1.3.12`/archive-sha values come from the core repository's pinned build
recipe; the in-image `package.xml` confirms the source overlay is `1.3.12`. The
apt `1.3.13` packages are present but shadowed by the overlay for every tested
node. **No claim is made about the robot's actually installed versions.**

## 4. Results

| # | Check | Result | Classification |
| --- | --- | --- | --- |
| 1 | `isolation_enforced` | pass | inventory |
| 2 | `native_inventory` | pass | inventory |
| 3 | `vs_upstream_defaults_observed` | pass | inventory |
| 4 | `vs_startup_readback` | pass | live_verified |
| 5 | `vs_caps_reduced_and_symmetric` | pass | live_verified |
| 6 | `vs_over_cap_input_clamped` | pass | live_verified |
| 7 | `vs_proportional_small_target_no_creep` | pass | live_verified |
| 8 | `vs_reversal_through_zero` | pass | live_verified |
| 9 | `vs_acceleration_bounded` | pass | live_verified |
| 10 | `vs_stop_explicit_zero_deadline` | pass | live_verified |
| 11 | `vs_stop_silence_timeout_deadline` | pass | live_verified |
| 12 | `vs_hot_reload_max_velocity_effect` | pass | live_verified |
| 13 | `amcl_startup_readback` | pass | restart_required |
| 14 | `rpp_startup_readback` | pass | restart_required |
| 15 | `rpp_runtime_set_not_effect` | pass | restart_required |

Summary: `result: pass`, 15 checks, 0 failed.

## 5. FINAL smoother behavior (native `nav2_velocity_smoother`, node `teleop_velocity_smoother`)

Configured from `smoother.yaml` and read back after lifecycle configure/activate:

- `max_velocity [0.30, 0.0, 0.70]`, `min_velocity [-0.30, 0.0, -0.70]`
- `max_accel [1.0, 0.0, 1.5]`, `max_decel [-1.4, 0.0, -2.0]`

Observed installed defaults (a name-mismatched launch so the YAML does not apply):
`max_velocity [0.5, 0.0, 2.5]`, `min_velocity [-0.5, 0.0, -2.5]`,
`max_accel [2.5, 0.0, 3.2]`. The patrolbot configuration is therefore genuinely
**reduced** and symmetric, not the Nav2 default.

| Behavior | Command | Observed |
| --- | --- | --- |
| Over-cap clamp | linear 0.50, angular 1.20 | peak linear 0.30, peak angular 0.70 (24 samples) |
| Proportional small target, no creep | constant 0.05 for 4.0 s | final 0.05, early max 0.05, late-window max 0.05 (cap 0.30, 80 samples) |
| Reversal through zero | +0.20 then −0.20 | reached −0.20, sign crossing observed |
| Acceleration bounded | ramp to 0.30 from rest | peak step 0.07 vs 0.12 per-cycle budget |
| Explicit-zero stop | 0.25 then zero command | 0.178 s (deadline 0.55 s) |
| Silence stop (timeout) | 0.25 then stop publishing | 0.664 s (deadline 0.95 s) |

Hot-reload: `SetParameters max_velocity [0.10, 0.0, 0.30]` was accepted, read back,
and **the clamp tightened**: with input 0.25 the output peaked at 0.10. This is a
callback **plus** observed effect, so `max_velocity` (linear and angular indices)
is classified `live_verified` for this installed node. A bare `SetParameters`
success without the observed clamp change is explicitly **not** accepted as
evidence.

## 6. Curated AMCL / Nav2-RPP groups

Both groups were written into a startup YAML, loaded by the **real installed
patched nodes**, and read back through the node parameter service:

- AMCL: `alpha1 0.11, alpha4 0.07, update_min_d 0.25, update_min_a 0.20,
  min_particles 1500, max_particles 3000, max_beams 90, z_hit 0.60, z_rand 0.40,
  sigma_hit 0.15, pf_err 0.06, pf_z 0.98` — all read back exactly (no configure
  needed; declared in the node constructor).
- RPP `FollowPath`: `desired_linear_vel 0.18, lookahead_dist 0.40,
  min_lookahead_dist 0.20, max_lookahead_dist 0.65, lookahead_time 1.20,
  min_approach_linear_velocity 0.06, approach_velocity_scaling_dist 0.50,
  rotate_to_heading_angular_vel 0.60` — read back exactly after `controller_server`
  configure (the RPP plugin was created: `Created controller : FollowPath of type
  nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController`).

Mutability is conservatively classified **`restart_required`**, with the precise
unsupported reason recorded in the receipt, not a faked pass:

- **AMCL**: startup YAML reaching the node is proven, but alpha/particles/laser
  model are consumed while building the filter on configure; no runtime hot
  behavior is proven offline, so a runtime set is not treated as live.
- **Nav2 / RPP**: startup YAML reaching the configured node and plugin is proven,
  but a runtime `SetParameters` on `FollowPath.*` is accepted and changes the
  stored value **without any observed behavioral effect** (no path/odom/TF graph
  offline). Acceptance alone is explicitly not a live claim.

These bounds are candidate/lab values only. No commissioned usable lower bound or
hardware deadband is invented.

## 7. Receipt validity and negative tests

`check_tuning_receipt.py` validates the receipt as machine-checkable, bounded
JSON (≤ 1 MiB, ≤ 64 checks, ≤ 4096 items/list) and requires: exact sha256 image
IDs, the loopback-only isolation block, provenance digests, ≥ 1 hashed library,
the 17-patch provenance, and the required check set. It rejects fabricated or
stale "shim" data by cross-checking claims against their own evidence:

- `physical_acceptance: true`, `live_pi_evidence: true` → reject
- missing image ID, non-loopback interfaces, low domain id, localhost-only off → reject
- missing patch hash, empty libraries, missing required check → reject
- AMCL/RPP claimed `live_verified` → reject
- hot-reload claimed `live_verified` without `effect_observed` or with a peak
  above the requested clamp → reject
- startup-readback mismatch, creep toward the cap, over-cap "pass",
  inconsistent summary → reject
- stale `generated_at` (age bound) → reject

`--self-test` passes (all shims rejected, valid fixture accepted). The checker
itself also refuses to run by default outside a loopback-only sandbox
(`--allow-unisolated` exists only for offline re-validation of a captured file).

Unit tests: **19 passed** (`scripts/tests/test_tuning_lab_tools.py`) covering the
isolation guard (fails before any ROS import), the snapshot reader, receipt
acceptance/rejection, the staleness bound, the oversized-receipt bound, and that
the default CLI isolation gate fails on a normal host.

## 8. Remaining holes (explicit)

- Image-internal only. Installed Pi package versions, live node FQNs, live
  effective limits and the live web-bridge tree remain **UNKNOWN**.
- The source overlay's exact upstream tag is inferred from in-image
  `package.xml` (`1.3.12`) plus the core build recipe pin; the image cannot
  itself re-verify the downloaded archive bytes at runtime.
- AMCL and Nav2/RPP hot behavior is unproven offline and treated as
  restart-required. Proving it needs a full map/scan/odom/TF graph and command
  comparison, or a supervised on-robot check.
- Acceleration is measured from subscriber receive timestamps; the per-cycle
  step budget carries a 2× jitter allowance rather than an exact 1.0 m/s² slope.
- The lab does not prove web/F710/keyboard producers, the mux arbitration, or
  the SBC DRIVE boundary; those belong to the existing teleop lab and later
  slices.
- This is **not** robot acceptance and does not authorize activation, deployment
  or any motion.

## 9. Preserved existing artifacts

The six existing teleop-lab receipts and the teleop-lab harness were not
modified (`infrastructure/teleop-lab/`, `scripts/teleop_lab.py`,
`scripts/check_teleop_receipt.py`, `scripts/run_teleop_lab.sh` are untouched). To
rebuild and re-run the existing teleop lab after other agents' changes, use the
existing recipe unchanged:

```sh
docker --context default build -f infrastructure/teleop-lab/Dockerfile.toolchain \
  -t patrolbot-dashboard:teleop-test-tools .
docker --context default build -f infrastructure/teleop-lab/Dockerfile \
  -t patrolbot-dashboard:teleop-lab .
docker --context default run --rm --network none patrolbot-dashboard:teleop-lab
```

## 10. Revalidation on 2026-10-18

The current harness was rebuilt and run after fixing the host wrapper's bare
image-ID `FROM` failure. The wrapper now creates a content-ID local tag, verifies
its exact image ID, and builds with `--pull=false --network none`. Five portable
regressions cover the tag binding, isolation, malformed/mismatched IDs and build
failure preventing execution.

- Generated at: `2026-10-18T08:05:56Z`.
- Lab image: `sha256:7a10473272d2d9cdcfb14b2379f5c1d564e1963624c98b0218034235fa984194`.
- Base image: `sha256:441470f84264595217dcc839ac59934975dc030c2e8c3831683638c8d3d43ca6`.
- Result: **15 checks passed, 0 failed**; receipt checker also passed.
- Receipt SHA-256: `6a3f0b3c367483f6eb6116922e6167a3e6563a696dfc00dc88d820b8515ac430`.

The receipt/logs were captured in the local review evidence directory, not
committed as release artifacts. The historical numbers above remain the
2026-10-17 run, not measurements from this revalidation. The new run retains
`physical_acceptance=false`, `live_pi_evidence=false`, loopback-only isolation
and the conservative AMCL/RPP `restart_required` classifications. It does not
exercise the new runtime tuning store/activation integration or prove a live
robot's installed versions, usable lower speed bound or writable capability.
