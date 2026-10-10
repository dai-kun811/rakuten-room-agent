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

## Seven-day completion summary

- Qualifying dates: 0 / 7
- Phase 8 status: `OBSERVING` (Day 1 retained as non-qualifying evidence)
- Phase 9 authorization: `BLOCKED_UNTIL_GATE_PASSES`
