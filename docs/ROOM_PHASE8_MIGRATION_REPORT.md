# ROOM Phase 8 migration report

## Scope

Phase 8 moves generation, one-slot posting, catch-up, and the 20:30 consistency audit under one local state owner. The old `daily.yml` and the four legacy Windows tasks remain disabled. No legacy definition is deleted, so rollback remains available.

## Safety invariants

- One JST day uses one deterministic `production-YYYYMMDD` recovery ID and one consumed generation-dispatch budget.
- Generation uses only `room-fenced-recovery.yml`, fixed `origin/main` HEAD, a monotonic manifest revision, and historical `POSTED` URLs.
- `daily.yml` remains disabled, so queued run `37579267266` cannot regain generation ownership.
- Each runner invocation can submit at most one ROOM slot. `POSTED` is a no-op and unresolved `CLAIMED`, `SUBMITTING`, `SUBMITTED_UNCONFIRMED`, or `UNCERTAIN` fails closed.
- A local manifest is accepted only when recovery ID, HEAD, revision, routine date, Actions run ID, and recovery control agree.
- The 20:30 audit compares SQLite, the latest submitted attempt, open incidents, and the rollback-compatible legacy ledger.
- No Codex process participates in a normal run.

## Schedule and catch-up

One Windows task, `RakutenROOMOrchestrator`, owns all Phase 8 work. Its daily triggers are 07:00, 08:00, 08:20, 12:00, 12:20, 19:00, 19:20, and 20:30 JST. The `:20` triggers are bounded catch-up opportunities after sleep, restart, a delayed artifact, or a transient pre-submit failure; they are not additional owners. `MultipleInstances=IgnoreNew` and SQLite/process locks prevent concurrent ownership.

## Rollback

Disable `RakutenROOMOrchestrator`. Keep the database and ledger unchanged. The legacy task XML exports and pre-Phase 7 database backups remain under `.local/room-worker/rollback`. Re-enabling any old task requires a separate ownership review; the new and old poster tasks must never be enabled together.

## Completion gate

Phase 8 is not complete on installation. It requires at least seven consecutive production days with duplicate posts 0, wrong-slot posts 0, false `POSTED` 0, no lost incidents, all 20:30 audits consistent, old tasks unused, and normal-day Codex usage 0.
