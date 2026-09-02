# Live State Check — 2026-09-02 (post-commit d94817b)

Doc-drift check scoped to commit `d94817b` ("ops-brief context-budget fix,
email routing for weekly/second-brain-daily/weekly reports, IPv6-flakiness
CLAUDE.md drift reconciliation"), run ~09:11–09:15 EDT, minutes after the
commit landed. Checked README.md, CLAUDE.md, docs/ (ALERT_REFERENCE.md,
ALERT_ARCHITECTURE.md, DATA_SOURCES.md, DEDICATED_MODELS_PLAN.md,
CLAUDE_MD_DRIFT_REPORT.md), src/ingest/README.md,
src/shared/watchlist_README.md — against the diff and the live system
(systemctl --user, podman images, journalctl, verify-manifest).

Prior art consulted first (second-brain search): vault note
`corporatetraveldc/01-Sources/manual/20260902T070747Z.md` (this morning's
root-cause pass) already documents both halves of the headline fix — the
IPv6-tether root cause of the build failures and the ops-brief
exceed_context_size_error outage + the token-budget fix this commit ships,
including the ~1.9–2.1 chars/token trap for aviation-dense text. Nothing
below contradicts it; this check builds on it.

## Drift found (real)

### 1. docs/ALERT_REFERENCE.md — new FDPS meter-fix proximity alert is undocumented

The FDPS watchlist section (~line 204) documents exactly one `TH` (track)
alert path: `_maybe_alert_on_approach`, within 50 nm of the *destination
airport*. This commit added a second, parallel `TH` path,
`fdps_parser._maybe_alert_on_meter_fix_approach`: fires when a watched
flight is within 50 nm of any coordinate-resolved DC-area TBFM meter fix
(the 5 of 10 in `tbfm_parser.DC_METER_FIXES` with confirmed NASR/CIFP
lat/lons — SWANN, RAVNN, FLUKY, WOOLY, PALEO). It writes a
`watchlist_event_hit` (priority 3, trigger `fdps_th_meterfix_approach`)
and fires `_fire_fdps_nas_alert` → `fdps-alerts`/`fdps-<zone>`, with its
own 600 s per-aircraft dedup (`_FDPS_METERFIX_PROX_DEDUP`, distinct from
`_FDPS_PROX_DEDUP`). ALERT_REFERENCE.md is the canonical alert-path
reference and now under-describes a live alert path on the fdps topics.

Related, same section still accurate: the TBFM `check_tbfm_alerts`
description is unaffected — `DC_METER_FIXES` changed shape (frozenset →
dict with coords/None) but the TBFM alert logic itself didn't change.

### 2. docs/ALERT_REFERENCE.md — email delivery leg not reflected (topics table, line ~174)

The `dispatch-debriefs`/`dispatch-ops` row's 2026-08-30 narrative
("run-status ping only; report content stays vault-only") is now
incomplete on the *delivery channel* axis:

- `weekly_summary.py` now passes `email=True` to `send_dual` — the FULL
  weekly summary content (not just a ping) is now also delivered to the
  operator inbox via ntfy's `X-Email` relay (`config.operator_email()`).
- `second_brain_daily.py` / `second_brain_weekly.py` pass `email=True` to
  `send_run_status` — email carries only the "ran OK, report at <vault
  path>" status line; their report content genuinely stays vault-only, so
  that part of the doc's claim still holds for the digest fleet.

Before this commit no code path in the repo ever set `X-Email` (confirmed
in the diff's own audit note). The email leg is opt-in per call, off by
default, so no other topic's behavior changed.

### 3. docs/DATA_SOURCES.md — OpenSky registry section is stale (pre-dated-snapshot)

The "OpenSky Network Aircraft Database" section (~line 750, "Last
verified: 2026-07") describes only the rolling
`aircraftDatabase.csv` URL and says updates "were on hold as of the last
check" — treat as frozen. Since de0f53d (yesterday) and this commit, that
is no longer how the platform consumes OpenSky: real dated monthly
snapshots (`aircraft-database-complete-YYYY-MM.csv`) exist at
`s3.opensky-network.org/data-samples/metadata/`, and
`check_opensky_freshness()` was repointed this commit from the dead
rolling-file HEAD probe (which could never detect a new dated snapshot —
the thing it polled never changes) to a cheap prefix-filtered S3
ListObjectsV2 listing, run monthly by the poller
(`WatchlistSweep.OPENSKY_FRESHNESS_INTERVAL`), importing the ~103 MB file
only when a genuinely new month appears. The doc's access URL, freshness
characterization, and "supplementary/best-effort frozen" framing all
predate this and need updating.

## Minor / cosmetic (noted, not vault-worthy on their own)

- `geo_filter.distance_nm()` (added this commit) is dead on arrival: its
  docstring says it was added so `fdps_parser`'s meter-fix alert uses a
  public shared primitive, but `fdps_parser` still imports and calls
  `_haversine_nm` directly (fdps_parser.py:60, :1920). Zero callers.
  Harmless — the primitive *is* shared, just via the private name — but
  the wrapper's stated reason for existing is false.
- `docs/DEDICATED_MODELS_PLAN.md` mentions ops-brief Modelfiles — already
  historical (Ollama-era, superseded by the 2026-08-27 llama.cpp
  cutover), not new drift from this commit.

## Still accurate (checked, no drift)

- `src/ingest/README.md` — TBFM `<sta>` capture-trap note, feed table,
  SWIM handler descriptions: unaffected by the parser diffs (the fdps
  change is alert-side, not parse-side; DC_METER_FIXES isn't described
  here).
- `src/shared/watchlist_README.md` — no FDPS approach/proximity claims;
  unaffected.
- `README.md` — no claims invalidated by this commit.
- `docs/CLAUDE_MD_DRIFT_REPORT.md` — regenerated 2026-09-02 05:15 by the
  daily checker, "No drift found"; consistent with the failed-unit
  reality below (verify-manifest failures are in CLAUDE.md's Known-bad).
- CLAUDE.md's 2026-09-02 expected/self-resolving entry — confirmed
  accurate live, with a count update and one wrinkle (next section).

## Live-state verification

- **22 user units are in failed state** (06:00–09:11 EDT today), every
  one checked and every one the *expected* `verify-manifest: INTEGRITY
  FAILURE` pattern from CLAUDE.md's 09-02 entry (which names only 4 —
  same pattern, wider blast radius: all poller-image skills whose timers
  fired this morning). Running `poller:latest` is build-date
  `20260902T032046Z` — built from the night's edited-but-unsigned source,
  exactly as that entry says. ops-brief's 09:05 fire (4 min pre-commit)
  failed the same way, so **the context-budget fix has still never run
  live**; first real test is the first fire after a post-signing rebuild.
- **Wrinkle on "resolves once this pass signs"**: an in-flight
  `podman build -f Containerfile.ingest` has been running since 07:15 EDT
  (2 h+, consistent with the IPv6-tether slowness in this morning's vault
  note). Its build context was snapshotted *before* the 09:09 signing, so
  when it finally lands, the ingest image will fail verified-exec again —
  that build is wasted and needs a re-run against the signed tree.
- **Concurrent session activity observed mid-check** (~09:12–09:14 EDT):
  `scripts/scrub-public-tree.py` was edited (allowlisting
  `config.operator_email()`'s default address, comment says
  operator-confirmed safe to publish — a direct follow-on to this
  commit's hard-coded default, which the public-mirror email scrub would
  otherwise block) and the manifest was re-signed; changes staged,
  uncommitted at check time. My first `verify-manifest` run at ~09:13
  caught the mid-edit window and reported a failure that was a race, not
  a real break — as of 09:14 `verify-manifest: OK, all 873 files match`
  against the working tree. Any image rebuild should happen after that
  pass commits, or the freshly-baked manifest will exclude the scrub
  change.

## Not verified (pending, by design)

- ops-brief fix end-to-end in-image (blocked on rebuild, above). The fix
  itself was validated this morning against llama-chat's real `/tokenize`
  (3791 vs 4096 budget) per the vault note.
- Actual email delivery through ntfy's SMTP relay — no test email sent
  (would notify the operator); first scheduled fire of weekly-summary /
  second-brain-daily post-rebuild is the real test.
- Meter-fix alert firing — needs a watched flight within 50 nm of a
  resolved fix, post-rebuild.

## Second-pass verification (09:18 EDT, separate session)

A second drift-check session ran ~2 minutes after the above was written
(same prompt, re-invoked — the earlier "concurrent session" observations
above and this file's two passes are two runs of the same check). It found
the prior pass first via second-brain search (vault note
`corporatetraveldc/01-Sources/manual/20260902T131642Z.md`) and
independently re-verified rather than re-deriving:

- Drifts 1–3 confirmed still present in the docs verbatim
  (ALERT_REFERENCE.md `TH` section line ~204 and topics-table
  `dispatch-debriefs`/`dispatch-ops` row; DATA_SOURCES.md OpenSky section
  line ~750). `geo_filter.distance_nm()` still has zero callers.
- Live state unchanged: same 22 failed units (all the expected
  verify-manifest pattern), `poller:latest` still the pre-signing
  `20260902T032046Z` build, ops-brief context-budget fix still never run
  live. The wasted `Containerfile.ingest` build is still in flight
  (2 h 02 m at check time, build-date label `20260902T032046Z` — confirms
  its context predates the signing; still needs a re-run when it lands).
- New since the first pass: the scrub-allowlist change committed as
  `f001fb5` (09:15:49), and `verify-manifest: OK -- signature valid, all
  873 files match` now holds against the *committed* tree, not just the
  working tree. Images can rebuild against HEAD safely now.

No new drift found; nothing persisted to the vault by the second pass —
the 09:16 note already covers all three drifts, and duplicating it would
pollute future searches.
