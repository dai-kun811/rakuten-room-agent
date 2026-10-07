# Phase 7 production canary record

## Gate status

- Status: `PRE_CANARY_READY`
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
