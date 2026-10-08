# ROOM Phase 8 — seven-day production observations

## Rules

- Record exactly one row per JST routine date after the 20:30 local audit.
- Reuse already-recorded evidence. Do not rerun healthy-day regression suites.
- A day counts toward the seven-day gate only when all three slots have conclusive evidence and every gate column is `0` or `OK`.
- `UNCERTAIN` is never cleared from scheduler evidence alone. Correlate the authenticated ROOM before resolving it, and never resend unless absence and duplicate safety are technically proven.
- Keep `RakutenROOMOrchestrator` as the only enabled posting owner. Keep the four legacy Windows tasks and `daily.yml` disabled.
- Phase 9 remains blocked until seven distinct qualifying routine dates exist.

## Daily gate ledger

| JST routine date | Slots / ROOM | Duplicate | Wrong slot | False POSTED | UNCERTAIN | DB-attempt-ledger-ROOM | Fence | Retry / incident | 20:30 vs 20:45 | Copy quality | Normal-path Codex recovery | Qualifies |
|---|---|---:|---:|---:|---:|---|---|---|---|---|---|---|
| 2026-10-09 | pending | — | — | — | — | pending | pending | pending | pending | pending | pending | No |

## Evidence notes

### 2026-10-09

- Observation opens with Phase 7 already reconciled and Phase 8 task installed.
- Expected first run: fenced generation at 07:00 JST; posting gates at 08:00, 12:00, and 19:00; local audit at 20:30; heartbeat correlation at 20:45.
- Evidence is intentionally pending until those gates have elapsed. No result is inferred in advance.

## Seven-day completion summary

- Qualifying dates: 0 / 7
- Phase 8 status: `OBSERVING`
- Phase 9 authorization: `BLOCKED_UNTIL_GATE_PASSES`
