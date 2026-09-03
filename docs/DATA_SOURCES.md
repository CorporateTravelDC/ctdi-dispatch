# CTDI Data Sources & Access Guide

**Snapshot verified 2026-08-11** (credential variable names checked against
current source; per-section "last verified" dates track the external portals).
**Scope and integration status re-verified against live code and live
`/api/v1/feeds` on 2026-08-19; reconciled again 2026-08-23** (`FETCH_SCHEDULE`
symbol name, Amtrak single-path, NWWS 2026-08-20/22 fixes, ACARS zero-rows
caveat, thermal-guard restore verification, runsheet duplicate-insert fix).
**Second 2026-08-23 pass, live-system-first:** rewrote the thermal-ingest-guard
section for the same-day load/LOCKDOWN redesign, retired the
`disabled: SWIM_NMS_SKIP_FEEDS` paragraph (that DB write was removed), pinned
`_LOW_PRIORITY_FEEDS`, and refreshed the cadence table's live-state column.
**Reconciled 2026-09-03** against
`docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md`: the `bandwidth_priority=ollama`
mode was **removed 2026-08-28** (post-llama.cpp-cutover — weather/nexrad are
the surviving modes); the thermal guard's fallback-count LOCKDOWN trigger was
demoted to informational-only and the guard no longer touches any LLM
service (2026-08-27); Amtrak's deployed push-primary is the `amtrak-tracker`
container; airplanes.live is no longer queried programmatically (2026-08-27
local-only rule); LADD data now arrives via a manual weekly CUI-handled
import (`docs/LADD_CUI_HANDLING.md`).

This document covers two distinct categories of source, and the distinction
matters — an earlier version of this line claimed the document "covers every
integrated data source", which read as if everything catalogued below were
wired in. It is not:

1. **Integrated sources** — a fetcher or ingest handler exists in `src/`, the
   credential variable names below are read by real code, and the feed appears
   in `/api/v1/feeds`. Everything in "US Sources", "European Sources"
   (EUROCONTROL), "Asia-Pacific Sources" (JASDAT), "AIS/Radar Aggregators",
   and "Global Aircraft Registry Sources" is in this category. *Integrated*
   does not mean *credentialed* — EUROCONTROL, JASDAT and the REST NOTAM
   fetcher are integrated but sit in `awaiting_credentials`; see the cadence
   table at the end of the US section for the live per-feed state.
2. **Researched / not yet integrated** — access research only. No fetcher, no
   parser, no credential wiring; the env var names shown are *proposed*, not
   implemented. These live in their own clearly-labelled section near the end
   of this file. The research is genuinely useful and is kept verbatim — it is
   just not a description of running software.

It also serves as the canonical reference for wiring in new sources — any new source that requires API signup or an email request should have an entry added here when it's integrated.

> **Maintenance:** When a portal URL, email address, or signup process changes, update this file in the same commit that updates the code. The last-verified date in each section header tracks when the information was confirmed current.
>
> **When a source moves from researched to integrated**, move its entry out of
> the "Researched / not yet integrated" section into the appropriate regional
> section in the same change that lands the fetcher — otherwise this file
> drifts back into implying coverage it doesn't have.

---

## US Sources

### FAA SWIM / NMS (System Wide Information Management — Network Management Server)

**Last verified:** 2026-08-02

**What it provides:** Push-primary flight plan data (FDPS), real-time surface/terminal tracks (STDDS), NAS flow programs (GDP, GS, AFP, AAR -- TFMS), digital NOTAMs (AIM/AIM_FNS), terminal weather alerts (ITWS), and arrival sequencing (TBFM). Six separate Solace PubSub+ VPN sessions, one per feed, each with its own durable exclusive queue -- see src/ingest/swim_client.py. The highest-fidelity aviation data feed available in the US.

**Correction (2026-07-26):** TFRs are NOT delivered via SWIM in this system, despite FAA's SWIM program including them in principle. This platform's TFR data comes from the completely separate tfr.faa.gov/tfrapi/getTfrList JSON REST endpoint (src/poller/fetchers/tfr.py), polled every 5 minutes independently of the SWIM ingest container. Do not assume a SWIM outage affects TFR data, or that fixing SWIM fixes TFR staleness -- they share no code path.

**Known operational note (2026-07-26):** all six SWIM sessions were found hitting SOLCLIENT_SUBCODE_KEEP_ALIVE_FAILURE in simultaneous bursts every 1-3 minutes, traced to this Pi's WiFi/gateway congestion combined with the Solace SDK's default ~9s keep-alive tolerance (3000ms x 3). Widened to 5000ms x 8 (~40s) in swim_client.py -- reduced but did not eliminate the churn; some congestion events exceed even 40s. See swim_client.py's inline comment at the SOLCLIENT_SESSION_PROP_KEEP_ALIVE_INT_MS property for detail. A priority/backpressure scheme to reduce ingest's own contention footprint during high-load periods is tracked separately (see ingest/backpressure work, 2026-07-26).

**Container topology (2026-07-26):** ingest is no longer a single container running all sources as threads. It is now split into seven independent Podman Quadlet units sharing the same `localhost/corporatetraveldc-ingest:latest` image, differentiated purely by environment variables -- no code duplication:

| Unit | Runs | Memory cap | CPU cap |
|---|---|---|---|
| `corporatetraveldc-ingest-core` | NWWS-OI, Amtrak, local airspace monitor -- zero SWIM | 256m | 80% |
| `corporatetraveldc-ingest-fdps` | SWIM FDPS only | 448m | 150% |
| `corporatetraveldc-ingest-stdds` | SWIM STDDS only | 384m | 120% |
| `corporatetraveldc-ingest-tfms` | SWIM TFMS only | 320m | 90% |
| `corporatetraveldc-ingest-tbfm` | SWIM TBFM only | 320m | 80% |
| `corporatetraveldc-ingest-itws` | SWIM ITWS only | 256m | 60% |
| `corporatetraveldc-ingest-notam` | SWIM AIM_FNS (NOTAM) only | 256m | 60% |

Resource caps are weighted by real observed volume the night this was built: FDPS (continuous nationwide flight events) and STDDS (surface/terminal tracks) are the heaviest; TFMS/TBFM moderate; ITWS/NOTAM lightest (NOTAM in particular reports "wrote 0" most cycles). The per-feed split is driven entirely by `SWIM_NMS_ENABLED`, `SWIM_NMS_SKIP_FEEDS` (pre-existing mechanism in swim_client.py), `NWWS_ENABLED`, `AMTRAK_ENABLED`, and a new `LOCAL_AIRSPACE_ENABLED` flag (src/ingest/main.py) that ensures only the core container runs the local airspace monitor thread.

**Why split it:** this lets any single feed be stopped, started, or restarted independently -- without dropping the other five SWIM sessions, NWWS-OI, Amtrak, or local airspace along with it. Previously all six SWIM sessions plus NWWS-OI/Amtrak/local-airspace lived in one process, so any restart (manual or OOM-triggered) dropped everything at once. Total measured footprint across all seven at steady state is roughly 300-350MB combined, versus roughly 126MB for the old single process under equivalent load -- a real but moderate increase, confirmed empirically (not guessed) before this split was built: a clean-room `podman run --rm` test showed the Solace PubSub+ client library itself costs ~30-38MB per process regardless of which single feed it's handling, since that's the dominant fixed cost, not the feed-specific parser code.

**Control:** `scripts/ingest-feed-ctl.sh status|stop|start|restart` operates on any single unit, `core`, or a comma-separated list, or `all`. `start`/`restart` support `--stagger=Ns` (default 15s) and `--order=lightest-first|heaviest-first` (default lightest-first) to control how the six SWIM feeds come up relative to each other; `core` always comes first regardless of order, since NWWS-OI/Amtrak/local airspace have no SWIM contention profile and nothing should wait on them. `stop` always fires every target at once (no reason to delay freeing resources).

**Relationship to the existing bandwidth-priority soft-pause (nexrad/weather modes, see /admin/bandwidth-priority):** that mechanism is unchanged and independent of this split -- it keeps a feed's Solace session connected but stops draining its queue, a soft, reversible-in-seconds lever that auto-triggers off severe/extreme NWS alerts. `ingest-feed-ctl.sh` is a separate, stronger lever: a real process stop that frees the container's actual CPU/memory, for situations (planned maintenance, a leaking/misbehaving single feed, genuine Tailscale/WiFi saturation) where soft pausing isn't enough. The two are not yet integrated -- the weather auto-trigger still only does the soft pause, not a hard stop. Whether/how to wire the two together is an open design question, not yet decided.

**Preventive OOM-leak restart watchdog (scripts/scheduled-ingest-restart.sh):** previously scoped to the single monolithic ingest service; updated the same day to check and restart each of the seven containers independently (own cooldown per-container), so one container's threshold-triggered restart never touches the other six.

**Parser status confirmed live, all 7 feeds (2026-08-02):** FDPS, STDDS, TFMS, TBFM, ITWS, and AIM/NOTAM parsers are all fully deployed and writing real data -- confirmed today via direct SQL against the notams table (4,836 rows, 267 distinct facilities, `last_seen_at` from minutes prior) and via `corporatetraveldc-ingest-notam`'s own logs showing continuous `aim: wrote N NOTAM(s)` activity. None of the 7 feeds are in a "partial" state as of tonight. (Re-checked 2026-08-23: `notams` now holds 5,419 rows across 307 distinct facilities — still growing, so the AIM/FNS path is genuinely live. Re-derive rather than trusting either figure.)

**Credential status (verified 2026-08-19):** all six SWIM feeds are
provisioned and credentialed --
`SWIM_NMS_{HOST,USER,PASS,QUEUE}_{FDPS,STDDS,TFMS,AIM,TBFM,ITWS}` are all
present and populated in `dispatch-secrets.env`. Nothing below about a feed
being stopped is a credential problem.

> **Liveness caveat -- read this before "fixing" a stopped ingest unit.**
> "All six SWIM feeds are live" describes provisioning, not a guarantee of
> continuous running. Two independent mechanisms routinely stop or suspend
> individual SWIM feeds as *designed behaviour*:
>
> * **`thermal-ingest-guard` hard-stops** whole ingest containers under CPU
>   load or heat (see
>   [Thermal ingest guard](#thermal-ingest-guard-2026-07-26-load-model-redesigned-2026-08-23)
>   at the end of this file). A shed unit shows `inactive (dead)` with
>   `Result=success` -- it exited 0, so **it will never appear in a
>   `systemctl list-units --failed` sweep or any "failed unit" grep**. Since
>   the 2026-08-23 redesign there are two shed shapes, not two tiers of
>   feeds: a mild **temp tier 1** stops `tfms,stdds` only, and a
>   **LOCKDOWN** stops all six SWIM feeds (`fdps,stdds,tfms,tbfm,itws,notam`)
>   *plus* `ingest-core`, `poller`, `pusher`, `runner`
>   — everything except `web`. (2026-08-27 corrections: the third,
>   LLM-contention-fallback trigger was demoted to informational-only, and
>   the guard no longer stops any LLM service — `ollama.service` is gone
>   with the llama.cpp cutover and the `corporatetraveldc-llama-*` units are
>   deliberately out of LOCKDOWN scope.) A LOCKDOWN therefore looks like a
>   platform-wide outage while being entirely by design. The tier flips on a
>   2-minute cadence (a real LOCKDOWN fired and fully restored on
>   2026-08-23 between 12:18 and 12:30 EDT), so always check the state file
>   rather than trusting any snapshot written here.
> * **`bandwidth_priority` soft-suspends** a feed's queue drain while keeping
>   its Solace session connected (below).
>
> The authoritative state for the hard-stop path is
> `/var/lib/corporatetraveldc/thermal_ingest_guard_state.json` plus
> `journalctl --user -u corporatetraveldc-thermal-ingest-guard`. The
> authoritative state for the soft-suspend path is
> `GET /api/v1/bandwidth-priority` (Tier 0, no auth). Check both before
> concluding a feed is broken.

**`push:*` feeds reporting `suspended: bandwidth_priority=<label>` --
corrected 2026-08-19; label set updated 2026-09-03.** An earlier version of
this section recorded the
`push:fns` entry's error string as a stale, un-root-caused
`"disabled: SWIM_NMS_SKIP_FEEDS"` display bug. That is no longer what the live
API reports, and the current string has a real, fully-understood mechanism
behind it. Live `/api/v1/feeds` has shown `push:fns`, `push:itws`,
`push:stdds` and `push:tbfm` carrying (historical example — the `ollama`
label no longer exists, see below):

```
"error": "suspended: bandwidth_priority=ollama"
```

**Which feeds this can hit is a fixed set, not arbitrary:**
`_LOW_PRIORITY_FEEDS = {"stdds", "tbfm", "itws", "fns"}`
(`src/ingest/swim_client.py:271`). `fdps` and `tfms` are deliberately
never paused this way. Note ITWS — the *terminal weather* feed — is in the
deprioritized set, which cost real visibility during a live severe-weather
event on 2026-08-22/23; whether it belongs there is an open operator
question tracked in CLAUDE.md, not settled here.

This is the **bandwidth-priority backpressure valve**, not a skip and not a
display bug:

* State lives in the `bandwidth_priority_state` singleton row, read/written by
  `get_bandwidth_priority()` / `set_bandwidth_priority()` in
  `src/common/db.py`.
* `src/ingest/swim_client.py::_bandwidth_priority_says_pause()` consults it on
  its own ~5 s cadence (deliberately *not* per-message -- a per-message
  `sqlite3.connect()` caused lock contention that looked like an fdps hang in
  2026-07-21 testing). When it says pause, the feed thread stops draining its
  queue, leaves the Solace session connected, and stamps
  `suspended: bandwidth_priority=<label>` into `feed_state`
  (`swim_client.py` ~line 541). The string clears on resume.
* It is set automatically from one place, plus manually
  (**corrected 2026-09-03**):
  * ~~`src/common/llm.py` `_engage_ollama_backpressure()` setting
    `priority=ollama` around inference calls~~ — **REMOVED 2026-08-28**,
    post-llama.cpp-cutover: the whole `OLLAMA_BACKPRESSURE_*` apparatus was
    deleted from `llm.py` (tombstone comment at the old site) and
    `swim_client.py`'s docstring records the mode's removal. The surviving
    labels are `weather` (pauses the low tier — `_LOW_PRIORITY_FEEDS`) and
    `nexrad` (pauses `fdps` only).
  * `src/poller/fetchers/nws.py` (`_maybe_set_weather_priority()`)
    sets `priority=weather`, `set_by=auto-weather` on an active
    Severe/Extreme NWS alert for the DC region, and clears it afterwards.
    The auto-trigger will not override an operator's manual setting.
  * `POST`/`DELETE /admin/bandwidth-priority` (admin tier) for manual control;
    `GET /api/v1/bandwidth-priority` is Tier 0 so the state is readable
    without a token.

**`"disabled: SWIM_NMS_SKIP_FEEDS"` is gone as of 2026-08-23 and should
never reappear.** It used to be a real string the API could show
(`push:stdds` carried it on 2026-08-19), because `swim_client.py`'s
`SWIM_NMS_SKIP_FEEDS` branch called
`_db.upsert_feed_skip(f"push:{feed}", …, "disabled: SWIM_NMS_SKIP_FEEDS")`
for every feed a container skips. That was correct when one container ran
all six feeds, and wrong after the per-feed split: `feed_state` is a single
table shared by all seven containers keyed only by feed name, and every
container correctly skips the five feeds it doesn't own — so the write
clobbered the real owning container's health the moment any sibling
(re)started. Confirmed live at the time: `push:itws` showed
`disabled: SWIM_NMS_SKIP_FEEDS` while the itws container was actively
connected and processing severe-weather alerts. The DB write was removed
from that branch (log-only now, `swim_client.py` ~line 824); only the
owning container's own 30 s `mark_push_healthy` heartbeat writes a feed's
status. Verified 2026-08-23: no `push:*` row in `/api/v1/feeds` carries a
`disabled:` string any more.

The one `upsert_feed_skip` write that *does* survive in that function is
the un-credentialed branch (`swim_client.py` ~line 835), which stamps
`"pending_credentials: NMS credentials not yet provisioned"` — a genuinely
per-feed, non-clobbering condition, since a credential gap is the same in
every container.

Separately fixed 2026-08-02: `/api/v1/feeds` now nulls `error` whenever `push_covered` is true for any feed (pull-side detail preserved in a new `pull_error` field instead of being shown as if it were the feed's current state) -- see `web/main.py`'s `/api/v1/feeds` handler. That fix is correct and verified.

**Backlog fast-forward triage (2026-07-26):** every SWIM feed session now checks each message's Solace `sender_timestamp` on reconnect. If the oldest queued message is already older than `SWIM_BACKLOG_STALE_SECONDS` (default 7200s / 2h) -- meaning a real backlog built up during whatever outage just ended, soft-paused or hard-stopped -- the feed keeps processing anything under that threshold or within the most recent `SWIM_BACKLOG_RECENT_FRACTION` (default 0.10) of the backlog's total time span, and silently drops everything else without ever handing it to the per-feed parser (not written to the DB, not pushed, not counted as filter-accepted -- still counted as received bytes for the existing feed-data-usage counters). For a 2-day outage that means the newest ~4.8h of backlog still gets processed; the rest is discarded. For anything shorter than the 2h floor (e.g. the observed 10-15 minute Ollama-governor-scale pauses), this never engages at all -- the whole backlog processes normally, identical to pre-2026-07-26 behavior. Live-verified 2026-07-26 across all 7 containers post-rollout: no handler errors, no spurious triage triggers (nothing was actually behind), normal write volume unaffected. Not yet exercised against a real multi-hour+ backlog -- next genuine outage will be the first live test of the drop path itself.

**API type:** Solace PubSub+ message queue (AMQP/JMS). The `solace-pubsubplus` Python library handles the connection.

**Signup portal:** [https://portal.swim.faa.gov/](https://portal.swim.faa.gov/)

**Policy documentation:**
- SWIM program overview: [https://www.faa.gov/air_traffic/technology/swim/](https://www.faa.gov/air_traffic/technology/swim/)
- NMS user guide: [https://www.faa.gov/air_traffic/technology/swim/products/](https://www.faa.gov/air_traffic/technology/swim/products/)

**Access process:** Submit a request at the portal. FAA reviews organizational eligibility and intended use. Approval typically takes several weeks. There is no fee for qualified requestors.

**Email template (if portal submission requires follow-up or direct contact):**
```
To: swim@faa.gov
Subject: SWIM NMS Access Request — [Your Organization Name]

FAA SWIM Team,

I am requesting access to the FAA SWIM Network Management Server (NMS) for the
following data feeds: FDPS, STDDS, TFMS, AIM, TBFM, ITWS.

Organization: [Your organization name and type — e.g., aviation operator, ANSP, research]
Use case: Real-time operational situational awareness for [describe your operation].
Deployment: Self-hosted, on-premises. Data is not redistributed or resold.
Technical contact: [Your name, email, phone]

I have submitted a request via the portal at portal.swim.faa.gov on [date].
This email is a follow-up to confirm receipt and ask about estimated review timeline.

Thank you,
[Your name]
[Organization]
[Contact information]
```

**Credentials location in dispatch-secrets.env:**
```bash
SWIM_NMS_USER_FDPS=
SWIM_NMS_PASS_FDPS=
SWIM_NMS_QUEUE_FDPS=
# (repeat pattern for STDDS, TFMS, AIM, TBFM, ITWS)
```

---

### FAA NOTAM API

**Last verified:** 2025-12

**What it provides:** Active NOTAMs for US airports and airspace. REST API returning JSON.

**Signup portal:** [https://api.faa.gov/signup](https://api.faa.gov/signup)

**API documentation:** [https://api.faa.gov/notam/home](https://api.faa.gov/notam/home)

**Policy documentation:** [https://api.faa.gov/](https://api.faa.gov/) — terms of use on the portal

**Access process:** Self-serve API key registration at api.faa.gov. No organizational review required; individual developers qualify. Key is issued immediately after email verification.

**No email required** — portal registration is fully self-serve.

**Credentials location** (both are required —
`src/poller/fetchers/notam.py` reads the key *and* the secret; the feed
reports `awaiting_credentials` until both are set):
```bash
FAA_NOTAM_API_KEY=
FAA_NOTAM_API_SECRET=
```

**Live status (verified 2026-08-19):** neither `FAA_NOTAM_API_KEY` nor
`FAA_NOTAM_API_SECRET` is present in `/etc/corporatetraveldc/dispatch-secrets.env`,
and `/api/v1/feeds` accordingly reports the `notam` feed as
`error: "awaiting_credentials"`. **This does not mean the platform is without
NOTAM data.** NOTAMs flow via the SWIM AIM/FNS *push* feed
(`corporatetraveldc-ingest-notam` → `push:fns`), which is separately
credentialed and writing to the `notams` table. The REST fetcher here is a
redundant pull-side path that has never been credentialed; `poller/main.py`
registers it with `"push_feed": "fns"`, so a healthy push heartbeat suppresses
the REST poll anyway. Wiring these two keys is an optional redundancy
improvement, not a fix for missing data.

---

### FAA Aircraft Registry (N-Number Database)

**Last verified:** 2026-07

**What it provides:** The full US civil aircraft registration database — every N-number, Mode S hex code, registrant name/address, aircraft type, engine type, status code, and certification dates. Refreshed by the FAA daily (the underlying file only changes once/day regardless of poll frequency, but polling daily buys faster recovery from a failed import rather than fresher data).

**Source:** `https://registry.faa.gov/database/ReleasableAircraft.zip` — a ~73MB ZIP containing `MASTER.txt` (fixed-column CSV, no header, ~314K rows).

**API type:** Static bulk file download. No API, no pagination, no auth.

**Access:** No credentials required. Public download, no registration.

**Reliability note (2026-07):** `registry.faa.gov` has shown frequent mid-transfer connection drops on this file — not a fixed timeout, just generally poor/variable throughput. The fetcher (`src/poller/fetchers/faa_registry.py`) resumes via HTTP Range headers on failure rather than restarting from byte 0, with exponential backoff and a 40-minute wall-clock ceiling. Verified end-to-end: a full import completed in ~28 minutes across 8 resumed attempts on a genuinely bad connection day.

**No email required.**

**Local lookup endpoint (once imported):**
```
GET /api/v1/aircraft/<N-NUMBER-or-HEX>
```
Only covers US-registered (N-number) aircraft — foreign-registered aircraft (UK G-, Canada C-, etc.) will 404 here regardless of import freshness. For non-US registries the only *integrated* fallback is OpenSky under [Global Aircraft Registry Sources](#global-aircraft-registry-sources) below; UK CAA G-INFO is researched but not integrated (see [Researched / not yet integrated](#researched--not-yet-integrated--no-fetcher-parser-or-credential-wiring-exists)).

**Credentials location:** None needed.

---

### FAA LADD (Limiting Aircraft Data Displayed)

**Last verified:** 2026-07 (integration status re-verified 2026-08-19)

> **Update 2026-09-03 — LADD data is live again via a different path.** The
> operator now downloads the two LADD filter files (FAA Source + Industry,
> both CUI SP-PRVCY) manually each week and imports them with
> `scripts/import-ladd-filter.py` into the `faa_ladd_aircraft` table (full
> replace, fail-safe on empty parse). Handling rules, exposure tiering, and
> scrub-gate coverage live in **`docs/LADD_CUI_HANDLING.md`** — read that
> before touching anything LADD-shaped. The paragraphs below about the dead
> anonymous ZIP and the un-built ADX path remain accurate as far as the
> *automated* fetcher goes.

**Integration status — partial, and the working half is dead upstream.**
Unlike the sources in the "Researched / not yet integrated" section, LADD
*does* have real code: `src/poller/fetchers/faa_registry.py` defines
`_FAA_LADD_URL` (`:45`), implements `_parse_ladd()` (`:259`), and the weekly
registry import run from `WatchlistSweep` via `_FAARegistrySweep`
(`src/poller/main.py` ~line 1673, logging `ladd_count` at `:1686`) reports
it. But that code targets the anonymous
`registry.faa.gov/database/LADD_Aircraft.zip` download, which has been dead
since June 2026 (HTTP 302 to an FAA office page); the fetcher treats it as a
non-fatal warning and carries on with the registry import. So the parser path
exists and runs, and reliably imports nothing. **The ADX / IndustryLADD access
path described below has no code behind it at all** — no fetcher, no
credential wiring, and the "Credentials location" note is explicitly a TBD.
Treat everything below the "Current official path" heading as research.

**What it provides:** A boolean flag per N-number indicating the registrant has opted out of display on services that redistribute FAA-source data (historically the ASDI feed). **Important:** LADD does not remove the aircraft from the registry, and does not stop it from broadcasting ADS-B — independent receiver networks (airplanes.live, this platform's own UltraFeeder) are not FAA-source-derived and are not bound by LADD restrictions. The only value LADD adds here is a discretion/context signal ("this owner requested privacy elsewhere"), not a tracking capability.

**Old access path — dead:** The anonymous `https://registry.faa.gov/database/LADD_Aircraft.zip` download that used to work is confirmed dead as of 2026-07 (returns a non-zip response).

**Current official path:** FAA's NAS Aeronautical Data Exchange (ADX) portal — the "IndustryLADD" list, published monthly (first Thursday).

**Signup portal:** [https://adx.faa.gov](https://adx.faa.gov)

**Access process:** Requires a Login.gov account associated with an ADX/MyAccess profile. Eligibility for a small commercial operator (vs. FAA/DoD/contractor accounts) is **unconfirmed** — worth a direct inquiry to the program office before investing time in the Login.gov registration flow.

**Email template (inquiry to the LADD program office before registering):**
```
To: LADD@faa.gov
Subject: IndustryLADD Access Eligibility -- [Your Organization Name]

FAA LADD Program Office,

I operate [describe your operation -- e.g., a small executive ground
transportation dispatch platform] and would like to confirm eligibility
for IndustryLADD list access via the NAS Aeronautical Data Exchange (ADX)
portal before registering for a Login.gov / MyAccess account.

Organization: [Your organization name and type]
Use case: Cross-referencing the LADD flag as a discretion/context signal
          alongside our existing FAA aircraft registry lookups -- not for
          redistribution or public display.
Technical contact: [Your name, email, phone]

Could you confirm whether a private commercial operator of my type
qualifies for IndustryLADD access via ADX, and if so, the registration
steps beyond creating a Login.gov account?

Thank you,
[Your name]
[Organization]
[Contact information]
```

**Alternative (unofficial, unvetted):** [laddlist.com](https://laddlist.com) is a third-party site that publishes both the Industry and Source LADD lists via a lookup tool. As of 2026-07 it has no discoverable public API for programmatic per-aircraft or bulk queries (client-rendered Next.js app -- `/api/search`, `/api/aircraft/<tail>` and similar guesses all 404). Would need the actual request shape captured from browser devtools before integrating. Not currently wired in -- flagged here as a known option, not a recommendation, given it's an unofficial source for FAA program data.

**Credentials location:** TBD pending eligibility confirmation -- likely Login.gov/OAuth-style rather than a simple username/password. Do not assume a `USER`/`PASS` env var pair matches the real mechanism until the registration flow is actually walked through.

---

### AviationWeather.gov ADDS (Aviation Digital Data Service)

**Last verified:** 2025-12

**What it provides:** METARs, TAFs, PIREPs, SIGMETs, AIRMETs. REST API returning raw METAR text or JSON. Covers ICAO codes globally, not just US airports.

**API endpoint used:** `https://aviationweather.gov/api/data/metar?ids={AIRPORT_LIST}&format=raw&hours=1`

**API documentation:** [https://aviationweather.gov/data/api/](https://aviationweather.gov/data/api/)

**Access:** No credentials required. Public API, no registration.

**No email required.** If the feed goes down or returns errors, check the status page at [https://www.aviationweather.gov/](https://www.aviationweather.gov/).

---

### NWS api.weather.gov (National Weather Service REST API)

**Last verified:** 2025-12

**What it provides:** Active weather alerts (Severe, Extreme, Moderate) filtered by US state/territory. REST API returning GeoJSON.

**API endpoint used:** `https://api.weather.gov/alerts/active?area={STATES}&status=actual&severity=...`

**API documentation:** [https://www.weather.gov/documentation/services-web-api](https://www.weather.gov/documentation/services-web-api)

**Access:** No credentials required. Public API. No registration. NWS asks that requests include a `User-Agent` header identifying the application — this is configured in the fetcher.

**US only.** For international weather alert equivalents: EUROCONTROL NM B2B (below) has a fetcher but no credentials; the JMA/Météo-France/DWD/KMA/BoM/CMA equivalents are researched only — see [Researched / not yet integrated](#researched--not-yet-integrated--no-fetcher-parser-or-credential-wiring-exists). Nothing outside the US is currently delivering weather alerts into this platform.

---

### NWS NWWS-OI (NOAA Weather Wire Service — Open Interface)

**Last verified:** 2025-12

**What it provides:** Push feed of all NWS text products (severe weather warnings, SPS statements, LSRs, AFDs) from all WFOs nationwide. XMPP/XMPP-based multi-user chat. Filtered by `NWWS_WFO_FILTER` in `dispatch.env`.

**Signup page:** [https://www.weather.gov/nwws/](https://www.weather.gov/nwws/)

**Policy documentation:** [https://www.weather.gov/nwws/NWWS-OI_FAQ](https://www.weather.gov/nwws/NWWS-OI_FAQ)

**Access process:** Register for an account at the NWS NWWS-OI registration page. NWS reviews the application; approval typically takes a few business days. There is no fee.

**Email template:**
```
To: nwws@noaa.gov
Subject: NWWS-OI Account Request — [Your Name / Organization]

NWWS Team,

I am requesting access to the NWWS-OI (Open Interface) XMPP feed for use in
an operational weather alerting system.

Name: [Your name]
Organization: [Your organization or "Individual operator"]
Use case: Real-time severe weather push alerts for ground transportation operations
          in [your region]. Data is used for personal situational awareness only
          and is not redistributed.
WFOs of interest: [e.g., LWX, AKQ, CTP — or "Multiple; full nationwide feed requested"]

I have reviewed the NWWS-OI terms of service and agree to the usage conditions.

Thank you,
[Your name]
[Contact information]
```

**Credentials location:**
```bash
NWWS_JID=
NWWS_PASSWORD=
```

> ⚠️ **Never quote values in `dispatch.env`/`dispatch-secrets.env`** —
> systemd's `EnvironmentFile=` passes quote characters through as literal
> bytes of the value. A shell-quoted `NWWS_PASSWORD="…"` silently broke this
> feed for 4+ days (constant reconnect loop, `SASL not-authorized`) until
> root-caused 2026-08-20. Write bare `KEY=value`.
>
> **2026-08-22 fixes, both deployed:** (1) the `NWWS_WFO_FILTER` comparison
> used bare 3-letter codes (`LWX`) against the raw 4-letter ICAO `cccc`
> attribute (`KLWX`), silently dropping **every** configured-WFO product for
> the life of the feature — fixed with K-stripping normalization, and genuine
> KLWX/KAKQ rows have been landing in `nws_alerts` since; (2) WPC products
> were looked up by AWIPS id against a `ttaaii`-keyed table — fixed to prefer
> `ttaaii`. See `src/ingest/README.md`.

---

### ATCSCC Ops Plan (Air Traffic Control System Command Center)

**Last verified:** 2025-12

**What it provides:** Daily NAS operations plan — planned flow control initiatives, GDP/GS advisories, system notes. Plain text file updated approximately hourly.

**API endpoint used:** Polled directly from the ATCSCC public server.

**Access:** No credentials required. Public feed.

---

### Amtrak (via amtraker.com)

**Last verified:** 2025-12

**What it provides:** Real-time Amtrak train positions, delay status, OOOI estimates. Unofficial API reverse-engineered from Amtrak's public systems.

**API documentation:** [https://api.amtraker.com/](https://api.amtraker.com/)

**Access:** No credentials required. Public, unofficial API. No SLA.

**Note:** This is not an official Amtrak API. Amtrak does not provide a public developer API as of this writing. If an official API becomes available, migrate to it — it will be more reliable. For rail data outside the US (UK National Rail, Deutsche Bahn, SNCF, etc.), see [REGIONALIZATION.md](REGIONALIZATION.md).

**Integration path (verified 2026-08-22; updated 2026-09-03):** the deployed
push-primary is now the **`amtrak-tracker` container**
(`src/amtrak_tracker/main.py`, quadlet `amtrak-tracker.container`, active —
polls api.amtraker.com, serves locally on :8898, heartbeats `push:amtrak`).
`src/ingest/amtrak.py` inside `ingest-core` is a *second* implementation of
the same capability (gated `AMTRAK_ENABLED`, same heartbeat key — the
failover contract keeps the two from double-writing, but two implementations
exist and which is authoritative is only discoverable from quadlet
enablement state). **Still no scheduled REST fallback** —
`poller/fetchers/amtrak.py` remains absent from `FETCH_SCHEDULE`
(re-verified 2026-09-03), so there is no automatic recovery if both push
writers stop. NEEDS OPERATOR DECISION (tracked in CLAUDE.md): wire the
fetcher in with a heartbeat gate, or correct the docstrings — and pick one
push-primary implementation as authoritative on paper.

---

## Integrated feed cadence & live state

Added 2026-08-19. Two authorities, and they are different files — this table
exists so neither has to be inferred from the other:

* **Poll interval** — `FETCH_SCHEDULE` in `src/poller/main.py` (an earlier
  revision of this doc called it `FETCHERS`, a symbol that exists nowhere —
  grep for `FETCH_SCHEDULE`). That list is the
  complete set of REST fetchers the poller schedules; a module sitting in
  `src/poller/fetchers/` and *not* in `FETCH_SCHEDULE` (e.g. `faa_registry.py`,
  `opensky_registry.py`, `airport_fids.py`, `ops_plan.py`) is
  driven from somewhere else — `WatchlistSweep`, an ingest container, or a
  timer. **Exception: `poller/fetchers/amtrak.py` is dead code** — no
  schedule entry, no importer anywhere (confirmed 2026-08-22). Train data has
  exactly one live path, the `ingest-core` poll loop (`src/ingest/amtrak.py`),
  and no automatic fallback if it stops.
* **Stale threshold** — the `stale_thresholds` dict in
  `src/web/main.py`'s `/api/v1/feeds` handler. Unlisted feeds default to
  3600 s. Convention is 2–3x the poll interval.

Poll intervals and thresholds re-verified against source 2026-08-23
(`FETCH_SCHEDULE`, `src/poller/main.py:33-59`; `stale_thresholds`,
`src/web/main.py:472-490`) — all eleven rows below matched exactly. The
"live state" column is a snapshot of `/api/v1/feeds` at 2026-08-23
~12:44 EDT and will drift.

| Feed | Poll interval | Stale threshold | Live state 2026-08-23 |
|---|---|---|---|
| `tfr` | 300 s | 900 s | OK — no push-primary, always REST-polls |
| `metar` | 300 s | 900 s | OK, pull path verified (HTTP 200) |
| `nas` | 300 s | 900 s | OK — no push-primary, always REST-polls |
| `nws` | 300 s | 2700 s | OK, `push_covered` by `push:nws` |
| `notam` | 300 s | 900 s | `error: null` because `push_covered` is true; `pull_error` still **`awaiting_credentials`** — see FAA NOTAM API above; data arrives via `push:fns` |
| `runsheet` | 300 s | 900 s | OK (a duplicate-insert bug wrote one row per cycle, ~288/day, from the fetcher's inception — fixed, deployed, inserts confirmed stopped 2026-08-23) |
| `dca_fids` | 300 s | 600 s | OK, pull path verified |
| `iad_fids` | 300 s | 600 s | OK, pull path verified |
| `atcscc_opsplan` | 3600 s | 7200 s | OK |
| `eurocontrol` | 900 s | 3600 s | **`awaiting_credentials`**, `pull_state: unconfigured` — see below |
| `jasdat` | 900 s | 3600 s | **`awaiting_credentials`**, `pull_state: unconfigured` — see below |

**EUROCONTROL and JASDAT are integrated but not live, and are further from
live than "just needs a key".** Both have real fetchers registered in
`FETCH_SCHEDULE`, both are credential-gated on env vars that real code reads, and
both stubs exist (empty) in `dispatch-secrets.env`. But `pull_path_verify`
reports `pull_state: "unconfigured"` for both, with a **DNS resolution
failure** underneath it, not an auth failure:

* `eurocontrol` — `NameResolutionError` for
  `www.b2b.opsnetwork.eurocontrol.int`
* `jasdat` — `NameResolutionError` for `www.jasdat.go.jp`

So these two host names do not currently resolve from this box at all. Do not
present either feed as live, and do not assume dropping credentials in will be
sufficient — the hostname reachability needs confirming as part of the same
exercise.

Push-side rows (`push:fdps`, `push:stdds`, `push:tfms`, `push:tbfm`,
`push:itws`, `push:fns`, `push:nws`, `push:amtrak`) are heartbeats stamped by
the ingest containers every 30 s, not poll intervals; their thresholds are
300 s except `push:tfms`/`push:tbfm`, which fall through to the 3600 s
default. See the SWIM liveness caveat above before reading a `suspended:` or
`disabled:` string on one of them as an outage.

---

## European Sources

### EUROCONTROL NM B2B (Network Manager Business-to-Business)

**Last verified:** 2025-12

**What it provides:** The European equivalent of FAA SWIM + ATCSCC combined. Flight plans, ATC flow management measures (CTOT, regulations, MCIs, GDP/GS equivalents), OPMET (METARs, TAFs, SIGMETs), NOTAMs, airspace status. Uses SOAP/XML web services.

**Fetcher:** `src/poller/fetchers/eurocontrol.py` — registered in `FETCH_SCHEDULE` (`src/poller/main.py`) on a 900 s interval, credential-gated on the three vars below, same skip/retry pattern as every other fetcher in this codebase. Marks the feed `awaiting_credentials` until they are set.

> **Live status 2026-08-19 — integrated, not live, and not one step away.**
> `/api/v1/feeds` reports `error: "awaiting_credentials"` with
> `pull_state: "unconfigured"`. All three variables below exist as **empty**
> stubs in `dispatch-secrets.env`. Underneath the credential gap,
> `pull_path_verify` records a **DNS `NameResolutionError` for
> `www.b2b.opsnetwork.eurocontrol.int`** — the host does not resolve from this
> box. The "activates automatically the moment they are [set]" line above is
> the fetcher's *intended* behaviour and is true of the credential gate
> specifically; it is not a promise that filling in the keys makes the feed
> work, because the endpoint is currently unreachable regardless. Do not
> present this feed as live.

**Portal:** [https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services](https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services)

**Access request form:** [https://www.eurocontrol.int/contact/nm-b2b-access-request](https://www.eurocontrol.int/contact/nm-b2b-access-request)

**Technical specification:** [https://www.eurocontrol.int/service/nm-b2b-web-services-user-specification](https://www.eurocontrol.int/service/nm-b2b-web-services-user-specification)

**Policy documentation:** [https://www.eurocontrol.int/service/network-manager-ops](https://www.eurocontrol.int/service/network-manager-ops)

**Access process:** Submit the online access request form. EUROCONTROL reviews organizational eligibility — ANSPs, licensed aviation operators, and aviation research institutions qualify. Certificate-based authentication is used for production access.

**Email template (follow-up or direct inquiry):**
```
To: nmb2bsupport@eurocontrol.int
Subject: NM B2B Access Request — [Your Organization Name]

EUROCONTROL NM B2B Team,

I am writing to request access to the EUROCONTROL Network Manager B2B API
for operational situational awareness purposes.

Organization: [Your organization and country]
Use case: Real-time operational monitoring of European airspace for
          [describe your operation — e.g., executive ground transportation].
Data requested: OPMET (METARs/TAFs/SIGMETs), ATFM flow measures, NOTAMs.
Deployment: Self-hosted, on-premises. Data is not redistributed.
Technical contact: [Your name, email, phone]

I have submitted a request via the online form at eurocontrol.int on [date].
Please advise on the review process and estimated timeline.

Thank you,
[Your name]
[Organization]
[Contact details]
```

**Credentials location in dispatch-secrets.env:**
```bash
EUROCONTROL_NM_B2B_USER=
EUROCONTROL_NM_B2B_PASS=
EUROCONTROL_NM_B2B_CERT_PATH=
```

---

## Asia-Pacific Sources

### JASDAT (Japan AIS Data Tool)

**Last verified:** 2025-12

**What it provides:** The Japanese equivalent of FAA AIM SWIM. NOTAMs, AIS data, SIGMET/AIRMET, airspace information for Japanese airspace. Operated by JCAB (Japan Civil Aviation Bureau), Ministry of Land, Infrastructure, Transport and Tourism.

**Fetcher:** `src/poller/fetchers/jasdat.py` — registered in `FETCH_SCHEDULE` (`src/poller/main.py`) on a 900 s interval, credential-gated on the two vars below, same skip/retry pattern as every other fetcher in this codebase. Marks the feed `awaiting_credentials` until they are set.

> **Live status 2026-08-19 — integrated, not live.** `/api/v1/feeds` reports
> `error: "awaiting_credentials"` with `pull_state: "unconfigured"`.
> `JASDAT_USER` / `JASDAT_PASS` exist as **empty** stubs in
> `dispatch-secrets.env`, and `pull_path_verify` records a DNS
> `NameResolutionError` for `www.jasdat.go.jp` — the host does not resolve
> from this box. Same caveat as EUROCONTROL above: credentials alone will not
> bring this up. Do not present this feed as live.

**Portal:** [https://www.jasdat.go.jp/en/](https://www.jasdat.go.jp/en/)

**Access process:** Requires organizational registration with JCAB. Access is available to licensed aviation operators, ANSPs, and approved aviation service organizations operating in Japanese airspace.

**Email template:**
```
To: jasdat@mlit.go.jp
Subject: JASDAT API Access Request — [Your Organization]

JASDAT Team,

I am writing to request access to the JASDAT aeronautical information system
for operational use.

Organization: [Your organization name and country]
Operation type: [e.g., executive ground transportation operations; CERT/emergency management]
Japanese operations: [Describe your connection to Japanese airspace or operations]
Data requested: NOTAMs, SIGMET/AIRMET, airspace status for [airport list].
Technical contact: [Your name, email, phone]

Please advise on the eligibility requirements and application process for
international operators.

Thank you,
[Your name]
[Organization]
[Contact details]
```

**Credentials location:**
```bash
JASDAT_USER=
JASDAT_PASS=
```

---

## AIS/Radar Aggregators (Aviation flight tracking)

### airplanes.live

**Last verified:** 2025-12 · **No longer queried programmatically as of
2026-08-27.**

**What it provides:** Crowdsourced ADS-B flight tracking worldwide. No registration required.

> **Status (2026-08-27 operator directive, "everything is meant to be
> local"):** every programmatic airplanes.live call was removed — the
> watchlist flight chain, `/api/v1/adsb`, and the runner's `/api/adsb/live`
> all resolve from the box's own receiver, ingested FDPS, and local registry
> tables instead. What remains is click-through/embed use only:
> `globe.airplanes.live` URLs in push bodies and the runner's globe-mode
> iframe (allowed as phone conveniences), and the box still *feeds* the
> aggregator via ultrafeeder. Kept here as source research, not as a live
> integration.

**API documentation:** [https://airplanes.live/api-guide/](https://airplanes.live/api-guide/)

**Access:** No credentials required for standard queries.

---

### FlightAware AeroAPI

**Last verified:** 2025-12

**What it provides:** Premium flight tracking with historical data, filing status, and OOOI timestamps.

> **Status (2026-09-03):** dormant remnant, not the top tier. The 2026-08-27
> local-only directive made local sources primary everywhere; the AeroAPI
> code survives (`poller/main.py::_check_flight_aeroapi()` and
> `common/flight_resolver.py` tier 3) but is inert without a key — no key is
> configured on this box. Whether it survived the local-only rewire as an
> intentional exception or an oversight is not stated in the code.

**Portal:** [https://flightaware.com/aeroapi/](https://flightaware.com/aeroapi/)

**Pricing:** Tiered; personal/hobbyist tier available at low cost. Commercial use requires a higher tier.

**Credentials location** (note the two consumers read **different** var
names — `common/flight_resolver.py` reads `FLIGHTAWARE_API_KEY`, while
`poller/main.py`'s watchlist check reads `FLIGHTAWARE_AEROAPI_KEY`; set both
if ever enabling this):
```bash
FLIGHTAWARE_API_KEY=
FLIGHTAWARE_AEROAPI_KEY=
```

---

### ACARS / acarsdrama Jumpseat

**Last verified:** 2025-12 (integration status re-verified 2026-08-19)

> **Integration status: genuinely integrated and credentialed.** Recorded
> explicitly because a 2026-08-19 audit pass provisionally grouped this entry
> with the researched-only international sources; re-verification against
> source shows that is wrong, and it stays here. `ACARSDRAMA_JUMPSEAT_TOKEN`
> is read by `src/acars_watcher/acars_watcher.py` (~line 88, where a comment
> names it the canonical variable) and by `src/runner/main.py` (~line 62),
> which uses it in `_acarsdrama_messages()` as the primary external fallback
> for VDL2/ACARS/HFDL behind the local AIS/ACARS hardware. The token is
> **populated** in `dispatch-secrets.env` (not an empty stub). The runner UI
> surfaces it as the `JUMPSEAT` source label
> (`src/runner/frontend/src/components/SignalsView.jsx`). Unlike the REST
> feeds above it is consumed by the runner and the ACARS watcher rather than
> by a `poller/fetchers/` module, so it has no `/api/v1/feeds` row — absence
> from that endpoint is not evidence it is unwired.

**What it provides:** ACARS message feed from crowdsourced ground stations. Used for supplemental flight status and out-of-band flight data.

**Data caveat (2026-08-23):** despite being wired and credentialed, the
*local* ACARS ingest path has never produced a row — `acars_messages` has 0
rows ever, behind a green `acars.heartbeat` that only proves the reader
thread is alive. Instrumentation (`lines_received`/`parse_failures` +
connected-but-idle warnings) was added 2026-08-22; root cause still unknown.
Treat the local ACARS path as unproven end-to-end.

**Portal:** [https://acarsdrama.com/](https://acarsdrama.com/)

**Access:** Registration at acarsdrama.com; Jumpseat API token available to contributors.

**Credentials location:**
```bash
ACARSDRAMA_JUMPSEAT_TOKEN=
```

---

## Global Aircraft Registry Sources

Aircraft *ownership/registration* data (N-number, registrant, hex mapping) is a different category from *live position tracking* (the AIS/Radar Aggregators section above) -- every country maintains its own civil aircraft register, and there's no single global authority. This section covers cross-national aggregators and the pattern for adding a country-specific registry.

### OpenSky Network Aircraft Database

**Last verified:** 2026-07

**What it provides:** A crowdsourced aggregation of national aircraft registry metadata (icao24/hex, registration, manufacturer, model, typecode, serial number, operator) spanning 127 countries. Useful as a best-effort fallback for hex/registration lookups outside the US and UK, where a dedicated national registry integration doesn't exist yet.

**Access:** [https://opensky-network.org/datasets/metadata/](https://opensky-network.org/datasets/metadata/) -- free CSV download, no credentials, no registration.

**Reliability note:** Updates are irregular and were on hold as of the last check (2026-07) -- treat this as a supplementary/best-effort source, not authoritative. Cross-check against the FAA registry or UK CAA G-INFO where those apply; only fall back to OpenSky for aircraft outside US/UK coverage.

**No email required.**

**Credentials location:** None needed.

### Adding a new national registry

Follow the FAA/UK CAA pattern above: what it provides, API type (bulk file vs. purchased product vs. live API), access process, email template if a human request is needed, and credentials location (or "none" if it's a public download or manual product). Add the entry under the appropriate regional section (US/European/Asia-Pacific Sources) rather than here -- this section is for cross-national aggregators only (currently just OpenSky).

---

### Kpler Maritime 2.0 (vessel/AIS tracking)

**Last verified:** 2026-07

**What it provides:** Real-time vessel positions and characteristics for the AIS map view's tier-2 fallback (`runner/main.py`, `/api/ais/vessels`). Fallback chain: local AIS-catcher hardware (tier 1) -> Kpler Maritime 2.0 GraphQL (tier 2) -> AISHub free cooperative (tier 3).

**Migration note:** MarineTraffic's classic REST Vessels API (`services.marinetraffic.com/api/getVessels/v:8/...`) is discontinued platform-wide -- Kpler acquired Spire Maritime (which had already absorbed MarineTraffic) and moved all users to the GraphQL API below as of 2025. The old endpoint 404s unconditionally now regardless of key validity; this is not a per-account permission issue. See `runner/main.py::_fetch_kpler_vessels` for the current implementation.

**Portal:** [https://developers.kpler.com/](https://developers.kpler.com/)

**API base URL:** `https://api.sml.kpler.com/graphql` (Bearer token auth, GraphQL POST)

**Access process:** Vessel-data API access is a sales-gated enterprise product (`Contact Kpler` / `Request a trial`), not a self-serve developer signup. A MarineTraffic embed/widget key (`AIS_MARINETRAFFIC_KEY`) is a separate, unrelated product from a MarineTraffic donated-receiver "station operator" account, which typically comes with a properly-scoped Maritime 2.0 API token.

**Credentials location in dispatch-secrets.env:**
```bash
KPLER_MARITIME_API_TOKEN=
```

**Separate, still-functioning credential (do not confuse with the above):**
```bash
AIS_MARINETRAFFIC_KEY=   # MarineTraffic embed widget ID, not a data API token
```

---

## Researched / not yet integrated — no fetcher, parser, or credential wiring exists

Everything in this section is **access research**, not running software. For
each entry below there is:

* **no fetcher module** in `src/poller/fetchers/` (and no entry in
  `FETCH_SCHEDULE` in `src/poller/main.py`),
* **no parser** anywhere in `src/`,
* **no credential wiring** — where an env var name is shown, that name is
  **proposed, not implemented**. Verified 2026-08-19 by repo-wide grep: these
  names occur only in `dispatch-secrets.env.template` and as empty stubs in
  the live `/etc/corporatetraveldc/dispatch-secrets.env`, and are read by no
  Python code. Populating one of them changes nothing.

None of these appear in `/api/v1/feeds`, because there is nothing to report a
state for.

The research is kept in full because it is genuinely useful — portal URLs,
pricing, eligibility notes, and drafted request emails are the expensive part
of onboarding a source, and re-deriving them later would be wasteful. It is
segregated here so that it stops reading as a description of live coverage.
When one of these is actually integrated, move its entry back into the
appropriate regional section in the same change that lands the fetcher.

**Europe**

### UK CAA G-INFO (UK Register of Civil Aircraft)

> **Integration status: researched only.** No fetcher, no parser, no ingestion path. This is a paid Excel product delivered by email; nothing in `src/` reads, parses, or stores it. The entry's own "Credentials location" already says "None" — correctly, but that reads as "no credential needed" rather than "nothing is wired". Nothing is wired.

**Last verified:** 2026-07

**What it provides:** The UK equivalent of the FAA N-number registry -- registration marks, registered owner details, aircraft type/manufacturer/serial number, engine details, airworthiness certificate info, and technical details for every UK-registered aircraft (18,000-21,000+ aircraft; ~35% of records change annually).

**API type:** None -- this is a purchased data product, not an API. Delivered as an MS Excel file by email.

**Portal:** [https://www.caa.co.uk/aircraft-register/g-info/](https://www.caa.co.uk/aircraft-register/g-info/)

**Order form:** [SRG1860](https://www.caa.co.uk/SRG1860) (PDF)

**Pricing (as of 2026-07):**

| Product | Price (inc. VAT) |
|---|---|
| Single issue, MS Excel file | GBP 450.00 |
| Quarterly subscription (4 issues/yr) with email updates | GBP 745.00 |
| Monthly subscription (12 issues/yr) with email updates | GBP 1,745.00 |
| Corporate licence, unlimited users | GBP 1,855.00 |

**Access process:** Complete order form SRG1860 and submit with payment. No organizational eligibility review -- this is a straightforward commercial purchase. The database is licensed for single-PC use unless a corporate licence is purchased.

**Email template (order / inquiry):**
```
To: aircraft.reg@caa.co.uk
Subject: G-INFO Database Order Inquiry -- [Your Organization Name]

Aircraft Registration Section,

I would like to order the G-INFO database (UK Register of Civil Aircraft)
and have a question before submitting form SRG1860.

Organization: [Your organization name]
Product of interest: [e.g., monthly subscription with email updates]
Use case: [describe -- e.g., cross-referencing UK-registered aircraft
          against ADS-B tracking data for operational awareness]

Could you confirm the current turnaround time for [monthly/quarterly]
subscription delivery, and whether the Excel format is suitable for
programmatic parsing (column layout, date of most recent format change)?

Thank you,
[Your name]
[Organization]
[Contact information]
```

**Contact:** Aircraft Registration Section, aircraft.reg@caa.co.uk, +44 (0)330 022 1917 (weekdays 09:00-16:30 UK time).

**Credentials location:** None -- this is a manually-delivered email product, not an API. If automated periodic ingestion is added later (parsing the emailed Excel file), it would follow a similar push-primary pattern to how Amtrak/NWWS-OI are wired, not a `dispatch-secrets.env` credential -- no env var needed for the current manual-purchase workflow.

---
### Météo-France Open Data API

> **Integration status: researched only.** No fetcher, no parser, no code path. `METEOFRANCE_API_KEY` appears **only** in `dispatch-secrets.env.template` (as an empty stub, mirrored empty into the live `dispatch-secrets.env`) — repo-wide grep finds it in no `.py` file. The variable name below is **proposed, not implemented**; setting it would have no effect.

**Last verified:** 2025-12

**What it provides:** French national weather products including METARs, TAFs, and severe weather warnings for metropolitan France and overseas territories.

**Developer portal:** [https://portail-api.meteofrance.fr/](https://portail-api.meteofrance.fr/)

**Access:** Free registration at the developer portal. API key issued immediately.

**Credentials location:**
```bash
METEOFRANCE_API_KEY=
```

---
### DWD Open Data (Deutscher Wetterdienst / German Weather Service)

> **Integration status: researched only.** No fetcher, no parser, no code path, and no env var — `opendata.dwd.de` appears nowhere in `src/` or `scripts/`. "No credentials required" is true of the upstream portal and says nothing about integration: there is none.

**Last verified:** 2025-12

**What it provides:** Aviation weather products for Germany including METARs, TAFs, SIGMETs. Fully open, no registration required.

**Open data portal:** [https://opendata.dwd.de/](https://opendata.dwd.de/)

**Aviation products:** `https://opendata.dwd.de/weather/aviation/`

**Access:** No credentials required.

---

**Asia-Pacific**

### JMA (Japan Meteorological Agency) Open Data API

> **Integration status: researched only.** No fetcher, no parser, no code path. `JMA_API_KEY` appears **only** in `dispatch-secrets.env.template` and as an empty stub in the live `dispatch-secrets.env`; no `.py` file reads it. **Proposed, not implemented.** Note that the entry's own advice is still sound and is the actual current behaviour: METARs for Japanese ICAO codes already arrive via AviationWeather.gov ADDS, which *is* integrated.

**Last verified:** 2025-12

**What it provides:** Weather observations, forecasts, SIGMETs, and aviation weather products for Japan. Supports METAR format for Japanese airports — these are also covered by AviationWeather.gov ADDS using standard ICAO codes.

**Open data portal:** [https://opendata.jma.go.jp/gpv/](https://opendata.jma.go.jp/gpv/)

**API documentation:** [https://www.data.jma.go.jp/developer/index.html](https://www.data.jma.go.jp/developer/index.html)

**Access:** No credentials required for public data products. Some advanced products require registration.

**Note:** For basic METAR data for Japanese airports, AviationWeather.gov ADDS works without any configuration change. JMA's own API is valuable for Japan-specific products (typhoon tracks, SIGMET products, detailed forecast data).

**Credentials location (for registered products):**
```bash
JMA_API_KEY=
```

---
### KMA (Korea Meteorological Administration) Open API Hub

> **Integration status: researched only.** No fetcher, no parser, no code path. `KMA_API_KEY` exists only as a template/empty stub. **Proposed, not implemented.**

**Last verified:** 2025-12

**What it provides:** Korean national weather data, including aviation weather products.

**API hub:** [https://apihub.kma.go.kr/](https://apihub.kma.go.kr/)

**English information:** [https://www.kma.go.kr/en/](https://www.kma.go.kr/en/)

**Access:** Free registration at the API hub. API key issued after registration.

**Credentials location:**
```bash
KMA_API_KEY=
```

---
### Airservices Australia NAIPS

> **Integration status: researched only.** No fetcher, no parser, no code path. `NAIPS_USER` / `NAIPS_PASS` exist only in `dispatch-secrets.env.template` and as empty stubs in the live secrets file; no `.py` file reads either. **Proposed, not implemented.**

**Last verified:** 2025-12

**What it provides:** Australian NOTAM database, PIREPs, meteorological reports, and aeronautical information via the NAIPS (National Aeronautical Information Processing System).

**Portal:** [https://www.airservicesaustralia.com/](https://www.airservicesaustralia.com/)

**NAIPS information:** [https://www.airservicesaustralia.com/industry-information/aeronautical-information/naips/](https://www.airservicesaustralia.com/industry-information/aeronautical-information/naips/)

**Email template:**
```
To: Customer Enquiries (via contact form at airservicesaustralia.com)
Subject: NAIPS API Access Request — [Your Organization]

Airservices Australia,

I am writing to request access to NAIPS data feeds for operational
situational awareness purposes.

Organization: [Your organization and country]
Use case: Real-time monitoring of Australian airspace for [describe operation].
Data requested: NOTAMs, PIREPs, MET reports.
Technical contact: [Your name, email, phone]

Please advise on the access process and any applicable fees.

Thank you,
[Your name]
```

**Credentials location:**
```bash
NAIPS_USER=
NAIPS_PASS=
```

---
### Bureau of Meteorology (BoM) — Australia

> **Integration status: researched only.** No fetcher, no parser, no code path, no env var. As with JMA, Australian ICAO METARs already arrive via the integrated AviationWeather.gov ADDS feed.

**Last verified:** 2025-12

**What it provides:** Australian weather observations, forecasts, and aviation weather products including METARs and SIGMETs.

**Open data portal:** [https://open-data.bom.gov.au/](https://open-data.bom.gov.au/)

**Aviation weather:** [http://www.bom.gov.au/aviation/](http://www.bom.gov.au/aviation/)

**Access:** Much of BoM's data is open with no credentials. Some products require registration.

**Note:** For METAR data at Australian airports, AviationWeather.gov ADDS covers Australian ICAO codes without any configuration change.

---
### CMA (China Meteorological Administration)

> **Integration status: researched only.** No fetcher, no parser, no code path. `CMA_API_KEY` exists only as a template/empty stub. **Proposed, not implemented.** Access is additionally restricted for international operators (see below), so this is the least actionable entry in this section.

**Last verified:** 2025-12

**What it provides:** Chinese national meteorological data. International access to real-time data is limited.

**Data portal:** [https://data.cma.cn/](https://data.cma.cn/)

**Access:** Registration required. Access for international operators to real-time aviation weather data is restricted and typically requires engagement through CAAC or approved aviation data vendors.

**Credentials location:**
```bash
CMA_API_KEY=
```

---
## Adding a new data source

When integrating a new feed:

1. Add the credential stub(s) to `dispatch-secrets.env.template` with a comment block that includes the signup URL and a brief description.

2. Add an entry to this file (`docs/DATA_SOURCES.md`) following the same template:
   - Last-verified date
   - What it provides
   - Portal/documentation URLs
   - Access process
   - Email template (if required)
   - `dispatch-secrets.env` key name(s)

3. Update [REGIONALIZATION.md](REGIONALIZATION.md) if the source is region-specific or has regional equivalents.

4. Commit all three files in the same commit as the code integration.

The goal is that any operator who deploys this system can open `docs/DATA_SOURCES.md` and find exactly what they need to sign up for every feed — without hunting through code for credentials or searching documentation externally.

## Thermal ingest guard (2026-07-26; load model REDESIGNED 2026-08-23)

**Rewritten 2026-08-23** against the live
`scripts/thermal-ingest-guard.py`. Two earlier framings of this section are
now both wrong and are called out so a reader coming from an old copy
doesn't carry them forward: it is **not** temperature-only (the pre-08-19
framing), and it is **no longer** a symmetric two-tier temp-OR-load
escalation (the 08-19 framing — tier 1 at load 10, tier 2 at load 14,
resume at 6.0). Load and temperature are now deliberately asymmetric.

`scripts/thermal-ingest-guard.py` -- automatic fallback that sheds ingest
(and, under LOCKDOWN, effectively the whole stack) when the box runs hot or
when the CPU run queue gets extreme; it restores once the signals hold clear
for a dwell period. **2026-08-27 updates (verified against the script
2026-09-03):** the guard no longer touches any LLM service — `ollama.service`
and `ollama_governor.py` were retired at the llama.cpp cutover, and the
`corporatetraveldc-llama-*` units are deliberately excluded from LOCKDOWN
scope; and the LLM-contention-fallback trigger was demoted to
informational-only (still computed and logged, no longer trips or blocks
anything). Runs
every 2 minutes via `corporatetraveldc-thermal-ingest-guard.timer`
(systemd --user). Fires an ntfy alert (`ops-health` topic) on every
trip/restore/restore-failure.

### Real thresholds (read from `scripts/thermal-ingest-guard.py`, 2026-08-23)

| Trip | Condition | What is shed |
|---|---|---|
| Temp tier 1 (mild) | `temp >= 74.0C` | `tfms`, `stdds` only (`THERMAL_GUARD_TIER1_FEEDS`) |
| **LOCKDOWN** | `temp >= 79.0C` **OR** `load1 >= 40.0` (the third, fallback-count trigger was demoted to informational-only 2026-08-27) | **the entire stack except `web`**, immediately, with no intermediate stage |
| Informational only | `temp` 70–74C, or `load1` 15–40, or any LLM-contention fallback count | nothing — logged to the journal, no ntfy push, no action |
| Restore | `temp < 65.0C` **AND** `load1 < 15.0`, held for `THERMAL_GUARD_RESUME_DWELL_S` (300 s) — fallback count no longer blocks resume | tier 1 restores `tfms,stdds`; LOCKDOWN restores the whole stack |

**Load no longer has a mild stage at all.** 15–40 covers this box's entire
normal-to-busy operating range and is logged only; the sole load-driven
shed is the full LOCKDOWN at `>= 40`, roughly 10x a healthy 4-core
baseline. Temperature keeps its original two-stage shape because the
empirical margin justifies keeping a real backstop — but tier 2 (79C) now
triggers the same full-stack LOCKDOWN rather than a five-feed shed.

**What LOCKDOWN actually stops** (`_lockdown_stop_stack()`, fixed in code —
deliberately *not* tunable via `dispatch.env`): all six real SWIM feeds
(`ALL_SWIM_FEEDS = fdps, stdds, tfms, tbfm, itws, notam` — `notam` runs the
AIM/FNS feed and is a real 6th SWIM feed, not a NOTAM-only afterthought),
`ingest-core` (NWWS-OI/Amtrak/local airspace), and
`LOCKDOWN_USER_UNITS = poller, pusher, runner`. Since 2026-08-27 it does
**not** touch any LLM service: `ollama.service` is gone with the llama.cpp
cutover, and the `corporatetraveldc-llama-hot/chat/report-*` units are
deliberately excluded so the hot alert path survives the event. `web` is
deliberately absent from every list, so `/healthz` and the API stay
observable through the event. The guard's own `TimeoutStartSec` was raised
to 600 s to survive a full sequential stop/start.

**The LLM-contention signal** (formerly the third trigger, **demoted to
informational-only 2026-08-27** — one night produced ~15 fallback-attributed
LOCKDOWN trips with real load1 of only 4–9; shedding SWIM ingest does nothing
to relieve LLM contention, and the fallback-count restore gate could hold the
stack down indefinitely): `src/common/llm.py::_record_load_fallback()`
appends to
`/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl` only for
`OllamaBusyError` (slot busy — name kept from the Ollama era) or an
`httpx.TimeoutException` from a real generate call — never for a plain
`ConnectError`, so a deliberately-stopped LLM server can never look like
contention.
`count_recent_load_fallbacks()` still reads and prunes that log each cycle
(1 h retention) and logs the count for visibility; it no longer triggers
LOCKDOWN and no longer blocks resume.

### Tunability

`dispatch.env` (lines 278-284) sets exactly seven variables — unchanged by
the redesign, and verified live:

```
THERMAL_GUARD_ENABLED=true
THERMAL_GUARD_TIER1_TEMP_C=74.0
THERMAL_GUARD_TIER2_TEMP_C=79.0
THERMAL_GUARD_RESUME_TEMP_C=65.0
THERMAL_GUARD_RESUME_DWELL_S=300
THERMAL_GUARD_TIER1_FEEDS=tfms,stdds
THERMAL_GUARD_TIER2_FEEDS=fdps,tbfm,itws
```

Six further knobs the script reads are **script-only defaults present in no
env file**: `THERMAL_GUARD_TEMP_INFO_C` (70.0),
`THERMAL_GUARD_LOAD_INFO_MIN` (15.0), `THERMAL_GUARD_LOAD_LOCKDOWN` (40.0),
`THERMAL_GUARD_RESUME_LOAD` (15.0),
`THERMAL_GUARD_FALLBACK_TRIGGER_COUNT` (2) and
`THERMAL_GUARD_FALLBACK_WINDOW_S` (300). `_cfg()` reads `dispatch.env`
first and falls back to these, so they are tunable in principle — but an
operator working from `dispatch.env` will not find them. Note also that
`THERMAL_GUARD_TIER2_FEEDS` is now effectively vestigial: LOCKDOWN sheds
the fixed `ALL_SWIM_FEEDS` list plus the stack, not that variable.

### Observed reality (2026-08-23, since the redesign went live)

> **Historical — read with the 2026-08-27 demotion in mind.** The two
> LOCKDOWNs below were both fired by the contention-fallback trigger, which
> no longer trips LOCKDOWN at all; the "roughly every two hours on a busy
> afternoon" recurrence prediction died with that trigger. Kept as the
> record that motivated the demotion.

* **Two real LOCKDOWNs fired on 2026-08-23, both triggered by the new
  Ollama-contention signal, neither by load or heat** — 12:18:22 → 12:29:35
  EDT and 14:34:42 → 14:45:51 EDT, both clean restores. The second one's
  reading at trip time was `temp=68.30C load1=11.94`, i.e. ~11 °C under
  even the tier-1 line and about a quarter of the `load1` LOCKDOWN bar —
  further confirmation that on this box the contention signal, not
  temperature or load, is what actually fires this mechanism. Plan for
  these as recurring (roughly every two hours on a busy afternoon), not as
  a one-off. Detail on the first:
  `tripped LOCKDOWN (2 load-attributed brief fallbacks/300s) fan=2313rpm`.
  It restored cleanly 11 minutes later:
  `restored (the whole stack (all 6 SWIM feeds, ingest-core, poller,
  pusher, runner, ollama.service)) at 56.75C load=3.73`. So the full-stack
  scope has now been exercised against real conditions, and the restore
  path verified each unit before declaring success.
* Earlier the same day, under the *old* thresholds, three tier-2 sheds
  fired at load 16.30 / 14.10 / 14.17 — exactly the pattern the redesign
  targeted. Under the new `>= 40` bar none of those would shed anything;
  the journal now logs them as
  `INFO: load1 … in watch band [15-40) -- normal-to-busy range, no action`
  (seen repeatedly at 10:39, 11:07, 11:11 and 12:45).
* Temperature has still never tripped this guard. Peak recorded across the
  guard's journal history is ~71C, under the 74C tier-1 line, with an
  independent auto-ramping PWM fan regulating underneath it.
* This is a snapshot; the guard's state flips on a 2-minute cadence. Read
  `/var/lib/corporatetraveldc/thermal_ingest_guard_state.json`, not this
  bullet list.

**Restore verification (2026-08-21):** the restore path is no longer
fire-and-forget. After `feed_ctl("restart", …)` the guard waits 5 s and
verifies each restored feed via `_feed_is_active()`; any feed that did not
come back triggers a priority-5 **RESTORE FAILED** ntfy alert naming the
specific feed(s), and the guard leaves its tier state alone so the next
2-minute cycle retries automatically. (Added after a restore-restart failure
went unnoticed for ~27 h because the old path pushed "restored" and reset
`tier: 0` without ever checking the restart's outcome.)

**Operational consequence:** because a shed container exits 0, these outages
are **invisible to `systemctl --user list-units --failed` and to any
failed-unit grep**. A multi-hour, five-feed SWIM outage produces no failed
unit anywhere. Monitor the guard directly, not the ingest units' failure
state.

### Where to look

State: `/var/lib/corporatetraveldc/thermal_ingest_guard_state.json` — carries
`tier`, `shed_at`, `below_resume_since`, `peak_temp`, `peak_load1`,
`peak_fan_rpm`, and a `guard_label` that names which signal tripped it.
`tier` is `0` (clear), `1` (temp tier 1 — `tfms,stdds` shed) or `2`
(LOCKDOWN). At tier 0 the file collapses to just `{"tier": 0,
"below_resume_since": null}`, so the absence of the peak/label fields is
normal, not truncation. `guard_label` is built from whichever causes
actually tripped it — the cause words (`Thermal`, `Load`,
`Ollama-contention`) are `+`-joined and the literal ` Guard` suffix is
appended **once**, so the real values are `"Thermal Guard"`,
`"Load Guard"`, `"Ollama-contention Guard"`, `"Thermal+Load Guard"`,
`"Load+Ollama-contention Guard"`, etc. — never `"Thermal Guard+Load
Guard"` (`thermal-ingest-guard.py:574-576`). (A temp tier-1
shed is always labelled `"Thermal Guard"`, since load no longer
participates at that stage). That label is the fastest way to confirm
which signal fired.

History and trip reasons:
`journalctl --user -u corporatetraveldc-thermal-ingest-guard.service` — every
run logs a `temp=..C load1=.. tier=N fan=..rpm` line, informational-band
readings log a `INFO: … no action` line, and trips log an explicit reason,
e.g. `tripped LOCKDOWN (2 load-attributed brief fallbacks/300s)`.
