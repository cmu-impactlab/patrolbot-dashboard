# Reviewed dashboard cleanup — 2026-11-21

Implemented locally against `5946892bdea249a79c12ce02d463bb23ae009d30` in the
current-main worktree. Read the dashboard/core audit reports and reviewed
`git diff 371abb4^..43b7d91`; carried forward its supported removals, with the
configuration, isolation, map tests and historical-verification corrections
below. This is candidate evidence, not reviewer approval, CI acceptance or a
merge/deployment record. The referenced `RTK.md` was absent from the checkout
and checked parent paths.

## Removed and retained

* Retired the reachable but explicitly unsupported PostgreSQL backend and
  `asyncpg` extra. Removed its dedicated tests, the three PostgreSQL-only mocked
  migration/retention tests and their exclusive imports, the `external` marker,
  Make target and both obsolete exclusion filters. SQLite executable AST is
  identical to base: schema, migrations, UTC battery indexes, retention,
  recordings and public CSV/ZIP/query behavior remain intact. No data conversion
  or fallback from nonempty URL configuration is provided.
* Deleted exactly `cmuq-floor1/first_map.{pgm,yaml}` and
  `cmuq-floor2/second_map.{pgm,yaml}`: **20,967,629 bytes** of duplicate map
  packaging. Both canonical catalog floors, pixels, transforms and identities
  remain unchanged. Dockerfile.server and both Compose defaults now select
  `/maps/catalog/second_map.yaml`. Custom paths and missing-path fallback remain
  supported. External overrides naming the deleted paths must be updated.
* Removed the unused Tooltip manifest entry and exactly its lock node plus the
  exclusively orphaned visually-hidden node: **297 to 295 package nodes including
  the root**. Every surviving non-root lock entry is identical, the root agrees
  with package.json, and no surviving dependency points to a removed node.
  This is installation-graph evidence, not a measured bundle-size claim.
* Removed only the confirmed unused helpers/exports `resolve_ws_user`,
  `validate_actor_shape`, `tuningValuesEqual`, `COMMAND_COPY`, `entriesForGroup`,
  `readRequest`, `motionAgeMs`, their exclusive imports, the two
  `.restore-pose-facts` rules and the three orphan field rules
  `.field-disabled-reason`, `.field-unknown`, `.field-pending`. Searches covered
  runtime, lazy/preview and test consumers. Retained `valuesEqual`, public types,
  registered callbacks, dynamic styles and test-only helpers (`rejectionReason`,
  `readMotionSnapshot`, `BADGE_LABEL`, `statusRequest`, `outcomeLabel`, `build_zip`).

## Exact URL rejection surface and isolation

The supported reject-only names are `PATROLBOT_DATABASE_URL` and unprefixed
`DATABASE_URL` in environment/dotenv/secrets configuration, case insensitive.
Constructor spellings are `database_url`, `DATABASE_URL`, and
`PATROLBOT_DATABASE_URL`, likewise case insensitive; the first two differ only
by case. No URI, PostgreSQL-specific or other invented aliases are recognized.

`Settings` inspects raw constructor inputs before Pydantic normalizes them.
`settings_customise_sources` checks source output, raw process environment,
dotenv values and URL-named secret files before normal settings validation.
Regular dotenv files are also inspected separately with original key spelling:
case folding or a later file's empty value must not conceal a nonempty URL.
Every nonempty value, including whitespace, unknown schemes and malformed
constructor containers/numbers, raises a fixed RuntimeError before application
construction can open a database. The error contains no input values or URL
credentials. Empty/unset URLs remain allowed. Non-database source precedence
and production validation are unchanged.

Removed the unused module-global `Settings()` instance; actual `create_app()`
startup still constructs and validates settings. The frontend mock launcher
filters both URL names and the deployment database path case insensitively,
sets its disposable path, and disables dotenv before importing the application.
Fresh-process tests exercise the real child command with only uvicorn.run
stubbed, so they cannot start a server. Test fixtures clear all case variants
of deployment settings, including unprefixed DATABASE_URL.

## Historical release verification

Historical manifests are byte-for-byte unchanged. `verify_release --source-ref`
reads exact Git blobs at the named revision; it never substitutes current map
paths. Tests cover missing paths, exact hash mismatches, missing refs and an
actual Git blob against a different temporary working tree. That test uses the
available HEAD and does not require a historical branch in shallow CI checkouts.
Separately, the local `deployed/dashboard-20261015` ref matched **all 62 entries**
of the observed manifest with `--live` omitted. No remote inspection occurred.

## Validation obtained and limits

Used `/home/yousef/Projects/repos/patrolbot-dashboard/server/.venv/bin/python -B`
(Python 3.14.10, Pydantic 2.13.4), `PYTHONDONTWRITEBYTECODE=1`, pytest
`-p no:cacheprovider --capture=sys`. The parent-prepared venv lacked Pydantic
when checked; nothing was installed or tests weakened to compensate.

| Check | Fresh result |
| --- | --- |
| Portable server shard, 20 files excluding TestClient/persistence/isolation files | 472 passed |
| Database configuration + fresh-process isolation, `-k 'not sqlite_factory'` | 230 passed, 4 deselected |
| Map catalog packaging/identity, `-k packaged` | 2 passed, 5 deselected |
| Complete server collection | 1,288 tests collected |
| Complete script suite | 350 passed |
| Complete mock suite | 26 passed |
| Complete web-adapter suite, run separately from mock | 339 passed, 3 skipped, 1 failed: sandbox denied Unix-socket bind in test_transport_bounds_reply |
| Contract schema suite | Collection blocked: supplied venv lacks jsonschema; no tests ran |
| Map catalog/validator parity with local core | Passed, both floors |
| Shared tuning artifacts against local core | All 8 byte-identical |
| Historical release source check | 62 entries matched |
| Static checks | 152 Python files parsed; shell syntax and git diff --check passed |

The three completed server shards represent **704 distinct passing tests**,
not a full-server pass. Full server and focused TestClient runs timed out waiting
in AnyIO's cross-thread portal; an existing API-parameter test with unchanged
executable code reproduced the timeout. SQLite migration/retention shards also
timed out waiting for aiosqlite's worker-thread completion. These observations
do not establish the precise environmental cause; persistence and HTTP/map
runtime regression acceptance remains pending. The four new SQLite startup
cases and five map runtime cases are still present for the parent to run.
Map runtime tests cover both catalog YAML overrides, the default Floor 2,
a real custom YAML/PGM pair and missing-path fallback.

An initial combined mock/adapter invocation encountered duplicate pytest module
names; separate suite invocations resolved collection. Existing Starlette/httpx
deprecation warnings remain. No application workaround was added for the test
environment.

## Parent-review URL-source correction at `907de51`

**P1, reproduced by parent:** a nonempty `PATROLBOT_DATABASE_URL` secret file
survived the unchecked standard secrets source, allowing `create_database` to
select SQLite. This correction wraps that source's public `__call__`, checks its
output, and returns its values unchanged in the original fourth source position.
Only URL-named files receive an additional raw check: Pydantic strips whitespace
and selects one case spelling/directory, which can hide other nonempty inputs.
All configured secret directories are checked, including lower-priority ones.
The fixed redacted RuntimeError text is unchanged; database/factory code and
production validation are untouched. Non-database secrets retain normal source
precedence, parsing and values.

Installed **pydantic-settings 2.15.0** also folds constructor key case before the
custom source hook. The existing empty-canonical/nonempty-alias regression
failed on this version, so raw constructor values now pass through the same
URL-specific rejection helper before normal initialization. Other constructor
arguments pass unchanged to BaseSettings. Non-string, non-None URL inputs are
refused without comparing arbitrary input objects against empty values.

Fresh checks used `/tmp/opencode/bloat-cycle/venv/bin/python -B`,
`PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider --capture=sys`:

| Check | Result and distinct log under `/tmp/opencode/bloat-cycle/` |
| --- | --- |
| Configuration + isolation, `-k 'not sqlite_factory'` | 343 passed, 4 deselected in 22.31s; `dashboard-parent-review-config-accepted.log` |
| Complete portable script suite | 350 passed in 3.45s; `dashboard-parent-review-scripts-verified.log` |
| Static checks | Three changed Python files parsed; `git diff --check` passed; `dashboard-parent-review-static.log` |

Regressions cover both URL names/case variants in all four standard sources,
whitespace/unsupported schemes, malformed constructor inputs, empty aliases,
case collisions, multiple secret directories and higher-priority empty values
hiding nonempty secrets. Exception repr/formatted tracebacks exclude synthetic
credentials; disposable database paths remain absent. Non-database secret,
dotenv, environment and constructor precedence is exercised alongside the
existing fresh-process fixture/mock-launcher isolation checks.

The first focused run logged 305 passed and 36 failed in
`dashboard-parent-review-config.log`: one exposed the 2.15 constructor-folding
gap; 35 were an overbroad traceback assertion matching the library identifier
`file_secret_settings`. The assertion now uses a distinct credential marker.
Intermediate passing logs are retained separately from final evidence.

Full server/persistence, frontend/browser, native and CI acceptance remain with
the parent. The earlier worker-thread/socket limits above were not retried or
assigned a guessed cause; no application workaround was introduced. The parent's
reported baseline of 1,053 server/persistence passes is baseline evidence, not a
result for this correction. No network, installs or publication occurred.

## Deferred and protected

Save/Apply feature work remains separate. No changes were made to command
identity/outcomes, safety/freshness/session/auth behavior, trust-boundary validators,
recordings/export APIs, shared contracts/protocol, canonical map content,
workflows or protection policy. Optional map streaming, disabled reseeding,
offline tools, historical releases and public compatibility helpers remain.

Frontend clean install/typecheck/build/browser acceptance and native/hosted CI
belong to the parent/reviewer and were not run here. No subagents, network,
installs, Docker, ROS, robot contact, deployment, commits, pushes, PRs or merges
were performed. No user data, secrets, volumes or caches were cleanup targets.
The candidate still requires the parent's review and all required CI before any
normal merge.
