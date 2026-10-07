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
