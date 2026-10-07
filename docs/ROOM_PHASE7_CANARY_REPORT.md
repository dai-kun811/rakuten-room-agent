# Phase 7 production canary record

## Gate status

- Status: `BLOCKED_EXTERNAL_QUEUE`
- Production slots posted by the new Orchestrator: none yet
- Old `RakutenROOMAutoPoster` / `RakutenROOMPostGuard`: disabled and retained
- Scheduled new Orchestrator: not installed or enabled

## Implemented preconditions

- One slot per invocation with an explicit active-slot ownership allow-list.
- SQLite CAS plus in-process execution lock plus cross-process lock file.
- Submit-boundary callbacks: `SUBMITTING` is committed before click and
  `SUBMITTED_UNCONFIRMED` immediately after click.
- A slot becomes `POSTED` only after its generated body is observed on the
  authenticated owner ROOM page. Missing evidence becomes `UNCERTAIN`; it is
  never treated as safe absence and never automatically reposted.
- CAPTCHA, expired login, and account restriction are classified as human-stop
  conditions before submit.
- Dry-run is the default; `--apply` is required for one external submission.
- Logs contain slot/status/product type and immutable manifest identity only;
  body, URL, credentials, and browser data are excluded.

## Tests and review

- Targeted posting/orchestrator tests: 33 passed.
- Full suite after the pre-canary implementation: 340 passed.
- `git diff --check`: passed.
- Secret-pattern diff scan: no finding.
- A concurrent-trigger race was detected during review: a second thread could
  turn the first valid in-flight post into `UNCERTAIN`. It was fixed by
  serializing an execution through the state-store lock; the production worker
  additionally requires a cross-process lock before opening the browser.
- Rollback snapshot was created under ignored local state at
  `.local/room-worker/rollback/phase7-pre-canary-20261007`, including four task
  XML definitions and the legacy ledger, with SHA-256 inventory.

## Canary sequence

1. Push the isolated migration branch after a fresh divergence check.
2. Dispatch one generation run on that branch and validate manifest v2.
3. Reconcile DB, legacy ledger, and current authenticated ROOM before morning.
4. Execute morning only; require actual ROOM evidence, DB `POSTED`, compatible
   ledger `posted`, and a rerun no-op.
5. Repeat the same gate for noon and then evening, never owning more than the
   stage's allowed slots.

Any `UNCERTAIN`, authentication/CAPTCHA/account restriction, duplicate risk,
or inability to reconcile actual ROOM stops Phase 7 immediately.

## 2026-10-07 execution result

- Run `37578206857` on commit `9a09409` succeeded and produced revision 295.
  Morning and noon passed the manual content audit. Evening did not: an
  electric breast pump was classified as a bottle-holder style nursing
  support product. No candidate from this manifest was posted.
- The root cause was the generic `授乳用品` / `ハンズフリー` match taking
  precedence over the breast-pump identity. Breast-pump terms are now excluded
  from automatic copy until dedicated extraction and templates exist. The fix
  is general rather than product-URL-specific and has a regression test.
- The first morning invocation stopped before browser launch while importing
  historical six-slot ledger rows such as `morning_1`. The three-slot importer
  now treats obsolete slot names as skipped/malformed history and continues to
  import current three-slot rows. The database contained no slot or attempt,
  and ROOM was not changed.
- Full regression after both fixes: 344 tests passed; `git diff --check`
  passed. Fix commit `1171686` was pushed to the isolated branch.
- The bounded second generation dispatch created run `37579267266` on commit
  `1171686`, but GitHub left it `queued` without creating a job. Repository
  Actions permissions are enabled. Two bounded enable/wait/disable checks,
  including a five-minute wait, did not start it. Normal cancel and
  force-cancel both returned HTTP 409. The workflow is confirmed
  `disabled_manually`, so the queued run cannot begin under the current state.
- The two-run Actions recovery budget is exhausted. No third dispatch or local
  production substitute was attempted. Incident
  `2026-10-07-actions_run_delayed-4fbd0434` is recorded as
  `BUDGET_EXHAUSTED`; its database backup is stored with the rollback assets.
- Final safe state: actual ROOM posts 0, legacy ledger rows for 2026-10-07 are
  0, DB slots/post attempts are 0, old AutoPoster/PostGuard remain disabled,
  GitHub workflow remains disabled, and new Orchestrator has no schedule.

Phase 7 is not complete and Phase 8 must not start. Before resumption, the
stuck run must be made terminal or confirmed harmless by GitHub, then one
explicit decision is required on whether a replacement generation run may
consume a new recovery budget.

## Approved deletion attempt

The user approved deleting only run `37579267266` and granting exactly one
replacement generation attempt. The GitHub DELETE request was made once and
returned HTTP 403. A verification GET returned 200 and the run remained in the
workflow run list, so deletion was not successful. In accordance with the
approved boundary, deletion was not retried and no replacement budget was
recorded or consumed. No replacement run and no ROOM canary post occurred.

Incident `2026-10-07-secret_or_auth_change_required-1159260a` records the
required human action. The exact run must be deleted through an authorized
GitHub identity or the current credential must receive the necessary Actions
run-deletion permission. Deletion must then be verified before any replacement
generation is authorized again.

The user subsequently reported the run deleted and approved one replacement
budget. The mandatory API preflight still returned HTTP 200 for run
`37579267266`; the run was present in the workflow list with status `queued`.
Because the user-visible state and GitHub API source of truth disagreed, the
replacement budget was not added, no dispatch was issued, and no ROOM action
was attempted. Resumption remains blocked until this exact API check returns
404 and the run is absent from the workflow list.
## Fenced recovery continuation (2026-10-07)

- The old `daily.yml` run was not deleted or re-enabled. It remained `queued` while `daily.yml` remained `disabled_manually`.
- Recovery control used `recovery-20261007-01`, source revision `297`, and main HEAD `cb07ab1`. Dedicated `room-fenced-recovery.yml` run `37625981589` ran exactly once.
- The replacement was artifact-only (`ROOM_SHADOW_MODE=true`): three ready slots, no missing slots, empty quality errors, and no Sheets writes. Recovery ID, HEAD, revision, and SQLite control all matched.
- The moved `.venv` was repaired after two browser-startup failures. Both had `submit_started=0`; only that non-submit budget was reclaimed. Full regression passed with 349 tests.
- The repaired morning canary reached the submit boundary but ended in `UNCERTAIN` (`2026-10-07-post_result_uncertain-0be298b4`). The database records `morning=uncertain` and `submit_started=1`. No retry, repost, noon/evening canary, PostGuard, or Phase 8 action was performed.

Phase 7 is stopped at the human-safety gate. An authenticated human must confirm the morning ROOM result before any state resolution; automatic reposting is prohibited.

## Human-confirmed morning reconciliation (2026-10-07)

- The user confirmed the matching morning item in the authenticated ROOM screen. No repost was performed.
- Added and tested an evidence-required atomic reconciliation path. It accepts only the existing `UNCERTAIN` slot with `submit_started=1` and its matching `NEEDS_HUMAN` incident, then updates the slot and post attempt to `POSTED`, resolves the post-result incident, and records `reposted=false`.
- Morning is now `POSTED` in SQLite (revision 297, URL `https://item.rakuten.co.jp/maryplus/babyswaddle`), one compatible `posted` ledger row exists, and the post attempt is `posted`. The earlier pre-submit incident is resolved; the stuck-run incidents remain retained and unresolved where appropriate.
- Noon and evening remain `READY`. At 22:40 JST the 22:30 posting window is closed, so no late canary or gate bypass was attempted. Phase 7 remains incomplete and Phase 8 is not started.
- Full regression after the change: 350 tests passed.
