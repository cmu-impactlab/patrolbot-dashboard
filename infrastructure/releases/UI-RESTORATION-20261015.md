# Dashboard UI restoration candidate — not deployed

Branch: `review/dashboard-ui-restoration-20261015`, based on exact observed
deployment `749cb18`. Runtime changes are confined to four frontend/type files
restored byte-for-byte from merged fixes `d4925f8` / `4ffc514`:

- `src/lib/localizationUncertainty.ts`: map-axis/heading filter spread and signed
  variance margins; no conversion of covariance into physical accuracy or readiness.
- `src/widgets/RobotStatusWidget.tsx`: display uncertainty and withdraw stale
  AMCL claims independently of fresh dashboard frame receipts.
- `src/widgets/NavControlsWidget.tsx`: independent motor-disable control when the
  existing toggle is not showing disable. Account/online/capability/duplicate
  guards remain; enable still requires explicit confirmation.
- `src/types/protocol.ts`: existing optional telemetry fields. The deployed
  backend and Pi web bridge already provide these; no schema migration is needed.

No server, Pi web, robot, map, protocol-policy, joystick, override or planner code
is changed. This does not resolve those other deployment problems.

## Testing and artifact boundaries

Historical merged-main CI and WebKit acceptance passed on October13; this is
historical evidence, not acceptance of today's artifact. Fresh candidate evidence
is recorded in the robot audit's `UI-RESTORATION-20261015.md`.

The unmodified widget contracts reproduced11 failures on the deployed baseline.
After restoration the same72 focused cases passed, and all297 baseline frontend
cases passed. Additional tests explicitly retain observer/offline/missing-capability/
duplicate-command refusals for both displayed motor states. The built entry hash
is `8299aab51de57519e19ac86b94e1c3b878052216f95ced92a0d6cf27e20b029e`, identical
to the previously accepted October13 UI, not the current live `92453189…` bundle.

Build/types/bundle checks pass. Chromium device tests pass; the first local
WebKit invocation failed before browser startup due to missing `libicudata.so.74`.
That failed run must remain recorded; isolated browser revalidation is required.

Final narrow revalidation: **305 frontend cases pass** (297 existing +8 added
account/connection/capability/duplicate guards). All12 focused browser cases pass
in pinned Playwright image `eff16c30…`, including Chromium and WebKit phone,
tablet and desktop projects. The first container attempt failed before testing
because cap-dropped root could not write owner-private evidence/cache paths;
running as the workstation owner resolved it without weakening isolation/tests.
The missing host-library and failed-harness evidence remain preserved.

`Dockerfile.ui-only` derives from the verified current production server image
`sha256:04a464df6668b2197795ed965f4c1704f180a7b08bf733dcd2086af47e9240bc`.
It replaces only `/app/static`. The build context must contain ONLY the reviewed
frontend `dist/` contents under `static/`; never send a repository/private archive
directory as its context. Base-image bytes must be verified before assigning a
local build alias. Record the immutable resulting image, source revision and
frontend tree hash; an available tag is not sufficient provenance.

Built candidate `sha256:9a9235d31278d9959855b693cfced11336ac23a0277e70f7bcb45840bef96529`
contains source revision `fa94998559ec06af5b26550097f526d38a144fad` and frontend
tree digest `13845994ba5e6ffb996f82683c23bb45c45155d8b82e129eb2ee1d95acfd40c6`.
Run the network-isolated, installed-image check:

```bash
python3 -B scripts/check_ui_image.py infrastructure/releases/ui-restoration-20261015.candidate.json
```

It verifies production base-layer ancestry, unchanged environment/command/user,
all49 application/map files, all15 static files and actual HTTP delivery/startup/
application shutdown with temporary data only. The first smoke harness incorrectly
required exit0 after SIGTERM; installed Uvicorn0.52.1 deliberately re-raises its
captured signal after cleanup. The corrected check requires completed application
shutdown logs and accepts -SIGTERM only with that evidence. No runtime shutdown
behavior was changed. All these artifact checks pass; no production instance was
started or restarted. Source code is unchanged by this evidence-only follow-up.

## Before any later activation

1. Complete isolated browser/layout and exact-image tests/review, record hashes.
2. Recheck current dashboard image/source/assets; refuse unexpected drift.
3. Verify an off-device rollback archive and retrieval of the previous image,
   preserve Compose/env/TLS/SQLite volume, and confirm no pending dashboard actions
   or recordings. No runtime credentials/database are included in this candidate.
4. Obtain explicit approval for dashboard-only server recreation. Do not rebuild
   mutable dependencies or restart nginx/Pi/SBC as a shortcut.
5. Reverify installed app/maps unchanged, new static/public asset hashes, health,
   authentication, robot connection when available and read-only browser display.
   Send no motor/pose/goal/undock request during this check.
6. Update the deployment branch/manifest only after actual successful activation.
   A candidate build is not a deployment. SBC is operator-reported down; no robot
   telemetry/physical acceptance is claimed while it is unavailable.

No activation, service restart, issue closure or robot command is authorized by
this document. Actual motor-disable response remains a separately authorized
hardware acceptance step; mock button clicks are not a physical test.
