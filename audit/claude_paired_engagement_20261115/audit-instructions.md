# Independent audit: production paired engagement change

Audit commit `fc7df1b` against its parent `d9fb01e`. Limit review to the eight files changed by that commit. Do not review or disclose untracked files.

You may read repository files and use only read-only `git diff`, `git show`, and `git status` commands. Do not edit files, access external sites, authenticate, follow, like, post, change schedules, inspect secrets, or invoke subagents.

## Production intent

1. Replace the active daily 50/50 behavior with exactly 15 newly followed accounts and 15 likes paired one-to-one to those same accounts.
2. An account already followed must neither count as a new follow nor receive a like from this routine.
3. Before following, verify that the first visible/latest post has an available unfilled like control. Never skip a filled latest post to like an older post.
4. If a new follow succeeds but its paired like cannot be verified after the bounded retry, stop before following another account and retain resumable fail-closed state.
5. On same-day resume, only finish the pending like for the exact account newly followed that day. Never like a fresh or already-followed account merely because the follow goal has been reached.
6. Preserve login/CAPTCHA stops, SQLite resumability, profile lock, 05:30-07:30 start window, bounded delay, sanitized progress, same-day idempotency, and posting safeguards.
7. The November 15 completed 50/50 run must not be rerun; the new goal begins with the next routine date.

## Existing verification

- Focused related tests: 31 passed.
- Full regression in a fresh project-local temp directory: 472 passed.
- Python compile, PowerShell parse, diff check, and secret-pattern scan passed.

Review state/accounting correctness, exact pairing, preflight/action races, retry and resume behavior, dynamically discovered candidates, candidate exhaustion, former-goal migration, accidental external actions, task activation safety, and information leakage.

Return only one JSON object with `verdict`, `p0`, `p1`, `p2`, and `summary`. `verdict` must be `PASS`, `CONDITIONAL_PASS`, or `FAIL`. Each finding must contain `id`, `title`, `evidence`, and `recommendation`. PASS requires zero P0 and zero P1 findings. Write `summary` and finding text in Japanese.
