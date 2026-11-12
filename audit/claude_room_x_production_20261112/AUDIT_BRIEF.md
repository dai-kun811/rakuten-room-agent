# Independent audit brief: scheduled official-X integration

## Objective

Audit the supplied implementation for a schedule-aware official X API
publisher linked to the existing Rakuten ROOM operation. This is a read-only
code audit. Do not use tools, access files or the network, modify anything, or
authorize production rollout.

## Intended behavior

- Monday, Wednesday, Friday, Sunday: publish one pre-reviewed helpful post with
  an original image and no sales link.
- Tuesday, Thursday, Saturday: preselect one quality-passed ROOM item from the
  07:00 manifest, then publish within the item's bounded window only after the
  exact DB slot and authenticated ROOM detail both prove the same product.
- Promotion must include `#PR` and X's `paid_partnership=true` field.
- Never late-post, fall back to a different item, duplicate a daily post, or
  automatically retry an uncertain X create result.
- Use OAuth 2.0 PKCE with minimum scopes and DPAPI-encrypted refresh tokens.
- Browser automation is used only to read authenticated Rakuten ROOM evidence;
  the X website is never scripted. X publication uses the official API.

## Review questions

1. Can OAuth secrets or access tokens leak through files, logs, exceptions, CLI
   arguments, or test output? Is the DPAPI implementation correct on Windows?
2. Is refresh behavior correct for public and confidential OAuth clients?
3. Can a network error, HTTP 5xx, invalid success body, process crash, or
   concurrent scheduled run cause a duplicate Post or unsafe automatic retry?
4. Can a promotion omit `#PR` or Paid Partnership, attach unreviewed media,
   publish the wrong ROOM item, use a non-owned ROOM URL, or publish late?
5. Is DB and authenticated ROOM evidence sufficiently exact and fail-closed?
6. Can malformed manifests, card catalogs, historic plans, files, URLs, or
   symlinks cause unsafe publication, path escape, overwrite, or denial of
   service?
7. Are the helpful cards original, non-medical, non-testimonial, non-promotional,
   appropriately cautious, and suitable for the documented four-week test?
8. Does the board-game exclusion correctly stop shogi listings from receiving
   wooden-block copy without breaking supported products?
9. Are the Windows trigger times, missed-run behavior, and `IgnoreNew` policy
   aligned with the schedule and no-late-post requirement?
10. Identify P0/P1/P2 findings with concrete file and line references. P0 is an
    immediate security/publication/data-loss risk; P1 blocks production setup;
    P2 is a non-blocking quality improvement.

## Supplied verification facts

- Focused X planner/API/publisher/schedule tests: 33 passed.
- Full repository regression with project-local TEMP/TMP: 455 passed.
- `py_compile` passed for all new and modified Python modules.
- Sixteen 1200x675 PNG cards were generated; each is 31-46 KB. One representative
  card was visually inspected for legibility and layout.
- Windows task installer was executed in Preview only. It reported no token
  store and no client ID; no task was registered.
- No X API call, OAuth authorization, payment, developer-console enrollment,
  public X post, ROOM edit/delete, DB migration, or production task registration
  occurred.
- Today's already-published ROOM shogi item is not being republished to X. The
  classifier change affects future generation only.

## Required output

Return one markdown report with:

- `VERDICT: PASS`, `CONDITIONAL_PASS`, or `FAIL`
- P0, P1, P2 counts
- Findings ordered by severity with file and line references
- Explicit go/no-go for proceeding to the parent agent's final review
- Explicit statement that this audit does not authorize OAuth, payments,
  developer-console changes, task registration, or public X publication
