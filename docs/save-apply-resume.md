# Save/Apply: parked feature branch — resume here

**2026-10-24. Work in progress; not merged, deployed or commissioned.**

Both this repository and `cmu-impactlab/patrolbot` use the branch
`feat/save-apply-commissioning`. This dashboard branch starts at main `c168dce`;
the robot branch starts at `f710cc8` plus handoff `412eda5`.

Primary handoff, current design, blockers and robot test instructions:
[patrolbot/docs/save-apply-resume.md](https://github.com/cmu-impactlab/patrolbot/blob/feat/save-apply-commissioning/docs/save-apply-resume.md).
Local checkout: `../patrolbot-repo/docs/save-apply-resume.md`.

## Included here

- Separate explicit Save and Apply; drafts do not autosave on blur/Enter/timers.
- Complete CAS saves; later edits survive old responses; conflicts and unknown
  outcomes retain the original intent and never retry automatically.
- Frozen exact-revision Apply/restore review, hot versus whole-navigation impact,
  seed-loss wording, conservative success/status handling and preserved readback
  freshness including HTTP round-trip latency.
- Server review/contract validation; web producer command ingress/drain rejection,
  exact-job maintenance/policy handshake, manual rearm and no input replay.
- UI-only synthetic Save/hot-Apply/whole-Nav2-redeploy/refusal/unknown scenarios.

No deployment or SBC connection was performed. Actual Apply remains operator-
disabled by default, and its full target-native fake-hardware cycle plus failure/
recovery, packaging and live commissioning acceptance remain outstanding. Do not
weaken admission or synthesize a native proof to make the button available.

## Local checks and UI preview

Recorded passes: 500 frontend units, 281 tuning server tests, 103 web adapter/
input/manual tests, two isolated native web tests and 72 Chromium/WebKit browser
cases. Latest production build and both preview Save/Apply reviews passed.
All eight vendored tuning contract artifacts must match the robot branch exactly.

```sh
cd frontend
npm run preview:advanced
```

- Hot Apply: <http://127.0.0.1:4180/advanced-preview.html?advanced=1&scenario=scoped>
- Whole Nav2 redeploy: <http://127.0.0.1:4180/advanced-preview.html?advanced=1&scenario=docker>

The operator selected **UI simulation only**. This preview blocks external API/
robot traffic; it is not a fake SBC, and its success messages do not hot-write
ROS parameters or restart Docker. No signing secrets are required by this preview.

## Resume in this order

1. Read the robot handoff. Keep the agreed two-mode policy: verified hot updates,
   otherwise one complete Pi `navigation` container restart. No SBC/core/bringup/
   dashboard restart, per-node lifecycle schedule or parameter-only image build.
2. Finish isolated whole-cycle native/fake-hardware verification and failure/
   recovery acceptance before claiming runtime readiness. Reject goals during
   either Apply mode; do not queue/replay them. Preserve all normal motion gates.
3. Review changes and run applicable units, lint, contract parity and browsers.
   Land the robot contract PR with green CI before dashboard parity against
   merged main, then dashboard PR/green CI/merge. No PR CI or full lint acceptance
   is claimed by this checkpoint.
4. Deploy only merged revisions after separate operator authorization and when
   the SBC is available. No live permission is implied by the parked branch.
