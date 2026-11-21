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
`DATABASE_URL` in environment/dotenv configuration, case insensitive.
Constructor spellings are `database_url`, `DATABASE_URL`, and
`PATROLBOT_DATABASE_URL`, likewise case insensitive; the first two differ only
by case. No URI, PostgreSQL-specific or other invented aliases are recognized.

`Settings.settings_customise_sources` inspects constructor inputs, raw process
environment, and dotenv values before normal settings precedence/validation.
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
