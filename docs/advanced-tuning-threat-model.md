# Advanced tuning — threat model (slice 1)

Status: **design/threat model for work that is not implemented yet.** Slice 1
ships only offline inventory, this model, the contract-parity tool and CI. No
route, frontend, safety gate, capability, coordinator or actuator path is added
by this branch. Claims about future behavior are marked **TO BUILD**; claims
about existing behavior cite source lines.

> **Status update (2026-10-18).** The paragraph above is the slice-1 snapshot and
> is left as written. A later, **uncommitted** working-tree runtime slice now
> implements much of the `TO BUILD` list below. It is not committed, merged,
> deployed or commissioned, and no end-to-end write path is proven. The
> `TO BUILD` markers are retained as the historical slice-1 record; see §13 and
> `docs/advanced-tuning-runtime-verification.md` for the current reconciliation.

Related: `docs/advanced-tuning-inventory.md`. Baselines: dashboard `28af8dc`,
robot `37a4c1a`.

## 1. Assets and non-goals

Assets to protect, in priority order:

1. Physical safety: no unintended motion, no weakened stop path, no motor
   enable or dock release caused by viewing/editing/saving settings.
2. Motion authority: the single existing arbitration and lifecycle owner.
3. Persisted tuning state integrity (what the robot boots with).
4. Authorization integrity (only administrators change/activate/restore).
5. Auditability (who changed what, when, from where).

Explicit non-goals / forbidden outcomes:

- Settings screens must never arm teleop, enable motors, start/resume a goal,
  undock, or publish a pose. Editing is not driving (plan §3.1-3.2).
- No raw ROS parameter service, shell/SSH/Compose, or filesystem-write
  endpoint is exposed to the browser.
- No safety bypass knob (watchdog freshness, collision zones, mux priorities,
  footprint, scan min range, odom guards, localization-health gates, motor/dock
  limits, DDS discovery) becomes editable (plan §3.8).

## 2. Trust boundaries

```
browser --HTTP/WS(ui)--> dashboard server --WS(robot)--> web bridge --ROS--> core bridge --> SBC
   untrusted-ish            trusted authz           typed adapter      existing owner     final arbiter
```

- The browser is never trusted for role, revision or authorization.
- The web bridge is a typed adapter; it must not become a second coordinator.
- The core bridge (`patrolbot_bridge`) remains the single authority for
  lifecycle/map/undock/mutation.
- The SBC is the final physical arbiter (`protocol.cpp::classifyDrive`, accept
  bounded ordinary DRIVE ≤0.30 m/s / ≤1.20 rad/s; the *commissioned* cap is
  0.70 rad/s).

## 3. Administrator boundary

### 3.1 HTTP

- Existing: `get_current_user` (`server/app/authentication/local.py:43-50`),
  `require_operator` (`:56-68`), `require_administrator` (`:71-82`). The role
  comes from the verified session, never the request body.
- TO BUILD: every tuning HTTP route that reads the full catalog/snapshot MUST
  require authentication (`Depends(get_current_user)`; any authenticated role
  may read); every mutation (save patch, apply, redeploy, restore-last-good)
  must be `Depends(require_administrator)`. A signed-in observer/operator must
  receive 403, tested directly against the API (not only via the hidden UI).
- TO BUILD: server derives actor/role and stamps them; never trust a
  client-supplied `administrator:true` (mirror the existing rule that
  `operator_authorized` is server-stamped and ignored inbound:
  `server/app/protocol/messages.py:239-242`, `broker.py:290-293`).

### 3.2 WebSocket

- Existing: `/ws/ui` rejects a disallowed `Origin` and requires a verified user
  (`server/app/websocket/ui_gateway.py:23-48`); `resolve_ws_user`
  (`local.py:85-91`); the command broker independently checks `can_command`
  (`broker.py:120-123`). `/ws/robot` authenticates the bridge with a
  constant-time token compare (`robot_gateway.py:37-41`).
- TO BUILD: any tuning mutation or activation delivered over the browser WS must
  repeat the administrator check server-side on every message. A WS origin
  allowlist and an authenticated socket are not authorization to mutate.
- `/ws/robot` is a network-reachable endpoint: a browser (or anything on the
  network) can open it, but it cannot authenticate without the shared bridge
  token, which is compared in constant time (`robot_gateway.py:37-41`). Network
  reachability is not an authorization boundary — treat the token as the only
  gate and keep it server-side. This does not make the robot socket a browser
  command surface, but it must not be described as unreachable.

## 4. Role expiry, session, CSRF, origin

- Session cookie is `base64(json).hmac-sha256`, verified with
  `hmac.compare_digest`; `verify` checks `exp` (`sessions.py:28-39`). TTL is set
  at login (`oidc.py:169`). HTTP calls `verify` on every request
  (`get_current_user`, `local.py:43-50`), so HTTP expiry is re-checked per
  request. The browser WebSocket resolves the user once at handshake
  (`ui_gateway.py:44` → `resolve_ws_user`, `local.py:85-91`) and reuses that
  resolved `user` for later frames (`browser_connected(..., user=user)`,
  `ui_gateway.py:50`); expiry and role are **not** re-verified per WS message
  today.
- TO BUILD: any tuning mutation or activation delivered over the browser WS must
  re-resolve the session (so `exp` is checked again) and re-check the current
  role on every mutation — it must not rely on handshake-time resolution. A
  socket that was valid when it connected must not be treated as authorized
  after the cookie expires or the role changes.
- Roles: `administrator` / `operator` / `observer`, assigned from configured
  username lists (`oidc.py:44-53`); read-only by default.
- CSRF/origin: the session cookie is `HttpOnly`, `SameSite=Lax`
  (`oidc.py:113-116,166-171`); `/ws/ui` enforces an origin allowlist
  (`ui_gateway.py:23-29`); an HTTP middleware requires a session for `/api/*`
  (`main.py:63-71`). There is no separate CSRF token in source.
- TO BUILD: tuning HTTP mutations must reuse the existing origin/CSRF/session
  posture, not invent a weaker one. Test cross-origin rejection, expired-cookie
  rejection, and direct-API bypass by observer/operator/unsigned callers.

## 5. Revision / CAS / generation / UUID dedup / unknown outcomes

- Existing teleop precedent the tuning path should mirror:
  - Per-robot revision counter and generation binding
    (`server/app/commands/teleop.py:46,73-84,180`).
  - Server duplicate protection: bounded in-memory LRU plus a DB lookup so a
    replayed `command_id` is caught even after restart
    (`broker.py:182-189`).
  - Robot closed-session replay barrier sized 4096 with fail-closed behavior
    when full (`ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/teleop.py:98-102`).
  - Canonical UUID validation (`teleop.py:39-45`).
  - Unknown outcomes are held, never retried blindly: `phase="unknown"`
    (`teleop.py:159,167,512`) and `_mark_uncertain` (`broker.py:504-517`).
- TO BUILD: save patch carries `{request_id (UUID), base_revision, changes}`;
  the store rejects a stale `base_revision` with an explicit conflict, never a
  silent overwrite. `request_id` is deduplicated durably so a reconnect does not
  re-apply. Apply/redeploy carries `expected_revision`; an unknown outcome is
  queried by job ID, not retried. `generation` binds the request to the robot
  session it was admitted against.
- TO BUILD: the server must reject stale/mismatched revisions *and* the robot
  side must re-check, because a compromised or buggy adapter is not the only
  possible source of a stale frame.

## 6. Restart uses ACTIVE, never DESIRED

- Existing precedent: `ActiveMapStore` boots from the committed active artifact
  (`patrolbot_bridge/map_catalog.py:122-134`) and only a missing file in an
  available durable dir is first install.
- TO BUILD: boot loads the last committed **active** startup artifact. Pending
  (desired, restart-required, unactivated) edits must survive a reboot as
  *pending* and must not become the startup config. A crash or reboot cannot
  silently promote desired settings. Live activation also commits the matching
  active startup artifact so a later reboot reproduces it.

## 7. Atomic, fsynced manifest

- Existing pattern to reuse: temp file in the same directory, `fsync` the file,
  `os.replace`, then `fsync` the directory (`map_catalog.py:136-156`); reject
  unsupported `schema_version` (`:132-133`).
- TO BUILD: an atomically replaced, fsynced manifest names desired revision,
  active component revisions, pending diff, last-known-good and the active
  job/unknown state. Rename must be within one filesystem; the directory must be
  fsynced. Reject unsafe YAML tags, aliases/resource bombs, duplicate keys,
  arbitrary filenames, symlinks/path traversal and unknown keys. Corrupt or
  missing initialized state fails closed/read-only and retains last-good.
- TO BUILD: one writer/transaction lock; no cross-node atomicity assumption —
  ROS `SetParametersAtomically` is atomic only within one node, so a partial
  multi-node apply is reported as a hold/unknown, never as a fully active
  revision.

## 8. Existing core ownership; no independent coordinator

- Existing: `patrolbot_bridge` is the lifecycle/map/odom/undock mutation owner
  (`patrolbot_bridge/epoch_runtime.py`, `map_runtime.py`, `odom_epoch.py`;
  compose service `docker-compose.yml:48-71`). The final smoother has its own
  persistent lifecycle manager (`patrolbot-launch/lifecycle_mgr.py`).
- TO BUILD: tuning activation is a typed adapter *inside* the existing owner,
  serialized under the same locks. **Do not add a second lifecycle coordinator.**
  A new independent coordinator racing the existing one is an explicitly
  rejected design (plan §3.5).

## 9. Physical + web deadman / busy / unknown refusal

- Existing physical refusal: `manual_active.py:26-65` refuses the localization
  bypass while undock holds, LiDAR is unavailable, odom origin is not ready,
  raw odom is stale/non-finite, or any map/mutation owner is active.
- Existing web refusal: `gates.teleop_reason` (`gates.py:392-426`) and the
  broker's busy/unknown logic (`broker.py:125-157`); the teleop controller
  checks identity, lease, sequence, freshness and rate on every frame
  (`teleop.py:104-144`).
- TO BUILD: before activation, require fresh stationary raw odom + zero command
  buffers, no active/unknown autonomous or manual owner, no held F710 deadman,
  no map/localization/undock mutation, and valid hardware evidence. **Unknown is
  busy.** Refuse with a reason; never cancel someone's motion to tune settings
  (plan §3.6). Check both web sessions and physical deadman/evidence, not only
  browser state. Re-validate idle/freshness immediately before activation
  (TOCTOU).

## 10. Protection knobs and speed ceiling: no arming

- The speed setting is a **ceiling**, never a command and never a minimum. Nav2
  keeps curvature/obstacle/approach slowdowns (plan §1, §7).
- Existing: manual drive requires `motors_enabled` before it is admitted
  (`gates.py:419-420`); the manual path publishes only through
  `/input/teleop` at priority 7 and never enables motors or releases the dock.
- TO BUILD: saving/applying/redeploying/restoring settings must not arm teleop,
  enable motors, start/resume a goal, undock, or publish a pose, and must not
  replay old input. No protected knob (watchdog, collision, mux priorities,
  footprint, scan min range, odom guards, localization-health gates,
  motor/dock limits) is exposed. Enforce protection in the registry *and* on
  the robot. Registry validation is server-side enforcement at a trusted
  boundary — the vendored validator/schema rejects protected, read-only,
  not-applicable, unknown and path-injecting IDs (`contracts/validate_robot_tuning.py`,
  slice 1) — not merely browser-side hiding. But slice 1 exposes no tuning
  endpoint, so no runtime request reaches it yet and it must not be treated as
  runtime authorization on its own.
- TO BUILD: enforce normalized-axis proportional mapping with bounded
  acceleration; a small held deflection must never ramp to full speed over time.
  Reset ramps on release, blur, disconnect, and session/policy generation
  change. Enforce rate/acceleration on the trusted side, not only in browser
  animation.

## 11. Abuse and robustness

- TO BUILD: bounded payloads, finite numbers (reject NaN/Inf), strict booleans,
  fixed enums, no client-provided file/node path. Per-operator rate limits and
  payload size caps mirroring `RateLimiter` (`server/app/commands/lease.py:53-73`).
- TO BUILD: audit actor, source IP, before/after desired+effective values,
  revision, time, result and restart scope; no secrets in audit records
  (mirror `broker.py:_identity` and command audit).
- Slice 1 already instructs the shared validator to reject path-injection IDs,
  duplicate change IDs and duplicate JSON keys (`contracts/validate_robot_tuning.py`,
  tested in `contracts/test_robot_tuning.py`). TO BUILD for the persistent store:
  YAML-specific defenses (unsafe tags, aliases/resource bombs, symlinks/path
  traversal on file paths) with tests.

## 12. Slice 1 acceptance / reproduction checklist (offline only)

Implemented now (this branch):

- [x] `docs/advanced-tuning-inventory.md` — source-backed inventory.
- [x] `docs/advanced-tuning-threat-model.md` — this model.
- [x] `scripts/check_tuning_contract.py` — local-only, byte-for-byte parity of
      the six shared robot-tuning artifacts. No network. All six are required
      and the comparison is fail-closed: a missing, unreadable or drifted copy
      is an error, never a pass on an empty vendor directory.
- [x] `scripts/tests/test_check_tuning_contract.py` — portable unit tests for
      the parity tool (hash/compare/exit-code logic, missing and unreadable
      cases).
- [x] The shared contract's own **self-tests** (`contracts/test_robot_tuning.py`
      plus `contracts/validate_robot_tuning.py`, vendored once the robot side
      publishes them) are wired to run on every change, not behind a manual
      gate. Slice-1 coverage: schema validity (draft 2020-12), strict JSON that
      rejects NaN/Infinity and duplicate keys, invalid/boolean-as-number types,
      out-of-bounds ranges, unknown logical IDs, path-injection IDs, protected /
      read-only / not-applicable parameters, dependent limits (particle
      min ≤ max, `z_hit`/`z_rand`), capability/version constants and
      desired-vs-active snapshot honesty. Persistence, auth and native
      mutability are still future.
- [x] `Makefile` targets `test-tuning-contract` (installs the dev-only contract
      requirements, then runs both the parity-tool unit tests and the vendored
      contract self-tests; `TUNING_PY` selects the interpreter) and
      `test-tuning-contract-parity` (explicit local core path).
- [x] `.github/workflows/tuning-contract.yml` — portable job on PR/push that
      runs the parity-tool unit tests **and** the vendored contract self-tests;
      parity job gated on `workflow_dispatch -f core_ref=<ref>` because the
      robot repo's public main baseline does not yet carry the artifacts.

Reproduce:

```bash
# Runs the parity-tool unit tests and the vendored contract self-tests.
make test-tuning-contract
# After the deterministic core checkout exists:
PATROLBOT_CORE_REPO=/path/to/patrolbot make test-tuning-contract-parity
python scripts/check_tuning_contract.py --core-repo /path/to/patrolbot
```

Expected: tests pass; parity exits 0 only when every vendored artifact is
byte-identical to the canonical one. Exit 2 means the canonical repo or one of
its artifacts is missing or unreadable; exit 1 means a vendored copy is missing,
unreadable or drifted.

**Not implemented in slice 1** (future gates, each requires its own reviewed
slice; deployment and physical work require separate authorization): the
persistent store and manifest; the typed
inspect/save/status adapter; admin HTTP/WS routes; live apply/redeploy and
readback; the motion-telemetry slice and gauges; the curated AMCL/Nav2 editor;
the redeploy/rollback helper; any physical acceptance. None of these may ship
until validation, persistence, all-path enforcement and recovery coexist
(plan §10).

> **Historical note (slice 1, 2026-10-17).** The paragraph above is the slice-1
> snapshot and is left as written. As of 2026-10-18 an **uncommitted**
> working-tree runtime slice implements several of those items (typed server
> adapter, admin HTTP routes, a read-only curated editor, the measured-motion
> telemetry slice and gauges). None of it is committed, merged, deployed or
> commissioned, and none of it is proven end to end. See §13 and
> `docs/advanced-tuning-runtime-verification.md`.

Offline gates to add with those slices (not yet present as tests):

- Admin succeeds; operator/observer/unsigned direct HTTP and WebSocket mutations
  fail; per-mutation role/expiry revalidation, cross-origin, payload/rate bounds
  and audit identity. (Slice 1 has no endpoint; the schema/type/range/injection
  contract tests above already exist.)
- YAML round-trip; fsync/rename failure; disk full; corruption; crash between
  each phase; duplicate requests; two-admin CAS conflict; rollback; schema
  version. Full restart uses ACTIVE; pending stays pending.
- No settings mount/save/reload publishes motion or enables motors.
- Isolated native ROS lab proves hotload callbacks/readback/effects before any
  field is marked live; unverified mutability stays restart-required.

## 13. Status update — runtime slice in the working tree (2026-10-18)

This section **adds** a current reconciliation; it does not rewrite the dated
slice-1 model above. Everything it describes is an **uncommitted** working-tree
change on `feat/advanced-tuning-contract` (the branch equals `origin/main`; there
are no tuning commits). No merge, push, deploy, robot/SBC/SSH/DDS contact or
physical acceptance was performed for this record. Working-tree tests were run
locally; no write path is proven end to end. Full detail, test counts and open
gates: `docs/advanced-tuning-runtime-verification.md`.

Reconciliation of the §12 "Not implemented in slice 1" list (and the related
`TO BUILD` items above). "Landed" means the code exists in the working tree and
has local tests; it does **not** mean reviewed, merged or commissioned.

| Slice-1 `TO BUILD` / not-implemented item | 2026-10-18 working-tree state | Paths (all uncommitted) |
|---|---|---|
| Persistent store + atomic fsynced manifest | Robot-side only, uncommitted and unproven; the dashboard persists nothing (durability is delegated) | robot repo `patrolbot_bridge/tuning_store.py`; `server/app/tuning/broker.py` |
| Typed inspect / save / status adapter | Landed; strict canonical request/snapshot validation | `server/app/tuning/{broker,validation,authz,errors}.py` |
| Admin HTTP routes with per-mutation role/expiry/origin checks | Landed | `server/app/api/tuning.py` |
| Admin browser-WS mutation/activation route | Deliberately not offered: `/ws/ui` refuses any `tuning.*` frame and re-resolves the session per message; tuning uses HTTP | `server/app/websocket/ui_gateway.py` |
| Live apply / redeploy / readback | UI and transport landed but **unreachable**: writes are gated on `robot_tuning_v1`, which the bridge never advertises | `frontend/src/components/TuningPanel.tsx`; `ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/tuning.py` |
| Motion-telemetry slice + gauges | Landed; bounded measured-`/odom` twist, independent of AMCL | `ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge/normalizers.py`; `shared/schemas/protocol.md`; `frontend/src/components/SpeedGauges.tsx`; `frontend/src/lib/motion.ts` |
| Curated AMCL / Nav2 editor | Landed as a read-only catalog panel with explicit restart-required labels; hot apply remains unproven | `frontend/src/components/TuningPanel.tsx`; `server/app/tuning/validation.py` |
| Redeploy / rollback helper | Dialogs landed but gated behind the unadvertised write capability; unverified | `frontend/src/components/TuningPanel.tsx` (`RedeployDialog`, `RestoreDialog`) |
| Dynamic speed policy / no-arm / bounded ramp | Landed as a read-only dynamic cap bound to a drive-base session; legacy robots keep the fixed envelope | `server/app/commands/tuning_policy.py`; `server/app/commands/teleop.py`; `frontend/src/lib/teleop.ts` |
| Physical acceptance | Not done; external, blocked on the SBC |

The §12 offline gates remain the bar for merging. Working-tree tests now exist
for the server HTTP/WS authz, validation, broker, policy and motion transport,
but the robot-side persistence/fsync/crash gates and any physical acceptance are
still absent. Verified locally on 2026-10-18 (see the runtime doc for the exact
counts and the explicit non-claims): server and contract suites pass, the six
vendored contract artifacts are byte-identical to the robot checkout, and a
production image built and passed an import smoke with no runtime tuning service
loaded into it.
