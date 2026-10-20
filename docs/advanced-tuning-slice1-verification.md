# Advanced tuning: slice 1 verification

Date: 2026-10-17. Scope: offline contract and source inventory only.

## Baseline and boundaries

Both working trees were clean before work. A fetch of `origin/main` confirmed:

- `patrolbot`: `37a4c1a99e912d2779a21a98d8bfef177c2e86c3`
- `patrolbot-dashboard`: `28af8dc95a447494e3c09d05b24b51927f7442c6`

Work is on `feat/advanced-tuning-contract` in each repository. No robot was
contacted; no deployment, ROS graph operation, or physical motion test occurred.
The tuning capability is not advertised and there are no tuning endpoints or
writable controls. JSON contract examples are not commands sent to a robot.

Delivered: a shared offline schema, 37-entry candidate/protected registry,
103 fixtures, portable validator/tests, core source-consistency tests, parity
tooling, CI, inventory and threat model. This is **slice 1 only**, not a runtime
tuning feature. No commits, pushes, merges or deployments were performed.

## Existing regression checks

Local results before contract integration:

| Check | Result |
| --- | --- |
| Dashboard `make test`: server, non-external | 596 passed; 2 external deselected |
| Mock robot | 26 passed |
| Web bridge, portable tests | 198 passed; 3 skipped |
| Frontend Vitest | 372 passed |
| Frontend `npm run build` | Passed (TypeScript and Vite) |
| Frontend `npm run check-bundle` | Passed; 158.1 kB gzip / 175 kB budget |
| Frontend `npm run check:e2e` | Passed |
| Core web teleop/manual/joystick portable checks | 94 passed |
| Core static safety, reverse policy, controller feedback, odom validation, undock admission | 177 passed |

The joystick runtime fixture initially failed 38 setups without ROS because its
module stubs omitted `std_msgs.msg.Bool`. A minimal test-only `Bool(data=False)`
stub fixes this; no production code or assertions were changed.

The default Playwright command could not clear a pre-existing root-owned
`frontend/node_modules/.vite/deps` cache. Review used a temporary Vite config and
cache under `/tmp/opencode`, leaving that cache and repository configuration
untouched. All six projects were attempted: bundled Chromium was absent and
WebKit could not load `libicudata.so.74`. These are environment blockers, not
passing browser receipts. A separate run uses `/usr/bin/chromium` for the three
Chromium projects: **366 passed** (android-phone, android-tablet and
desktop-chromium). This includes the existing Advanced-tab no-arm, exact-zero
release and cross-tab isolation tests. WebKit (iphone, ipad and desktop-webkit)
remains environment-blocked; no system-library changes were made to bypass it.

The first local production-image build (`infrastructure/Dockerfile.server`,
without starting an application container) was blocked by Docker build-network
DNS resolving PyPI while installing the existing `setuptools>=68` dependency.
A local retry with build-only `--network host` succeeded, without changing Docker
configuration. After final contract vendoring, the complete image build passed
again, including the `COPY contracts/ /contracts/` frontend build input. Final
local image: `patrolbot-dashboard:tuning-contract-review`, ID
`sha256:0c6e50af5460fde57fdf1403eddf81813dc7f90ec6b9be1efcc80885ec5e730e`.
The tuning files remain development-only: no runtime loading or service was
added to the final image. The image was not deployed or started as an application.

## Final contract verification

| Check | Result |
| --- | --- |
| Core portable + source-consistency tests, Python 3.14 | 183 passed |
| Independent review probes, Python 3.14 | 23 passed |
| Standalone validator | Passed; 37 parameters, 103 fixtures |
| Dashboard `make test-tuning-contract`, Python 3.14 | 12 parity-tool + 136 shared tests passed |
| Local cross-repository byte/SHA-256 parity | All 6 shared artifacts match |
| Python 3.12 isolated core contract/review/safety regression run | 481 passed |
| Python 3.12 isolated dashboard contract/parity-tool run | 148 passed |
| Workflow structure checks and `git diff --check` | Passed |

The Python 3.12 test containers used `--network none`, a read-only root and source
mount, dropped capabilities, `no-new-privileges`, and temporary `/tmp`. They ran
portable/static tests and mocked ROS modules only, not native ROS nodes or DDS.
The combined 481 checks include the 183 core contract tests, 23 independent review
probes and 275 existing regression checks; counts across rows overlap.

Review corrections include incoming dependency-edge validation, finite/overflow
and resource bounds, strict integer revisions, independent session epochs,
complete authoritative validation context, immutable restore targets, truthful
unknown-runtime/durable-pending state, and rejection of external schema refs.
Validation never authorizes an operation: `activatable` and
`authorization_evaluated` are always false. Current-context comparisons are not
a transaction lock, durable deduplication or a store implementation.

Reproduce the tracked checks from the relevant repository root:

```sh
# Robot repository (dev-only environment):
python -m pip install -r contracts/requirements-test.txt
python -m pytest contracts/ -q
python contracts/validate_robot_tuning.py

# Dashboard repository (TUNING_PY may select an isolated dev environment):
make test-tuning-contract
PATROLBOT_CORE_REPO=/path/to/patrolbot make test-tuning-contract-parity
```

Local Python versions tested were 3.12 and 3.14. The configured CI matrix has not
been run on GitHub; no branch was pushed. Cross-repository Actions parity is an
explicit dispatch against a matched core ref; private-repository read access is
a maintainer gate, not assumed from the dashboard's default token.

## Remaining release gates

This is not robot acceptance. Installed/live package versions, runtime node names,
active limits, native parameter side effects and safe activation behavior remain
unverified. Registry source defaults are not live readback. No live mutability
claim is permitted from a static test or a successful parameter-service response.

Later slices must implement and test persistence, existing-owner serialization,
all-producer enforcement, runtime readback and recovery before exposing writes.
The draft dependency graph does not model every web/F710/SBC producer, and the
snapshot's boolean `pending` does not model genuinely unknown manifest status.
Resolve these before runtime integration; never turn missing evidence into
permission to activate or a false “no pending changes” claim.
Physical acceptance and read-only deployment inventory require separate operator
authorization. WebKit and unrun native checks remain explicit release
gates rather than inferred successes.
