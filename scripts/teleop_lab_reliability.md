# Isolated teleop harness investigation (2026-11-20)

## Packaged candidate follow-up (2026-11-21)

Base HEAD: `1f73539ca567b08bff1ead29bbf08c1b6cff1872`. The parent's actual
`lab-initial-34ccad3-env2/full.log` failed with `PackageNotFoundError` for
`patrolbot-launch`. The test toolchain copies the launch source fixture but does
not build its package index. Lab startup now resolves the actual smoother YAML
in this order: `TELEOP_LAB_SMOOTHER_CONFIG` when explicitly supplied, otherwise
the indexed `patrolbot-launch/param/defaults/smoother.yaml`, otherwise (only if
the package is absent) the documented source fixture
`/opt/patrolbot/ros2_ws/src/patrolbot-launch/param/defaults/smoother.yaml`.
A missing selected file fails startup; no smoother YAML is synthesized and no
library defaults substitute for it. `smoother-config.json` and stdout record
the selected source, absolute path and SHA-256. The native mux/smoother still run.

Task disposal follows the inspected, completed core helper in commit
`70b2345c0b3983d48f89056279352e433a23699e`. After dispatcher/pool drain, inactive
tasks are canceled with their task lock held, and real coroutine handlers are
closed while guards remain valid. The cached Jazzy Task.cancel already closes
pending handlers; explicit closure also supports the older state-only variant
and already-canceled handlers. Completed exceptions remain teardown failures.
Tasks created by cancellation are disposed too. Unknown internals, active
handlers and blocked finalizers fail cleanup and retain ROS handles.

Never-awaited coroutine RuntimeWarnings are promoted to errors. Thread and
unraisable hooks record failures in teardown and delegate to the existing hooks.
The supervisor also rejects never-awaited, background-thread, unraisable and
unretrieved-exception diagnostics in retained logs, including interpreter exit
output or warnings emitted under a local filter override. No warning is hidden.

The existing six acceptance checks retain their command correlation/outcome
rules. A seventh starts healthy, keeps velocity production alive, then stops
synthetic odometry via `odom_stale`. It requires the bridge's actual stale-odom
safety reason and age with continuing base/tick receipts, actual zero and
inactive web-input receipts, and the exact robot/start's `failed` terminal with
`data.detail == "Raw odometry is missing or stale."`. The protocol's
`CommandResultData.detail` holds this reason; `TeleopSession.tick()` and `.frame()`
emit it through `end(reason, 'failed')`, and the server broker forwards the
payload unchanged. A separate explicit expected-fault helper requires this pair;
`finish()` and `terminal_id()` retain their existing policies. Wrong identities
cannot satisfy the fault check; canceled/succeeded terminals and wrong/missing
details fail. Healthy sessions still reject failed terminals, including this one.
A fresh start must be refused with the server gate's exact `command.ack` reason,
`Manual drive requires fresh base, raw odometry and LiDAR readings.`, before
healthy publishing is restored. Fault observation is a separate `fault_observed`
field; it never labels stale sensors ready.

The earlier canceled-terminal requirement was an incorrect parent assumption,
now corrected, not a production-runtime blocker or runtime fix. Production
deliberately reports `failed` for stale odometry; only producer timeout maps to
`canceled`. A different raced outcome still fails this specific fault acceptance.

Portable checks cover the resolver's installed/source/override/missing paths,
real coroutine finalization and cancellation, follow-up tasks, active/unknown
task refusal, completed exceptions, background error reporting, log rejection,
and the acceptance workflow with mocked I/O. No new helper or packaging path is
needed. The packager continues to reject missing `frontend/dist`; the parent
will supply cached static assets for harness-only validation, which does not
validate a frontend build. Parent-owned native serial/load runs remain required.

Focused command (no ROS/network/install):
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:server /tmp/opencode/ci-audit/bridge-minimal-venv/bin/python -W 'error:coroutine .* was never awaited:RuntimeWarning' -m pytest -q -p no:cacheprovider -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning scripts/tests/test_teleop_lab.py scripts/tests/test_check_teleop_receipt.py`

Corrected acceptance result: **203 passed**, zero warnings/skips/deselections.
Python compilation, runner shell syntax and `git diff --check` pass. Focused
fault tests reject wrong robot/command/type, canceled/succeeded/timeout outcomes,
missing/wrong detail (including a reason in the wrong field or a zero-publication
failure suffix), absent zero/inactive receipts, incorrect/missing refusal reason,
accepted restart and unobserved fault. Deadline noise cannot extend the wait.
The workflow exercises the real expected-fault helper; healthy `finish()` still
rejects the exact stale failure. Prior follow-up result was **183 passed**.
An earlier offline self-check loaded the supplied cached `jazzy-task.py` directly
and finalized both pending and canceled coroutine Tasks with this adapter.
A separate direct `TeleopSession` check confirmed the deliberate stale-safety
terminal is `failed`; the test expectation now follows that runtime contract.
This was not a native run. The parent reports its core `70b2345` native validation
passed all 1163 tests with zero warnings, skips, deselections or surviving groups,
and a clean localCodex final review. That evidence does not validate this
dashboard resolver/task-cleanup follow-up or the corrected seventh acceptance;
the parent will run their actual native validation.
The referenced `RTK.md` remains absent from the checkout and its ancestors.
Git worktree metadata is read-only; the parent must commit these local changes.

## Cached Jazzy teardown follow-up (2026-11-21)

The supplied `executor-shutdown-baseline.json` reports `shutdown_result=true`
while `callback_drained_when_shutdown_returned=false`. Inspection of the cached
Jazzy 7.1.11 executor confirms that its inherited shutdown destroys guards and
does not drain the multi-thread pool. The previous cleanup order was unsafe.

The lab now owns bounded `spin_once` loops. Cleanup stops dispatch, wakes and
joins all spinners, snapshots tasks, drains each real Python pool, checks all
retained completed tasks for exceptions, and only then invokes executor shutdown,
session closure and node destruction. Both single-thread executors (no pool or
`_futures`) and multi-thread executors are covered. The lab-only private adapter
supports legacy `_tasks` triples and cached Jazzy `_pending_tasks` dictionaries /
`_ready_tasks` deques, plus multi-thread `_futures`. Unknown layouts fail clearly.
Spin exceptions remain visible through the normal thread exception hook and are
also retained in teardown evidence, including tasks already removed by rclpy.
A blocked drain fails within the existing shared budget and retains guards,
nodes and context; the supervisor remains responsible for retiring the process.

Native cleanup now validates exit status: zero or a signal actually sent during
cleanup, including direct negative, shell `128+signal`, and launcher-wrapped
negative `256-signal` codes. Early exits and forced kills still fail. Supervisor
cleanup checks checker/lab/native groups even after launchers exit, reports any
surviving group, kills it, and bounds confirmation of retirement. Packaging now
rejects missing or symlinked source roots before creating an incomplete context
and includes the Dockerfile in its hash manifest. This checkout has no built
`frontend/dist`; the parent must supply the normal frontend build before packing.

Validation command:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:server /tmp/opencode/review-resume/test-venv/bin/python -m pytest -q -p no:cacheprovider scripts/tests/test_teleop_lab.py scripts/tests/test_check_teleop_receipt.py`

Result: **151 passed**. Tests use real Python thread pools with both private task
layouts, single/multi executors, success/error callbacks, blocked drains, spinner
exceptions, native exit codes, exited-launcher descendants, and packaging paths.
An additional offline check extracted and executed the actual cached Jazzy
`MultiThreadedExecutor._spin_once_impl` and `Executor.shutdown` AST methods,
without importing ROS. A submitted callback finished and its exception appeared
in teardown evidence before guards were destroyed; all pool workers joined.
Python compilation, shell syntax and `git diff --check` also pass.

The requested separate local Codex review was attempted using
`codex exec --ephemeral --sandbox read-only ... review --uncommitted`, but failed
before review with `failed to initialize in-process app-server client: Read-only
file system (os error 30)`. It remains a parent-owned review blocker. Git worktree
metadata is outside the writable sandbox, so these changes are left uncommitted
for the parent. Base HEAD: `34ccad3231c7c152194991fc119748185a582bce`.
The referenced `RTK.md` was absent in the checkout and its parent directories.
No Docker, ROS, installations, network operations, or native/load runs were used.
Parent-owned serial/load acceptance and separate review remain required.

Scope: lab scripts and portable tests only. No runtime/safety changes, Docker,
ROS execution, hardware access, installations, publication or native load runs
were performed for this patch.

## Evidence and cause boundaries

The supplied `teleop-lab-final.log` contains an exact second-start terminal with
`outcome=failed`, detail `Wait for fresh robot hardware readings.` The strict
checker correctly fails that run. Around the failure, successive diagnostics
show ticks increasing 119 → 159 → 172, rather than continuing at about 40/s;
base/odom counters increase 94 → 120 → 130. Idle age reaches 0.545 s and probe
pending age reaches 0.55 s. These are scheduling/service progress gaps, not
evidence of a malformed command identity. The serial log passes all six checks.

The old harness checks readiness only once, checks presence rather than actual
bridge hardware receipt age, and does not require subscriber discovery or
smoother lifecycle readback before each start. It also starts an untracked probe
executor, does not assert executor/thread termination, starts HTTP outside the
cleanup scope, and permits the shell's unbounded `wait` to mask teardown errors.
Those are established harness defects. The supplied logs do **not** establish
whether CPU contention, Python executor scheduling, DDS delivery, or lingering
workers caused the particular mid-session starvation. Readiness alone cannot
prevent a later scheduling gap.

## Changes and evidence contract

- Each healthy start requires new bridge-observed base/odom/idle/tick events and
  server base receipt after its readiness request, an active smoother readback,
  subscriber discovery, the real sole mux/smoother identity, and both existing
  server and bridge safety checks. No receipt timestamps are rewritten.
- The missing-hardware refusal waits for the fault to reach both bridge and
  server instead of sleeping a guessed interval. Exact command IDs and terminal
  outcomes remain unchanged. Failed terminals in the six existing checks still
  fail immediately; only the deliberate seventh fault expects its exact failure.
- Receiver, bridge, probe, HTTP, WebSocket and native child ownership includes
  partial startup. Transport intent and callbacks quiesce before session closure
  and node destruction. Cleanup shares a 20 s budget; surviving workers, early
  child exits and forced kills fail. The supervisor allows 25 s for lab exit,
  then kills the lab group and checks the recorded native groups independently.
- The supervisor reserves an ephemeral loopback socket and passes its descriptor
  to the lab. Smoother configuration resolves through the installed ROS package,
  rather than a cached source checkout path. The native mux/smoother remain real.
- Every run retains full merged stdout/stderr in `lab.log`, `checker.log` and
  `native.log`, emits them on both success and failure, and writes `run.json` plus
  lab `teardown.json`. Diagnostics include monotonic bridge ages and maximum
  sensor/tick/buffer gaps. Missing teardown evidence fails the overall run.

After the isolated native environment is prepared, invoke `run_teleop_lab.sh`, or
`python3 -u scripts/teleop_lab.py --supervise` with the same ROS overlays and
server PYTHONPATH. A bespoke wrapper that starts `teleop_lab.py` directly bypasses
supervision; standalone mode retains port 8027 for compatibility.

## Validation and remaining acceptance

Portable tests mock ROS ownership, native children and HTTP; they open no network
endpoints. They cover readiness events/guards/discovery, cleanup order, partial
startup, surviving workers, forced kills, one cleanup budget, full logs and runner
exit propagation. Existing exact receipt correlation tests run alongside them.

Command: `PYTHONPATH=/tmp/opencode/native-ci/dashboard/scripts:/tmp/opencode/native-ci/dashboard/server /tmp/opencode/ci-audit/bridge-minimal-venv/bin/python -m pytest -q scripts/tests/test_teleop_lab.py scripts/tests/test_check_teleop_receipt.py`

Result: 127 passed. Python compilation, shell syntax and `git diff --check` pass.

Parent-owned acceptance remains: repeated serial and healthy concurrent-load
native runs must pass all six receipts **and** teardown. Intentional starvation
is a separate fault-acceptance case: the exact failed/refused terminal and zero/
inactive ROS receipts are evidence, not a healthy-run pass. This patch does not
add a stress mode, retry failed sessions, widen freshness limits, or guarantee
stopping independently of scheduler/load behavior. No physical stop is claimed.
