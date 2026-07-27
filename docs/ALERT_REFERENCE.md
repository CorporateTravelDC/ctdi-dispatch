# Alert Reference — ntfy topics, triggers, and dedup logic

_Compiled 2026-07-20. This is a full sweep of every ntfy push in the
corporatetraveldc codebase: what fires it, what topic it goes to, at what
priority, and what stops it from spamming. Written after a session that
fixed FDPS/ITWS/TFMS parsers, added GDP/GS/APTC/GADV/sector coalescing,
and found + fixed an intermittent ntfy 403 that was silently dropping
alerts — see "Push reliability" at the end._

## How to read this

Every alert path in this codebase funnels through one of two shared
helpers, never a raw `requests.post` in the calling code:

- **`shared/watchlist.py` → `_fire_ntfy_dual(domain_topic, title, detail_body, dispatch_body, priority)`**
  Fires two pushes at once: the full-detail body to `domain_topic`, and a
  concise one-liner to `dispatch` (the "everything" feed). Used by the
  ingest-side parsers (FDPS, ITWS, TFMS, TBFM) and the web-side webhooks.
- **`common/ntfy_push.py` → `send(topic, message, ...)` / `send_dual(full, concise, ...)`**
  Single-topic send, or a full+concise pair to two explicit topics (default
  `dispatch-debriefs` + `dispatch-ops`). Used by the poller-side skills
  (ops-brief, ep-advance, daily-brief, weekly-summary, freshness-audit,
  AIM/NOTAM alerts, route-impact, TFR enrichment, OSINT monitor).

Both now retry on failure (3 attempts, 0.5s/1s backoff) as of 2026-07-20 —
see the reliability note at the bottom.

## Topic index

| Topic | Fired by | Typical priority | Purpose |
|---|---|---|---|
| `tfr-alert` | fdps_parser (Marine One), tfr_enrichment | 3–5 | TFR / VIP movement narratives |
| `hot-alerts` | fdps_parser (Marine One only, via tfr-alert not this), tfr_enrichment, route_impact | 5 | VIP-only escalation feed — never fires for routine traffic |
| `nas-alerts` | tfms_parser, aim_parser | 2–5 | NAS congestion / program / restriction / NOTAM alerts. TFMS paths sector-coalesced (see below). As of 2026-07-21 this is NOT metering/proximity anymore — see tbfm-alerts. |
| `tbfm-alerts` | tbfm_parser (meter-fix sequencing), fdps_parser (DCA-proximity/watchlist track events) | 2–3 | **Added 2026-07-21.** Metering + proximity congestion signal, folded together per operator direction (both answer "how compressed is this airspace right now"). Also fires to a per-sector topic below when the event's controlling facility is one of the 8 tracked ARTCCs. |
| `tbfm-zny` / `tbfm-zdc` / `tbfm-zid` / `tbfm-zob` / `tbfm-zatl` / `tbfm-zhu` / `tbfm-zla` / `tbfm-zse` | tbfm_parser, fdps_parser (same events as tbfm-alerts, additionally routed) | 2–3 | **Added 2026-07-21.** Per-ARTCC copy of the tbfm-alerts push (ZNY/ZDC/ZID/ZOB/ZATL/ZHU/ZLA/ZSE=Seattle), so a sector-specific subscription shows only that sector's metering/congestion trend. See `shared/sector_coalesce.py::sector_ntfy_topic()`. |
| `wx-alerts` | itws_parser | 4 | High-severity terminal weather (microburst, wind shear, etc.) |
| `flight-alerts` | web/routes/watchlist.py (add/remove) | 2–3 | Flight watchlist add/remove events |
| `train-alerts` | web/routes/watchlist.py (add/remove) | 2–3 | Train watchlist add/remove events |
| `vessel-alerts` | web/routes/watchlist.py (add/remove) | 2–3 | **Added 2026-07-21** alongside the vessel entry_type stub — previously misrouted into train-alerts, now its own topic. |
| `dispatch` | every `_fire_ntfy_dual` call (paired with domain topic) | matches paired topic | Concise everything-feed — one line per event across all domains |
| `dispatch-debriefs` / `dispatch-ops` | `common.ntfy_push.send_dual` default topics | 3 | Generic full/concise pair when a skill doesn't specify its own topics |
| `ops-brief` | poller/skills/ops_brief.py, daily_brief.py | 3 | Hourly + daily operational brief |
| `ops-health` | freshness_audit.py, container-mem-watch.sh, scheduled-ingest-restart.sh | 2–4 | Feed staleness, container memory pressure, preventive restarts |
| `ep` / `ep-advance` | ep_advance_brief.py | 3 / 4 | Executive-protection concise / full narrative |
| `ep-briefs` | (reserved, on-demand EP snapshots) | — | — |
| `cps` | pusher (from cps_recompute.py's written score) | — | Critical Predictability State change |
| `osint-alerts` | osint_monitor.py | scope-dependent (`PUSH_PRIORITY` by score_label) | Keyword/RSS/marketing intel hits above `push_threshold` |
| `reservations` | web/routes/webhooks.py (LimoAnywhere) | 3 | Reservation created/updated/cancelled |
| `calls` | web/routes/webhooks.py (RingCentral, 3CX) | 3 | Call events from phone system integrations |

## Ingest-side parsers (push-primary, `shared/watchlist.py` path)

### FDPS (`ingest/parsers/fdps_parser.py`)

- **Marine One / POTUS detection** (`check_marine_one` → `_fire_marine_one_ntfy`):
  fires when `is_marine_one(callsign, squawk)` matches AND (source is FH,
  callsign alone is enough) OR (source is TH, position is within
  `MARINE_ONE_RADIUS_NM` of DCA). Topic `tfr-alert`, **priority 5**, no
  dedup window (every detection fires — this is deliberately not
  throttled; a real POTUS movement should never be silenced). Also writes
  a 30-minute `swim_alert` row.
- **Watchlist matches** (`check_fdps_watchlist`): matches a parsed FDPS
  event's callsign/gufi against active flight watchlist entries.
    - `FH` (filed) → `watchlist_event_hit` priority 3, plus `nas-alerts`
      via `_fire_fdps_nas_alert`.
    - `CL` (cancelled) → `watchlist_event_hit` priority 4, plus `nas-alerts`.
    - `TH` (track) → `_maybe_alert_on_approach`: fires only when the
      watched flight is within 50nm of its destination — deduped via
      `_FDPS_PROX_DEDUP` on `fdps:prox:{hex_id or callsign}` so continuous
      position updates don't spam once a flight is in the approach cone.

### ITWS (`ingest/parsers/itws_parser.py`)

- `check_itws_alerts`: fires for any parsed alert with `severity >=
  ITWS_ALERT_SEVERITY` (4). Topic `wx-alerts`, priority 4. No explicit
  dedup in this function — relies on the underlying product refresh
  cadence (ITWS products themselves only update every few minutes) to
  avoid rapid re-fire; genuinely worth adding a PushDedup here if this
  proves noisy in practice (not yet observed to be).

### TBFM (`ingest/parsers/tbfm_parser.py`)

- `check_tbfm_alerts`: groups sequences by meter fix, fires one alert per
  fix per distinct sequence count. Topic `tbfm-alerts` (moved off nas-alerts
  2026-07-21), **priority 2**, plus a duplicate push to the matching
  `tbfm-<sector>` topic when the fix's facility is one of the 8 tracked
  ARTCCs (ZNY/ZDC/ZID/ZOB/ZATL/ZHU/ZLA/ZSE)
  (lowest of any NAS alert — metering updates are routine, high-volume,
  and only useful as a trend signal, not an individual-event alert).
  Deduped via `_TBFM_ALERT_DEDUP` on `tbfm:{fix}:{seq_count}` — a fix
  holding steady at the same count won't re-fire; a genuine change in
  count will.

### TFMS (`ingest/parsers/tfms_parser.py`) — most alert paths of any single feed

All of the following are gated by `_DC_FACILITIES` (ZDC, PCT, DCA, IAD,
BWI) — non-DC-area events are written to `nas_programs` (if applicable)
but never alerted.

- **RSTR / GDP / GS** (`check_tfms_alerts`, called from `parse_tfms_message`
  whenever any program was extracted): deduped via `_TFMS_ALERT_DEDUP` on
  `{type}:{facility}`. **As of 2026-07-20, routes through
  `shared/sector_coalesce.py`** instead of firing `nas-alerts` directly —
  see "Sector/corridor coalescing" below for what that changes. Base
  priority 3, escalated to 4 when the sector is trending.
- **APTC** (airport config): not a program alert, doesn't share the RSTR/
  GDP/GS dedup. Fires only on a *change* for a DC-area airport — arrival
  rate drop of >=20% from the last-seen config, or weather category
  degrading to IMC/LVMC from VMC/unknown. Deduped via `_APTC_ALERT_DEDUP`
  on `aptc:{airport}:{rate}:{weather}` (15-min window). Also
  sector-coalesced.
- **GADV** (ATCSCC general advisory): fires only when the advisory's
  `facilities` field names a DC-relevant ARTCC/airport. Deduped via
  `_GADV_ALERT_DEDUP` on the advisory number (not content hash — ATCSCC
  re-broadcasts the same numbered advisory verbatim on every SWIM refresh,
  so the number itself is the correct "have we shown this one" key, 1-hour
  window). Also sector-coalesced.
- **Per-flight watchlist events** (TMI_FLIGHT_LIST, FlightTimes/
  FlightModify OOOI, departureInformation, arrivalInformation,
  trackInformation approach-proximity, flightPlanAmendmentInformation,
  TMI_UPDATE reroute status, FlightRoute SID/STAR): all couple into
  `watchlist_event_hit` (priority 3–4 depending on event type) rather than
  firing `nas-alerts` directly — these only matter for flights an operator
  has actually put on the watchlist, so there's no DC-area gate; the
  watchlist membership itself is the gate. `trackInformation` additionally
  only fires when the watched flight's TFMS ETA is within 30 minutes,
  deduped per-entry so continuous position pings don't spam.

### AIM/NOTAM (`ingest/parsers/aim_parser.py`)

- `_fire_notam_alert`: fires for any NOTAM matching the operator's watch
  set (facility/keyword scope, evaluated upstream of this function). VIP
  NOTAMs (POTUS/AF1/Marine One keyword match via `_is_vip_notam`) →
  `hot-alerts`, priority 5. Everything else in the watch set →
  `nas-alerts`, priority 3. Deduped via `_NOTAM_DEDUP` on the NOTAM ID —
  a NOTAM re-transmitted unchanged won't re-fire; an amended NOTAM (new
  ID) will.

## Poller-side skills (`common/ntfy_push.py` path)

- **ops_brief.py**: hourly operational brief, `_send_ntfy_dual` →
  `dispatch-debriefs` (full) + `dispatch-ops` (concise), priority 3.
  Webinar-defer path (fixed this session — see commit history) pushes a
  one-line defer notice instead of crashing.
- **daily_brief.py**: daily brief, topic `ops-brief`, priority 3.
- **weekly_summary.py**: topic `ops-brief`, priority 3 (docstring says
  "ops-brief" but fires via `send_dual` with default topics — verify
  against live behavior if this ever looks mismatched; not re-derived
  line-by-line in this pass).
- **freshness_audit.py**: topic `ops-health`, priority 2. Feed staleness
  check.
- **ep_advance_brief.py**: `ep-advance` (full narrative, priority 4,
  tags `shield,rotating_light`) + `ep` (concise, priority 3, tags
  `shield`).
- **route_impact.py**: VIP-path only — fires `hot-alerts` (priority 5)
  when active TFRs include a VIP TFR, using a stable dedup key built from
  VIP TFR IDs only (so routine non-VIP TFR churn, which changes hundreds
  of IDs every cycle, doesn't defeat the dedup or spam the feed). No VIP
  TFRs → DB write only, no push — this is the correct/expected path most
  of the time, not a degraded state.
- **tfr_enrichment.py**: same VIP-only gating pattern as route_impact —
  fires `tfr-alert` + `hot-alerts` (priority 5) on a stable VIP-TFR-ID
  dedup key, 1-hour window, VIP pushes always bypass suppression (`hot=True`).
- **osint_monitor.py**: fires `osint-alerts` for any monitored keyword/RSS/
  marketing item scoring above its scope's `push_threshold`. Priority
  comes from `PUSH_PRIORITY[score_label]` (LOW/MED/HIGH-style scoring, not
  a fixed value). Title is tagged `[EP]`/`[MKT]`/`[OSINT]` depending on
  scope type.
- **cps_recompute.py**: doesn't push directly — writes to `cps_scores`,
  and the **pusher** container (separate service) is what actually fires
  the `cps` topic on a score change.

## Web-side webhooks (`web/routes/webhooks.py`, `shared/watchlist.py` path)

- **LimoAnywhere** (`/limoanywhere/reservations`): every reservation
  create/update/cancel event → `reservations` (full) + `dispatch`
  (concise), priority 3. No dedup — every distinct webhook delivery fires
  (LimoAnywhere doesn't re-send unchanged events, so this is safe as-is).
- **RingCentral** (`/ringcentral/events`) and **3CX** (`/3cx/events`):
  every call event → `calls` (full) + `dispatch` (concise), priority 3.
  Same no-dedup reasoning.

## Sector/corridor coalescing (new, 2026-07-20)

`shared/sector_coalesce.py` sits between TFMS's RSTR/GDP/GS/APTC/GADV
alert paths and the actual ntfy fire. Every event first resolves to a
named sector (`DC_LOCAL`, `NEW_YORK`, `BOSTON`, `ST_LOUIS`, `ATLANTA`;
`OCEANIC_ATLANTIC` and `GULF` are defined but empty — no real captured
sample this session confirmed a facility code for either, left honest
rather than guessed), then:

- **Escalation**: if the sector's rolling 15-minute event count is >=3x
  the count in the 15 minutes before that (with a floor so a single event
  never trivially "escalates" against an empty baseline), the alert title
  gets an `[ESCALATING/{sector}]` prefix and priority bumps by 1 (capped
  at 5).
- **Silence**: an operator can silence a sector or a feed independently
  via `POST /api/v1/sectors/{sector}/silence` or `POST
  /api/v1/sectors/feed/{feed_name}/silence` — off by default, persisted
  to `/var/lib/corporatetraveldc/sector_coalesce_silence.json` so it
  survives the frequent preventive ingest restarts. Silencing suppresses
  the ntfy push but does NOT stop counting, so escalation detection stays
  accurate underneath and un-silencing later shows the true trend rather
  than a blank slate.
- **Query**: `GET /api/v1/sectors` (per-sector rollup, including which
  feeds are contributing) and `GET /api/v1/sectors/by-feed` (reverse —
  which sectors a given feed is touching) expose both directions the
  operator asked for.

Not yet wired: FDPS/ITWS/AIM alert paths still fire directly rather than
through sector coalescing (TFMS was the highest-overlap starting point
given today's parser work). Extending coalescing to the other feeds is a
natural next increment if the sector/trend view proves useful in practice.

## Push reliability (found + fixed 2026-07-20)

Both `_fire_ntfy_dual` (shared/watchlist.py) and `send`
(common/ntfy_push.py) were firing exactly once with no retry. Over a
90+ minute window this session, pushes to `nas-alerts` and `dispatch`
intermittently returned `403 Forbidden` — with nothing in ntfy's own
server logs to explain it (its `messages_published` counter climbed
steadily and healthily through the same windows, and a manual replay of
an identical failed request succeeded immediately). This points to a
transient client/network-path hiccup — six SWIM feed threads plus the
poller/pusher/web containers all potentially hitting ntfy at once under
rootless podman networking is the leading suspect — rather than a
deterministic auth or config bug. Root cause not fully confirmed.

Both push paths now retry up to 3 times with exponential backoff (0.5s,
1s) before giving up, and log the response body on final failure (previously
only the status line was logged). Confirmed live: one push hit 403 on
attempt 1 and succeeded on retry 2, 0.5s later — an alert that would
previously have silently vanished.

**Known earlier fixes in this area, for context:**
- ntfy's default rate limits (`visitor-request-limit-burst`/`-replenish`)
  are tuned for a public multi-tenant instance and were 429-ing internal
  container traffic (`behind-proxy: true` with no reverse proxy in the
  internal container→host path collapses visitor identity to ~4 buckets).
  Raised generously in `config/ntfy/server.yml` — this is a *different*
  status code (429 vs. the 403 above) and a different root cause.
- `container-mem-watch.sh` and `scheduled-ingest-restart.sh` had a bug
  (fixed 2026-07-19, predating this session) where `NTFY_TOKEN` was read
  from the wrong env file entirely, causing every alert from those two
  scripts to silently 403 from day one. Already fixed; mentioned here only
  because it's the same failure mode (silent 403) with a different, fully
  root-caused cause — worth keeping distinct from the still-open one above
  in any future investigation.
