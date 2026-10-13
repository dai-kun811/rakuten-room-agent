# ROOM Phase 8 — seven-day production observations

## Rules

- Record exactly one row per JST routine date after the 20:30 local audit.
- Reuse already-recorded evidence. Do not rerun healthy-day regression suites.
- A day counts toward the seven-day gate only when all three slots have conclusive evidence and every gate column is `0` or `OK`.
- `UNCERTAIN` is never cleared from scheduler evidence alone. Correlate the authenticated ROOM before resolving it, and never resend unless absence and duplicate safety are technically proven.
- Keep `RakutenROOMOrchestrator` as the only enabled posting owner. Keep the four legacy Windows tasks and `daily.yml` disabled.
- Phase 9 remains blocked until at least seven distinct qualifying routine dates exist; the dates may be consecutive or cumulative, but every counted date must satisfy all gates simultaneously.

## Daily gate ledger

| JST routine date | Slots / ROOM | Duplicate | Wrong slot | False POSTED | UNCERTAIN | DB-attempt-ledger-ROOM | Fence | Retry / incident | 20:30 vs 20:45 | Copy quality | Normal-path Codex recovery | Qualifies |
|---|---|---:|---:|---:|---:|---|---|---|---|---|---|---|
| 2026-10-09 | morning POSTED; noon POSTED; evening BLOCKED | 0 | 0 | 0 | 1 resolved | morning/noon aligned; evening intentionally blocked | OK | 2/2 generation budget; terminal copy incident | 20:30 failed, 20:47 rechecked | 1 pre-post defect blocked | 2 bounded generation runs; one recovery | No |
| 2026-10-10 | morning POSTED; noon READY (window missed); evening READY (ordered canary stopped) | 0 | 0 | 0 | 1 resolved by authenticated ROOM | morning DB/attempt/ledger/ROOM aligned; later slots had no attempts | OK (revision 302 / production-20261010 / fenced HEAD) | 20:30 failed closed on unresolved morning; 20:49 reconciled; no post after audit cutoff | 3 generated copies passed manifest quality checks | 1 bounded generation run; no Codex recovery | No |
| 2026-10-11 | no manifest; no slots created; no ROOM posts | 0 | 0 | 0 | 0 | no slot/attempt/ledger state for this date | pre-dispatch fence mismatch detected; no external generation run | 20:30 stopped safely; no late recovery | N/A (generation blocked before artifact) | 0 (no Codex recovery) | No |
| 2026-10-12 | morning confirmed absent (no repost); noon/evening READY and unattempted | 0 | 0 | 0 | 1 resolved by authenticated ROOM absence | morning attempt retained as confirmed_not_posted; ledger reserved only; no posted mismatch | OK (revision 304 / production-20261012 / fenced HEAD) | 20:30 failed closed; absence reconciled at 20:48; no late retry | 3 generated copies passed (85/85/93) | 1 bounded generation run; no Codex recovery | No |
| 2026-10-13 | morning confirmed absent (no repost); noon/evening READY and unattempted | 0 | 0 | 0 | 1 resolved by authenticated ROOM absence | morning attempt retained as confirmed_not_posted; ledger reserved only; no posted mismatch | OK (revision 305 / production-20261013 / fenced HEAD) | 20:30 failed closed; absence reconciled at 20:47; no late retry | 3 generated copies passed (93/85/95) | 1 bounded generation run; no Codex recovery | No |

## Evidence notes

### 2026-10-09

- Observation opens with Phase 7 already reconciled and Phase 8 task installed.
- Expected first run: fenced generation at 07:00 JST; posting gates at 08:00, 12:00, and 19:00; local audit at 20:30; heartbeat correlation at 20:45.
- Base run `37850610183` / revision 300 succeeded. Morning was reconciled from `UNCERTAIN` to `POSTED` only after authenticated ROOM showed the exact full comment, five tags, product image, and profile count change; it was not resent.
- The revision 300 copy audit found two pre-post defects. General classification/evidence fixes passed regression, then the final allowed daily generation budget produced run `37858210514` / revision 301. Morning remained immutable at revision 300; noon/evening alone moved to revision 301.
- Revision 301 noon initially returned `UNCERTAIN` after submit. The authenticated ROOM showed the exact full manifest body and five tags, profile count 232, and the `housei-baby` product image; DB URL/content hash and submit-started attempt matched. It was reconciled to `POSTED` without repost, and the compatibility ledger was synced once.
- Revision 301 evening misread a two-pack lotion refill as a multi-step wash-and-moisturize set. It was blocked before any attempt and incident `2026-10-09-copy_validation_regression-50c0faa1` records the exhausted 2/2 generation budget.
- Root cause was fixed generally in commit `1980cd6`; same-source regeneration now produces lotion-specific copy. The follow-up audit-terminal-state fix is commit `b478cd2`; all 368 tests pass. No third generation, local manifest rewrite, quality relaxation, or evening post is permitted today.
- The 20:30 audit correctly detected noon unresolved and evening not posted. After authenticated noon reconciliation, the remaining failure is only the intentional evening block. A focused audit fix now treats terminal `BUDGET_EXHAUSTED` as historical evidence while continuing to fail on `NEEDS_HUMAN` and in-flight incidents; the date still cannot qualify because evening is not posted and copy quality required recovery.

### 2026-10-10

- Fenced generation run `37996742815` produced revision 302 under recovery `production-20261010` and the accepted HEAD fence. All three manifest candidates were `ready` with quality status `passed` (scores 93/85/95); no replacement generation was used.
- The 08:01 morning submission was `UNCERTAIN` with `submit_started=1`. At 20:49 JST, authenticated ROOM `tora_papa/items` showed the exact full morning comment and five hashtags, the `kyarahouse/7175562` item identity/image, and profile collect count 233. It was reconciled to `POSTED` without repost; the attempt and post-result incident were resolved and the compatibility ledger received one idempotent `posted` row (the prior `reserved` row remains audit history).
- Noon and evening had no attempts; noon missed its window and the ordered canary was not resumed after the 20:30 audit cutoff. No late posting or catch-up was performed. This date is non-qualifying; the slots will be expired at the normal JST cutoff/boundary without reuse.
- SQLite quick check was `ok`; no duplicate, wrong-slot, false-POSTED, or DB/attempt/ledger/ROOM inconsistency was observed after reconciliation. The 20:30 audit failure was the expected fail-closed response to the unresolved morning result and missed later slots.

### 2026-10-11

- The Orchestrator stopped before generation because the pre-dispatch recovery lease (`production-20261011`, revision 303) was reserved against the prior local HEAD while `origin/main` had not yet received the observation commit. No Actions dispatch, artifact generation, slot creation, browser action, or ROOM write occurred.
- The divergence was resolved by a normal fast-forward of `origin/main` to the already-reviewed observation commit. A bounded pre-dispatch fence rebind was added: it is allowed only when local and remote HEAD are identical and no dispatch intent exists; it cannot rebind after an external side effect or when local/remote diverge. Focused 35 tests and the full 369-test suite passed; no generation budget was added.
- Windows ownership remained correct: `RakutenROOMOrchestrator` is the only enabled task; the four legacy tasks remain disabled. Day 3 is non-qualifying and no late generation/posting was attempted. The next routine date starts normally from a fresh fence.

### 2026-10-12

- Fenced generation run `38178633455` / recovery `production-20261012` / revision `304` completed under HEAD `edf99122`. All three generated copies were `ready` and quality-passed (scores 93/85/85; no errors; maximum structure similarity 0.483).
- Morning submission for `plusmart/4977693220182` became `UNCERTAIN`. Authenticated ROOM inspection found no matching URL/shop, no exact full comment or five hashtags, no matching product image, and no profile-count increase; the only similarly titled existing item had different content. The attempt was atomically marked `confirmed_not_posted`, the incident resolved, and exactly one bounded retry was authorized without reposting. The compatibility ledger contains only the reserved audit row.
- Because the 20:30 audit cutoff had passed, no retry or noon/evening posting was performed. Noon and evening remain unattempted `READY` slots and will expire at the JST boundary without reuse. SQLite quick check was `ok`; no duplicate, wrong-slot, false-POSTED, or DB/attempt/ledger inconsistency was observed. Day 4 is non-qualifying.

### 2026-10-13

- Fenced generation run `38314070069` / recovery `production-20261013` / revision `305` completed under HEAD `653895b2`. All three generated copies were `ready` and quality-passed (scores 93/85/95; no errors; maximum structure similarity 0.517).
- Morning again selected `plusmart/4977693220182` and became `UNCERTAIN`. Authenticated ROOM inspection again found no matching URL/shop, exact comment/hashtags, matching image, or profile-count increase; the existing similarly titled item differed. The attempt was reconciled to `confirmed_not_posted` without repost, with one bounded retry budget recorded.
- The repeat selection exposed a general gap: confirmed-absence URLs were not excluded on later routine dates. The state store and fenced workflow now pass these URLs through `ROOM_BLOCKED_URLS`; they remain eligible only for the same-day bounded retry and are excluded from subsequent generation. Focused 55 tests and full 369 tests passed. No additional generation or late retry was used.
- Noon/evening had no attempts and remained `READY`; no late posting occurred. SQLite quick check was `ok`; no duplicate, wrong-slot, false-POSTED, or DB/attempt/ledger inconsistency was observed. Day 5 is non-qualifying.

## Seven-day completion summary

- Qualifying dates: 0 / 7
- Phase 8 status: `OBSERVING` (Day 1 retained as non-qualifying evidence)
- Phase 9 authorization: `BLOCKED_UNTIL_GATE_PASSES`
