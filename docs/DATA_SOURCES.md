# CTDI Data Sources & Access Guide

**Snapshot verified 2026-08-11** (credential variable names checked against
current source; per-section "last verified" dates track the external portals).

This document covers every integrated data source, how to request access, and email templates for sources that require it. It also serves as the canonical reference for wiring in new sources — any new source that requires API signup or an email request should have an entry added here when it's integrated.

> **Maintenance:** When a portal URL, email address, or signup process changes, update this file in the same commit that updates the code. The last-verified date in each section header tracks when the information was confirmed current.

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

**Parser status confirmed live, all 7 feeds (2026-08-02):** FDPS, STDDS, TFMS, TBFM, ITWS, and AIM/NOTAM parsers are all fully deployed and writing real data -- confirmed today via direct SQL against the notams table (4,836 rows, 267 distinct facilities, `last_seen_at` from minutes prior) and via `corporatetraveldc-ingest-notam`'s own logs showing continuous `aim: wrote N NOTAM(s)` activity. None of the 7 feeds are in a "partial" state as of tonight.

**Known display bug, not yet root-caused (2026-08-02):** `/api/v1/feeds`'s `push:fns` entry (the AIM/NOTAM push heartbeat -- see `_FEED_HANDLERS` in swim_client.py, `"fns": (cfg.aim, _handle_aim_message)`) shows a stale `error: "disabled: SWIM_NMS_SKIP_FEEDS"` even though `fns` is confirmed NOT present in the live `SWIM_NMS_SKIP_FEEDS` env var (`fdps,stdds,tfms,tbfm,itws` on `corporatetraveldc-ingest-notam` tonight -- no `fns`/`notam` entry) and the feed is actively healthy per the paragraph above. Likely a status string written once (at startup or by a different ingest container sharing the same `push:fns` feed_states key) that never gets cleared once real messages start flowing, rather than an actual skip. Data is unaffected -- this is a status-display accuracy issue only. Separately fixed tonight: `/api/v1/feeds` now nulls `error` whenever `push_covered` is true for any feed (pull-side detail preserved in a new `pull_error` field instead of being shown as if it were the feed's current state) -- see `web/main.py`'s `/api/v1/feeds` handler. That fix is correct and verified; the `push:fns` stale string itself is a separate, still-open loose end.

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
Only covers US-registered (N-number) aircraft — foreign-registered aircraft (UK G-, Canada C-, etc.) will 404 here regardless of import freshness. See UK CAA G-INFO under European Sources and [Global Aircraft Registry Sources](#global-aircraft-registry-sources) below for non-US registries.

**Credentials location:** None needed.

---

### FAA LADD (Limiting Aircraft Data Displayed)

**Last verified:** 2026-07

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

**US only.** For international weather alert equivalents, see the EUROCONTROL and JMA sections below.

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

---

## European Sources

### EUROCONTROL NM B2B (Network Manager Business-to-Business)

**Last verified:** 2025-12

**What it provides:** The European equivalent of FAA SWIM + ATCSCC combined. Flight plans, ATC flow management measures (CTOT, regulations, MCIs, GDP/GS equivalents), OPMET (METARs, TAFs, SIGMETs), NOTAMs, airspace status. Uses SOAP/XML web services.

**Fetcher:** `src/poller/fetchers/eurocontrol.py` — credential-gated on the three vars below, same skip/retry pattern as every other fetcher in this codebase. Marks the feed `awaiting_credentials` until they are set; activates automatically the moment they are.

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

### UK CAA G-INFO (UK Register of Civil Aircraft)

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

**Last verified:** 2025-12

**What it provides:** Aviation weather products for Germany including METARs, TAFs, SIGMETs. Fully open, no registration required.

**Open data portal:** [https://opendata.dwd.de/](https://opendata.dwd.de/)

**Aviation products:** `https://opendata.dwd.de/weather/aviation/`

**Access:** No credentials required.

---

## Asia-Pacific Sources

### JMA (Japan Meteorological Agency) Open Data API

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

### JASDAT (Japan AIS Data Tool)

**Last verified:** 2025-12

**What it provides:** The Japanese equivalent of FAA AIM SWIM. NOTAMs, AIS data, SIGMET/AIRMET, airspace information for Japanese airspace. Operated by JCAB (Japan Civil Aviation Bureau), Ministry of Land, Infrastructure, Transport and Tourism.

**Fetcher:** `src/poller/fetchers/jasdat.py` — credential-gated on the two vars below, same skip/retry pattern as every other fetcher in this codebase. Marks the feed `awaiting_credentials` until they are set; activates automatically the moment they are.

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

### KMA (Korea Meteorological Administration) Open API Hub

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

**Last verified:** 2025-12

**What it provides:** Australian weather observations, forecasts, and aviation weather products including METARs and SIGMETs.

**Open data portal:** [https://open-data.bom.gov.au/](https://open-data.bom.gov.au/)

**Aviation weather:** [http://www.bom.gov.au/aviation/](http://www.bom.gov.au/aviation/)

**Access:** Much of BoM's data is open with no credentials. Some products require registration.

**Note:** For METAR data at Australian airports, AviationWeather.gov ADDS covers Australian ICAO codes without any configuration change.

---

### CMA (China Meteorological Administration)

**Last verified:** 2025-12

**What it provides:** Chinese national meteorological data. International access to real-time data is limited.

**Data portal:** [https://data.cma.cn/](https://data.cma.cn/)

**Access:** Registration required. Access for international operators to real-time aviation weather data is restricted and typically requires engagement through CAAC or approved aviation data vendors.

**Credentials location:**
```bash
CMA_API_KEY=
```

---

## AIS/Radar Aggregators (Aviation flight tracking)

### airplanes.live

**Last verified:** 2025-12

**What it provides:** Crowdsourced ADS-B flight tracking worldwide. No registration required. Used as primary FlightAware fallback for watchlist tracking.

**API documentation:** [https://airplanes.live/api-guide/](https://airplanes.live/api-guide/)

**Access:** No credentials required for standard queries.

---

### FlightAware AeroAPI

**Last verified:** 2025-12

**What it provides:** Premium flight tracking with historical data, filing status, and OOOI timestamps. Used as the top-tier watchlist data source when an API key is configured.

**Portal:** [https://flightaware.com/aeroapi/](https://flightaware.com/aeroapi/)

**Pricing:** Tiered; personal/hobbyist tier available at low cost. Commercial use requires a higher tier.

**Credentials location:**
```bash
FLIGHTAWARE_API_KEY=
```

---

### ACARS / acarsdrama Jumpseat

**Last verified:** 2025-12

**What it provides:** ACARS message feed from crowdsourced ground stations. Used for supplemental flight status and out-of-band flight data.

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

## Thermal ingest guard (2026-07-26)

`scripts/thermal-ingest-guard.py` -- automatic, tunable fallback that stops
the heaviest SWIM ingest containers when the box runs hot, and restores
them once temps hold cool for a dwell period. Independent of
`ollama_governor.py` (never touches Ollama's own thermal pause/resume).
Runs every 2 minutes via `corporatetraveldc-thermal-ingest-guard.timer`
(systemd --user, no root needed). All thresholds and feed groupings are
tunable in `dispatch.env` (`THERMAL_GUARD_*` -- see that file for the
full rationale and defaults). Two tiers: tier 1 sheds the two heaviest
feeds (tfms, stdds) at 74C; tier 2 adds the remaining three (fdps, tbfm,
itws) at 79C if tier 1 alone isn't enough. Fires an ntfy alert
(`ops-health` topic) on every shed and restore.

State: `/var/lib/corporatetraveldc/thermal_ingest_guard_state.json`.
Check current tier/temp: `cat` that file, or watch
`journalctl --user -u corporatetraveldc-thermal-ingest-guard.service`.
