# Operation fault visibility and recovery

Status: local implementation, not deployed or physically accepted.

The main dashboard and Advanced controls show a persistent **Robot operation
fault** banner for a failed controller/dashboard hardware undock, a fault-stopped
dock observer, E-stop or hardware fault flags. **Details / recovery** explains the
failure and retains its raw reason and operation ID when the robot supplies them.
Missing historical detail is shown as unavailable, never inferred.

The companion robot change adds `operation_fault_reason`,
`operation_fault_request_id` and `operation_outcome_unknown` to `BaseState.msg`.
Build and deploy every ROS interface consumer together. An older robot remains
readable, but cannot provide a historical reason; recovery is available only when
the web bridge advertises `dock_reset` from the real typed service.

**Confirm recovery** is an operator-authorized, audited, single-operator-leased
`dock_reset` command mapped to `/patrolbot/dock_reset`. It is not a software reset,
motor-enable request, velocity command or automatic retry. The browser requires a
separate confirmation, invalidates any old Resume offer, and waits for robot
telemetry to clear the banner; a sent request is not proof of physical recovery.

Admission requires fresh valid hardware and session-matched stationary raw wheel
motion. It does not require an AMCL pose or selected map. E-stop, hardware faults,
unknown operation outcomes, active operations and stale evidence remain blocked.
The robot repeats tighter local checks, including scanner readiness and released
manual input, and returns the descriptive refusal to the command-result panel.
Recovery leaves motor power unchanged and does not resume any old maneuver or goal.
Ordinary manual-control/bumper/sensor interlocks remain in force.

The physical dock observer remains authoritative. Acknowledging a recoverable
already-clear/docked stop does not change physical state. Clearing a fault-stopped
latch returns it to observation; sustained physical charger evidence, not the old
undock failure, establishes docked state. After recovery, release all controls and
make a fresh RB press or a new dashboard manual-control request.

Physical acceptance still needed: full undock/turn, obstacle stop with motors
retained after proven clearance, explicit recovery/refusal, manual handoff, return
to charger, cancellation, E-stop and stale-sensor handling. Do not test live motion
without a clear area and an operator at the physical E-stop.

## Local validation

- Server: 880 tests pass (two external-service cases deselected).
- Web bridge: 226 tests pass, three opt-in cases skipped.
- Frontend: production build passes; 493 tests pass with `--maxWorkers=2`.
  An unchanged LiveMap measured-seed freshness test failed once in an unconstrained
  parallel run; the full bounded-worker run passed without changing that test.
- Focused tests cover explicit confirmation, no commands on render, unknown/stale
  recovery refusal, AMCL-independent stationary evidence, and no optimistic
  removal of the fault banner before a new robot state arrives.

These are software/isolated checks, not live robot acceptance or a deployment.

## Browser CI policy

PR #38's browser shard 1 was cancelled twice after the 20-minute job limit while
`playwright install --with-deps` was still downloading Ubuntu packages from
`azure.archive.ubuntu.com`. Neither attempt reached the browser tests.

At the operator's request, browser acceptance is now **manual-only** through
`workflow_dispatch`, with no automatic PR/main trigger or shard matrix. Automatic
CI still requires frontend unit tests, TypeScript, production build and bundle
budget checks, alongside the existing Python/contract checks. Browser test files
remain available; this is an explicit CI policy change, not a claimed test pass.

The optional manual job uses the digest-pinned Playwright 1.63.0 image matching the
lockfile, rather than apt-installing browser dependencies on every runner. An
early no-download check launches and renders with Chromium and WebKit, and fails
clearly if the image and lockfile drift. Both runtimes and the previously blocked
123-test shard were verified locally in that image with external networking off.
