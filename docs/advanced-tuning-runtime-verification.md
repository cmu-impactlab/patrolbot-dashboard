# Advanced tuning — runtime slice verification (uncommitted working tree)

> Superseded implementation status, 2026-10-20: guarded signed mutations, local
> initialization/recovery and fixed-scope reload/readback are now implemented.
> The earlier read-only-only/UnavailableBackend descriptions below are historical,
> not current code status. See `docs/tuning-code-readiness.md` for current unit
> evidence and the hard rule deferring deployment/environment tests.

Date: 2026-10-18. Scope: the **runtime tuning slice** that exists only as
uncommitted working-tree state on `feat/advanced-tuning-contract`. This document
records what the working tree delivers, what stays deliberately disabled, and the
limits of the local verification.

## 0. Status and boundary

- **No commit, push, merge, deploy or release was performed for this slice.**
  The branch equals `origin/main`; there are no tuning commits ahead of it.
- **No robot, SBC, SSH, live ROS graph, DDS-wide operation or physical motion
  command was used.** Native ROS tests used isolated local containers with
  synthetic inputs only. No physical acceptance was performed or claimed.
- The checks in §3 were run **locally** as working-tree verification. They are
  not CI receipts and they do not prove runtime behaviour on the robot.
- This is **not** end-to-end writable tuning. The writable activation backend
  remains unavailable and the write capability is deliberately never advertised
  (§2). Do not read anything here as a commissioned tuning feature.
- The historical records remain authoritative for their dates and are **not**
  updated by this document: `docs/advanced-tuning-inventory.md`,
  `docs/advanced-tuning-threat-model.md`, `docs/advanced-tuning-slice1-verification.md`
  and `docs/advanced-tuning-native-evidence.md`.

## 1. What the working tree delivers

All paths are repository-relative and **uncommitted** unless noted.

- **Shared contract tooling** — `contracts/robot-tuning.*.json`,
  `contracts/validate_robot_tuning.py`, `contracts/test_robot_tuning.py`,
  `contracts/README.md`; `scripts/check_tuning_contract.py`,
  `scripts/tests/test_check_tuning_contract.py`; `Makefile` targets
  `test-tuning-contract` / `test-tuning-contract-parity`;
  `.github/workflows/tuning-contract.yml`.
- **Server runtime** — HTTP surface `server/app/api/tuning.py`; broker and
  strict validation `server/app/tuning/{broker,validation,authz,errors,__init__}.py`;
  transport wiring in `server/app/telemetry/hub.py`,
  `server/app/websocket/{ui_gateway,robot_gateway}.py`; protocol in
  `server/app/protocol/messages.py`; router mounted in `server/app/main.py`.
- **Dynamic teleop speed policy** — `server/app/commands/tuning_policy.py`,
  `server/app/commands/teleop.py`; frontend limits/ramp in
  `frontend/src/lib/teleop.ts`, `frontend/src/components/ManualDrive.tsx`,
  `frontend/src/stores/teleopStore.ts`.
- **Robot bridge adapter** — `ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/tuning.py`,
  wired in `bridge_node.py`.
- **Measured-motion telemetry** — `normalizers.normalize_motion` in
  `ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/normalizers.py`;
  documented in `shared/schemas/protocol.md`; consumed in
  `frontend/src/stores/telemetryStore.ts`, `frontend/src/lib/motion.ts`,
  `frontend/src/components/SpeedGauges.tsx`.
- **Frontend editor** — `frontend/src/components/TuningPanel.tsx`,
  `frontend/src/stores/tuningStore.ts`, `frontend/src/lib/tuning.ts`,
  `frontend/src/api/tuning.ts`, mounted in `frontend/src/pages/AdvancedPage.tsx`.
- **Native lab harness** — `infrastructure/tuning-lab/`,
  `scripts/tuning_lab.py`, `scripts/check_tuning_receipt.py`,
  `scripts/run_tuning_lab.sh`, `scripts/tests/test_tuning_lab_tools.py`.
- **Tests** — `server/tests/test_tuning_{api,broker,validation}.py`,
  `server/tests/test_teleop_tuning_policy.py`,
  `server/tests/test_motion_tuning_integration.py`, `server/tests/tuning_fixtures.py`;
  `ros2_ws/src/patrolbot_web_bridge/test/test_tuning_adapter.py`;
  `frontend/src/{lib,stores,components}/*tuning*.test.*`,
  `frontend/src/lib/motion.test.ts`,
  `frontend/src/components/SpeedGauges.test.tsx`,
  `frontend/e2e/tuning.spec.ts`.

The dashboard wires the transport but does not persist tuning revisions: durability is delegated
to the robot side (`server/app/tuning/broker.py`). The robot-side service, store
and activation code (`patrolbot_bridge/tuning_*.py`, `patrolbot_interfaces/srv/RobotTuning.srv`)
is likewise **uncommitted** in the paired repository. Local tests exercise the
foundation; deployed behavior and full activation remain unverified.

## 2. Deliberately disabled / unfinished

- **Writes are gated closed.** The robot bridge advertises
  `robot_tuning_read_v1` (only while the core service is reachable) and
  `robot_tuning_policy_v1`, but **never `robot_tuning_v1`**; the server broker
  refuses mutations without it. Save/apply/redeploy/restore exist in the UI and
  transport but are unreachable end to end.
- **The core ROS boundary is explicitly read-only.** It accepts only `read`
  and `status` before dispatching into the foundation service. A DDS caller's
  forged administrator actor cannot invoke save/apply/redeploy/restore. Thirteen
  targeted tests exercise the actual callback, including duplicate-key and
  malformed-payload refusals. Hiding a dashboard capability alone is not auth.
- **The curated AMCL / Nav2 editor is read-only**, with explicit
  restart-required labels; no hot-apply behaviour is proven.
- **The dynamic cap is trusted and fail-closed.** A robot that advertises a
  dynamic policy with a missing, stale, malformed or session-mismatched value is
  refused rather than handed the old higher fixed default. Legacy
  `web_teleop_v1`-only robots keep the fixed commissioned envelope in
  `legacy_v1` mode.
- **No browser-WebSocket mutation channel.** `/ws/ui` refuses any `tuning.*`
  frame and re-resolves the session per message; tuning uses the authenticated
  HTTP endpoints.
- **The local production image packages the server tuning code and registry.**
  Import smoke testing proves packaging, not activation or deployment. Offline
  validator/test dependencies are not added to the production runtime.

## 3. Local verification run 2026-10-18

Run by the test owner on the local working tree. Counts are as reported; exact
artifacts are not committed here.

| Check | Result |
| --- | --- |
| Server suite (`make test-server`) | 807 passed; 2 external deselected; 1 warning |
| Mock robot (`make test-mock`) | 26 passed |
| Web bridge portable (`make test-bridge`) | 206 passed; 3 skipped |
| Frontend Vitest (`make test-frontend`) | 428 passed |
| Frontend `tsc --noEmit` and `vite build` | passed |
| Frontend bundle budget (`npm run check-bundle`) | 170.5 kB gzip — under the 175 kB budget |
| Frontend `npm run check:e2e` typecheck | passed |
| Contracts + scripts tests (including runner regressions) | 172 passed |
| Vendored contract parity (`scripts/check_tuning_contract.py`) | all 6 artifacts byte-identical to the robot checkout |
| Core (robot) contracts + tuning tests | 280 passed on host Python 3.14 and in isolated Python 3.12 |
| Production image build + import smoke (local only) | passed; image `sha256:f82861f0fbd6d0826738568853814530b57fb500a92bdba6d0c120f1d745720b` |
| Initial native ROS bridge suite | 489 passed; 1 skipped; lint failed in this initial run |
| Final focused tuning/manual/epoch tests plus full-package flake8/pep257 | 175 passed after fixes |
| Native dock / portable navigation suites | 163 / 196 passed |
| Six-project Playwright, isolated network-none container | 732 passed; 6 skipped |
| Rebuilt isolated tuning lab and receipt checker | 15 passed; no live-Pi or physical acceptance claim |
| Forged ROS actor / read-only callback boundary | 13 passed |
| Real EpochRuntime tuning-hold regressions, native ROS messages | 4 passed; guard-removal probes fail as expected |
| Next-slice bootstrap preview, no filesystem mutation | 16 passed; targeted flake8/pep257 passed |

The fresh native receipt is recorded in §10 of
`docs/advanced-tuning-native-evidence.md`, including exact image IDs and receipt
hash. Its AMCL/RPP classifications remain restart-required. It does not verify
the new persistent activation transaction or authorize writable tuning.

Per the operator's direction, subsequent development uses focused authorization,
persistence and motion-hold regressions rather than repeatedly expanding the
broad browser/native matrix. Existing unresolved checks remain visible.

### YAML starting values and documented input rules

The operator clarified that initial values must be the current robot YAML, not
Nav2 library examples, and edits must satisfy the relevant ROS/Nav2 domains.
The source is `nav2_params.yaml` plus the final `smoother.yaml`; existing saved
state is not reset. See `docs/tuning-parameter-rules.md` for versioned references.

The shared registry now refuses `sigma_hit=0` and RPP approach minimums
`<= 0.01`, accepts documented quantile `pf_z=2.33` without pretending it is a
probability, and binds the approach-scaling budget to current costmap geometry.
The ordinary-drive hard envelope is unchanged. Unit checks passed: core contract
and documented-domain tests **193**, server validation/domain tests **95**,
frontend domain tests **8**. These checks use no deployed environment or robot.

The completed source-default wiring uses `tuning_spec.yaml_starting_values()`
for initialization, bootstrap preview and uninitialized inspection. Missing or
invalid YAML values are refused rather than replaced by registry defaults.
Existing saved values take precedence; corrupt saved revisions refuse inspection
instead of falling back to YAML under a saved revision number. A focused core
unit run covering YAML defaults, service behavior and documented domains passed
**56 tests**. Server tuning/auth/policy unit checks passed **217 tests**.

Frontend save-status helpers distinguish unsaved initial values from saved state
and require known matching readback before a saved-active label. Refresh does
not carry an old saved-active phase into an unsaved YAML snapshot; the focused
store unit run passed **12 tests**. The final focused frontend run passed
**77 tests** across the tuning panel, range helpers, documented bounds and store.
It verifies exact numeric input preservation, exclusive slider endpoints, no
invented range for unbounded `pf_z`, no save on mount, and group-scoped debounce.
These are unit results, not browser or deployed-environment receipts.

## 4. Explicitly not verified / unresolved gates

These are open and must not be read as passes:

- **Final full-suite release gate remains.** Initial full-suite lint failures
  are fixed and the focused 175-test run passes, including full-package lint.
  A complete rebuild/regression run on the eventual committed release candidate
  is still required before merge; this is not a claim that the initial run passed.
- **Writable activation backend is unavailable.** Writes are not commissioned;
  no end-to-end save/apply/redeploy/restore is proven.
- **Robot-side persistence/activation is uncommitted and unproven** (atomic
  manifest, fsync/rename/crash/disk-full gates, ACTIVE-vs-DESIRED boot).
- **CI parity needs external setup.** The `tuning-contract.yml` parity job needs
  a pushed canonical ref carrying `contracts/robot-tuning.*` and a cross-repo
  read token, neither of which is provisioned.
- **Production bootstrap is not yet supported.** Do not expose an initializer
  that seeds packaged defaults over unknown lower deployed settings. Safe
  baseline capture/migration and local initialization are the next slice. The
  core now provides `config_bootstrap` / `tuning_bootstrap_preview` as an honest
  read-only preflight, with `initialization_supported=false`; it is not an
  initializer or a claim that the next slice is complete.
- **Physical acceptance** and live Pi versions/effective limits remain UNKNOWN;
  the SBC is operator-reported down.
- **Nothing above is committed.** The whole runtime slice, the contract tooling
  and these documents are working-tree state pending review and commit.

## 5. Reproduce (relative paths)

```sh
# Dashboard application suites
make test-server
make test-mock
make test-bridge
make test-frontend

# Contract parity and vendored self-tests (dev-only deps; never fetches)
make test-tuning-contract
PATROLBOT_CORE_REPO=../patrolbot-repo make test-tuning-contract-parity
python scripts/check_tuning_contract.py --core-repo ../patrolbot-repo

# Direct runners
python3 -m pytest scripts/tests -q
cd server && python -m pytest -q
cd ros2_ws/src/patrolbot_web_bridge && python -m pytest test -q
cd frontend && npx vitest run

# Native lab (isolated; regenerates a receipt, not a release acceptance)
BASE_REF=<patched-nav2-image-ref> EVIDENCE_DIR=/tmp/tuning-lab-evidence \
  bash infrastructure/tuning-lab/run_host.sh
python3 scripts/check_tuning_receipt.py --self-test
```

`TUNING_PY=/path/to/dev-venv/bin/python` selects an isolated interpreter for the
contract targets; `PATROLBOT_TUNING_REGISTRY` overrides the catalog path used by
the server at runtime.

## 6. Relationship to the other records

- `docs/advanced-tuning-inventory.md` and `docs/advanced-tuning-threat-model.md`
  are the dated 2026-10-17 slice-1 records; each now carries a short, clearly
  labelled 2026-10-18 current-runtime pointer to this document rather than being
  rewritten.
- `docs/advanced-tuning-slice1-verification.md` is a historical verification
  snapshot and stays dated.
- `docs/advanced-tuning-native-evidence.md` preserves the historical measurements
  and appends the 2026-10-18 revalidation in §10; neither is live robot evidence.
