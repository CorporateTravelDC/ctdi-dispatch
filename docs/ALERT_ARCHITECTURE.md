# Alert Architecture — Tunable Visibility, Not Noise

Status: living document, started 2026-08-03, **reconciled against live
code 2026-08-19; alert-surface delta re-checked 2026-08-23 (see the §2
caveats and §8)** (§1 Layer 1 scope was materially wrong — see the
correction there). A second 2026-08-23 live-system-first pass re-verified
`_ARTCC_GROUPS` (all 8 zones, `zatl`→`ZTL`), `_STDDS_REGIONAL_AIRPORTS`
(`KDCA`/`KIAD`/`KBWI`), the §5 family/feed_name/base_priority table, the
`_throttle_allows()`/`_load_silence_state()` line references, and the
`ntfy-topic-count-watchdog.sh` warn threshold (140) — all correct — and
added the LOCKDOWN caveat in §2. Companion to `INFRA_MAP.md` §8/§8.1 (topic inventory
and the escalating-family mechanics) and `ALERT_REFERENCE.md` (per-parser
trigger/dedup catalog — refreshed in the same 2026-08-19 pass, see §7).
This document is the "why," those are the "what" and "how."

## 1. The problem this solves

The raw feeds behind this platform (SWIM TFMS/TBFM/FDPS/ITWS/AIM-FNS,
NOTAM, METAR/NWS, TFR) are extremely high volume — nationwide TFMS/FDPS
traffic alone runs into thousands of qualifying events per day. Alerting
on every individual event that matches a watch condition is not a design
goal; it's the failure mode this architecture exists to prevent. An
operator (or an FBO desk, or an affiliate office) who wants situational
awareness without drowning in notifications needs a system that can be
tuned to their actual attention budget, not one that assumes "more alerts
= more informed."

The guiding question for every alert-routing decision on this platform is:
**does this operator need to know about this specific event right now, or
do they need to know when the underlying situation changes?** Most raw
events are the former only in aggregate — a single TFMS ground-stop
program is routine; three ground-stop programs in fifteen minutes for the
same sector is a genuine signal. The architecture below is built to tell
the difference and let each consumer of the system choose how much of
each layer they want.

## 2. The three-layer model

Every alert-worthy event **from the six SWIM-derived families** is routed
through all three of these layers simultaneously. An operator picks which
layers to actually receive by subscribing (or not) to specific ntfy topics;
the platform doesn't decide this for them.

> **Scope caveat added 2026-08-19.** This sentence originally read "Every
> alert-worthy event on this platform," which overstates the model's
> reach. The three-layer model covers the SWIM family topics and nothing
> else. Poller skills, web webhooks, the local ADS-B/ACARS/AIS watchers and
> the ~30 standalone ops scripts all publish outside it, with no
> escalation, no coalescing, and no Layer 1 control. Three 2026-08-21/22
> additions also sit outside the layer model: the watchlist
> `identity_resolved` trigger (`flight-alerts`+`dispatch`, p3, one-shot
> per entry) and the two alert-only guards (`corporatetraveldc-watchdog.timer`
> root-scope, `failover-kickover-guardrail.timer` user-scope).
>
> **Second scope caveat, added 2026-08-23; triggers updated 2026-09-03 —
> the layer model assumes the stack is running, and there is a designed
> state in which it isn't.**
> `scripts/thermal-ingest-guard.py`'s LOCKDOWN (as of the 2026-08-27
> corrections: trips at `temp >= 79C` or `load1 >= 40` — the third,
> contention-fallback trigger was demoted to informational-only) stops all
> six SWIM ingest containers, `ingest-core`,
> `poller`, `pusher`, and `runner` — everything except
> `web` (the guard no longer touches any LLM service — `ollama.service` is
> gone with the llama.cpp cutover and the `corporatetraveldc-llama-*` units
> are deliberately out of scope). Every layer above therefore goes dark
> simultaneously and silently:
> the parsers that call `fire_family_alert()` are not running, and `pusher`
> is stopped so even non-family paths like `cps` cannot fire. The guard's
> own priority-5 `ops-health` push is one of the only things still able to
> publish. (Historical frequency note: **two** real
> LOCKDOWNs fired on 2026-08-23, both triggered by the since-demoted
> Ollama-contention signal — 12:18 → 12:29 EDT and 14:34:42 → 14:45:51 EDT.
> With that trigger demoted, expect LOCKDOWNs to be rare genuine
> load/thermal events, not a bi-hourly occurrence.) Treat a
> total absence of family alerts as a state to
> check the guard about, not as a quiet NAS. See `docs/DATA_SOURCES.md`
> §"Thermal ingest guard".
>
> More pointedly, the
> model says nothing about whether a *documented* alert path exists at all:
> the 2026-08-19 reconciliation of `ALERT_REFERENCE.md` found two poller
> skills — `daily_brief.py` and `freshness_audit.py` — documented for
> months as publishing to `ops-brief` and `ops-health` respectively, which
> contain no ntfy code whatsoever and only write a text file to the state
> directory. The freshness audit is the sharper case: its
> 2026-08-19 06:00 run logged `5 stale, 3 failures` and notified nobody.
> Nothing in this architecture guarantees delivery, and nothing in it
> detects a publisher that silently isn't one. Read the layer model as a
> description of how *family* alerts are shaped once they are fired — not
> as an assurance that every alert-worthy condition on the platform reaches
> a human.

### Layer 1 — Per-topic control (mute, throttle, sanitize)

> **CORRECTED 2026-08-19 — read this before the paragraph it replaces.**
> This section used to open: *"Every ntfy topic on the platform
> (`tbfm-alerts`, `tfms-zdc`, `nas-alerts`, `hot-alerts`, all of them) can
> be independently…"*. That was false, and unluckily so: **the two topics
> it named as illustrative examples, `nas-alerts` and `hot-alerts`, are
> precisely the two that cannot be muted by this mechanism.** Both are
> fired by direct pushes that never consult the gate. The corrected scope
> is below.

**What is actually controllable.** The enable/throttle/sanitize gate is
consulted in exactly **one** place in the codebase:
`src/shared/sector_coalesce.py:301-309` (`_throttle_allows()`, which calls
`is_topic_enabled()` and `get_topic_throttle()`). That function is reached
only from `fire_family_alert()`. Therefore the controls govern **only the
coalesced family/zone topics** — `tbfm-alerts`/`tbfm-<zone>`,
`tfms-alerts`/`tfms-<zone>`, `fdps-alerts`/`fdps-<zone>`,
`itws-alerts`/`itws-<zone>`, `aim_fns-alerts`/`aim_fns-<zone>`,
`stdds-alerts`/`stdds-<zone|airport>` — i.e. the topics described in
Layers 2 and 3 below, and nothing else.

**What is not controllable.** Neither `src/common/ntfy_push.py`'s `send()`
nor `src/shared/watchlist.py`'s `_fire_ntfy_dual()` imports or calls
`sector_coalesce` at all, and neither do the hand-rolled publishers
(`ingest/local_airspace.py`, the ACARS/AIS watchers, or any of the
`scripts/*.sh` / `scripts/*.py` ops scripts). Everything they publish is
outside this gate: `hot-alerts`, `nas-alerts`, `tfr-alert`, `wx-alerts`,
`dispatch`, `flight-alerts`/`train-alerts`/`vessel-alerts`, `ops-health`,
`ops-brief`, `dispatch-debriefs`/`dispatch-ops`, `ep`/`ep-advance`, `cps`,
`osint-alerts`, `reservations`, `calls`, `approval-gate`,
`dispatch-alerts`. Setting `enabled=false` on any of those succeeds — the
API returns 200 and the state is persisted — and changes nothing. Note
this includes the two *deliberately retained* legacy direct pushes
(ITWS→`wx-alerts`, AIM-residual→`nas-alerts`): muting `itws-alerts` still
leaves ITWS content arriving on `wx-alerts`.

Within that real scope, each topic can be independently:

- **Enabled/disabled** — a full mute; `_throttle_allows()` returns False
  and `fire_family_alert()` skips the push. Counting continues underneath,
  so escalation state stays accurate.
- **Throttled** — a minimum interval between pushes (default 60s),
  independent of escalation state. This is the fix for the reconnect-storm
  incident of 2026-08-03: even a genuinely escalating sector shouldn't
  push once per qualifying message.
- **Sanitized** — N-number and ICAO-hex tokens masked before publish, so a
  real alert stream can double as a demo/reporting source of truth without
  exposing live tail numbers.

Admin API: `GET/POST /api/v1/sectors/topic/{topic}[/throttle|/enabled|/sanitize]`.

**Two compounding facts that make even the in-scope control weaker than it
looks:**

1. **The state is read once, at module import.** `_load_silence_state()`
   is called at `sector_coalesce.py:371` — module level, not inside any
   function — and there is no reload path, no mtime watcher, no
   invalidation hook. The in-process dicts (`_topic_enabled`,
   `_topic_min_interval`, `_topic_sanitize`, `_silenced_sectors`,
   `_silenced_feeds`, `_escalate_overrides`) are whatever
   `/var/lib/corporatetraveldc/sector_coalesce_silence.json` held at the
   moment the process started. Contrast this with the watchlist system,
   which does have a 60-second mtime-poll reloader.
2. **The setters and the senders live in different containers.** The
   admin setters (`set_topic_enabled` / `set_topic_throttle` /
   `set_topic_sanitize`, called from `src/web/routes/sectors.py:99-117`)
   run in the **web** container. Every `fire_family_alert()` call site is
   in a parser, which runs in one of the **7 ingest** containers. The web
   container writes the JSON file and updates *its own* in-memory copy;
   the ingest containers never re-read it.

Taken together: **toggling a topic mute has no effect on live alerting at
all until every ingest container is restarted.** The admin API will
cheerfully report the new state back — because the web container's own
memory does reflect it — while the containers that actually send keep
using the values they loaded at boot. An operator muting a noisy topic
mid-incident and then seeing it keep firing is the expected behaviour of
the current code, not a bug report.

Within its real scope, this is still the layer an individual desk uses to
say "I don't want `itws-alerts` on my phone at all" without affecting
anyone else's subscription — provided the ingest containers are restarted
afterwards, and provided the topic is a family topic.

**NEEDS OPERATOR DECISION:** the per-topic enable/throttle control is
documented as platform-wide but only governs coalesced sector-family
topics, is read once at import, and is set from a different container than
the one that sends. Decide whether to (a) narrow the documented contract
permanently, or (b) wire the gate into `ntfy_push.send()` with a reload
path so it genuinely applies platform-wide.

### Layer 2 — Family-level escalating aggregate (`{family}-alerts`)

Each family (`tbfm`, `tfms`, `fdps`, `itws`, `aim_fns`) has one aggregate
topic covering every facility nationwide. This topic is **escalating-only
by default** — it does not fire on every qualifying event, only when the
rate of events in the current 15-minute window is a genuine step up from
the window before it (default: 3+ events, at least 3x the prior window).
This answers "is this whole system getting better, worse, or staying
steady" without requiring the subscriber to know or care which specific
sector is driving the trend.

This is the layer that gives an operator (or an office that only cares
about the big picture) a single low-noise channel that stays quiet during
routine operations and speaks up when something is actually changing.

### Layer 3 — Per-sector/zone alerts (`{family}-<zone>`)

Each family also fires a per-ARTCC-zone topic (`tfms-zdc`, `fdps-zny`,
etc.) for the 8 tracked zones, using the *same* escalation classification
as the aggregate (one `record_event()` call feeds both, so the two never
disagree about whether a given moment counts as "escalating") but each
zone's sensitivity is independently tunable via
`set_escalate_threshold(feed_name, sector, multiplier, floor)`.

This is the layer a specific desk, department, or affiliate office
subscribes to for "just tell me what's happening in *my* airspace" — they
get the same escalation discipline (no per-event spam) but scoped to the
zone that's actually their concern.

### Isolation between siblings sharing a family (added 2026-08-03)

A family/topic can have more than one contributing feed_name — e.g.
`fdps-alerts`/`fdps-<zone>` now carries both fdps_parser's own proximity-
tracking events (`feed_name="fdps"`) and NOTAM-sourced flight-restriction
events (`feed_name="fdps_notam"`). By default, every feed_name sharing a
sector pools into that sector's escalation count (this was the original
2026-07-20 design intent — "is this sector busy, regardless of source" —
and is still correct when two feed_names genuinely represent the same
underlying phenomenon reported by different means).

But two feed_names that represent *different concerns* sharing a
family/topic must not be able to trigger each other's escalation state
just because they resolve to the same sector — a burst of unrelated TFR
notices shouldn't make real FDPS proximity tracking read as "escalating."
`record_event()`/`fire_family_alert()` support `isolate=True` for exactly
this case: the feed_name's events are counted only against its own
history, never mixed into the shared sector bucket, while both still
publish to the same operator-facing topics.

## 3. Why escalation, not raw event count or keyword matching alone

A simpler design would fire on every event matching a watch condition
(any TFMS program, any FDPS proximity match, any non-VIP NOTAM). That was
roughly the original shape of this system before 2026-08-02 and it
produced exactly the failure this architecture exists to avoid: `tfms-zdc`
and `tbfm-alerts` hit thousands of pushes/day and put the operator's ntfy
client into a persistent "reconnecting" state. Two-window trend comparison
(current 15 minutes vs. the 15 minutes before it) is deliberately simple —
no time-series model, no external dependency — but it directly answers
the only question that actually matters operationally: *is this worse
than it was a few minutes ago.*

## 4. Emerging capability: a secondary dispatch layer for FBOs and
   multi-office operators

The three-layer model above wasn't originally designed with multi-tenant
use in mind — it grew out of a single operator's need to not drown in his
own system's output. But the shape of the solution generalizes directly
into something closer to **a lightweight, secondary ATC-adjacent dispatch
capability for FBOs or operators running multiple affiliate offices**:

- **One core system, ingesting once.** A single Pi-class deployment
  ingests the full SWIM/NAS/NOTAM/weather feed set nationwide — the
  expensive part (SWIM session management, parsing, dedup, sector
  resolution) happens exactly once regardless of how many downstream
  consumers exist.
- **Each office subscribes to its own sector.** An affiliate office at,
  say, a New York-area FBO subscribes to `tfms-zny`/`tbfm-zny`/etc. and
  gets alerts scoped to their own operating area — not the noise from
  every other office's zone.
- **Every office still sees the family-level trend.** Because the
  aggregate `{family}-alerts` topics exist independently of any zone
  subscription, every office retains upstream/downstream visibility into
  the *system-wide* picture — "is the whole NAS getting worse today" —
  without needing to subscribe to every other office's zone-level detail.
  This is the "hybrid upstream/downstream visibility" model: local
  relevance plus system-wide awareness, without full-feed noise.
- **Muting is per-consumer, not per-system.** Because Layer 1 (mute/
  throttle/sanitize) is scoped to the *topic*, not the underlying event
  stream, one office silencing `itws-alerts` entirely has zero effect on
  another office's subscription to the same topic name on their own ntfy
  client/device. **Clarified 2026-08-19:** this holds only for *client-side*
  muting (each subscriber's own ntfy app). The *server-side* Layer 1
  controls described above are global to the deployment — one operator
  calling `POST /api/v1/sectors/topic/itws-alerts/enabled` with
  `enabled=false` stops that topic being published for **everyone**, since
  the mute is applied at send time, not per subscriber. In a genuine
  multi-office deployment that distinction is the whole ballgame, and the
  server-side control as built is not per-consumer at all.

**What exists today vs. what this would need**: the escalation/throttle/
mute mechanics described in §2 already support this model as-is — any
number of independent ntfy subscribers can already point at the same
topic set with per-client mute state living entirely on the client side
(ntfy has no server-side per-subscriber config on this deployment). What
does **not** yet exist is any server-side concept of "office" or
"department" scoped specifically to alert topics — there's a comparable
pattern already built for a different subsystem (RSS/intel feed
visibility — company/department/personal scope via a `whoami-token`
endpoint, see the multi-operator/department visibility model from
2026-08-02) that this could reuse rather than reinvent, if/when
department-scoped *provisioning* of ntfy subscriptions (as opposed to
each office manually subscribing to the zone topics that matter to them)
becomes a real requirement. Flagging this as future work, not committing
to it here.

## 5. Current implementation status (as of 2026-08-03)

| Family | Feed source(s) | Aggregate + zone topics | Escalation default | Isolated siblings |
|---|---|---|---|---|
| `tbfm` | ingest-tbfm | `tbfm-alerts` / `tbfm-<zone>` | escalating-only, base_priority=2 | none |
| `tfms` | ingest-tfms (core + `tfms_aptc` + `tfms_gadv`) | `tfms-alerts` / `tfms-<zone>` | escalating-only, base_priority=3 | none (all three share the sector-wide count by design) |
| `fdps` | ingest-fdps (`feed_name="fdps"`) + ingest-notam NOTAM restrictions (`feed_name="fdps_notam"`) | `fdps-alerts` / `fdps-<zone>` | fdps: escalating-only, base_priority=3. fdps_notam: fires on first occurrence (`escalating_only=False`), base_priority=4 | fdps_notam isolated from fdps (2026-08-03) |
| `itws` | ingest-itws | `itws-alerts` / `itws-<zone>` (plus legacy direct `wx-alerts`, retained) | escalating-only, base_priority=4 | none |
| `aim_fns` | ingest-notam ("fns" feed, non-VIP/non-restriction NOTAMs) | `aim_fns-alerts` / `aim_fns-<zone>` (plus legacy direct `nas-alerts`, retained) | escalating-only, base_priority=3 | none |
| `stdds` | ingest-stdds | `stdds-alerts` / `stdds-<zone>` (see §6 -- 10 zone topics: 3 individual airport topics for the DC regional-focus trio, 7 pooled ARTCC-zone topics for the rest) | stdds_surface: escalating-only, base_priority=2. stdds_safety (incursion): fires on first occurrence/change (`escalating_only=False`), base_priority=3. stdds_taxi: escalating-only, base_priority=2. **All three pass through `_stdds_priority()` — see note below** | none |

**STDDS is also the one family with a geographic *priority* split, not just
a topic split** (`_stdds_priority()`, `smes_parser.py:518-521`, added
2026-08-05 — omitted from earlier revisions of this table). The
`base_priority` values above are what a DC-regional airport
(`_STDDS_REGIONAL_AIRPORTS` = `KDCA`/`KIAD`/`KBWI`) actually fires at;
every other tracked airport fires one level lower, floored at ntfy's
minimum of 1 — so nationwide `stdds_surface`/`stdds_taxi` land at priority
**1** and nationwide `stdds_safety` at **2**. This changes only the push's
`priority` field, never whether it fires or which topics it reaches. No
other family does this; `fdps_parser.py`'s DC-scoping is the pattern it was
modelled on.

Not part of this family pattern by design: VIP NOTAMs (`hot-alerts` only,
never diluted into a family topic — the one case where every single event
is independently critical regardless of trend).

## 6. The eight tracked facility zones, and the regional-focus pattern

Every family in the table above (except the intentionally-unsplit `aim_fns`/legacy `nas-alerts` case) can fire a per-zone topic in addition to its aggregate `<family>-alerts` topic. "Zone" here means one of eight named facilities, defined once in `shared/sector_coalesce.py`'s `_ARTCC_GROUPS` and reused by every parser rather than each feed keeping its own copy:

| Zone code | Facility (ARTCC/TRACON) | Representative airports tracked |
|---|---|---|
| `zdc` | Washington ARTCC (ZDC) / Potomac TRACON (PCT) | **DCA, IAD, BWI** -- [operator LLC]' home region, see below |
| `zny` | New York ARTCC (ZNY) / N90 TRACON | JFK, LGA, EWR |
| `zid` | Indianapolis ARTCC (ZID) | CVG, SDF |
| `zob` | Cleveland ARTCC (ZOB) | CLE, PIT, DTW |
| `zatl` | Atlanta ARTCC (ZTL) / Atlanta TRACON | ATL |
| `zhu` | Houston ARTCC (ZHU) | IAH |
| `zla` | Los Angeles ARTCC (ZLA) | LAX, LAS |
| `zse` | Seattle ARTCC (ZSE) | SEA, PDX |

**How a feed gets zone-routed.** TBFM and FDPS pass their raw ARTCC-style facility code straight into `fire_family_alert()`, which resolves it against the table above via `resolve_sector()`/`sector_ntfy_topic()`. ITWS and STDDS instead carry airport ICAO codes (K-prefixed, e.g. `KATL`) -- the airport codes in the table exist specifically so those two feeds resolve correctly too, not just the ARTCC code itself.

**STDDS is the one exception with extra granularity.** Every other family pools all traffic for a zone onto one topic (e.g. `tfms-zatl` carries every tracked Atlanta-area TFMS program). STDDS instead gives DCA/IAD/BWI their own individual topics (`stdds-dca`, `stdds-iad`, `stdds-bwi`) rather than pooling them into a single `stdds-zdc` -- this is [operator LLC]' own home-region operational focus, and predates the 2026-08-03 extension to the other seven zones (see `stdds_incursion_taxi_per_airport_zones_20260803` in project memory). The other seven STDDS zones (`stdds-zny`, `stdds-zid`, `stdds-zob`, `stdds-zatl`, `stdds-zhu`, `stdds-zla`, `stdds-zse`) are pooled, matching every other family.

**Swapping the regional focus for a different home base.** If this platform is ever deployed for an operator based somewhere other than the DC area, the fix is exactly one constant: `_STDDS_REGIONAL_AIRPORTS` in `src/ingest/parsers/smes_parser.py`. Swap in that operator's own three (or however many) local airports and STDDS will give them individual zone topics the same way DCA/IAD/BWI get them today -- nothing else in the code treats DCA/IAD/BWI specially. The other seven zones' `_ARTCC_GROUPS`/`_STDDS_ZONE_AIRPORTS` entries are geography, not configuration, and don't need to change just because the home region moves -- though an operator based near one of those seven (say, Atlanta) may want to promote that zone to the same individual-topic treatment zdc gets, which is a slightly bigger change (see `_stdds_sector_for()` in `smes_parser.py` for where that logic lives).

**Scope is deliberately bounded to these eight zones, not literally nationwide.** STDDS, TBFM, TFMS, ITWS, and FDPS all see genuinely nationwide traffic (STDDS alone has logged real data from over 40 distinct airports). Data storage stays nationwide across all of them -- only alerting is scoped to these eight zones, on purpose. The `ntfy-topic-count-watchdog.sh` (warn threshold 140 topics) is the concrete reason: this platform already caught one live incident of unscoped incursion/taxi alerting nationwide-firing dozens of zone topics within 15 seconds of a restart (see the same 2026-08-03 memory entry above), and the airport list per zone in this section was chosen to stay well clear of that ceiling (105 topics measured immediately after this extension shipped, against the 140 warn threshold) rather than to be an exhaustive account of every ASDE-X-equipped airport in the country. **Live re-measurement 2026-08-23: `bash scripts/ntfy-topic-count-watchdog.sh --status` reports `topic count 87 (warn threshold 140) -- no action`** — comfortably clear, and *below* the 105 measured on 2026-08-03. The figure is ntfy's own `ntfy_topics_total` Prometheus gauge, i.e. topics the ntfy server is currently holding, not the theoretical maximum the zone tables could produce — so it moves with real traffic and subscriptions and is not a monotonically growing number. Re-run the script rather than trusting either figure.

**Two real bugs found and fixed while building this (2026-08-03):**
- `_ARTCC_GROUPS["zatl"]` used the facility code `"ZATL"`, which does not exist -- the real Atlanta ARTCC identifier is `"ZTL"`. This meant Atlanta traffic had never zone-resolved for TBFM (or anything else) since the zone was added; confirmed against TBFM's own captured facility values (`tbfm_sequences` table has real `ZTL` rows, never `ZATL`). Fixed by correcting the code in the facility set; the zone *key* (`zatl`, used for topic naming like `tbfm-zatl`) is unaffected.
- `check_tfms_alerts()` and the general-advisory (GADV) handler in `tfms_parser.py` were hardcoded to a DC-only `_DC_FACILITIES` filter, despite the surrounding docstring already describing an "8 sectors, same pattern as TBFM" design. TFMS had, in practice, never actually reached the other seven zones -- every non-DC program was silently dropped before `fire_family_alert()` was ever called. Fixed by replacing the hardcoded set with `shared.sector_coalesce.is_tracked_facility()`, a new small helper that checks against the single shared zone table instead of each parser keeping its own copy of "which facilities count" in sync by hand. Verified live post-fix: TFMS now logs real `sector=NEW_YORK`, `sector=ATLANTA`, and `sector=DC_LOCAL` resolutions in the same short window, not DC_LOCAL alone.

**ITWS and FDPS required no parser code changes at all** for this extension -- both already called `fire_family_alert()` unconditionally for every airport/facility they saw, with no DC-only pre-filter. They simply couldn't zone-resolve outside `zdc` before this change because the shared facility table didn't have K-prefixed airport codes or any airport code at all for five of the eight zones. Fixing the shared table was sufficient for both.

## 7. Related documents



- `INFRA_MAP.md` §8 (topic inventory) / §8.1 (mechanics — throttle,
  sanitize, per-zone threshold tuning) — the technical reference for what
  exists and how to operate it.
- `ALERT_REFERENCE.md` — per-parser trigger/dedup catalog. **Refreshed
  2026-08-19**: its "Sector/corridor coalescing" section no longer
  describes the pre-family-pattern state, and its FDPS/ITWS/AIM/STDDS
  sections now match this document. That pass also corrected several
  fabricated or stale claims in it (a non-existent `ctdi-watchdog` timer —
  though a *different*, real root-scope `corporatetraveldc-watchdog.timer`
  was added 2026-08-21; see ALERT_REFERENCE's rewritten watchdog section —
  two skills documented as ntfy publishers that publish nothing, six
  standalone scripts' priorities, and `stdds` recorded as "not wired") and
  added a section enumerating the publishers it had been missing. Read its
  2026-08-19 scope note before treating it as exhaustive.

## 8. Open questions / future work

- ~~**ALERT_REFERENCE.md refresh**~~ — done 2026-08-19: its
  FDPS/ITWS/TBFM/TFMS/AIM-NOTAM sections, a new STDDS section, and the
  "Sector/corridor coalescing" section are current with the family-alert
  pattern, per-topic throttle, and isolation mechanism.
- **Layer 1 scope decision** — see the `NEEDS OPERATOR DECISION` note at
  the end of §1: narrow the documented contract to coalesced family topics
  permanently, or wire the enable/throttle/sanitize gate into
  `ntfy_push.send()` with a reload path so it genuinely applies
  platform-wide. Whichever way it goes, the read-once-at-import behaviour
  and the web-container-sets / ingest-containers-send split need fixing
  before the control can be described as usable mid-incident.
- **Two documented-but-silent publishers** — `daily_brief.py` and
  `freshness_audit.py` (see the §2 scope caveat and `ALERT_REFERENCE.md`'s
  poller-skills section). Decide whether they should alert or whether
  their docstrings should stop saying they do. The freshness audit going
  unheard is the operationally significant half.
- ~~**STDDS alert criteria**~~ -- resolved 2026-08-03: ASDE-X ground
  congestion, SafetyLogicHoldBar incursion signals, and
  SurfaceMovementEventMessage taxi-phase gauges are all built and
  extended to the 8-zone model (§6). ~~Genuinely open follow-on: the other
  ~11 STDDS message types confirmed live but never wired~~ — **largely
  closed 2026-08-30**: `smes_parser.py` now parses the four TDES/APDS
  shapes on the same queue — `RVRDataUpdateMessage` (→ `stdds_rvr`),
  `TowerDepartureEventMessage` (→ `tdes_departure_events`),
  `TDLSCSPMessage` (PDC/CPDLC text → `tdls_messages`, with regex-extracted
  EDCT/route), and `DATISData` (→ `datis_snapshots`) — as **storage**, not
  new alert paths. The `Asset*`/`*ServiceStatus` health-check shapes remain
  deliberately unparsed (documented in the parser).
- **FDPS/FIDS OOOI standing-record channel** — a planned always-on log of
  OOOI milestone events (separate concern from the alert families above,
  intended as a sanitizable demo/reporting source of truth).
- **Department/office-scoped topic provisioning** — see §4. No current
  requirement, but the multi-operator/department pattern already built for
  RSS/intel feeds is the natural template if this becomes one.
- **Alert-only guard layer (2026-08-21)** — three guards now alert instead
  of acting (watchdog system-restart gate, CRIT feed staleness, thermal
  restore verification). Decide whether these belong in the layer model's
  escalation discipline or stay deliberately outside it.
