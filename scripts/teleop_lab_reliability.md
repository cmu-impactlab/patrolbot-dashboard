# Isolated teleop harness investigation (2026-11-20)

## Producer-loss completion correction on cb9bcfe (2026-11-21)

The immutable parent native matrix at `cb9bcfe` is **4 passed, 2 failed**,
with all six teardowns clean. Failures remain at
`/tmp/opencode/bloat-cycle/lab-matrix-cb9bcfe/serial-3/full.log` and
`/tmp/opencode/bloat-cycle/lab-matrix-cb9bcfe/bounded-load-2/full.log` (and their
complete child evidence). Both fail producer-loss `finish(expect_stop=None)`
at `remaining()`, after the exact start logs `canceled` with no logged automatic
stop. This does not establish which watchdog won or validate the failed cases.
The checker deliberately postponed final audit until `deadline - .02`; HTTP
latency and scheduling could consume that reserve or start the final poll late.
The previous fake advanced only on WebSocket receives, with zero HTTP latency.

The local checker now reads existing LAB ONLY `/lab/status` completion facts
before each audit read. Return requires no active IDs (including unrelated
commands on the lab robot), no owner, unlocked admission and zero pending
revokes; completed start audit; any correlated stop's completed `succeeded`
audit; and every exact expected WebSocket terminal. A no-stop return additionally
requires the exact `canceled` terminal and completed `canceled` start audit.
It retains the half-second settlement for the bounded .3-second zero send, and
can return before the artificial last-20-ms poll only with these stronger facts.
An owner removed during an in-flight revoke cannot alone prove completion.
Reading audit after facts catches a stop audited as that revoke finishes.
Known stops still require their exact successful WebSocket terminal, even with
successful completed audit. A short bounded sleep prevents queued telemetry
from driving a busy polling loop. All operations share the original six-second
budget. There are no command, release or restart retries and no new outcomes.

Scope is `check_teleop_receipt.py`, its portable tests and these audit documents.
Production broker/teleop/receiver/safety files, existing stale-odometry barrier,
fault evidence/readiness/exact refusal, command counts, zero/inactive receipts
and physical-acceptance-false checks are unchanged. `RTK.md` was not found in
the checkout or checked ancestors. No network/install/Docker/ROS/robot,
subagents, commits, pushes or merges were used.

Evidence is separately retained in `/tmp/opencode/bloat-cycle/receipt-cb9-local/`:

| Check | Actual result / log |
| --- | --- |
| Original receipt tests before edits | 117 passed; `before.log` |
| New regression selection against original HEAD checker | 15 failed, 3 passed, 119 deselected; `regression-before.log` |
| Intermediate receipt runs | 117 passed in `first-after.log`; 137 passed in `focused-after.log` |
| Final lab/checker/stale-evidence tests | 320 passed, 2.40 s; `focused-final.log` |
| Final complete `scripts/tests` | 370 passed, 3.50 s; `all-scripts-final.log` |

Final runs used `/tmp/opencode/bloat-cycle/venv/bin/python -B`,
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:server`, pytest `-q -rs
-p no:cacheprovider`, with never-awaited coroutine, unraisable and thread
warnings treated as errors. They reported no warnings, skips or deselections.
Tests model 25–120 ms HTTP scheduling latency, WebSocket scheduling, bounded
poll sleeps, detached revokes beyond settlement, late stop audit/DB completion,
near-deadline stop discovery, failed/missing/mismatched stops, retained owner/
admission/unrelated active work, expired HTTP/WS budgets and noise. Python AST
parsing and `git diff --check` pass. Initial/intermediate logs remain separate.

The parent's prior evidence is retained as reported: full Codex review 4 clean,
all 19 CI checks green and 14 stages at `cb9bcfe` passing (1527 server,
575 frontend, 350 scripts, 342 adapter plus 1 ROS skip, 26 mock, 136 contracts,
3 browser). Those results do not validate this uncommitted patch. Core PR 86
was reported merged and dashboard PR 43 open; no remote state was inspected.
Parent-owned new committed-head native matrix, full validation/CI and independent
re-review remain mandatory before the authorized cleanup merge.


## Fault completion ordering correction (2026-11-21)

Base HEAD: `538b96f61dd8b2e28e796a63aac9536f9df3455f`. The parent's fixed-count
native matrix remains **5 passed, 1 failed**; both hosted PR checks were green.
The retained failure is
`/tmp/opencode/author-review/lab-matrix-538b96f/serial-2/full.log`.
All six healthy checks passed, and the actual fault correctly produced the exact
failed terminal, fault-time evidence, zero and inactive receipts. The checker
then issued its single restart probe before automatic stop completion. The log
shows the start closing at `09:29:22,323`, automatic stop forwarding at `,324`,
the probe's busy refusal at `,337`, and stop success at `,341`. The busy reason
is correctly rejected; it is not an alternative acceptable stale-sensor reason.

The checker now inserts a bounded passive completion barrier after the exact
fault terminal and zero/inactive receipts, before sending the one refusal probe.
It requires the exact start's completed `failed` audit and matching retained
detail, and any automatic stop's original session identity and completed
`succeeded` audit. It reuses `session_stop_id()` and the existing six-second
budget / half-second send settlement policy (`send()` is bounded at .3 seconds).
It polls observations without retrying commands. Missing completion, failed or
misidentified stops, and exhausted budgets fail closed.

The lab alone observes in-flight `revoke()` calls with a delegating try/finally
wrapper installed during application lifespan startup. Its event-loop status
snapshot exposes pending revokes, active command IDs, owner presence and the
admission lock. An empty active set and completed start are insufficient while
a detached revoke can still create/audit a stop. The checker requires every
admission fact clear, then reads the audit after that snapshot so a just-finished
revoke's stop cannot escape validation. Stale odometry publishing remains off
through the barrier and exact refusal. Production files, guards, return values,
safety timing, healthy terminal policy and fault identity/detail checks are
unchanged.

Deterministic offline tests cover terminal-before-start-audit completion, a
detached revoke that creates its stop after the initial settlement interval,
delayed stop completion, failed/wrong-identity stops, missing completed rows,
wrong start detail/outcome, unrelated active commands, retained owner/admission/
revoke barriers and budget expiry. The production `revoke()` and `terminal()`
method bodies also execute against controlled send/audit events to reproduce
owner removal before stop creation. They are AST-loaded because the supplied
venv lacks optional server dependency `pydantic`; no dependencies were installed.
Workflow tests require no probe on barrier failure and exactly one probe after
completion, with the exact stale-odometry refusal still mandatory.

Validation uses `/home/yousef/Projects/repos/patrolbot-repo/.venv/bin/python -B`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=scripts:server`, pytest
`-q -rs -p no:cacheprovider`, with coroutine, unraisable and thread warnings
treated as errors. Focused lab/checker/evidence tests: **300 passed**. Complete
portable `scripts/tests`: **347 passed**. Python parsing, runner shell syntax
and `git diff --check` pass. This is an uncommitted local patch on the base SHA;
the parent owns independent re-review, a fresh fixed-count six-case matrix and
all 19 CI checks before any merge. No network, installs, Docker, ROS, robot,
remote operations or merge were performed. `RTK.md` remains absent. The previous
five native passes and one failure are retained and do not validate this patch.

## Full-review P2 correction (2026-11-21)

Base HEAD: `1c103f554b18d11d114c57eda776de09c19fcd1c`. Production
`RosTeleop.safety()` distinguishes source-stamp expiry from missing/expired
receipt: finite odometry at source age **505 ms**, receipt age **495 ms** ends
`TeleopSession` with `failed` / `Raw odometry is invalid or stale.` Receipt
expiry instead yields `Raw odometry is missing or stale.` The previous lab
checker accepted only the latter; later readiness cannot change an emitted
terminal. This was a lab acceptance defect, not a production safety defect.

The lab delegates the existing session safety/send callbacks and retains the
closing decision's robot/start identity, injection/safety/terminal timestamps,
raw finite odometry fields, source stamp and receipt time. Clock readings
bracket the delegated safety call to handle expiry during that call. The checker
requires the corresponding exact failed detail and matching retained terminal,
well-formed finite sensor evidence, non-future stamps/receipts, fresh
post-injection base evidence and post-injection safety/terminal times. It uses
this retained decision even when later readiness reports both clocks expired.
Missing/malformed odometry, unrelated reasons/identities, canceled/succeeded
outcomes and zero-publication-failure suffixes cannot pass. Stale sensors never
become ready. Production reasons, timing bounds, guards and outcomes are
unchanged; the six healthy checks, actual zero/inactive receipts, continued
producer and exact next-start refusal remain required.

Offline regressions execute the actual production safety/session code for both
expiry branches through `tick()` and `frame()`, including malformed/nonfinite/
future-stamped inputs, another safety failure, context mismatch and real zero
publication exceptions. Both legitimate fault terminals still fail the healthy
terminal policy. Additional checks cover malformed/cross-command evidence and
expiry inside the observation interval.

Validation used `/home/yousef/Projects/repos/patrolbot-repo/.venv/bin/python -B`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=scripts:server`, pytest
`-q -rs -p no:cacheprovider`, with coroutine, unraisable and thread warnings
treated as errors. Focused lab/checker/new evidence tests: **280 passed,
1 skipped**. Complete `scripts/tests`: **327 passed, 1 skipped**. The skip is
explicit: `/tmp/opencode/native-ci/jazzy-executors.py` is absent; the portable
queue-semantic check still runs. Python parsing, runner shell syntax and
`git diff --check` pass. No installs/network/ROS/Docker/hardware/production edits
or native acceptance runs were performed. Git worktree metadata is outside the
writable sandbox; the parent must commit, independently review, run native/CI
validation, and manage the PR/merge. `RTK.md` remains absent.

All following native evidence and earlier test counts are historical and do not
validate this correction.

## Final parent validation (2026-11-21)

The executable candidate `a21479d` passed **six fixed-count native cases**:
three serial and three bounded-load cases. Each passed all seven exact receipt
checks, supervisor exit validation and complete teardown, with zero retries.
Load was one additional busy CPU worker inside a container pinned to two CPUs
with a two-CPU quota. The deliberately stale-odometry case still required the
exact failed outcome/detail and zero/inactive subscriber receipts.

Validation used allowlisted packaged candidate sources, rebuilt current robot
interfaces and web adapter, and real installed mux/smoother. The cached public
static assets satisfied packaging only; this was not a frontend/browser build.
The smoother source-fixture SHA-256 matched current robot main:
`deef967e197037669d14ab145860ca4024ac0671c969b5538658ed96a5b87736`.
Containers were network-none/private IPC, non-root/read-only, with only dedicated
evidence writable. No production freshness/safety guards changed.

Evidence is retained in `/tmp/opencode/native-ci/lab-matrix-a21479d/`:
each case has the exact command/load settings, checker/native logs, config hash,
`run.json`, `teardown.json` and matrix verification. The earlier `d62b6b1`
six-case matrix is retained separately: all receipt checks passed, but every
case correctly failed premature-context teardown. These failures were fixed,
not suppressed or retried. Separate local Codex review of `a21479d` found no
actionable blocker. Complete portable `scripts/tests`: **256 passed**.

This is bounded software-lab evidence, not arbitrary-overload tolerance,
physical-stop acceptance, deployment authorization or GitHub maintainer approval.
The sections below preserve the earlier investigation and intermediate results.

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

## Review blocker fixes after d62b6b1 (2026-11-21)

The lab initializes rclpy with `rclpy.signals.SignalHandlerOptions.NO` so its
custom SIGTERM/SIGINT stop handlers remain installed. Context shutdown stays
explicit, after dispatch, tasks and node handles retire; background exceptions
remain failures. Readiness keeps one observation identity until delivery, even
when its answer times out. It never cancels a queued ROS observation, and polls
while one is outstanding return not ready. A failed or blocked node destruction
stops further node destruction and retains the context for supervisor containment.

Focused portable validation: **209 passed**, including the regression executing
the queue-dispatch block extracted from the supplied cached Jazzy executor,
custom-handler/init-option coverage, and blocked/false/raising node destruction.
The blocked-destruction check uses a shared 40 ms cleanup budget and verifies
remaining node handles and context are retained, with the surviving worker
reported as a failure.

Command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts:server /tmp/opencode/ci-audit/bridge-minimal-venv/bin/python -W 'error:coroutine .* was never awaited:RuntimeWarning' -m pytest -q -p no:cacheprovider -W error::pytest.PytestUnraisableExceptionWarning -W error::pytest.PytestUnhandledThreadExceptionWarning scripts/tests/test_teleop_lab.py scripts/tests/test_check_teleop_receipt.py`

This source revision has no native acceptance result yet. The parent's immutable
`d62b6b1` matrix and failed teardown evidence remain untouched. Parent validation
of the new source must separately check all seven exact receipts and teardown;
healthy failed outcomes remain rejected and deliberate stale odometry still
requires its exact failed outcome and detail. No retries or acceptance/freshness
changes were introduced.
