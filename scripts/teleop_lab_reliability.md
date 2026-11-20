# Isolated teleop harness investigation (2026-11-20)

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
  outcomes remain unchanged. Failed terminals still fail immediately.
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
