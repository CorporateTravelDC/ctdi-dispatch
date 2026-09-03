# Live State Check — 2026-09-03 (post-commit 457c61d)

Doc-drift check scoped to commit `457c61d` ("Forward-only content-hash
dedup redesign for SWIM alerts + watchlist path"), run ~03:10–03:25 EDT,
minutes after the commit landed. Checked README.md, CLAUDE.md, docs/
(ALERT_REFERENCE.md, ALERT_ARCHITECTURE.md, INFRA_MAP.md,
REFERENCE_INFRA.md), src/ingest/README.md, src/shared/watchlist_README.md
— against the diff, the current source, and the live system (systemctl
--user, podman ps/images, dedup state files in /var/lib/corporatetraveldc).

Prior art consulted first (second-brain search: `push_dedup`, `dedup`,
`forward-only`, `content-hash`, `UAL1369`): vault notes
`corporatetraveldc/01-Sources/manual/20260817T015317Z.md` and
`20260817T015915Z.md` document the 2026-08-16 slot/content_key-inversion
fix across seven call sites that this redesign directly builds on — the
"windows were never the problem, slot identity was" finding. Nothing in
the vault yet documents the forward-only redesign itself (the newest
session note, `20260903T031114Z.md`, predates this commit), so this
check's real-drift findings are persisted there as well.

## Live-state verification (all healthy)

- All 5 platform images (`poller`/`pusher`/`ingest`/`web`/`runner`)
  rebuilt 2026-09-03 04:38 UTC (00:38 EDT) — after the night's source
  edits and manifest signing, before the git commit itself (the usual
  sign → rebuild → deploy → commit sequence). All containers restarted
  ~01:10 EDT and running; **0 failed user units** — the 2026-09-02
  "expected/self-resolving verified-exec" CLAUDE.md entries have indeed
  resolved as predicted.
- The redesigned code is live: dedup state files actively written
  post-deploy (`pusher-tfms_alerts` 03:12, `pusher-tfms_gadv_alerts`
  03:09, `pusher-stdds_taxi_alerts` 03:12, `pusher-notam` 02:04 EDT).
  `pusher-watchlist-event-dedup.json` holds the UAL1369 slot
  (`wl-flight-ual1369-20260902:fids_update`) that motivated the redesign.
- Caller split audited against source — matches the commit message
  exactly. `should_push_periodic()` (deliberate time-based semantics):
  `ntfy_push.py:210` + `watchlist.py:1040` (90s ambiguous-status TTL),
  `fdps_parser.py:1891/:1957` (proximity episode gates, 600s),
  `itws_parser.py:766` (severe-wx 20-min heartbeat, with an explicit
  stay-periodic comment at :729), `ingest_feed_watch.py:171`,
  `route_impact.py:195`, `tfr_enrichment.py:54`, `pusher/main.py:123`
  (VIP TFR) and `:463/:495` (flight-landing). Every other call site is
  forward-only `should_push()`.

## Drift found (real — docs invalidated by this commit)

None of these were updated by the commit (its only ALERT_REFERENCE.md
change was the daily-watch line-number table). Report-only: no doc edits
made this pass — fixing them means another sign cycle, operator's call.

### 1. src/shared/watchlist_README.md:190–192 — the headline drift

> "**Deduplication:** the same `entry_id` + `event_type` (content-aware —
> detail hashed with timestamps bucketed to 10-minute windows) will not
> re-fire within 5 minutes (`_DEDUP_WINDOW_SECS = 300`)."

This describes exactly the behavior the commit was written to kill, on
exactly the path (watchlist `_check_dedup`) the UAL1369 TMI re-page
incident hit. Now: unchanged content never re-fires on elapsed time —
suppression lasts as long as PushDedup retains the slot (floor 7 days);
only a genuine content change (or the fca_id sub-identity split) fires
again. `_DEDUP_WINDOW_SECS = 300` still exists at `watchlist.py:87` and
is still passed as `dedup_secs`, but no longer governs re-firing for this
caller. The 10-minute timestamp bucketing claim is still accurate
(`_TS_BUCKET_MINUTES = 10`).

### 2. docs/ALERT_REFERENCE.md — four per-parser dedup descriptions now wrong

The canonical alert-path doc still describes time-window semantics for
paths that are now forward-only:

- **GADV** (~line 296): "on the advisory number (**not content hash** — …
  the number itself is the correct 'have we shown this one' key, 1-hour
  window)". Now content-keyed on `advisoryTitle|advisoryText`
  (`tfms_parser.py:1495`) precisely so in-place revisions under the same
  advisory number re-fire; there is no 1-hour re-fire window anymore.
- **NOTAM** (~line 328): "Deduped via `_NOTAM_DEDUP` on the NOTAM ID — a
  NOTAM re-transmitted unchanged won't re-fire; an amended NOTAM (**new
  ID**) will." Mechanism is now slot=ID, content=classification +
  effective window + full text (`aim_parser.py:285`), so an amendment
  under the **same** ID re-alerts. Note the doc's "unchanged won't
  re-fire" was actually false before this commit (~daily repeats) and is
  only now true. Operational side note: the code's own migration comment
  says each already-alerted live NOTAM re-alerts once on next rebroadcast
  post-deploy, then goes quiet — expect one bump, not a regression.
- **APTC** (~line 293): "on `aptc:{airport}:{rate}:{weather}` (15-min
  window)". Doubly stale — the key was split into slot=airport /
  content=rate:weather back on 2026-08-16 (this doc never caught up),
  and the 15-min window no longer applies.
- **TBFM** (~line 260): "on `tbfm:{fix}:{seq_count}`". Now
  slot=`tbfm:{fix}`, content=**band-bucketed** count
  (`bucket_count(seq_count)`, ±1 jitter hashes identically). The doc's
  conclusion ("holding steady won't re-fire; a genuine change will") is
  directionally still right, but a ±1 wiggle no longer counts as change.
- **TFMS amendment header note** (~lines 104–109): "against the 30-min
  `_TFMS_ALERT_DEDUP` window … previously … no dedup beyond the generic
  5-minute window". The window framing is obsolete; ironically its
  "suppressed indefinitely" claim only became true with this commit.

### 3. src/ingest/README.md — same window framing, two spots

- Lines 513–524 (amendment dedup): "against the shared 30-minute
  `_TFMS_ALERT_DEDUP` window", "generic 5-minute watchlist window" —
  same obsolete framing as above.
- Lines 212–213 (REROUTE → watchlist): "6 h dedicated dedup … window so
  a real revision fires immediately" — `_REROUTE_WATCHLIST_DEDUP` is a
  forward-only caller now (`tfms_parser.py:2393`); revisions still fire
  immediately, but there is no 6-hour re-fire cadence.

### 4. Top-level one-liners: README.md:412, docs/INFRA_MAP.md:648, docs/REFERENCE_INFRA.md:173

"5-minute content-aware dedup" (README, INFRA_MAP) / "A short dedup
window prevents re-firing the same event repeatedly" (REFERENCE_INFRA).
The honest one-liner is now "content-change-only dedup" — five minutes
plays no role on the watchlist path.

## Verified still accurate (checked, no drift)

- ALERT_REFERENCE.md's **ITWS** section (20-min window, content change
  fires immediately) — that path deliberately stayed
  `should_push_periodic()`; the doc happens to remain exactly right.
- **FDPS proximity / meter-fix proximity** (600s episode gating), **VIP
  TFR** (1-hour window + `hot=True` bypass in pusher/route_impact/
  tfr_enrichment), **feed-health** watch, **flight-landing** dedup — all
  periodic by design, docs unaffected.
- Marine One "no dedup window, every detection fires" — unchanged.
- STDDS incursion path's previous_bitmask change-gate description —
  unchanged (and its "a time window would be the weaker gate" rationale
  now describes the whole platform's default).
- docs/ALERT_ARCHITECTURE.md — its "window" references are sector-trend
  comparison windows, not PushDedup; clean.
- `src/ingest/README.md:459` "50 NM approach alerts (10-min dedup)" —
  still true (periodic caller).
- Dated audit/investor documents (DRIFT_AUDIT_2026-08-16, OPUS blind
  reviews, investor-materials v1.5 "5-minute content-aware dedup"
  claims) — frozen historical snapshots, not current-state claims; noted,
  not counted as drift.

## Pre-existing drift observed in passing (NOT from this commit)

The banned airplanes.live flight-monitoring chain (purged from all code
2026-08-27/28; skills/flight-hifi-track/SKILL.md caught 2026-08-31) is
still documented as current in three places nobody swept:

- README.md:413–415 — "Flight monitoring source chain: FlightAware
  AeroAPI (if key set) → airplanes.live (free) → local UltraFeeder…"
- docs/REFERENCE_INFRA.md:174 — "Flight tracking defaults to the free
  `airplanes.live` API, with an optional FlightAware AeroAPI fallback…"
- src/shared/watchlist_README.md:200–202 — "…local FAA/OpenSky
  registries before airplanes.live's flaky `/v2/reg/`" (describes the
  pre-purge chain as current).

Code verified clean: `watchlist.py`'s remaining references are historical
comments plus the allowed `globe.airplanes.live/?icao=` click-through URL.
These three doc spots pre-date this commit and were not previously
recorded in the vault (searched `airplanes.live`, `README AND airplanes`).
