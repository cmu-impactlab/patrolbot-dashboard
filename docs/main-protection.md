# Main branch protection

`.github/main-protection.json` is the REST `PUT /repos/{owner}/{repo}/branches/main/protection`
request body for this dashboard repository. The parent operator applies it to
`main` after the bootstrap PR is merged and validated. Checking in this file does
not apply protection.

The policy requires all 19 checks from the unconditional PR workflows `ci.yml`
and `tuning-contract.yml`, including every Python 3.11–3.14 matrix cell, with the
branch up to date. These exact contexts were observed completed successfully
under the `github-actions` app (ID `15368`). The manual-only mobile-browser
acceptance workflow is intentionally excluded; no unimplemented gate is required.

Future PRs need one eligible independent review, approval of the most recent
reviewable push by someone other than its pusher, and resolved conversations.
New commits dismiss stale approvals. The author's local Codex or CLI review
cannot satisfy GitHub's approval requirement. Other write-access maintainers
can provide eligible reviews. Administrators follow the same rules, with no
actor bypass allowances. Force pushes and deletion are blocked; unrestricted
eligible maintainers retain normal merge permissions once the requirements pass.
The branch remains unlocked, without a linear-history requirement.

After application, read back `GET /repos/{owner}/{repo}/branches/main/protection`.
Compare the `required_status_checks.checks` context/app-ID pairs (order does not
matter) and `strict` against the file. Verify `enforce_admins.enabled`,
`required_conversation_resolution.enabled`, `allow_force_pushes.enabled`,
`allow_deletions.enabled`, `required_linear_history.enabled`, and
`lock_branch.enabled` against the corresponding booleans. Verify the review
count, stale-review dismissal, last-push approval, code-owner setting, empty
user/team/app bypass allowances, and null restrictions. The response contains
additional metadata and nested `enabled` values, so it is not byte-identical to
the PUT body. Also confirm a subsequent PR shows all 19 required checks and
the review requirement before treating rollout as validated.

Coordinate workflow/job/matrix renames with this payload and its regression
tests. Validate the new contexts on GitHub and confirm their app ID before the
parent operator updates the live policy, so no PR is stranded waiting for an
obsolete check. Keep required validation workflows unconditional for PRs:
branch/path filters or job conditions can prevent a required context appearing.
Preserve protection throughout the coordinated update.

Local regression check (only the existing contract test dependencies are needed):

```sh
python -m pytest scripts/tests/test_main_protection.py -q
```
