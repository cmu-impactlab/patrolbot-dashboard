# First CI remediation tranche — 2026-11-20

This isolated candidate starts from fixes-only local main `41da64e`. It does not
merge the separate bloat cleanup or deferred Save/Apply commissioning feature.
No robot runtime, server admission policy or production dependency constraint
is relaxed.

## Changes

- The host bridge matrix reuses the server dev dependency contract and explicitly
  imports server/bridge source from the candidate checkout. The supported Python
  3.11–3.14 matrix is preserved.
- Native bridge manifests declare asyncio/timeout test dependencies, not a
  dependency on the server's runtime. Native-only selection is documented.
- Constrained server tests use an executable, regression-tested source-copy
  helper. It supplies bridge source and the exact mock-launcher fixture without
  copying developer environments, frontend dependencies or private certificates.
- Protocol tests invoke the real TypeScript compiler on canonical fixture-derived
  literals. Positive variants and malformed payload cases test the actual
  interfaces, not assertions applied to `JSON.parse`'s unchecked `any`.
  Nullable optional command fields now match the authoritative Python model.
- Teleop receipts match the exact robot, session and audited stop identities and
  require appropriate outcomes. Ambiguous audit correlation fails closed.
  Producer-loss handles either watchdog ordering without accepting an unrelated
  stop; F710 cancellation and explicit release retain stricter requirements.
- Three database-backed teleop tests refresh synthetic hardware evidence after
  setup. Deterministic aged-setup coverage and explicit post-audit cancellation
  assertions preserve real stale-data safety gates.

The wire protocol does not expose a parent-session identifier for generated
stops. The isolated checker therefore requires a sole intervening audited stop
with matching operator and generation identity, rather than claiming a generic
cross-session correlation mechanism.

## Validation

- Clean Python 3.12 compatible environment: **1,050 server tests passed**, with
  five documented optional-PostgreSQL skips; **340 bridge tests passed**, with
  three ROS/NumPy environment skips; **122 script tests passed**.
- Minimal contract-only environment, without server source on `PYTHONPATH`:
  **258 script/contract tests passed**.
- Fresh offline frontend install: **246 packages**, **573 unit tests passed**,
  production build, browser-test and preview typechecks, and bundle checks passed.
  The install used `--ignore-scripts`; no exhaustive browser-suite rerun is
  claimed for this test/type-only change.
- The new copy helper also passed **eight focused tests** using the separate
  cleanup candidate's settings/cross-layer tests, without merging that candidate.
- The unchanged complete constraints-check script passed with an offline Python
  test image and a container-isolation wrapper: both runtime dependency sets
  matched, both production Dockerfiles retained constraint use, and the copied
  server suite passed **1,050 tests**, with three optional-driver skips and two
  explicitly excluded external-service cases.
- Independent local Codex review found and closed the competing-watchdog issue;
  follow-up review found no remaining actionable P1/P2 findings.
- Final serial isolated software-lab execution passed **all six checks** using
  the current checker/server/adapter source, rebuilt current interfaces and
  cached native mux/smoother binaries. Hardware was synthetic;
  `physical_acceptance` remained false.

Evidence is retained in `/tmp/opencode/ci-fixes/`, including
`dashboard-compatible-final.log`, `constraints-exact-final.log`,
`constrained-suite-exact-final.log`, `minimal-contract-final.log`,
`frontend-tests.log`, `cleanup-copy-smoke.log`, `teleop-lab-serial-final.log` and
`review-followup-result.md`.

## Validation limitations

Docker external DNS is unavailable. Clean Python images were built offline from
genuine cached wheels, including a production-constrained runtime set. This
does not claim the latest package-index resolution or all four hosted Python
matrix cells were executed.

An initial isolated lab run passed all six real-ROS subscriber/arbitration
checks. A later concurrent-load run correctly failed on a session's explicit
failed terminal caused by stale synthetic hardware readings. The failure was
preserved, not accepted as successful cancellation or hidden by retries.
After concurrent test load ended, the final serial run passed all six checks.
This does not establish a load-independent or production-image acceptance gate.
These software labs use simulated hardware and do not establish physical safety.

Existing upstream async-loop-scope/TestClient deprecation warnings remain
visible. Optional PostgreSQL, NumPy and ROS skips are not new coverage claims.

## Scope boundaries

No new automatic browser/native/production-image gate, Pages account change,
target-architecture build, long soak or physical commissioning is included.
No main merge, push, workflow dispatch, robot connection or deployment was
performed. Separate bloat worktrees remain unchanged and unmerged.
