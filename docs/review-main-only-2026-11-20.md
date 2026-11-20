# Fixes-only review backport to main

Base: `18a4ea195b06c47f48256788fbf3d5c8f73dde66` (`origin/main`).
Branch: `fix/review-main-only-20261120`.
Paired robot base: `c661ed4ce086cc78afde7dfa47e81c143955f802`.

## Included scope

- Pending navigation Stop and delayed-acceptance cancellation ownership.
- Current credential/role checks and safe manual-input expiry cleanup.
- Emergency disable/cancel/end-input during reset.
- Protected command replies under bounded transport pressure.
- Unknown-motion ownership retained until real terminal evidence or authorized
  recovery, preserving current-main fault/reset recovery behavior.
- Passive recording does not block healthy manual drive.
- Indexed UTC battery history, additive migration and effective retention.
- Verified unused-helper cleanup and meaningful regressions.

The paired robot removes all unsupported gimbal implementation and preserves
main's newer manual-undocking/obstacle-recovery and boot reset-directory fixes.

## Explicitly deferred

The parked Save/Apply commissioning feature is excluded. No new Save/Apply UI
workflow, tuning schema/runtime changes, signed-refusal headers, producer
maintenance/lease protocol, host service or Nav2 native patches are imported.
Main's existing tuning implementation remains unchanged. Save/Apply-specific R7
fixes and the later malformed Save-status fix remain on the feature-based branch.

## Acceptance

The exact candidate must pass its own combined suites and a feature-exclusion
audit before merging. Previous broad-integration counts do not apply. No push,
deployment, robot connection, physical command, feature enablement or physical
commissioning is authorized. Completed evidence and independent review will be
recorded here before the main merge.

## Source and feature-exclusion audit

Backport commits: `c36f360` (recording/manual input and isolated test tooling),
`1f603d5` (command/adapter authority, lifecycle, transport, persistence and tests).
The parent's separate audit confirms all contracts, server tuning/API/startup
code, frontend tuning implementation and robot-side tuning adapter remain
identical to current main. Parked WIP `a189858` is not an ancestor.

The robot counterpart preserves current-main manual-undock/obstacle recovery and
boot reset directory changes. Its interface diff only removes Gimbal messages.
Shared tuning/teleop artifacts, map validator and map catalog files match across
the paired candidates. No feature-based integration branch is being merged.

## Completed candidate validation

- Parent rerun server suite: 1,052 passed, two external tests deselected; existing
  Starlette/httpx deprecation warning remains.
- Final portable web adapter: 340 passed, three environment skips. Explicit candidate
  server and bridge `PYTHONPATH` prevents imports from the original feature tree.
- Frontend: 498 unit/component tests, application/browser/preview typechecks,
  production build and bundle policy passed (174.4 kB gzip against 175 kB).
- Paired robot: 1,293 Python tests plus six subtests and all five ARIA-free C++
  suites passed, including 13,084 undock checks.

Full six-project browser matrix: 738 passed, six skipped, with a saved passing
result and no failed tests. Tested frontend tree:
`523036fce71c9b5f808c31dfc1562fd25e488eba`. Mock robot: 26 passed.

## Native functional evidence

All six paired ROS packages built from candidate source in a disposable
network-isolated container. Core bridge: 784 passed/one gated skip; dock manager:
162 passed; navigation: 206 passed. Final native web adapter: 341 passed, two server-app
integration cases excluded only from the ROS image because its server dependency
stack is intentionally absent. Those cases run on the host with explicit
candidate imports.

No host devices, production volumes, robot endpoint or Docker socket were mounted.
Lint was separate, old pytest nose discovery disabled. Existing rclpy teardown
and missing pytest-timeout configuration warnings remain. PostgreSQL execution,
production image acceptance and physical commissioning are not claimed.

## Independent review gate

Local Codex found one P1 in the newly adapted Undock exception path: a pending
Stop could miss cancellation after accepted goal result-subscription failure.
Fixed in `cc32d88` with eleven regression cases. Deferred cancellation reaches
the accepted handle even when result tracking fails, without duplicate cancels
or discarding ownership. Cancellation response callback setup errors also
preserve result tracking/Stop waiters instead of escaping the executor.

Final host adapter (340), native adapter (341) and server (1,052) reruns passed.
Codex reviewed `1f603d5..cc32d88` read-only, confirmed the P1 closed and found no
remaining actionable issues in that delta. The separate robot review also found
no actionable issues. Reviews are static, not physical commissioning evidence.

## Main merge acceptance

Current origin main was re-fetched and remained at the reviewed base. Both
candidates merge cleanly and pass the renewed feature-exclusion/parity audit.
Accepted for local fast-forward merges only after these completed checks. No
push, deployment, runtime enablement or parked Save/Apply merge is authorized.
