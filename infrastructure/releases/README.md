# Observed deployment and future releases

`deployed/dashboard-20261015` records the actual running backend/maps and Pi web
bridge. Its frontend source rebuilds all 15 installed static files exactly. It
is an observed baseline with known regressions, not an approved replacement for
main or proof of physical acceptance.

```bash
python3 -B scripts/verify_release.py infrastructure/releases/observed-20261015.json --live
```

The checker only reads source, container image IDs/files and public assets; it
does not restart or command anything. Without `--live`, it checks local files.
Historical image labels lack Git provenance, so the manifest supplements rather
than fabricates their identity. Keep its SHA/file evidence when preparing a
future reviewed build.

Original unmerged work is preserved on `review/*` branches. The dashboard host
source is separately recorded as `review/dashboard-device-checkout-20261015`;
host source must not be assumed identical to baked image contents.

Floor 1 map identity now matches the robot catalog: the packaged raster is the
canonical robot bytes (`411c5647…`, revision `sha256:22872ec1…`), so operator
seeds are no longer refused by the bridge map-identity gate. The rollout is
recorded in `deployed-20261016-floor1-map-parity.json` (image `549599a2…` from
main `5dc06d7`; rollback image `d2a78dc3…` kept as
`patrolbot-dashboard-server:pre-map-parity-20261017`). Any map change must
keep both catalogs equal — run `python3 scripts/verify_floor_maps.py` against a
patrolbot-repo checkout and update the pinned `EXPECTED_REVISIONS` in
`server/tests/test_map_catalog.py` together with the robot navigation leaf.

Known gaps: merged uncertainty and separate always-visible motor-disable UI were
restored/deployed, but live AMCL/motor-response acceptance still waits on the SBC
and operator authorization; newer Pi path handling needs a matched dashboard
contract. Override admission is not robot per-goal authority. Restore those only
through reviewed/tested releases.

Durable device backups were migrated to private off-device archives on the
workstation, not Git. Runtime SQLite/recordings, secrets/TLS and mounted release
directories remain persistent. Temporary testing/transaction snapshots must be
verified off-device before exact-path retirement; no age-based bulk deletion.
Do not use a direct mutable-tag rebuild as proof of deployed revision.

See the robot repository `docs/operations/source-and-release-state.md` and
`audits/2026-10-clean-state/REPORT.md` for branch inventory, issue corrections,
artifact identities, off-device archive paths and remaining acceptance gates.
