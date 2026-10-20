# Advanced tuning: code/unit readiness

Updated 2026-10-20. **Code and unit tests first; deployment/environment tests are
deferred by explicit operator instruction.** Latest operator status: SBC down.
Nothing in this document authorizes robot contact, deployment or motion.

Implemented dashboard paths include YAML-backed starting values, bounded
documented Nav2 input constraints, signed administrator mutations, desired-state
autosave, exact-revision apply/redeploy/restore dialogs, truthful saved/active
status, independent odometry gauges, session-bound speed policy and manual input
disarm/no-replay behavior.

The server signs the verified actor and canonical mutation with a private
robot-specific short-lived HMAC envelope. The web bridge validates its shape and
forwards it; only the core verifies the signature. It never receives a signing
key. Server/core keys must be provisioned separately, not generated or exposed
by the UI. `robot_tuning_write_v1` permits durable desired saves independently of
`robot_tuning_v1` activation capability and fresh safe-idle evidence.

The paired robot implementation supplies persistence, fixed-scope lifecycle
reload/readback, atomic navigator admission, manual producer holds, fresh-seed
and global-map gating, initialization and interrupted-job recovery. See its
`docs/tuning-code-readiness.md` for the complete implementation and deferred
gates. This is not a claim that the new ROS path has been compiled or verified
on a robot, or that tuning is enabled in a deployed generation.

Current unit evidence: **851 server tests passed** (2 external deselected),
**468 frontend tests passed**, **26 mock tests passed**. TypeScript static
checking (`tsc --noEmit`) also passed without starting an application. The focused web-bridge
tuning/teleop/normalizer unit run passed **101 tests** with 2 skips; a previous
full portable bridge run passed 221 with 3 skips. Core unit evidence is recorded
in its matching readiness document. All **8 contract artifacts** match locally.
No deployment/environment acceptance was run for this stage.

Shared interfaces are checked byte-for-byte by `scripts/check_tuning_contract.py`
(including auth and runtime contracts). Core changes must land before dashboard
parity can pass against main. Draft PRs run unit CI without mobile/browser
acceptance; ready-for-review starts that deferred acceptance workflow.

No deployment, image build, native ROS trial or physical test is part of the
current unit-only stage. Those later gates, followed by a separate operator
deployment go-ahead, remain mandatory. No unsafe bypass, higher speed ceiling or
fabricated live readback is permitted to claim readiness.
