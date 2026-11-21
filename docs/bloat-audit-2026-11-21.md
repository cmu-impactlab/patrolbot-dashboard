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
actual Git blob against a modified working file. The real-Git test now creates
its own miniature temporary repository and commit; it needs neither checkout
metadata nor a historical branch in archive or shallow CI checkouts.
Separately, the local `deployed/dashboard-20261015` ref matched **all 62 entries**
of the observed manifest with `--live` omitted. No remote inspection occurred.

## Earlier implementation validation and limits

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

## Round 3 corrections against `6e66315`

The parent's immutable committed `6e66315` snapshot produced the following
results in `/tmp/opencode/bloat-cycle/validation-dashboard-6e66315/`, with no
retries. These supersede the earlier runtime/environment limitations as evidence
for that commit, but do not validate the uncommitted round 3 changes.

| Parent check | Result |
| --- | --- |
| Complete server | 1,401 passed |
| Complete web adapter | 342 passed, 1 skipped |
| Complete mock suite | 26 passed |
| Contracts / tuning parity | 136 passed / passed |
| Clean npm install, TypeScript, preview and E2E types, production build, bundle budget | All passed |
| Mock browser | 3 passed |
| Complete frontend | **572 passed, 1 failed**: measured-profile map test indexed an absent sender call |
| Complete scripts in Git archive | **349 passed, 1 failed**: real-Git fixture assumed checkout `.git` metadata |

Independent Codex full review 2 additionally reproduced a **P1 dotenv bypass**:
Pydantic expands `~/first.env` and `~/second.env`, while the raw rejection loop
tested unexpanded paths. A later empty value could hide an earlier nonempty URL
and permit SQLite selection. The raw loop now expands each path once and uses
that same path for both `is_file()` and `dotenv_values()`. It preserves the
existing source order, normal non-database settings, and fixed redacted error.
Real files under a temporary HOME cover both reject-only names, uppercase,
lowercase and mixed case, same-file case collisions, multiple files, later
empty values and an empty constructor override. Absolute paths are controls.
Before the fix these tests gave **18 failed, 18 passed**; all 18 failures were
tilde cases. Non-database precedence now exercises absolute and tilde paths
through multiple dotenv files, environment, constructor and secrets sources.

Inspection of the actual LiDAR guard confirmed that source age `.1` plus elapsed
receipt age must remain at most `.5` seconds. The map test's one-time real
`performance.now()` receipt therefore expired after `.4` seconds. This explains
how the parent's host-load failure can arise; the original failed run itself did
not record elapsed time. This test file now spies on `performance.now()` with a
per-test synthetic monotonic clock. A controlled 401 ms advance reproduces the
exact explanation, `LiDAR is off, starting, stale or unavailable — wait for fresh
scans.`, a disabled measured confirmation, and zero sends. Another advance after
an enabled button renders proves that confirmation rechecks freshness before
sending, without waiting for a periodic render. Existing frame, coordinates,
heading and one-shot assertions remain. Unmount cleanup cancels the component's
interval before mocks/globals are restored. No fake timers, sleeps, retries,
production guard, timeout or runtime changes were introduced.

The real-Git fixture now commits known synthetic bytes in its own temporary
repository using command-local identity, with inherited Git overrides and
global/system configuration isolated. It checks the actual
`repository_mismatches` function against a commit hash after changing and
deleting the working file, as well as mutated hashes, missing committed paths
and missing refs. No assertions are skipped and no Git calls in that test are
stubbed. The fixture never depends on or writes the user's repository metadata.

Fresh round 3 checks used the ready venv and parent snapshot dependencies;
logs are separate files under `/tmp/opencode/bloat-cycle/`:

| Check | Result / log |
| --- | --- |
| Before-fix raw dotenv regression | 18 failed, 18 passed, 331 deselected; `dashboard-round3-tilde-before.log` |
| Configuration + process isolation, `-k 'not sqlite_factory'` | 380 passed, 4 deselected; `dashboard-round3-config.log` |
| Complete script suite | 350 passed; `dashboard-round3-scripts.log` |
| Release tests copied to a temporary archive with no `.git` and invalid inherited Git overrides | 3 passed; `dashboard-round3-archive-git.log` |
| LiveMapWidget file, including controlled elapsed-time reproduction | 23 passed; `dashboard-round3-map-resolved.log` |

The focused Vitest run used a temporary source/dependency symlink mirror and
config under `/tmp/dashboard-round3-frontend`, with React plugin, jsdom and the
normal setup file. `preserveSymlinks`, a contracts alias and filesystem allowlist
resolve existing files without modifying repository dependencies. Three initial
resolver/configuration attempts collected no tests; their logs
`dashboard-round3-map.log`, `dashboard-round3-map-verified.log` and
`dashboard-round3-map-focused.log` remain intact. The successful run did not
retry the unchanged parent failure. Earlier failed logs are also unmodified.

The parent owns the final clean install, full Vitest/server validation, review
and publication. No full-suite pass is claimed for round 3. The four existing
SQLite factory/TestClient cases were deselected in this focused configuration
run; the parent's 1,401-server pass above is evidence for `6e66315` only.

## Final P1 correction against `0e34133`: unsupported FIFO dotenv inputs

Independent `dashboard-review-3.md` proved that both reject-only URL names could
escape the raw regular-file guard through a real `/proc/self/fd` pipe containing
a nonempty URL followed by an empty case variant. This could silently select
SQLite. The previously fixed constructor, secrets and tilde cases remain intact.

Traced the installed `pydantic-settings==2.15.0` source in the ready venv:
`main.py` constructs `DotEnvSettingsSource` before `settings_customise_sources`;
`sources/providers/dotenv.py` expands paths and reads `is_file() or is_fifo()`
during source construction. `sources/types.py` defines the default env-file
sentinel as `Path('')`; comparison is by equality. This agrees with the parent's
retrieved version-specific Context7 documentation. No network lookup was made.

The application policy now explicitly refuses FIFO dotenv inputs, including
empty streams, named FIFOs, tilde paths and `/proc/self/fd` pipe paths. A narrow
pre-init `Path.expanduser().is_fifo()` check runs before Pydantic can open any
effective dotenv input. It selects the configured default only for the documented
`Path('')` sentinel, respects `_env_file=None`, and checks all files in an
effective sequence. An explicit regular-file override ignores a configured FIFO.
The exception is always `FIFO dotenv inputs are unsupported; use regular dotenv
files.` It contains no filename, payload or credentials. There is no stream
reread, global monkeypatch, source override or private Pydantic API dependency.
Existing raw URL guards and non-database settings source order are unchanged.

New tests use real OS pipes and named FIFOs with live writers in bounded
five-second subprocesses. They assert the real path is a FIFO, exact redacted
failure, unchanged unread payload bytes (or an empty nonblocking read), and no
configured/default database file. Cases cover both URL names with hidden empty
case variants, empty streams, constructor/config/default-sentinel selection,
single and multiple files in either order, and absolute/tilde paths. Controls
cover regular overrides, disabled dotenv, missing paths, directories, empty
lists, regular configured defaults and default `.env` loading.

Local evidence and remaining validation are recorded below; these are candidate
checks, not independent review approval or committed-head acceptance.

All pytest commands used `/tmp/opencode/bloat-cycle/venv/bin/python -B`,
`PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`, from `server/`:

| Check | Actual result / log under `/tmp/opencode/bloat-cycle/` |
| --- | --- |
| Pre-fix empty real pipe, `tests/test_dotenv_fifo.py -k 'single and constructor and pipe' --maxfail=1` | 1 failed with bounded `TimeoutExpired` at 5 seconds, 85 deselected; `dashboard-fifo-before-bounded.log` |
| `tests/test_dotenv_fifo.py tests/test_database_config.py tests/test_settings_isolation.py -k 'not sqlite_factory'` | 468 passed, 4 deselected, 1 existing Starlette/httpx deprecation warning, 78.17 seconds; `dashboard-fifo-config.log` |
| Default `.env`/missing-file/None control added after the above collection, `tests/test_dotenv_fifo.py -k default_dotenv_and_no_file` | 1 passed, 88 deselected; `dashboard-fifo-default-control.log` |
| Final `git diff --check` | Passed |

Together the successful runs cover all **89 new tests** (81 bounded FIFO cases
and 8 controls), plus **380 existing configuration/isolation tests**. The four
SQLite factory/TestClient cases remain explicitly deselected here; no new full
server, frontend, native, hosted CI or independent review result is claimed.
An initial command launched from the repository root failed to import `app`
before collecting tests (`dashboard-fifo-before.log`); the corrected pre-fix
command above ran from `server/`. All logs, including failures, remain distinct.
No sandbox rejection occurred during these focused runs. The parent owns final
validation of a new committed head, native results, CI and independent re-review.
Changes remain local and uncommitted.

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
