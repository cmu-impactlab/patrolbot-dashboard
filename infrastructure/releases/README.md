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

Known gaps: current Floor1 map differs from the robot; merged uncertainty and
separate always-visible motor-disable UI are absent; newer Pi path handling
needs a matched dashboard contract. Override admission is not robot per-goal
authority. Restore those only through reviewed/tested releases.

Durable device backups were migrated to private off-device archives on the
workstation, not Git. Runtime SQLite/recordings, secrets/TLS and mounted release
directories remain persistent. Temporary testing/transaction snapshots must be
verified off-device before exact-path retirement; no age-based bulk deletion.
Do not use a direct mutable-tag rebuild as proof of deployed revision.

See the robot repository `docs/operations/source-and-release-state.md` and
`audits/2026-10-clean-state/REPORT.md` for branch inventory, issue corrections,
artifact identities, off-device archive paths and remaining acceptance gates.
