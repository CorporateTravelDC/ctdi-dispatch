# REST fallback audit -- push feeds (2026-09-06)

Work-order item 1 ("solidify the REST fallbacks"). Scope: every push feed the
stack ingests, what the poller does when the push side goes quiet, and how
that interacts with the thermal guard's LOCKDOWN shed / restore and the
poller's startup hold-off. Code-derived; nothing here was observed by
touching a live service.

Vocabulary
- push heartbeat: `feed_state` row `push:<feed>` stamped by the push writer
  via `ingest.failover.mark_push_healthy` (every 30 s for SWIM/NWWS while the
  session is up; once per successful poll for amtrak). `mark_push_down`
  keeps the timestamp and sets `error`, so `push_is_healthy` goes False at
  once and the row then ages.
- gate: `FetchLoop.maybe_run` skips the REST fetch while
  `push_is_healthy(push_feed, push_max_age)` is True. Default
  `FALLBACK_MAX_AGE = 90` s; `push_max_age` in `FETCH_SCHEDULE` overrides it
  per feed (new).
- startup grace (new): for the first `POLLER_STARTUP_HOLDOFF_S +
  POLLER_STARTUP_PUSH_GRACE_S` seconds after the poller starts (120 + 120 =
  240 s by default), a push-gated fetcher does NOT fall back to REST if the
  stale heartbeat predates the poller's own boot. A heartbeat that goes
  stale AFTER boot still triggers REST immediately.
- kickover guardrail: host timer `corporatetraveldc-failover-kickover-guardrail`
  (every 5 min) forces a `refresh_feed` trigger when the push side is
  unhealthy AND the REST twin has not run for `interval * 4` (20 min). Now
  shed-aware (see section 3).

## 1. Per-feed table

| feed (push) | REST fallback | gate / threshold | heartbeat source | during LOCKDOWN shed | on restore | rate limit / 429 handling |
|---|---|---|---|---|---|---|
| nws (NWWS-OI XMPP) | `poller.fetchers.nws` -> api.weather.gov alerts + zone forecasts, every 300 s | `push_feed=nws`, max_age 90 s | `ingest.nwws` stamps every 30 s once `session_start` has really happened (2026-09-05 fix) | ingest-core AND poller are stopped: no push, no REST. Heartbeat ages; nothing polls. | poller restarts; nws slot fires at +165 s. Grace holds REST until +240 s unless the push heartbeat is fresh by then (NWWS normally reconnects inside the hold-off). Guardrail does not count the shed. | api.weather.gov: no published hard limit, requires User-Agent (set). No 429 branch; error -> `upsert_feed(error=...)`, retried next 300 s tick. |
| fns / NOTAM (SWIM FNS) | `poller.fetchers.notam` -> NMS-API `api-nms.aim.faa.gov/nmsapi/v1/notams`, every 300 s, OAuth2 client-credentials (`NMS_API_CLIENT_ID/SECRET`, 30-min token) | `push_feed=fns`, max_age 90 s | `ingest.swim_client` stamps every 30 s while the JMS session is up | ingest-notam stopped, poller stopped: nothing. | notam slot at +180 s; grace holds REST until +240 s if the stamp is pre-boot. SWIM FNS usually reconnects inside the hold-off, so the common case is "no REST at all". | NMS 429 observed 2026-09-05 after 18 back-to-back forced pulls (bunched `refresh_feed` triggers on restart, now coalesced per pass). No 429 branch in the fetcher; server 30 s cap -> 408; `FETCH_TIMEOUT=25`; 24 h `lastUpdatedDate` window. No documented numeric limit found in repo docs. |
| amtrak (Amtrak tracker JSON) | `poller.fetchers.amtrak` (existed, was NOT scheduled) -> now in `FETCH_SCHEDULE`, every 300 s | `push_feed=amtrak`, `push_max_age=660` (two missed 300 s polls + slack) | `amtrak-tracker` container (primary) and `ingest.amtrak` (in ingest-core) each stamp once per successful 300 s poll | amtrak-tracker container survives the shed (not in the guard's shed set), so push keeps running. ingest-core copy stops. Poller stopped. | amtrak slot at +195 s; push heartbeat is normally fresh (tracker never stopped) so REST stays idle. | Amtrak tracker endpoint: no documented limit; `FETCH_TIMEOUT=12`; no 429 branch. |
| tfr (no push twin since 2026-07-23) | `poller.fetchers.tfr` -> tfr.faa.gov `tfrapi/getTfrList`, every 300 s | none -- always polls | n/a | poller stopped: no TFR refresh for the shed duration | tfr is slot 0 -> +120 s | No 429 branch; `FETCH_TIMEOUT=15`. |
| nas (no push twin since 2026-07-23) | `poller.fetchers.nas` -> nasstatus.faa.gov airport-status-information, every 300 s | none -- always polls | n/a | as tfr | nas slot 2 -> +150 s | No 429 branch; `FETCH_TIMEOUT=10`. |
| fdps (SWIM FDPS flight data) | NONE | n/a | swim_client 30 s | ingest-fdps stopped; durable-queue backlog accumulates on the broker, drained in a burst on restore (ingest-fdps 40 -> 1336 lines/min observed) | reconnect + backlog drain; no REST | n/a |
| tbfm (SWIM TBFM metering) | NONE | n/a | swim_client 30 s | as fdps | as fdps | n/a |
| itws (SWIM ITWS weather) | NONE | n/a | swim_client 30 s | as fdps | as fdps | n/a |
| stdds (SWIM STDDS surface) | NONE | n/a | swim_client 30 s | tier-1 shed target as well as LOCKDOWN | as fdps | n/a |
| tfms (SWIM TFMS flow) | NONE | n/a | swim_client 30 s | tier-1 shed target as well as LOCKDOWN | as fdps | n/a |

tfr and nas deliberately have no push gate: the 2026-07-23 removal fixed
bogus `push_feed` mappings (stdds/tfms) that silently suppressed real polls.
Leave them independent.

## 2. Findings that were fixed in this pass

F-1 Gate would fire REST needlessly right after a restart. After a LOCKDOWN
shed or a flat restart, every `push:*` stamp is older than 90 s, so the
first eligible slot (+165 s nws, +180 s notam) would have hit
api.weather.gov and NMS while the SWIM/NWWS sessions were still coming
back. Fixed by the pre-boot heartbeat grace (`POLLER_STARTUP_PUSH_GRACE_S`,
default = hold-off = 120 s; `0` restores the old behaviour). The grace only
protects stamps older than the poller's own boot, so a push feed that dies
mid-run still falls over to REST at the next tick.

F-2 Amtrak REST fetcher was dead code (not in `FETCH_SCHEDULE`;
`TriggerReactor._run_fetcher` rejects unscheduled feeds, so even the web
`/admin/refresh-feed/amtrak` button was a no-op). Scheduled at 300 s with a
660 s push gate. Output is consumed: `db.insert_amtrak_status` feeds
`/api/v1/amtrak`, ops_brief, second_brain_daily, transport_pattern_digest
and the feed-db integrity check. The tracker container stays the primary;
the poller only writes when both push writers have missed two polls.

F-3 Kickover guardrail counted shed time as a gap. Under a LOCKDOWN the
push feeds AND the poller are down, so every 5-min guardrail tick saw
"push unhealthy + REST not run for > 20 min" and queued a forced
`refresh_feed`; on restore those all ran back-to-back (the NMS 429 above,
36 GAP alerts on 2026-09-06 all inside shed windows). Now: reads the guard
state file, observes only while `tier >= 2` or within
`FAILOVER_GUARDRAIL_RESTORE_GRACE_S` (600 s) of `restored_at`, and measures
the REST age from `restored_at` so shed time never counts toward the 20-min
threshold. Also fixed a latent crash formatting `rest_age` when the REST
twin had never run.

## 3. Shed / restore mechanics (as now implemented)

Shed (tier 2): guard stops ingest-{fdps,stdds,tfms,tbfm,itws,notam},
ingest-core, poller, pusher, runner. Heartbeats stop; `push_is_healthy`
goes False after 90 s. Nothing polls (poller down). Guardrail keeps ticking
on the host but sees `tier=2` and only logs.

Restore: `ingest-feed-ctl.sh restart all` (staggered), core, then
poller/pusher/runner. Poller: hold-off 120 s, then one loop per 15 s slot.
Push-gated fetchers: pre-boot stamps ignored until +240 s. Guardrail: inside
the 600 s restore grace it observes only; after that, REST age counts from
`restored_at`, so the first possible forced kickover is `restored_at + 20
min` (and only if the push side is still unhealthy AND the poller's own
fallback has not run -- which by then it would have, at its 300 s cadence).

Net effect: a clean restore produces zero REST calls to FAA/NWS. A push
feed that fails to reconnect is picked up by the poller's own fallback at
roughly +240..+300 s; the guardrail is only the backstop for a poller that
is itself wedged.

## 4. Rate limits (grep of fetchers + docs)

- No fetcher branches on HTTP 429. All raise on non-2xx, which lands in
  `upsert_feed(error=...)` and is retried at the next interval. That is
  acceptable at 300 s cadence; the problem case was bunched forced
  triggers, which is now closed at both ends (trigger coalescing in the
  poller, shed-awareness in the guardrail).
- FAA NMS-API: no numeric limit documented in this repo; the one observed
  429 followed 18 pulls in a few minutes. Keep forced pulls serialized.
- api.weather.gov: requires User-Agent (present); no numeric limit found.
- tfr.faa.gov / nasstatus.faa.gov / Amtrak tracker: no limits documented;
  300 s cadence has run for months without incident.
- Housekeeping inconsistencies (docs only, not fixed here):
  `docs/DATA_SOURCES.md` still documents `FAA_NOTAM_API_KEY/SECRET` (the
  fetcher uses `NMS_API_CLIENT_ID/SECRET`); `poller/skills/pull_path_verify.py`
  still lists notam as `active: False, auth_gated`; web `push_covers` has
  no amtrak entry (web `stale_thresholds` already has `push:amtrak: 300`,
  which is tighter than the poller's 660 s gate -- harmless, it only
  colours the status page).

## 5. FDPS / TBFM / ITWS / STDDS / TFMS -- no REST fallback (operator decision)

No public endpoint carries equivalent data for any of the five. Two
implementation passes on 2026-09-06 built degraded-mode twins anyway
(local-receiver first, then OpenSky live states NAS-wide) and were REMOVED
the same evening after the operator reviewed what they actually delivered:

- FDPS publishes flight plans (origin/destination, filed route, ETD/ETA,
  amendments) AND ERAM-derived NAS-wide track positions. OpenSky covers
  only the second half, only for ADS-B/MLAT-equipped aircraft inside its
  crowd-sourced coverage: no route, no origin/destination, no ETA, so every
  downstream consumer that needs a plan still had nothing.
- TBFM per-flight sequences/STAs have no public source at all. NAS Status
  airport-events (arrival rates, average metering delay) is program-level.
- STDDS is 1 Hz ASDE-X/ASSC surface movement including vehicles. One
  OpenSky snapshot per 300 s (credit budget) with on_ground=true is a
  presence list, not surface data.
- ITWS microburst / gust-front / wind-shear detections come from TDWR and
  LLWAS. METAR/TAF/PIREP/SIGMET are reports and forecasts, not detections.
- TFMS AFP/FCA/EDCT/reroute detail is not public; NAS Status covers GS/GDP
  at program level only.

Operator, 2026-09-06 (verbatim): "Given the no-go case, let's just keep
the three that are hardened, and we'll just take the L on the others until
something comes available."

What "something comes available" would look like: a nationwide source
carrying flight plans, routes and ETAs -- FlightAware AeroAPI (paid,
per-query, FAA-radar-derived positions plus filed route/ETA; the nearest
thing to FDPS) is the known candidate. TBFM and STDDS stay degraded-mode
regardless (no public equivalent exists). Wiring AeroAPI would follow the
notam pattern exactly: a poller fetcher gated on `push:fdps`, credential
names in the secrets file, `PUSH_FEEDS` unchanged (fdps is already in it).

Partial substitutes that already run and keep running during a shed:
local ADS-B (ultrafeeder) and ACARS for positions within receiver range;
DCA/IAD FIDS for gate/arrival status at the home airports; NAS status for
facility-level delays; ATCSCC ops plan hourly; NWS alerts, convective
SIGMETs and METAR/SPECI via ADDS for weather. The SWIM durable queue holds
each feed's backlog through a shed; the cost is the drain burst on restore
(the "bounded backlog drain" sub-task).

## 6. Operator decisions

D-1 CLOSED 2026-09-06: no REST fallback for FDPS, TBFM, ITWS, STDDS, TFMS
(section 5). Revisit when a nationwide flight-plan source is adopted.

D-2 Web `push_covers` entries for amtrak (and a degraded-mode marker for
itws) -- status-page only, no polling change. Not built; open.

D-3 The new knobs ship with defaults that need no env change:
`POLLER_STARTUP_PUSH_GRACE_S` (default = `POLLER_STARTUP_HOLDOFF_S`),
`FAILOVER_GUARDRAIL_RESTORE_GRACE_S` (default 600). Set either to `0` to
get the pre-2026-09-06 behaviour.

D-4 Rollout: the guardrail runs on the host from the repo path (no image),
so it takes effect at its next timer fire once the tree is signed. The
poller (`FETCH_SCHEDULE`, grace) and ingest (`failover.push_last_seen`,
`PUSH_FEEDS`) changes need sign + rebuild of the poller and ingest images
and a load-gated restart.
