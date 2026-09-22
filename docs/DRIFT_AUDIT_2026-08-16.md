# Code Drift / Validity Audit — 2026-08-16

Branch: `drift-audit-2026-08-16` (off `main`).

> **Status update 2026-08-19 — this branch has been adopted and is gone.**
> The original text below said "Not committed … review the working tree via
> `git diff main...drift-audit-2026-08-16` and commit/adopt selectively.
> `main` was never touched." All three statements are now out of date: the
> branch no longer exists (`git branch -a` lists only `main`, `ops/session`,
> `semantic-layer-2026-08-18` plus remotes), so that `git diff` command now
> errors, and **all 8 fixes below are on `main`.** Spot-verified 2026-08-19:
> `flock` present in `src/common/push_dedup.py`; `@router.delete("/batch")`
> at `src/web/routes/watchlist.py:446` now precedes `"/{entry_id}"` at
> `:490`; `secrets.compare_digest` at `src/web/routes/webhooks.py:58`; the
> double-decode vault guard in `src/web/main.py`; and all four new tests
> present (`tests/common/test_push_dedup_hot.py`,
> `tests/web/test_watchlist_route_order.py`,
> `tests/web/test_vault_path_guard.py`,
> `tests/ingest/test_tfms_alert_metric.py`).
>
> Original working note, retained for provenance: *"Not committed — GPG
> signing is unavailable to the audit agent and commits are the operator's."*

Verification: every finding checked against live data (sqlite at
`/var/lib/corporatetraveldc/corporatetraveldc.db`, `podman logs`, live WebDAV)
before fixing. No MCP used. Not a pentest. Full vault write-ups under
`Docs/code-drift-audit-2026-08-16/` in the second brain.

## Test status
`python -m pytest tests -q` → 138 passed, 17 failed. **All 17 failures
pre-exist this branch and are stale tests, not code defects** (verified by
stashing my changes and re-running):
- `tests/shared/test_watchlist.py` + `tests/web/test_watchlist_batch.py`:
  hand-rolled schemas / `_IsolatedDB` runs migrations only through v5, so
  `hex_id` (added later) is missing. Production schema has the column.
- `tests/runner/test_proxy_dispatch.py`: expects a `_dispatch_proxy_headers`
  helper that never existed in `runner/main.py` history; also asserts nginx
  sets `X-CTDI-Public` on the runner vhost, which it does not. Aspirational.
- `tests/ingest/test_marine_one_detection.py::test_smes_parser_basic`:
  fixture uses the pre-2026-08-03 flat `smes:positionReport` schema; the
  parser was rewritten to the real batched `asdexMsg` schema.

New tests added by this branch (all green): `tests/common/test_push_dedup_hot.py`,
`tests/web/test_watchlist_route_order.py`, `tests/web/test_vault_path_guard.py`,
`tests/ingest/test_tfms_alert_metric.py`.

## Fixes applied
| # | Sev | File(s) | Bug |
|---|-----|---------|-----|
| 1 | HIGH | common/push_dedup.py | Per-process cached state + whole-dict rewrite → ingest containers and poller clobber each other's shared dedup files and never see peers' writes. Fixed: mtime-aware reload + flock atomic merge-on-write. |
| 2 | HIGH | pusher/main.py, poller/skills/route_impact.py | Hardwired `hot=True` bypasses dedup entirely → active VIP/POTUS TFR re-fires ntfy p5 + Pushover Emergency every 30s. Dropped the flag. |
| 3 | HIGH | ingest/parsers/tfms_parser.py | "avg delay +?min" on MIT/MINIT/APREQ/STOP (RSTR programs have no avg delay). Type-aware metric; also fixed dedup content_key to include mit_value. Operator-confirmed live. |
| 4 | MED | web/routes/watchlist.py | Dynamic `/{entry_id}` registered before static `/batch` → batch delete unreachable (404). Reordered. |
| 5 | MED | web/main.py | Vault path `..` guard bypassable by double-encoding (`%252e%252e`; requests re-decodes before WebDAV). Full-decode+normalize+recheck on all 3 vault endpoints (2 are Tier-0). |
| 6 | LOW | web/routes/webhooks.py | Shared-secret compared with `!=` (timing side-channel) → `secrets.compare_digest`. |
| 7 | MED | shared/watchlist.py | Zone-less `scheduled_arrival` → naive datetime → `now(aware) > naive` TypeError aborts the entire landing/dead sweep every tick. Fail-safe skip (no UTC/ET guess). |
| 8 | MED | common/entity_tracking.py | Non-atomic `save_state` (crash mid-write + swallow-all load wipes tracker + Tier-2 corpus) → temp+os.replace; 2 sqlite leaks on exception → try/finally. |

## Documented, not auto-fixed (operator decision / larger change)

> **Re-checked 2026-08-19: every item in this section is still open.** Two
> were re-verified directly and remain exactly as described —
> `write_flight_event` still hardcodes `arrival_time=None` (the dead-tier
> explanation is still in `src/common/flight_resolver.py:343-350`), and
> `/api/v1/osint/feed` (`src/web/main.py:1379`) still carries **no auth
> dependency** while its sibling `/api/v1/osint/scopes` is `require_tier(T1)`
> at `:1634`.
>
> **NEEDS OPERATOR DECISION:** the OSINT feed endpoint is genuinely
> anonymous over the public tunnel, not merely tailnet-reachable — the
> public vhost proxies `/` to `:8000` and stamps `X-CTDI-Public: 1`, which
> forces `Tier.T0`, and a T0 request reaches this handler unimpeded. The
> 2026-08-16 note asked to "confirm intent for EP-scope item narratives" and
> that confirmation never happened. Decide: tier-gate it to match
> `osint/scopes`, or record explicitly that public EP-scope narratives are
> intended.
- **flight_resolver SWIM tier permanently dead** — `write_flight_event`
  hardcodes `arrival_time=None`; tier-1 query can never match; DCA/IAD/BWI
  run on paid AeroAPI. Query-time tbfm-eta enrichment verified viable
  against live data (needs a `tbfm_sequences(flight_id,last_seen)` index +
  live perf check). See vault note 03.
- **sr2_gate hash poisoning** — `hash_gate` records the input hash at check
  time, before the caller succeeds; a hard failure suppresses retry until
  inputs change. Narrow (skills have internal fallbacks). Fix = two-phase
  check/commit across 5 callers.
- **pusher landing `notified` set before send confirmed** — same shape as
  sr2_gate; a failed ntfy send loses the landing push until restart.
- **/api/v1/osint/feed** unauthenticated on the tunnel vhost while the
  sibling `osint/scopes` was Tier-1-locked by a prior pentest — confirm
  intent for EP-scope item narratives.
- **terminate_watchlist** shadowed by the router's `/{entry_id}`
  (admin-or-stricter on both; functional-only).
- entity_tracking E1 (demoted entities auto-re-promote), E3 (same-category
  lost-update race); watchlist W2 (permanent-removal lost across restart),
  W4 (`_load_file` TOCTOU on mtime-after-read).
