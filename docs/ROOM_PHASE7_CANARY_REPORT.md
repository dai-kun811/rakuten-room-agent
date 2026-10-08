# Phase 7 production canary record

## Gate status

- Status: `MORNING_PASSED_WAITING_NOON`
- Production slots posted by the new Orchestrator: morning (2026-10-08 revision 299)
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

At that point Phase 7 stopped at the human-safety gate; automatic reposting remained prohibited until the later authenticated reconciliation below.

## 2026-10-08 autonomous UNCERTAIN reconciliation

- The first revision 299 morning attempt was absent from the authenticated ROOM: profile count 227, exact comment absent from 117 loaded cards, and the latest-card body hash differed. It was not marked posted.
- A bounded confirmed-absence recovery was added. It retains the original submit-started attempt, allows exactly one retry with a distinct idempotency key, and refuses a second absence retry.
- The retry initially failed before submit because the system Python lacked Playwright. `submit_started=0` was proven; the failed attempt was preserved, the existing pre-submit budget was consumed once, and the worker was rerun with the project virtual environment.
- The resulting `UNCERTAIN` was reconciled without another send after the authenticated ROOM showed one exact full comment, the PLUSiiNE product image, and profile count 228. SQLite slot and attempt are `POSTED`; the incident is resolved; the compatibility ledger has exactly one 2026-10-08 morning posted row.
- A same-slot apply rerun returned `POSTED` with `attempt_id=null`, proving the duplicate-send no-op. Noon and evening remain ready and are not eligible before their JST gates.

## Human-confirmed morning reconciliation (2026-10-07)

- The user confirmed the matching morning item in the authenticated ROOM screen. No repost was performed.
- Added and tested an evidence-required atomic reconciliation path. It accepts only the existing `UNCERTAIN` slot with `submit_started=1` and its matching `NEEDS_HUMAN` incident, then updates the slot and post attempt to `POSTED`, resolves the post-result incident, and records `reposted=false`.
- Morning is now `POSTED` in SQLite (revision 297, URL `https://item.rakuten.co.jp/maryplus/babyswaddle`), one compatible `posted` ledger row exists, and the post attempt is `posted`. The earlier pre-submit incident is resolved; the stuck-run incidents remain retained and unresolved where appropriate.
- Noon and evening remain `READY`. At 22:40 JST the 22:30 posting window is closed, so no late canary or gate bypass was attempted. Phase 7 remains incomplete and Phase 8 is not started.
- Full regression after the change: 350 tests passed.

## 2026-10-08 noon canary

- Revision 299 noon was executed only after the 12:00 JST gate. The first invocation used system Python without Playwright and failed before submit; `submit_started=0` was verified and the attempt was preserved as `failed_pre_submit_reclaimed`.
- The one configured pre-submit retry budget was consumed. The retry used the verified project `.venv`, reached submit, and returned `UNCERTAIN`.
- Authenticated ROOM reconciliation showed the exact full comment and all five hashtags at the newest position, profile count 228 to 229, and an image hosted under `shop.r10s.jp/bebechambre`, matching the manifest shop/item. No repost was performed.
- SQLite noon slot and submitted attempt are `POSTED`; both noon incidents are `RESOLVED`; the compatibility ledger contains one noon posted row. The preserved pre-submit attempt remains immutable audit evidence.
- A same-slot apply rerun returned `POSTED` with `attempt_id=null`. Morning and noon are therefore duplicate-safe no-ops; evening remains `READY` until 19:00 JST.
- Recurrent terminal incident handling was independently reviewed and strengthened without a schema migration. Root-cause fingerprints remain visible in events/snapshots while terminal row identities avoid legacy uniqueness collisions. Full regression: 356 tests passed.

## 2026-10-08 evening canary and quality stop

- Revision 299 evening was executed only after 19:00 JST. It reached submit and returned `UNCERTAIN`; authenticated ROOM then showed the exact full comment and five hashtags, profile count 229 to 230, and the matching `calmisence/.../hip` image. It was reconciled to `POSTED` without another send, synced once to the compatibility ledger, and a same-slot rerun was a no-op.
- The authenticated display exposed a content failure that the manifest quality score missed: the product is a hip seat, but `おしりふき 収納ポケット` and the `20kg` load rating caused wipes classification and the public phrase `20kg入りのおしりふき`.
- The root classification path now sends hip-seat/baby-carrier/sling terms, including Japanese and English variants, to `unknown` until carrier-specific attributes and copy exist. Eight carrier variants and normal wipes/diaper non-regression are covered; all 357 tests pass and an independent safety review passed.
- The actual public post remains present and factually mismatched. DB, submitted attempt, legacy ledger, and ROOM agree that it was posted, while incident `2026-10-08-copy_validation_regression-61895840` remains `NEEDS_HUMAN` for the quality failure.
- Automatic delete, edit, or corrective repost is prohibited because it is destructive or can create a duplicate. Phase 7 therefore remains incomplete and Phase 8 has not started.

## 2026-10-08 authenticated in-place correction and Phase 7 completion

- After explicit action-time approval, the existing public ROOM post was edited in place. It was not deleted and no new post was created.
- The unchanged ROOM post URL is `https://room.rakuten.co.jp/tora_papa/1700396196370481`; its Rakuten Market target remains `https://item.rakuten.co.jp/calmisence/hipseatse`, and the displayed images remain under `shop.r10s.jp/calmisence/.../hip`.
- The public comment now describes a hip seat, states only grounded attributes (waist-sitting age through about 36 months, recommended load up to 20 kg, waist about 70-115 cm, slip-resistant seat and storage pockets), and uses the five matching tags `#ヒップシート #抱っこ紐 #子連れ外出 #収納ポケット #とらパパ厳選`.
- The exact 231-character corrected comment has SHA-256 `6fef9cc046fdacfbeb8b03604e7dbfd17ee1b2b99959912e5541072e07cfe2ab`. The authenticated detail page showed the exact full comment, all tags, matching product title/link and matching images immediately after save.
- A compensating `POSTED -> POSTED` audit event records the public edit and preserves the original manifest/post-attempt hashes as immutable send history. Incident `2026-10-08-copy_validation_regression-61895840` is `RESOLVED` with `reposted=false`.
- Pre-reconciliation SQLite backup: `.local/room-worker/rollback/phase7-post-edit-pre-reconcile-20261008.db`. SQLite quick check is `ok`.
- Morning, noon and evening are all revision 299 `POSTED`; each has exactly one final posted attempt and one compatibility-ledger posted row. Historical failed/confirmed-absent attempts remain immutable. There are no unresolved 2026-10-08 incidents and no duplicate ROOM submission occurred.
- The new reconciliation path is atomic and refuses non-POSTED slots, non-POSTED attempts, mismatched incidents, invalid ROOM URLs and invalid hashes. Full regression: 358 tests passed; `git diff --check` passed.

Phase 7 completion gate is satisfied. Phase 8 may start, but its seven-day production observation requirement remains unchanged and cannot be collapsed into this canary day.
