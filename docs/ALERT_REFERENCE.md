# Alert Reference — ntfy topics, triggers, and dedup logic

_Compiled 2026-07-20, extended 2026-07-27, **reconciled against live code
and live ntfy state 2026-08-19**. This catalogs the ntfy push paths in the
corporatetraveldc codebase: what fires it, what topic it goes to, at what
priority, and what stops it from spamming. Originally written after a
session that fixed FDPS/ITWS/TFMS parsers, added GDP/GS/APTC/GADV/sector
coalescing, and found + fixed an intermittent ntfy 403 that was silently
dropping alerts — see "Push reliability" at the end. Extended 2026-07-27
to cover the approval-gate topic and every standalone ops script that
pushes ntfy directly rather than through the two shared Python helpers
below — that surface had grown since the original sweep and had never been
swept the same way._

> **Scope honesty (2026-08-19).** Earlier revisions of this file described
> themselves as "a full sweep of every ntfy push in the codebase." They
> were not, and the claim was doing real harm — it made the catalog's
> silence about a publisher read as evidence the publisher didn't exist.
> The per-parser / poller-skill / standalone-script sections below are
> accurate for the paths they cover, but roughly a dozen-plus additional
> live publishers were never listed. They are now enumerated in
> **"Publishers this catalog previously missed"** near the end of this
> file. Treat that section plus the sections above it as the catalog;
> treat neither as a guarantee of completeness. The reliable way to
> enumerate publishers is a fresh grep for `send(`, `send_dual(`,
> `fire_family_alert(`, `_fire_ntfy_dual(`, `ntfy_send`, `ntfy_alert`, and
> raw `curl`/`urllib`/`requests.post` against the ntfy base across `src/`
> and `scripts/` — this pass did exactly that, and cross-checked the
> result against 12 hours of `/var/lib/ntfy/cache.db`.

> **Line-reference re-verification, 2026-08-23 (live-system-first pass).**
> Every code line number in this file was re-checked against the current
> tree.
>
> **Follow-up, later the same day: that sweep missed two, now fixed.** The
> topic index's `vessel-alerts` row still cited `shared/watchlist.py:187-193`
> for the vessel branch (those lines are now inside
> `resolve_flight_identity()`'s FAA-registry lookup — the branch moved to
> `:371-381` when that function was extracted on 2026-08-22) and
> `web/routes/watchlist.py:425` for the vessel add (actually `:444-445`).
> Read "every line number was re-checked" as "most were"; re-derive with
> `grep -n` rather than trusting any figure here.
>
> Four groups had drifted and are corrected inline: the entire
> `thermal-ingest-guard.py` entry (all four alert line numbers *and* all
> its thresholds — that script was redesigned the same day), the three
> `tfms_parser.py` `fire_family_alert()` call sites, `local_airspace.py`'s
> `_fire_ntfy` sites, and the `poller/main.py` / `web/main.py` /
> `sector_coalesce.py` references. The "Publishers this catalog previously
> missed" tables were checked line-by-line and were **exactly** right
> (all 12 poller skills, all four non-poller Python services, all the
> standalone-script priorities including the six corrected on 2026-08-19).
> Also re-confirmed unchanged: `daily_brief.py` and `freshness_audit.py`
> still contain no ntfy code beyond their (still wrong) docstrings;
> `ntfy-container-alert.service` still does not exist on this host;
> `NTFY_FALLBACK_URL` is still unset in both env files and the ntfy Quadlet
> still publishes only `2586:2586`; `/var/lib/corporatetraveldc/watchdog-last-run.json`
> still does not exist, so `/admin/watchdog/status` still always reports
> `available: false`.

> **2026-08-11 addendum — read this first.** The per-parser catalog below is
> the 2026-07-27 sweep and remains accurate for the paths it covers, but it
> **predates the escalating family-alert rollout (2026-08-02/03)**, which
> changed how SWIM-parser alerts reach ntfy:
>
> - New topic families with per-zone siblings: `tfms-alerts`/`tfms-<zone>`,
>   `tbfm-alerts`/`tbfm-<zone>`, `fdps-alerts`, `itws-alerts`,
>   `aim_fns-alerts` (zones: zny, zdc, zid, zob, zatl, zhu, zla, zse) — all
>   routed through `shared/sector_coalesce.py::fire_family_alert()`,
>   escalating-only, with a per-topic throttle (default 60 s min interval),
>   per-topic enable/sanitize switches, and per-(feed, sector) escalation
>   thresholds, JSON-persisted to
>   `/var/lib/corporatetraveldc/sector_coalesce_silence.json`. Admin API:
>   `GET/POST /api/v1/sectors/topic/{topic}[/throttle|/enabled|/sanitize]`
>   (`web/routes/sectors.py`). Design rationale:
>   `docs/ALERT_ARCHITECTURE.md`. **Note (2026-08-19): the per-topic
>   enable/throttle/sanitize switches only govern topics fired through
>   `fire_family_alert()` — they are consulted in exactly one place
>   (`sector_coalesce.py:301-309`) and neither `ntfy_push.send()` nor
>   `watchlist._fire_ntfy_dual()` checks them. See ALERT_ARCHITECTURE.md §1
>   for the full scope caveat.**
>   - **CORRECTED 2026-08-19: `stdds` IS wired.** The "deliberately not
>     wired (no alert-worthy criteria defined yet)" line above was true when
>     written and became stale on 2026-08-03. `src/ingest/parsers/smes_parser.py`
>     now has four live `fire_family_alert("stdds", ...)` call sites —
>     PCT track-count trend (`smes_parser.py:390`, feed_name `stdds`,
>     `zone_split=False`), ASDE-X surface congestion (`:584`, feed_name
>     `stdds_surface`), SafetyLogicHoldBar incursion (`:902`, feed_name
>     `stdds_safety`, `escalating_only=False`), and taxi-phase
>     (`:1047`, feed_name `stdds_taxi`) — three of the four fronted by a
>     300-second `PushDedup` (`_STDDS_PCT_DEDUP`/`_STDDS_SURFACE_DEDUP`/
>     `_STDDS_TAXI_DEDUP`, `smes_parser.py:540-542`); the incursion path
>     deliberately has none and is gated on a real bitmask change instead
>     (see the STDDS section below). Live traffic confirmed on `stdds-alerts` and
>     `stdds-zatl` in the ntfy cache. STDDS also uses the only
>     per-*airport* zone split on the platform (`stdds-dca`/`stdds-iad`/
>     `stdds-bwi` instead of a pooled `stdds-zdc`) — see
>     `docs/ALERT_ARCHITECTURE.md` §6.
> - `vessel-alerts` now carries vessel add/remove **and position events**:
>   vessel *position* events used to misroute to `train-alerts`
>   (`watchlist_event_hit()` had no vessel branch) — **fixed 2026-08-11**
>   with an explicit vessel branch (`VSL ` title prefix). See
>   `src/shared/watchlist_README.md`.
> - **TFMS `flightPlanAmendmentInformation` gained content-hash dedup
>   2026-08-10**: keyed `tfms:amendment:{entry_id}:{route_text}` against the
>   30-min `_TFMS_ALERT_DEDUP` window, so unchanged rebroadcasts are
>   suppressed indefinitely while a genuinely new amendment fires
>   immediately. It was previously the one TFMS watchlist path with no dedup
>   beyond the generic 5-minute window.
> - The brief-class pipeline gained `brief-fallback-monitor` (hourly, loud
>   alert on deterministic-fallback degradation, 2026-08-08).

## How to read this

Alert paths in this codebase come from three places. Two are shared
Python helpers, never a raw `requests.post` in the calling code:

- **`shared/watchlist.py` → `_fire_ntfy_dual(domain_topic, title, detail_body, dispatch_body, priority)`**
  Fires two pushes at once: the full-detail body to `domain_topic`, and a
  concise one-liner to `dispatch` (the "everything" feed). Used by the
  ingest-side parsers (FDPS, ITWS, TFMS, TBFM) and the web-side webhooks.
- **`common/ntfy_push.py` → `send(topic, message, ...)` / `send_dual(full, concise, ...)`**
  Single-topic send, or a full+concise pair to two explicit topics (default
  `dispatch-debriefs` + `dispatch-ops`). Used by the poller-side skills
  (ops-brief, ep-advance, daily-brief, weekly-summary, freshness-audit,
  AIM/NOTAM alerts, route-impact, TFR enrichment, OSINT monitor). This is
  the only one of the three paths with a configured (if currently unused)
  fallback URL and a 401/403 ambiguous-status guard — see "Push
  reliability" below.

The third is **not** a shared helper at all: a dozen-plus standalone ops
scripts (`scripts/*.sh`) and `scripts/thermal-ingest-guard.py` each define
their own small `ntfy_send()`/`ntfy_alert()` function and hit ntfy with a
raw `curl` or `urllib` call. These don't share any code with each other or
with the two helpers above — no shared retry logic, no shared fallback,
each one independently reads `NTFY_BASE_URL`/`NTFY_TOKEN` (or, in one
case, an entirely different token file — see below) and rolls its own
`curl -H "Title: ..." -H "Priority: ..."` call. See "Standalone
bash/script alerts" below for the full list — this path was not covered
in the original 2026-07-20 sweep.

Both Python helpers retry on failure (3 attempts, 0.5s/1s backoff) as of
2026-07-20 — see the reliability note at the bottom. The standalone
scripts do not retry at all; most wrap the curl call in `|| true` or `||
log "warn" ...`, so a failed push there is silently dropped, not retried.

**Correction 2026-08-19: there is a fourth path, not three.**
`src/ingest/local_airspace.py:110` defines its own `_fire_ntfy()` using
`requests.post` — in-tree Python, but sharing nothing with either helper
above and not a `scripts/` file either. `src/acars_watcher/` and
`src/ais_watcher/` each roll their own JSON-payload POST as well. None of
these three retry or fall back. Any statement in this doc of the form "all
push paths do X" should be read as covering the two shared helpers only.

## Topic index

| Topic | Fired by | Typical priority | Purpose |
|---|---|---|---|
| `tfr-alert` | fdps_parser (Marine One), tfr_enrichment | 3–5 | TFR / VIP movement narratives |
| `hot-alerts` | fdps_parser (Marine One only, via tfr-alert not this), tfr_enrichment, route_impact, aim_parser (VIP NOTAMs) | 5 | VIP-only escalation feed — never fires for routine traffic. (This table lists `hot-alerts` twice; the second row below is the fuller one, covering the non-VIP severe-ops publishers added 2026-07-27.) |
| `nas-alerts` | aim_parser only (**not** tfms_parser — corrected 2026-08-19) | 3 | Residual NOTAM bucket: IAP / ASDE-X / other NOTAM-D content, fired directly by `aim_parser._fire_notam_alert()`'s third branch at priority 3. **`tfms_parser.py` no longer fires `nas-alerts` at all** — every TFMS path now goes through `fire_family_alert("tfms", …)` (`tfms-alerts`/`tfms-<zone>`) or `watchlist_event_hit`; the only surviving `nas-alerts` strings in that file are stale docstrings. Also NOT metering/proximity since 2026-07-21 — see tbfm-alerts. |
| `tbfm-alerts` | tbfm_parser (meter-fix sequencing) — **fdps_parser no longer, see below** | 2 | **Added 2026-07-21.** Metering congestion signal. Also fires to a per-sector topic below when the event's controlling facility is one of the 8 tracked ARTCCs. **Corrected 2026-08-19:** FDPS DCA-proximity/watchlist track events were moved off this topic onto `fdps-alerts`/`fdps-<zone>` on 2026-08-03 (`fdps_parser._fire_fdps_nas_alert()` → `fire_family_alert("fdps", "fdps", …)`, `fdps_parser.py:923`); this topic is TBFM-only now. |
| `tbfm-zny` / `tbfm-zdc` / `tbfm-zid` / `tbfm-zob` / `tbfm-zatl` / `tbfm-zhu` / `tbfm-zla` / `tbfm-zse` | tbfm_parser only (corrected 2026-08-19 — fdps_parser moved to the `fdps-*` family) | 2 | **Added 2026-07-21.** Per-ARTCC copy of the tbfm-alerts push (ZNY/ZDC/ZID/ZOB/ZATL/ZHU/ZLA/ZSE=Seattle), so a sector-specific subscription shows only that sector's metering trend. See `shared/sector_coalesce.py::sector_ntfy_topic()`. |
| `fdps-alerts` / `fdps-<zone>` | fdps_parser (watchlist + DCA-proximity track events, `feed_name="fdps"`, escalating-only, base priority 3); aim_parser (non-VIP flight-restriction NOTAMs, `feed_name="fdps_notam"`, `escalating_only=False`, `isolate=True`, base priority 4) | 3–4 | **Added 2026-08-03; previously undocumented in this table.** Two isolated contributing feed_names sharing one topic family — see `docs/ALERT_ARCHITECTURE.md` §2 "Isolation between siblings". |
| `itws-alerts` / `itws-<zone>` | itws_parser (`fire_family_alert("itws", …)`, `itws_parser.py:696`) | 4 | **Added 2026-08-03; previously undocumented in this table.** Escalating-only aggregate + per-zone copy, fired *in addition to* the legacy direct `wx-alerts` push (deliberately retained, not replaced). |
| `aim_fns-alerts` / `aim_fns-<zone>` | aim_parser (`fire_family_alert("aim_fns", …)`, `aim_parser.py:335`) | 3 | **Added 2026-08-02/03; previously undocumented in this table.** Fires alongside the direct `nas-alerts` push for the same NOTAM, escalating-only. |
| `tfms-alerts` / `tfms-<zone>` | tfms_parser (RSTR/GDP/GS at `:583` feed_name `tfms`, APTC at `:1262` feed_name `tfms_aptc`, GADV at `:1348` feed_name `tfms_gadv` — line numbers re-verified 2026-08-23) | 3–4 | **Added 2026-08-02; previously undocumented in this table.** The topics TFMS program alerts moved *to* when they left `nas-alerts`. |
| `stdds-alerts` / `stdds-dca` / `stdds-iad` / `stdds-bwi` / `stdds-<zone>` | smes_parser (four call sites: PCT trend, ASDE-X surface, SafetyLogicHoldBar incursion, taxi phase) | 2–3 | **Wired 2026-08-03; this table previously said "deliberately not wired".** Live traffic confirmed. DCA/IAD/BWI get individual topics rather than a pooled `stdds-zdc`. |
| `wx-alerts` | itws_parser | 4 | High-severity terminal weather (microburst, wind shear, etc.). Deduped since 2026-07-28 — see the ITWS section below. |
| `flight-alerts` | web/routes/watchlist.py (add/remove); **also** `ingest/local_airspace.py` (watched-aircraft proximity, paired with `dispatch`), `acars_watcher/acars_watcher.py` and `ais_watcher/ais_watcher.py` (both default `NTFY_TOPIC=flight-alerts`); **also** `shared/watchlist.py::resolve_flight_identity()` `identity_resolved` events (added 2026-08-22, deployed 2026-08-23 — priority 3, dual push with `dispatch`, one-time per entry on first hex-lock, body carries resolved hex/registration + a `globe.airplanes.live` tracking URL) | 2–3 | Flight watchlist add/remove events, plus local ADS-B/ACARS/AIS watcher hits (the last three were missing from this table before 2026-08-19) and one-shot identity-resolution hits |
| `train-alerts` | web/routes/watchlist.py (add/remove) | 2–3 | Train watchlist add/remove events |
| `vessel-alerts` | web/routes/watchlist.py (add/remove — `_fire_ntfy_dual(domain_topic="vessel-alerts")` at `:444-445`; batch-remove resolves it from the `{"vessel": "vessel-alerts"}` map at `:534-535`); **also** `shared/watchlist.py::watchlist_event_hit`'s `elif etype == "vessel"` branch (`:371-381`) for vessel position/status events | 2–3 | **Added 2026-07-21** alongside the vessel entry_type stub — previously misrouted into train-alerts, now its own topic. **Row corrected 2026-08-19:** the position/status leg was described in the 2026-08-11 addendum above but never reflected here, so this row still read as add/remove-only. `watchlist_event_hit`'s vessel branch (`VSL ` title prefix) is the fix for the old misroute into `train-alerts`. |
| `dispatch` | every `_fire_ntfy_dual` call (paired with domain topic); `ingest/local_airspace.py` Marine One / squawk 7700-7500-7600 local alerts (`dispatch` only, no paired domain topic); `poller/skills/dispatch_desk_memo.py` (priority 2); `poller/tools/watchlist_import.py` | matches paired topic | Concise everything-feed — one line per event across all domains |
| `dispatch-debriefs` / `dispatch-ops` | `common.ntfy_push.send_dual` default topics — in practice **`weekly_summary.py` only** (see the poller-skills section) plus `ops_brief.py`'s full leg on `dispatch-debriefs` | 3 | Generic full/concise pair when a skill doesn't specify its own topics. `dispatch-ops` is effectively weekly-summary-only by operator direction (`ops_brief.py:751-764`). |
| `ops-brief` | **`ops_brief.py` only** (`_send_ntfy_dual` → `topic_brief="ops-brief"`, `ops_brief.py:764`; also the webinar-defer path at `:450`) | 3 | Hourly operational brief, concise leg. **Corrected 2026-08-19: `daily_brief.py` does NOT push here — it does not push anywhere.** See the poller-skills section. |
| `ops-health` | container-mem-watch.sh, scheduled-ingest-restart.sh, thermal-ingest-guard.py, restore-network.sh, threat-resolve.sh, renew-tailscale-cert.sh, restart-stack.sh, nextcloud-health-check.sh, **plus** scheduled-integrity-sweep.sh, feed_db_integrity_check.py, brief-fallback-monitor.sh, ingest_feed_watch.py, ollama-swap-alert.sh, ollama-wedged-detector.sh, pull_path_verify.py, sdr-crashloop-guard.sh, nms_v240_post_deploy_check.py, claude-md-drift-daily.sh, governor-watch.py, uber-traffic-watch.py, ntfy-topic-count-watchdog.sh, acars/adsb-feed-silence-watchdog.sh, adsb-link-watchdog.sh, board_sweep.py, the six `*_daily_watch.py` skills, scripts/watchdog.sh (p2/p3 container-restart and warn-only paths), and scripts/failover-kickover-guardrail.py | 1–5 | Feed staleness, container memory pressure, preventive restarts, thermal/load tier shed+resume, network lockdown lift, cert renewal, stack restart status, Nextcloud health, integrity-sweep failures, Ollama swap/wedge, SDR guard, cross-link digests. By far the busiest topic — 63 messages in the 12 h before the 2026-08-19 reconciliation (dated observation, not a current count). The publishers in bold were absent from every prior revision of this table; see "Publishers this catalog previously missed" below. **`freshness_audit.py` is NOT a publisher** despite its own docstring — corrected 2026-08-19, see the poller-skills section. |
| `hot-alerts` | fdps_parser (Marine One only, via tfr-alert not this), tfr_enrichment, route_impact, aim_parser (VIP NOTAMs, priority 5), lockdown.sh (lockdown engaged, priority 4), threat-initiate.sh (manual threat response, **priority 5**), watchdog.sh (p5 manual-run-required alert; p4 full-stack-restart notice only on an operator `--allow-system-restart` run) | 4–5 | VIP-only / severe-ops escalation feed. Originally documented as VIP-only (fdps/tfr/route); as of 2026-07-27 also carries non-VIP severe-ops events (network lockdown, manual threat response) — same "wake someone up now" intent, different domain. Worth deciding later whether ops-severity events belong on a separate topic from VIP-movement events, since they're currently sharing one feed for two different kinds of urgency. See "Standalone bash/script alerts" below. |
| `ep` / `ep-advance` | ep_advance_brief.py | 3 / 4 | Executive-protection concise / full narrative |
| `ep-briefs` | (reserved, on-demand EP snapshots) | — | — |
| `cps` | pusher (from cps_recompute.py's written score) | — | Critical Predictability State change |
| `osint-alerts` | osint_monitor.py | scope-dependent (`PUSH_PRIORITY` by score_label) | Keyword/RSS/marketing intel hits above `push_threshold` |
| `reservations` | web/routes/webhooks.py (LimoAnywhere) | 3 | Reservation created/updated/cancelled |
| `calls` | web/routes/webhooks.py (RingCentral, 3CX) | 3 | Call events from phone system integrations |
| `approval-gate` | scripts/sudo-approval-gate.sh | 4 | **Added 2026-07-27.** Allow/Deny push for the two approval-gated sudo grants (`ollama.service` start/stop/restart, `dnf remove`/`autoremove`) — see `docs/SUDO_JUSTIFICATION_PROPOSAL.md`. Uses ntfy's `actions` field for inline Allow/Deny buttons that hit `/admin/approval-requests/{id}/resolve` directly from the notification, no need to open the app. No response within the request's TTL (600s default) is treated as a denial. Two independent failure layers were found and fixed 2026-08-20: a stuck Android notification channel (delivery worked, alerting didn't; fixed only by unsubscribe+resubscribe — Android channels ignore ntfy per-message `priority` after creation) and a hostname-wide Cloudflare Access gate intercepting the resolve links (fixed with the narrowly-scoped `dispatch-approval-resolve-bypass` Access app). Diagnose delivery separately from alerting; note this ntfy server has no per-topic ACLs at all (admin role = every topic), so per-topic-ACL theories are always wrong here. |
| `dispatch-alerts` | scripts/check-pat-expiry.sh | 4–5 | **Inconsistent, flagged 2026-07-27** — this is the only alert path in the codebase that (a) uses this topic name instead of `ops-health`/`dispatch`, and (b) reads its ntfy token from `~/.secrets/ntfy.token` instead of `NTFY_TOKEN` in `dispatch-secrets.env` like every other script. Functionally fine (fires correctly for GitHub PAT expiry warnings within 5 days), but worth normalizing to `ops-health` + the standard token source next time this script is touched, purely for consistency — not urgent. |

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
    - `FH` (filed) → `watchlist_event_hit` priority 3, plus a family alert
      via `_fire_fdps_nas_alert`.
    - `CL` (cancelled) → `watchlist_event_hit` priority 4, plus a family
      alert via `_fire_fdps_nas_alert`.
    - `TH` (track) → `_maybe_alert_on_approach`: fires only when the
      watched flight is within 50nm of its destination — deduped via
      `_FDPS_PROX_DEDUP` on `fdps:prox:{hex_id or callsign}` so continuous
      position updates don't spam once a flight is in the approach cone.
      On a hit it also calls `_fire_fdps_nas_alert` with `dist_nm`.

  > **Corrected 2026-08-19 — `_fire_fdps_nas_alert` no longer fires
  > `nas-alerts`, and its name is now a misnomer.** Despite the function
  > name, `fdps_parser.py:923` calls
  > `fire_family_alert("fdps", "fdps", facility, …, base_priority=3)`, so
  > these events land on **`fdps-alerts` / `fdps-<zone>`**, escalating-only,
  > subject to the family throttle. This moved on 2026-08-03; before that
  > the same events had briefly been on `tbfm-alerts`/`tbfm-<zone>`, and
  > before *that* on `nas-alerts`. The `fdps-*` topics were new
  > subscriptions at the time of the move, so anyone still subscribed only
  > to `nas-alerts` or `tbfm-alerts` stopped seeing FDPS watchlist/proximity
  > events on 2026-08-03 without any other signal. Marine One is unaffected
  > — it still fires `tfr-alert` directly at priority 5 and is deliberately
  > never folded into a family topic.

### ITWS (`ingest/parsers/itws_parser.py`)

- `check_itws_alerts`: fires for any parsed alert with `severity >=
  ITWS_ALERT_SEVERITY` (4). Topic `wx-alerts`, priority 4, **plus** a
  second push through `fire_family_alert("itws", "itws", airport, …,
  base_priority=4)` → `itws-alerts` / `itws-<zone>` (added 2026-08-03;
  the direct `wx-alerts` push was deliberately retained, not replaced,
  because `wx-alerts` is shared with NWS/METAR-derived content and
  dropping ITWS from it would be a visibility regression).

  > **Corrected 2026-08-19 — the dedup this section asked for has
  > shipped.** The paragraph above used to read "No explicit dedup in this
  > function… genuinely worth adding a PushDedup here." That was fixed on
  > 2026-07-28: `itws_parser.py:657` now constructs
  > `_itws_dedup = PushDedup("itws-alerts", dedup_secs=1200)` and gates on
  > `should_push(f"{airport}:{product_type}", content_hash(f"{sev}:{detail}"))`,
  > recording only after a successful push. The 20-minute window is
  > deliberately shorter than the 1-hour default used elsewhere (an hour of
  > silence on a still-active severe weather hazard is too long), and any
  > real content change — new severity or new detail text — fires
  > immediately regardless of the window via the content-hash comparison.
  > The trigger was a confirmed-live case of ITWS re-broadcasting the same
  > "N active hazard cell(s)" state roughly every 2 minutes for one ongoing
  > condition. Note the `PushDedup` namespace string is `"itws-alerts"`,
  > which is *also* a topic name — they are unrelated; the dedup covers the
  > `wx-alerts` push, and the family push has its own throttle.

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

**Corrected 2026-08-23: the gate is no longer uniformly `_DC_FACILITIES`.**
This section used to open "All of the following are gated by
`_DC_FACILITIES` (ZDC, PCT, DCA, IAD, BWI)". The 2026-08-03 eight-zone
extension replaced that hardcoded DC-only set with
`shared.sector_coalesce.is_tracked_facility()` on two of the three
program paths; only APTC still uses `_DC_FACILITIES`. Verified against
`src/ingest/parsers/tfms_parser.py`:

| Path | Gate | Line |
|---|---|---|
| RSTR / GDP / GS | `is_tracked_facility(facility)` — all 8 zones | `:541` |
| APTC (airport config) | `airport_upper in _DC_FACILITIES` — DC-only | `:1232` |
| GADV (general advisory) | `is_tracked_facility(f)` over the advisory's facilities — all 8 zones (the local variable is still named `dc_hit` for historical reasons) | `:1311` |

`_DC_FACILITIES` itself still exists at `:136` as
`{"ZDC","PCT","KDCA","KIAD","KBWI","DCA","IAD","BWI"}`. Non-tracked events
are written to `nas_programs` (if applicable) but never alerted.

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
  `facilities` field names one of the 8 tracked ARTCCs/airports
  (`is_tracked_facility()`, `:1311` — not DC-only, despite the `dc_hit`
  variable name). Deduped via
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
- **Identity-resolved notification** (added 2026-08-22, deployed
  2026-08-23): `shared/watchlist.py::resolve_flight_identity()` fires one
  `identity_resolved` `watchlist_event_hit` per entry (priority 3, dual
  push `flight-alerts` + `dispatch`) the first time a watched flight
  hex-locks; the body carries the resolved hex/registration plus a
  `https://globe.airplanes.live/?icao={hex}` tracking URL (appended to the
  push body only, never the title). Call sites: the poller flight sweep
  (`source="sweep"`) and `tfms_parser.py::_handle_flight_times` on the OUT
  transition (`source="tfms_out"`). One-time per entry — an
  already-hex-locked entry never re-fires.

### AIM/NOTAM (`ingest/parsers/aim_parser.py`)

- `_fire_notam_alert`: fires for any NOTAM matching the operator's watch
  set (facility/keyword scope, evaluated upstream of this function).
  Deduped via `_NOTAM_DEDUP` on the NOTAM ID — a NOTAM re-transmitted
  unchanged won't re-fire; an amended NOTAM (new ID) will. Note the dedup
  is only `record()`ed when at least one of the pushes below actually
  fired, so a total push failure leaves the NOTAM eligible to retry.

  **Corrected 2026-08-19: there are THREE routing branches, not two.** The
  earlier "VIP → hot-alerts, everything else → nas-alerts" description
  predates the 2026-08-03 refinement (operator: *"make nas-alerts mostly
  iap and asde-x type alerts and flight restrictions in fdps-{*} or
  hot-alerts respectively"*). Live shape (`aim_parser.py:249-345`):

    1. **VIP** (`_is_vip_notam` — POTUS/AF1/Marine One keyword match) →
       `hot-alerts` direct, priority 5. Never folded into a family topic,
       same design as Marine One in fdps_parser.
    2. **Non-VIP flight restriction** (`_is_flight_restriction_notam`) →
       `fire_family_alert("fdps", "fdps_notam", facility, …,
       base_priority=4, escalating_only=False, isolate=True)` →
       **`fdps-alerts` / `fdps-<zone>`**, priority 4, firing on *first*
       occurrence rather than waiting for a burst (a lone TFR is itself the
       alert). `isolate=True` keeps this feed_name's escalation counting
       entirely separate from fdps_parser's own proximity events even
       though both publish to the same topics — a burst of TFR NOTAMs must
       not make real FDPS proximity tracking read as escalating, or vice
       versa. This branch does **not** touch `nas-alerts`.
    3. **Everything else** (IAP / ASDE-X / other NOTAM-D — the residual
       bucket) → `nas-alerts` direct at priority 3, **and additionally**
       `fire_family_alert("aim_fns", "aim_fns", facility, …,
       base_priority=3)` → `aim_fns-alerts` / `aim_fns-<zone>`,
       escalating-only. Both fire for the same NOTAM; a subscriber to both
       topics sees it twice.

  `dispatch-alerts` is explicitly not used for NOTAMs.

### STDDS / ASDE-X (`ingest/parsers/smes_parser.py`) — added to this doc 2026-08-19

Wired 2026-08-03; this catalog previously (and wrongly) recorded stdds as
"deliberately not wired." Four distinct `fire_family_alert("stdds", …)`
call sites, each with its own feed_name so their escalation counts stay
independent. **Corrected 2026-08-23: only *three* are fronted by a
`PushDedup`, not all four.** `smes_parser.py:540-542` defines exactly
`_STDDS_PCT_DEDUP` / `_STDDS_SURFACE_DEDUP` / `_STDDS_TAXI_DEDUP` (300 s
each); there is no `_STDDS_SAFETY_DEDUP`. The incursion path is instead
gated on a genuine state change: `check_incursion_alert()` returns early at
`:869` (`if previous_bitmask is not None and previous_bitmask ==
new_bitmask`), so an unchanged SafetyLogicHoldBar bitmask never reaches
`fire_family_alert()` at all. This is deliberate and the code says so — the
comment at `:536`, immediately above the three dedup definitions, records
that the safety path was left out "its previous_bitmask comparison already
only fires on a genuine [change]". A time window would be the *weaker*
gate here: a real incursion status flip must not be swallowed just because
another one fired 200 seconds earlier.

| Call site | feed_name | Topics | Escalation | Base priority |
|---|---|---|---|---|
| `:390` PCT overall track-count trend | `stdds` | `stdds-alerts` only (`zone_split=False` — PCT is one facility, a per-zone copy would be a duplicate) | escalating-only | 2 |
| `:584` ASDE-X surface congestion, per airport | `stdds_surface` | `stdds-alerts` + per-airport/zone topic | escalating-only | 2 |
| `:902` SafetyLogicHoldBar incursion signal | `stdds_safety` | `stdds-alerts` + per-airport/zone topic | **`escalating_only=False`** — an incursion signal fires on first occurrence/change. **No `PushDedup`** — gated on a real bitmask change instead (see above) | 3 |
| `:1047` SurfaceMovementEvent taxi-phase gauge | `stdds_taxi` | `stdds-alerts` + per-airport/zone topic | escalating-only | 2 |

**The "base priority" column is the DC-regional value only.** All four call
sites pass their base through `_stdds_priority(airport, N)`
(`smes_parser.py:518-521`): `KDCA`/`KIAD`/`KBWI` (`_STDDS_REGIONAL_AIRPORTS`)
keep the figure shown; every other tracked airport fires one level lower,
floored at 1. So nationwide surface/taxi/PCT pushes land at priority **1**
and nationwide incursion pushes at **2**. Topics and firing decisions are
unaffected — only the `priority` field changes. No other family does this.

STDDS is the only family with a **per-airport** zone split rather than a
purely per-ARTCC one: DCA, IAD and BWI get `stdds-dca`/`stdds-iad`/
`stdds-bwi` instead of pooling into a single `stdds-zdc`
(`_STDDS_REGIONAL_AIRPORTS` in `smes_parser.py`); the other seven zones are
pooled like every other family. Rationale and the "swap the home region"
instructions are in `docs/ALERT_ARCHITECTURE.md` §6.

## Poller-side skills (`common/ntfy_push.py` path)

> **Read this before trusting any entry below.** Three of the five
> brief/audit skills in this section were documented from their own
> module docstrings rather than from their call sites, and two of those
> docstrings are simply wrong — the skills write a file and push nothing.
> Corrected inline below on 2026-08-19; the docstrings themselves are code
> and are out of scope for this doc pass, so expect them to keep
> contradicting this section until someone fixes them.

- **ops_brief.py**: hourly operational brief, `_send_ntfy_dual` →
  `dispatch-debriefs` (full) + **`ops-brief`** (concise), priority 3.
  Webinar-defer path pushes a one-line defer notice instead of crashing
  (also to `dispatch-debriefs` + `ops-brief`, `ops_brief.py:450`).
  **Corrected 2026-08-19:** the concise leg is *not* `dispatch-ops`. It
  moved to `ops-brief` on 2026-08-02 by explicit operator direction —
  `dispatch-ops` had been shared, undocumented, with `weekly_summary.py`,
  while `ops-brief` (the topic every click-map entry and docstring claimed
  was real) had nothing publishing to it at all. The fix made `dispatch-ops`
  weekly-summary-only and moved ops_brief's concise push onto its own
  documented name. Rationale is preserved in the `_send_ntfy_dual`
  docstring at `ops_brief.py:751-764`. Confirmed live: `ops-brief` carried
  12 messages in the last 12 h, all hourly OPS BRIEFs.
- **daily_brief.py**: **sends no ntfy push at all.** Corrected 2026-08-19 —
  this entry previously read "topic `ops-brief`, priority 3", which came
  from the module docstring (`daily_brief.py:11`, "Pushes to ntfy topic
  'ops-brief' at priority 3"). The module neither imports nor calls any
  ntfy code; the only `ntfy` token in the file is that docstring line. Its
  single output is
  `pathlib.Path(config.state_dir()) / "daily-brief.txt"` via `write_text()`
  (`:100-102`). Separately, `daily_brief.py:6` documents its schedule as
  "05:00 ET daily (`corporatetraveldc-daily-brief.timer`)" — **that timer
  does not exist**; `systemctl --user list-unit-files` has no
  `corporatetraveldc-daily-brief.*` unit of any kind. So the skill neither
  runs on the schedule it claims nor pushes where it claims.
- **weekly_summary.py**: **`dispatch-debriefs` (full) + `dispatch-ops`
  (concise)**, priority 3 — resolved 2026-08-19. The previous entry
  recorded the docstring's "ops-brief" claim and declined to resolve the
  mismatch ("verify against live behavior if this ever looks mismatched").
  Resolving it now: `weekly_summary.py:209` calls
  `_ntfy.send_dual(summary, summary[:280], title=title)` with **no** topic
  arguments, so it takes `send_dual`'s defaults, which are
  `topic_full="dispatch-debriefs"` / `topic_brief="dispatch-ops"`
  (`src/common/ntfy_push.py:208-209`). The live ntfy cache confirms it: the
  most recent "Weekly Ops Summary [FALLBACK]" appears exactly once on
  `dispatch-debriefs` and once on `dispatch-ops`, and nothing by that title
  ever appears on `ops-brief`. The docstring (`weekly_summary.py:9`) is
  wrong, not the code.
- **freshness_audit.py**: **sends no ntfy push at all.** Corrected
  2026-08-19 — this entry previously read "topic `ops-health`, priority 2",
  again taken from the module docstring (`freshness_audit.py:10`). As with
  `daily_brief.py`, no ntfy import, no ntfy call; the single `ntfy` grep hit
  is that docstring. Its only output is
  `pathlib.Path(config.state_dir()) / "freshness-audit.txt"` via
  `write_text()` (`:112-114`).
  **Operational consequence, verified today:** the
  `corporatetraveldc-freshness-audit.service` run at 2026-08-19 06:00:03
  logged `freshness-audit: audit complete — 5 stale, 3 failures` and exited
  0. Nobody was notified. The staleness audit — the mechanism whose entire
  purpose is to tell an operator that feeds have gone quiet — currently
  reaches no one; its findings sit in a text file in the state directory
  and in the journal until somebody goes looking. The unit and its timer
  are real and firing on schedule; only the alerting leg is missing.
  **NEEDS OPERATOR DECISION:** `freshness_audit.py` and `daily_brief.py`
  are both documented (in this file and in their own docstrings) as ntfy
  publishers and neither is one. Decide per skill whether to (a) add the
  push the docstring promises — for freshness_audit that means `ops-health`
  at priority 2, and it should probably be higher than 2 given the
  `5 stale, 3 failures` result went unseen — or (b) accept them as
  write-to-state-file-only skills and correct their docstrings. Do not
  leave them documented as alerting; that is the state that let a real
  staleness finding pass silently. Related but separate: `daily_brief.py`
  also needs either a `corporatetraveldc-daily-brief.timer` or a docstring
  that stops naming one.
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

## Standalone bash/script alerts (raw curl/urllib, not through either Python helper)

**Added 2026-07-27** — this whole surface predated that sweep but was never
swept the same way the ingest/poller/web paths were on 2026-07-20. Every
entry below defines its own tiny `ntfy_send()`, reads
`NTFY_BASE_URL`/`NTFY_OPS_TOPIC`/`NTFY_HOT_TOPIC` (defaulting to
`http://127.0.0.1:2586`, `ops-health`, `hot-alerts`) independently, and
does **not** retry on failure — most swallow the curl error with `|| true`
or a log line only. None of these use `common/ntfy_push.py`'s
retry/fallback/ambiguous-403 logic, so a dropped push here is genuinely
dropped, not silently retried.

> **Priority claims in this section were wrong and are corrected below
> (2026-08-19).** Six entries were recorded as firing at "default priority
> (no explicit value)". All six set `-H "Priority: N"` explicitly. The
> worst case was `threat-initiate.sh`, documented as default (≈3) and
> actually firing at **5**, the maximum-urgency level that bypasses most
> quiet-hours handling on a phone. Verified line-by-line against each
> script.

### Full-stack watchdog (rewritten 2026-08-23 — it exists now)

**Historical note, still true:** the `ctdi-watchdog` timer and
`/opt/corporatetraveldc/bin/ctdi-watchdog.sh` this section once documented
never existed — the 2026-08-19 pass established that and it stands. What
changed is the conclusion drawn from it: as of 2026-08-21 there **is** an
automated watchdog, just a different one. Root-scope
`/etc/systemd/system/corporatetraveldc-watchdog.timer` (enabled
2026-08-21, `OnBootSec=90s` / `OnUnitActiveSec=90s`) runs
`scripts/watchdog.sh` as root. The "unscheduled, never-invoked helper"
description of that script is obsolete.

Behavior split (verified live 2026-08-23):

- **System-level restarts** (`pihole-FTL`/`unbound`/`cloudflared`/
  `tailscaled`) are **alert-only** unless a human passes
  `--allow-system-restart` — the installed unit passes no flag, so every
  automatic run pushes `hot-alerts` at priority 5 naming the exact manual
  command to run, and touches nothing.
- **Container-only restarts** (`web`/`poller`/`pusher`) are automatic and
  unattended: `ops-health` priority 3 notice → restart → priority 2
  completion, with a 300 s cooldown via
  `/run/corporatetraveldc-watchdog-cooldown`.
- **CRIT feed staleness is deliberately alert-only** as of 2026-08-21 —
  never a restart. Feed recovery belongs to `thermal-ingest-guard` /
  `ingest-feed-ctl`, not this watchdog.

Second-order effect, still live: `src/web/main.py:2409-2418`'s
`GET /admin/watchdog/status` still permanently returns
`{"available": false, "reason": "no run recorded yet"}` — the live
watchdog writes only the cooldown file, never
`/var/lib/corporatetraveldc/watchdog-last-run.json`, so the endpoint's
`if not status_path.exists()` branch remains the only one that ever runs.
(The MCP tools that once read this endpoint were retired 2026-08-18;
nothing consumes it.)

**NEEDS OPERATOR DECISION:** either wire `watchdog.sh` to write
`watchdog-last-run.json` so `/admin/watchdog/status` reports real state,
or drop the endpoint.

- **`scripts/lockdown.sh`**: fires `hot-alerts` at **priority 4**
  (`lockdown.sh:100`) when host-reach opt-ins for Ollama/pusher/acarshub
  are reverted — either fail2ban-triggered or manual. *Corrected
  2026-08-19: previously documented as "no explicit priority set, so ntfy's
  server default of 3 applies", along with a recommendation to add an
  explicit priority. The explicit priority is already there; the
  recommendation is resolved and dropped.*
- **`scripts/restore-network.sh`**: fires `ops-health` at **priority 3**
  (`restore-network.sh:85`, explicit — not the server default) when
  lockdown is lifted and host-reach opt-ins are restored.
- **`scripts/threat-initiate.sh`** / **`scripts/threat-resolve.sh`**:
  operator-invoked manual threat response (wraps lockdown.sh/
  restore-network.sh with an explicit "MANUAL" framing and optional
  banned-IP reference). Initiate → `hot-alerts` at **priority 5**
  (`threat-initiate.sh:54`); resolve → `ops-health` at **priority 3**
  (`threat-resolve.sh:53`). *Corrected 2026-08-19: both were documented as
  "default priority (no explicit value set)". `threat-initiate.sh` is in
  fact the platform's max-urgency path — priority 5 on `hot-alerts`, the
  same level as a POTUS movement — which is defensible for a manual threat
  response but is exactly the kind of thing that must not be mis-documented
  as routine.*
- **`scripts/renew-tailscale-cert.sh`**: fires `ops-health` at **priority
  3** (`renew-tailscale-cert.sh:61`, explicit) on cert renewal success,
  renewal failure, or an nginx-config-test failure after a successful
  renewal (nginx deliberately NOT reloaded in that last case, still serving
  the old cert). *Corrected 2026-08-19 from "all default priority". Note
  the same priority 3 is used for the success and the failure cases alike,
  which is a real (if minor) signal-flattening issue an operator may want
  to revisit — a failed renewal and a clean one arrive looking identical.*
- **`scripts/restart-stack.sh`**: fires `ops-health` at **priority 3**
  (`restart-stack.sh:86`, explicit — inside `ntfy_send()`, which starts at
  `:82`; an earlier revision cited `:83`, the `local topic=…` line) after a manual full restart, message
  differs by whether `/healthz` is responding yet (`Stack Restart Complete`
  vs. `Stack Restart -- API Pending`). *Corrected 2026-08-19 from "default
  priority".*
- **`scripts/container-mem-watch.sh`**: fires `ops-health` priority 3 when
  any container has been over its memory-pressure threshold (80% of cap)
  continuously for 10+ minutes, or a kernel-confirmed OOM event is found.
  Had a token bug (fixed 2026-07-19, predating this doc's original sweep)
  where the Authorization header was simply never sent — see "Push
  reliability" below.
- **`scripts/scheduled-ingest-restart.sh`**: fires `ops-health` on
  preventive ingest-container restart success/failure. Same 2026-07-19
  token-source bug as container-mem-watch.sh, already fixed.
- **`scripts/nextcloud-health-check.sh`**: fires `ops-health` priority 4
  when any of its checks fail (occ status, local/outbound HTTP, cron
  freshness).
- **`scripts/check-pat-expiry.sh`**: fires the non-standard
  `dispatch-alerts` topic (see topic index above) at priority 4, escalating
  to 5 inside the final warning day, when a GitHub PAT is within
  `WARN_DAYS` (5) of expiring.
- **`scripts/sudo-approval-gate.sh`**: fires `approval-gate` priority 4 —
  see the topic index entry above for the Allow/Deny action-button detail.
  This is the only standalone-script push that uses ntfy's JSON API
  (`actions` field) rather than the plain-text `-d "$msg"` convention
  every other script here uses, because it needs the inline buttons.
- **`scripts/thermal-ingest-guard.py`** (Python, but hand-rolled — not
  `common/ntfy_push.py`): its own `ntfy_alert(cfg, message, title,
  priority=4)` (`:432`) posts straight to `/ops-health` via
  `urllib.request`, no `requests` dependency, no retry, no fallback, tags
  `thermometer,warning`. **Re-verified 2026-08-23 — every line number and
  every threshold in the previous revision of this entry was stale.** It
  fires **priority 5 on a LOCKDOWN shed** (`:586`), **priority 4 on a
  temp tier-1 shed** (`:603`), **priority 5 on `RESTORE FAILED`**
  (`:651`, added 2026-08-21) when a restarted unit fails the post-restart
  `is-active` check, and **priority 3 on a *verified* restore** (`:668`).
  Nothing is pushed for the informational bands — see below.

  **Why this one matters more than its position in this list suggests.**
  Every other entry here reports on something; this one *takes an action
  that silences other alert sources*, and since the 2026-08-23 redesign
  that action is much broader. It runs every 2 minutes:

    - **Temp tier 1** at `temp >= 74C` stops `tfms,stdds`
      (`THERMAL_GUARD_TIER1_FEEDS`). Load no longer participates in this
      stage at all.
    - **LOCKDOWN** at `temp >= 79C` **or** `load1 >= 40.0` **or** `>= 2`
      load-attributed brief fallbacks in 300 s stops **the entire stack
      except `web`**: all six SWIM feeds (`fdps,stdds,tfms,tbfm,itws,notam`),
      `ingest-core`, `poller`, `pusher`, `runner`, and `ollama.service`.
      There is no intermediate load stage — `load1` 15–40 is logged as
      informational and sheds nothing.
    - **Restore** requires temp `< 65C` **and** `load1 < 15.0` **and**
      fallback count `< 2`, held continuously for 300 s.

  A shed feed produces no alerts of any kind while it is stopped: no
  `tfms-alerts`, no `stdds-alerts`, no `wx-alerts` from ITWS, no FDPS
  proximity pushes. Under LOCKDOWN the blackout is total — `pusher` is
  stopped too, so even `cps` and the watchlist dual-fire paths go dark, and
  the guard's own `ops-health` push is one of the very few things still
  capable of firing. That notice is the *only* signal that a whole class of
  alerting has gone quiet on purpose; silence on `tfms-zdc` looks identical
  whether the NAS is calm or the feed is stopped. Treat these messages as
  coverage-gap markers, not routine housekeeping.

  Live example from this pass — the first real LOCKDOWN on record (a
  **second** followed the same day, 14:34:42 → 14:45:51 EDT, same trigger
  and also a clean restore, so treat these as recurring rather than
  one-off), and notably triggered by the *Ollama-contention* signal rather
  than raw load or heat: at 2026-08-23 12:18:22 EDT the guard logged
  `tripped LOCKDOWN (2 load-attributed brief fallbacks/300s) fan=2313rpm`
  and pushed `Ollama-contention Guard -- LOCKDOWN shed` at priority 5; it
  restored cleanly 11 minutes later at 12:29:35
  (`restored (the whole stack …) at 56.75C load=3.73`, priority 3, after
  verifying each unit came back). Earlier the same day, before the new
  thresholds took effect, three old-style tier-2 sheds fired at load
  16.30 / 14.10 / 14.17 — under the new `>= 40` bar those now log as
  `INFO: load1 … in watch band [15-40) -- normal-to-busy range, no action`
  and push nothing.

**A genuine current gap, not something to leave undocumented:**
`.config/containers/systemd/ntfy.container` sets
`OnFailure=ntfy-container-alert.service` — but that unit does not exist
anywhere on this host (checked `systemctl list-unit-files`, the repo, and
both systemd unit search paths; nothing). If the ntfy container itself
crashes, nothing currently fires — there is no alerting path for "the
alerting service died." Every mechanism above depends on ntfy being up;
none of them cover ntfy being down. Worth either writing that unit
(probably a tiny script that hits Pushover/SMS/some out-of-band channel,
since ntfy itself obviously can't alert on its own death) or removing the
dangling `OnFailure=` reference so it stops looking like a real safety net
that isn't there.

## Publishers this catalog previously missed (added 2026-08-19)

Every revision of this file up to today claimed to be a full sweep while
omitting the publishers below. Nine of them are not merely code-reachable
but **proven live** — they appear by title in the last 12 hours of
`/var/lib/ntfy/cache.db` (marked ✅). The rest are wired and reachable but
were quiet in that window; absence of recent traffic is not absence of the
publisher.

### Poller skills (`common/ntfy_push.py`, missing from the section above)

| Skill | Topic | Priority | Notes |
|---|---|---|---|
| `feed_db_integrity_check.py:198` | `ops-health` | 3 | ✅ "Feed/DB integrity mismatch" — 10 pushes in 12 h, the joint-busiest publisher on the topic |
| `ingest_feed_watch.py:178,184` | `ops-health` | 3 | ✅ "Ingest feed/ntfy health -- degraded" (8 in 12 h) and a paired "recovered -- all clear now" |
| `pull_path_verify.py:175,182` | `ops-health` | 4 on failure, **1** on success | ✅ Confirms REST pull-path fallback still works when push is primary. The priority-1 success push is the lowest-priority push on the platform |
| `nms_v240_post_deploy_check.py:154` | `ops-health` | — | ✅ "NMS v2.4.0 post-deploy check: NEEDS REVIEW" |
| `board_sweep.py:94` | `ops-health` | 3 | tags `incoming_envelope` |
| `dispatch_desk_memo.py:296` | `dispatch` | 2 | ✅ "The Dispatch Desk — 2026-W34", tags `newspaper` |
| `aam_daily_watch.py:104` | `ops-health` | 2 | ✅ "advanced_air_mobility cross-link auto-promote" |
| `aviation_daily_watch.py:123` | `ops-health` | 2 | ✅ "aviation cross-link auto-promote" |
| `concierge_travel_daily_watch.py:109` | `ops-health` | 2 | one of the six daily category watches |
| `executive_protection_daily_watch.py:112` | `ops-health` | 2 | one of the six daily category watches |
| `gig_economy_daily_watch.py:118` | `ops-health` | 2 | one of the six daily category watches |
| `trains_yachts_daily_watch.py:109` | `ops-health` | 2 | one of the six daily category watches |

### Non-poller Python services

| Source | Topic(s) | Notes |
|---|---|---|
| `src/ingest/local_airspace.py:110` | `flight-alerts` + `dispatch` (watched-aircraft proximity); `dispatch` alone (Marine One / squawk 7700-7500-7600 local detections, `:258`, `:291`) | Its own `_fire_ntfy()` using `requests.post` — a fourth hand-rolled path, distinct from the two shared helpers *and* from the bash scripts. Docstring calls the routing "canonical — do not change" |
| `src/acars_watcher/acars_watcher.py:239` | `NTFY_TOPIC`, **defaulting to `flight-alerts`** | JSON-payload POST |
| `src/ais_watcher/ais_watcher.py:98` | `NTFY_TOPIC`, **defaulting to `flight-alerts`** | Vessel MMSI hits from the local AIS decoder. Note the default topic is `flight-alerts`, not `vessel-alerts` — likely unintended, but recorded here as observed rather than silently corrected |
| `src/poller/tools/watchlist_import.py:161` | `dispatch` via `_fire_ntfy_dual` | Bulk watchlist import notifications |
| `src/poller/main.py:274-281` | operator-chosen, **defaults `ops-health`** | The `push_test_alert` trigger handler: an admin trigger file can publish an arbitrary message to an arbitrary topic at an arbitrary priority via `pusher_main.send_test_alert()`. The only path on the platform where the topic is fully caller-supplied |

### Standalone scripts (missing from the section above)

All post to `ops-health` unless noted; each rolls its own `ntfy_send`/
`ntfy_alert` with no retry, per the "How to read this" preamble.

| Script | Priority | Notes |
|---|---|---|
| `scheduled-integrity-sweep.sh` | default 2 | ✅ "INTEGRITY SWEEP FAILED" — 10 pushes in 12 h. Fires whenever tracked files are edited but not yet re-signed; see CLAUDE.md's manifest note before treating it as an incident |
| `brief-fallback-monitor.sh` | **`max`** (5) | ✅ "⛔ BRIEF LLM DOWN: corporatetraveldc-ops-brief" (8) and "…-ep-advance" (2). Topic overridable via `BRIEF_FALLBACK_TOPIC`. One of only a handful of max-priority publishers |
| `ollama-swap-alert.sh` | explicit per call | ✅ "Ollama swap climbing" (4), "Ollama swap engaged", "Ollama swap cleared". Hardcodes `NTFY_TOPIC="ops-health"` rather than reading `NTFY_OPS_TOPIC` |
| `sdr-crashloop-guard.sh` | default 4 | ✅ "SDR stack auto-stopped: ultrafeeder". Like thermal-ingest-guard, this one *stops* things — a stopped SDR stack means no local ADS-B/ACARS alerting |
| `claude-md-drift-daily.sh` | default 3 | ✅ "CLAUDE.md drift found", tags `memo` |
| `ollama-wedged-detector.sh` | default 4 | Topic overridable via `WEDGE_ALERT_TOPIC` |
| `governor-watch.py:134` | 3 on corrected drift, **5** on "FIX FAILED" | Hand-rolled `ntfy_alert`, posts to `/ops-health` |
| `uber-traffic-watch.py` | 4 default, **5** on "ANOMALY" or a denylist gap | Hand-rolled `ntfy_alert`, posts to `/ops-health`. Consolidated 2026-08-29: adds blocked-hit-frequency tracking (how often a denylisted `TRACKED_ANOMALY_ENDPOINTS` domain still gets queried, Pi-hole `STATUS_DENYLIST` code) and discovery-pattern gap stats (mean/median days between new anomalies found), plus a read-only denylist-gap check (priority 5) since this script has no privilege to run `pihole deny` itself. Generalized, platform-agnostic twin of the same four capabilities lives in the public `agentic-management-tooling-mcp` repo as `gig_mobility/endpoint_anomaly.py` — a separate implementation, not imported by this script (see this script's own docstring for why) |
| `failover-kickover-guardrail.py:131` | 4 default, **5** on double failure | Hand-rolled `_ntfy_alert()`, posts to `ops-health`. Priority 5 when it detects a push AND REST double failure for `nws` (`push:nws`) / `notam` (`push:fns`) and force-fires a `refresh_feed` trigger — the only feed guard on the platform that takes an action rather than only reporting. User timer `corporatetraveldc-failover-kickover-guardrail.timer`, every 5 min, enabled 2026-08-21 |
| `ntfy-topic-count-watchdog.sh` | default 3 | Warns past 140 topics — the guard rail behind the "scope is bounded to eight zones" decision in ALERT_ARCHITECTURE.md §6 |
| `acars-feed-silence-watchdog.sh` | default 3 | Timer currently `disabled` |
| `adsb-feed-silence-watchdog.sh` | default 3 | Timer currently `disabled` |
| `adsb-link-watchdog.sh` | default 3 | Timer currently `disabled` |

Not publishers despite matching a naive `grep ntfy`: `scripts/brief-model-matrix.sh`
(the word appears only inside a model persona prompt), `scripts/session-restore.sh`,
`scripts/smoke-test-platform.sh`, `scripts/stack-boot-ctl.sh`,
`scripts/deploy-acars-stack.sh`, `scripts/populate-secrets.sh`,
`scripts/scrub-public-tree.py`, and `src/runner/main.py` (which *proxies*
ntfy SSE to the ops dashboard and, in `DEMO_MODE`, synthesises fake events
— it never publishes to the real broker).

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

~~Not yet wired: FDPS/ITWS/AIM alert paths still fire directly rather than
through sector coalescing (TFMS was the highest-overlap starting point
given today's parser work). Extending coalescing to the other feeds is a
natural next increment if the sector/trend view proves useful in
practice.~~

**Superseded 2026-08-19 — all three are wired, and so is STDDS.** That
"next increment" landed on 2026-08-02/03. Every SWIM parser now calls
`shared/sector_coalesce.py::fire_family_alert()`; the complete live set of
call sites is:

| Parser | Line(s) | family, feed_name | Notes |
|---|---|---|---|
| `tfms_parser.py` | `:583`, `:1262`, `:1348` | `tfms` / `tfms`, `tfms_aptc`, `tfms_gadv` | RSTR-GDP-GS, APTC, GADV — three *distinct* feed_names, not one. DC-only `_DC_FACILITIES` gate replaced by `is_tracked_facility()` on the RSTR/GDP/GS and GADV paths; APTC alone is still DC-only |
| `tbfm_parser.py` | `:323` | `tbfm` / `tbfm` | base_priority 2 |
| `fdps_parser.py` | `:923` | `fdps` / `fdps` | base_priority 3 |
| `itws_parser.py` | `:696` | `itws` / `itws` | base_priority 4, **in addition to** the retained direct `wx-alerts` push |
| `aim_parser.py` | `:317` | `fdps` / `fdps_notam` | flight-restriction NOTAMs, `escalating_only=False`, `isolate=True`, base_priority 4 |
| `aim_parser.py` | `:335` | `aim_fns` / `aim_fns` | base_priority 3, **in addition to** the retained direct `nas-alerts` push |
| `smes_parser.py` | `:390`, `:584`, `:902`, `:1047` | `stdds` / `stdds`, `stdds_surface`, `stdds_safety`, `stdds_taxi` | see the STDDS section above |

Two direct (non-coalesced) legacy pushes were deliberately kept rather than
migrated: ITWS→`wx-alerts` and AIM-residual→`nas-alerts`. Both are shared
topics whose subscribers would have lost visibility on a clean cutover, so
those feeds now publish twice. VIP paths (Marine One→`tfr-alert`, VIP
NOTAM→`hot-alerts`) are intentionally never coalesced — every single event
is independently critical regardless of trend.

The per-sector *silence* controls described immediately above, and the
per-topic enable/throttle/sanitize controls described in the 2026-08-11
addendum, apply **only** to pushes that go through `fire_family_alert()`.
Direct pushes — including the two retained legacy ones in this very
section — ignore them entirely. See `docs/ALERT_ARCHITECTURE.md` §1.

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

**`NTFY_FALLBACK_URL` — built, wired, never configured (checked 2026-07-27):**
`common/ntfy_push.py`'s `send()` is the only one of the three alert paths
(see "How to read this") with a fallback mechanism: if the primary ntfy
URL (`NTFY_URL`, currently `http://host.containers.internal:2586` —
resolves to the self-hosted ntfy container, reachable from containers on
this host or from the operator's phone over Tailscale) is unreachable
(connection error or timeout, not an HTTP error status), `send()` retries
the identical request against `config.ntfy_fallback_url()`
(`NTFY_FALLBACK_URL` in `dispatch.env`). The docstring calls this "native
fallback on :2587" — i.e., a second ntfy listener that wouldn't require
being on the tailnet to reach, for a topology where an operator doesn't
want every alert path fully gated behind VPN lockdown. As of this check:
`NTFY_FALLBACK_URL` is unset in both `dispatch.env` and
`dispatch-secrets.env`, and nothing in the ntfy Quadlet (either copy)
actually publishes a `:2587` port — only `2586:2586`. So this is real,
tested-shaped code with nowhere to land: if the primary ntfy endpoint ever
becomes unreachable, `common/ntfy_push.py` callers correctly attempt the
fallback and get nothing, same as if the mechanism didn't exist. Also
worth noting: even if `NTFY_FALLBACK_URL` were configured, it would only
help the `common/ntfy_push.py` path — the `shared/watchlist.py` dual-fire
helper and every standalone bash script/thermal-ingest-guard.py above have
no fallback of their own and would still go dark if the primary ntfy
endpoint became unreachable. Whether this is worth building out (a real
`:2587` listener, or a different non-Tailscale reachability path
entirely) versus leaving as documented-but-dormant is an operator call,
not something this doc is resolving.
