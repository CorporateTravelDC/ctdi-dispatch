# CTDI Regionalization Guide

Verified against HEAD db64018 and live state on 2026-10-06 18:12Z / 14:12 ET.

How to deploy Corporate Travel Dispatch Intelligence outside Washington, DC.
The architecture is the same everywhere; geography is set in configuration
and in a set of code constants. The credentials themselves do not change
when you move regions.

---

## 1. Settings (no code change)

`/etc/corporatetraveldc/dispatch.env` (template `config/dispatch.env.example`).
Write bare `KEY=value` — `EnvironmentFile=` keeps quote characters.

| Variable | Purpose | This deployment |
|---|---|---|
| `NWWS_WFO_FILTER` | NWS forecast offices for NWWS-OI. Bare 3-letter codes (`LWX`); the leading `K` of the feed's `cccc` is stripped before comparison (fixed 2026-08-22). Empty = no filter. | set |
| `NOTAM_HOME_ARTCCS` | Home ARTCC(s): 14 CFR 91.137 / 91.145 TFRs alert at p5 here. **No code default.** | `ZDC` |
| `NOTAM_MONITOR_ARTCCS` | Monitor ARTCCs: 91.137 / 91.145 at p4 (elsewhere p3). No default. | `ZNY,ZID,ZOB,ZLA,ZTL` |
| `NOTAM_FACILITY_FILTER` | Extra NOTAM facilities beyond the permanent set | — |
| `AMTRAK_PRIMARY_STATION`, `AMTRAK_REGIONAL_STATIONS`, `AMTRAK_WATCHLIST_STATIONS`, `AMTRAK_CORE_ROUTES` | Rail hub and routes | `AMTRAK_PRIMARY_STATION=WAS` in the template |
| `ULTRAFEEDER_LAT` / `ULTRAFEEDER_LON`, `ULTRAFEEDER_SCAN_RADIUS_NM` | Receiver location (see `GPS_COORDINATE_CONFIGURATION.md`) | in the secrets file |

The `amtrak-tracker` container's station is set in its quadlet
(`Environment=FILTER_STATION=WAS`).

## 2. Code constants that are DC-specific

These are constants, not settings; a non-DC deployment edits them (and
re-signs the manifest):

| File | Constant | DC value |
|---|---|---|
| `src/poller/skills/ops_brief.py` | `HUB_AIRPORTS`, `NWS_ALERTS_URL` | KDCA,KIAD,KBWI + 12 hubs; `area=VA,MD,DC,NY,NJ,CT,MA,PA,DE,RI` |
| `src/poller/fetchers/nws.py` | `ALERTS_URL`, zone forecast URLs | `area=DC,MD,VA`; DCZ001, MDZ014, VAZ036 |
| `src/poller/fetchers/metar.py` | `DC_STATIONS` (also CPS scoring) | KDCA KIAD KBWI KFDK KHEF KJYO KGAI |
| `src/ingest/parsers/aim_parser.py` | `_PERMANENT_AIRPORTS` (NOTAM watch set) | same seven |
| `src/ingest/parsers/geo_filter.py` | 250 NM radius around DCA, `CORE_AIRPORTS` (30) | FDPS storage filter |
| `src/ingest/parsers/fdps_parser.py` | `DC_LAT/DC_LON`, `MARINE_ONE_RADIUS_NM` | DCA, 50 NM |
| `src/ingest/parsers/smes_parser.py` | `_STDDS_REGIONAL_AIRPORTS`, `SMES_AIRPORTS` | KDCA KIAD KBWI |
| `src/common/db.py` | `_SMES_AIRPORTS` (OOOI: airline times never beat SWIM surface here) | KDCA KIAD KBWI |
| `src/ingest/parsers/tbfm_parser.py` | `DC_AREA_APTS`, `DC_METER_FIXES` | DCA IAD BWI |
| `src/ingest/parsers/tfms_parser.py` | `_DC_FACILITIES` (APTC gate) | ZDC PCT DCA IAD BWI (+K) |
| `src/shared/sector_coalesce.py` | `_ARTCC_GROUPS`, `_SECTOR_FACILITY_MAP` (8 alert zones) | see `ALERT_ARCHITECTURE.md` §5 |
| `src/common/flight_resolver.py` | `HUB_ICAO` | DCA IAD BWI |
| `src/common/airport_fids.py` | MWAA endpoints, `_AIRPORT_TZ` | DCA, IAD, America/New_York |
| `src/poller/skills/faa_cifp_parse.py` | `_AIRPORTS` (21) and bounding box | DC/Baltimore terminal area |
| `src/geo/dc_airspace.py` | P-56A/B, DC FRZ, DC SFRA polygons, served at `GET /api/v1/airspace` | DC |
| `src/common/personas.py` | `ops-brief` persona section labels | LEAD, DC METRO, NAS PROGRAMS, ATCSCC FORECAST, TFRs, NWS ALERTS, AMTRAK NEC, BOTTOM LINE |

Persona edits take effect on the next request (no rebuild);
`personas.py` is manifest-covered, so re-sign.

## 3. Regional airport reference

Replace `HUB_AIRPORTS` with your primary airports first, then the hubs you
want weather and delay data for. AviationWeather.gov METARs cover ICAO
codes worldwide.

| Region | Example list |
|---|---|
| Chicago / Great Lakes | KORD,KMDW,KGYY,KMKE,KDTW,KSTL,KDEN,KLAS,KLAX,KJFK,KBOS |
| Pacific Northwest | KSEA,KPDX,KBFI,KAOO,KGTF,KSFO,KLAX,KDEN,KORD,KJFK |
| Texas / Southwest | KDFW,KAUS,KIAH,KSAT,KELP,KDAL,KHOU,KLAS,KPHX,KLAX,KDEN |
| Southeast | KATL,KBNA,KCLT,KMCO,KMIA,KFLL,KTPA,KPNS,KBHM,KORD,KJFK |
| UK / Northern Europe | EGLL,EGKK,EGGW,EGSS,EGCC,EHAM,EDDH,EDDF,EDDM,LEMD,LFPG |
| Central Europe | EDDF,EDDM,EDDB,EDDL,EDDP,EHAM,EBCI,EBBR,LSZH,LOWW,EPWA |
| Southern Europe | LIRF,LIML,LEMD,LEBL,LFMN,LGAV,LTBA,LLBG,OMDB,OEJN |
| Middle East | OMDB,OMAA,OEJN,OERK,OTHH,OKBK,OBBI,LLBG,HECA |
| Japan | RJTT,RJAA,RJBB,RJOO,RJFF,RJSS,RJSN,RJCK,ROAH |
| Korea | RKSI,RKSS,RKPK,RKJJ,RKPC,RKTN |
| Australia / Pacific | YSSY,YMML,YBBN,YPER,NZAA,NZWN,NZCH,WSSS,WMKK |
| China / East Asia | ZBAA,ZSSS,ZGSZ,ZGGG,ZSPD,RPLL,VTBS,VHHH |

## 4. Weather and aviation-data equivalents

The DC deployment uses NWS `api.weather.gov` (REST) and NWWS-OI (push) for
weather, and FAA SWIM for flight/flow/NOTAM data. Outside the US:

| Region | Weather | Aviation data | Integration status here |
|---|---|---|---|
| Europe | EUROCONTROL NM B2B OPMET; national services (Météo-France, DWD) | EUROCONTROL NM B2B (flight plans, ATFM measures, NOTAMs) | NM B2B: fetcher exists, `awaiting_credentials`, host unreachable from this box. Others: research only |
| Japan | JMA open data | JASDAT (JCAB) | JASDAT: fetcher exists, `awaiting_credentials`, DNS fails. JMA: research only |
| Australia / NZ | Bureau of Meteorology | Airservices NAIPS | research only |
| Korea | KMA | AIS Korea ([aiskorea.molit.go.kr](https://aiskorea.molit.go.kr/)) | research only |
| India | IMD ([mausam.imd.gov.in](https://mausam.imd.gov.in/)) | AAI AIS ([aim.aai.aero](https://aim.aai.aero/)) | research only |
| China | CMA (restricted internationally) | CAAC AIS (AFTN/AMHS or approved providers) | research only |

Portals, access processes and proposed env names: `DATA_SOURCES.md` §4–§6.
Simplest European path: keep ADDS for METARs and replace the NWS alerts URL
with a national or EUROCONTROL alert source.

## 5. Aircraft registry equivalents

| Region | Service | Status here |
|---|---|---|
| US | FAA registry (daily bulk download) | integrated |
| Global fallback | OpenSky aircraft database (monthly dated snapshots) | integrated |
| UK | CAA G-INFO (paid Excel product) | research only |
| Other EU | per-country CAA registers | not integrated |

`GET /api/v1/aircraft/{identifier}` queries both the FAA and OpenSky
registries (`source`: `faa`, `opensky` or `faa+opensky`). Identity resolution is local-only (own ADS-B → SWIM →
local registry tables) since 2026-08-27; airplanes.live is not queried.

## 6. Rail

The reference deployment uses `api.amtraker.com` (unofficial). The live push
writer is the `amtrak-tracker` container (`src/amtrak_tracker/main.py`); the
REST fallback is `poller/fetchers/amtrak.py` in `FETCH_SCHEDULE`, gated on
`push:amtrak`; `src/ingest/amtrak.py` exists but is disabled in `ingest-core`.
To adapt to another JSON rail API, change the parse step in
`amtrak_tracker/main.py` and the fetcher together (both write
`amtrak_status`). MARC/VRE GTFS-RT is not built (`DATA_SOURCES.md` §3).

| Region | Source |
|---|---|
| UK | National Rail Darwin ([nationalrail.co.uk/developers](https://www.nationalrail.co.uk/developers/)) |
| Germany | Deutsche Bahn ([developer.deutschebahn.com](https://developer.deutschebahn.com/)) |
| France | SNCF ([numerique.sncf.com/startup/api](https://numerique.sncf.com/startup/api)) |
| Japan | no public real-time API |
| Australia | state transit authority APIs |

## 7. Restricted data outside the US

The repository's CUI rules cover US-specific material (LADD SP-PRVCY, the
SHARES/HEARS/HEART radio programs). Apply the same pattern — no real values
in tracked files, scoped credentials, audit — to any restricted data in
your jurisdiction.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-data.md`.


### CTDI Regionalization Guide

~~**Verified against current source 2026-08-11.**~~

~~This document covers everything you need to deploy Corporate Travel Dispatch Intelligence outside Washington, DC. The core architecture is identical everywhere — only the geographic filters and data source credentials change.~~

> ~~**Key principle:** The feed credentials themselves don't change when you move regions. You're pointing the same credential infrastructure at different geographic filters. No code restructuring required.~~

**~~What to change~~** *(former heading)*


### CTDI Regionalization Guide › What to change

~~Three files contain all DC-specific geography. Everything else is region-agnostic.~~

**~~`src/poller/skills/ops_brief.py` — Airport hubs and NWS area~~** *(former heading)*


### CTDI Regionalization Guide › What to change › `src/poller/skills/ops_brief.py` — Airport hubs and NWS area

*Superseded block:*
```text superseded
# Primary + regional hub airports (ICAO 4-letter codes)
HUB_AIRPORTS = "KDCA,KIAD,KBWI,KJFK,KEWR,KLGA,KBOS,KPHL,KORD,KATL,KLAX,KSFO,KSEA,KDEN,KDFW"

# NWS alerts geographic filter
NWS_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?area=VA,MD,DC,NY,NJ,CT,MA,PA,DE,RI&status=actual&severity=Extreme,Severe,Moderate"
)
```

~~Replace both with your region. See airport and weather office tables below.~~

**~~`dispatch.env` — NWS weather field office filter~~** *(former heading)*


### CTDI Regionalization Guide › What to change › `dispatch.env` — NWS weather field office filter

*Superseded block:*
```text superseded
# WFO codes for the ingest container's NWWS-OI filter
# DC reference: LWX (Sterling VA), AKQ (Wakefield VA), CTP (State College PA),
# PHI (Mount Holly NJ)
NWWS_WFO_FILTER=LWX,AKQ,CTP,PHI
```

~~Find your WFO codes at [weather.gov/srh/nwsoffices](https://www.weather.gov/srh/nwsoffices). Replace with the 3-letter codes for the offices covering your operating area. For non-US deployments, leave this blank and configure a regional weather API instead (see below).~~

> ~~**Bare 3-letter codes are correct** (`LWX`, not `KLWX`) — but only since 2026-08-22. `ingest/nwws.py::_on_msg` compares this list against the NWWS-OI message's `cccc` attribute, which carries the **4-letter ICAO** form; before that date the comparison was made without normalizing, so a configured `NWWS_WFO_FILTER` silently dropped **every** matching product for the life of the feature. The K-stripping normalization is now applied in both the filtered and unfiltered branches. If you regionalize onto an older checkout, check that fix is present before trusting a configured filter — a silently-empty `nws_alerts` table with a healthy-looking XMPP connection is the symptom.  Also note: `dispatch.env` / `dispatch-secrets.env` are read by systemd `EnvironmentFile=`, which does **not** strip shell quoting. Write `NWWS_WFO_FILTER=LWX,AKQ,CTP,PHI`, never `NWWS_WFO_FILTER="LWX,AKQ,CTP,PHI"` — the quote characters become literal bytes of the value.~~

**~~`src/geo/dc_airspace.py` — DC static airspace~~** *(former heading)*


### CTDI Regionalization Guide › What to change › `src/geo/dc_airspace.py` — DC static airspace

~~The static airspace definitions (P-56A, P-56B, DC FRZ, DC SFRA — per FAR 91.161 and FAAO 7400.11) live in **`src/geo/dc_airspace.py`** and are served via `GET /api/v1/airspace`. Replace or remove these polygons for deployments where different restricted areas apply. Non-DC deployments still receive TFR data for their region; only the static "always-on" areas need updating.~~

**~~Regional airport reference~~** *(former heading)*


### CTDI Regionalization Guide › Regional airport reference

~~Replace the `HUB_AIRPORTS` string with whatever makes operational sense for your area. The first three entries should be your primary operating airports; the remainder are the major connecting hubs you want weather and delay data for.~~

**~~US regions (sample configurations)~~** *(former heading)*


### CTDI Regionalization Guide › Regional airport reference › US regions (sample configurations)

~~**Chicago / Great Lakes:**~~

*Superseded block:*
```text superseded
KORD,KMDW,KGYY,KMKE,KDTW,KSTL,KDEN,KLAS,KLAX,KJFK,KBOS
```

~~**Pacific Northwest / Seattle:**~~

*Superseded block:*
```text superseded
KSEA,KPDX,KBFI,KAOO,KGTF,KSFO,KLAX,KDEN,KORD,KJFK
```

~~**Texas / Southwest:**~~

*Superseded block:*
```text superseded
KDFW,KAUS,KIAH,KSAT,KELP,KDAL,KHOU,KLAS,KPHX,KLAX,KDEN
```

~~**Southeast / Atlanta:**~~

*Superseded block:*
```text superseded
KATL,KBNA,KCLT,KMCO,KMIA,KFLL,KTPA,KPNS,KBHM,KORD,KJFK
```

**~~Europe (ICAO 4-letter codes)~~** *(former heading)*


### CTDI Regionalization Guide › Regional airport reference › Europe (ICAO 4-letter codes)

~~AviationWeather.gov ADDS provides METAR data for European airports using standard ICAO codes — no API change required, just swap the codes.~~

~~**UK / Northern Europe:**~~

*Superseded block:*
```text superseded
EGLL,EGKK,EGGW,EGSS,EGCC,EHAM,EDDH,EDDF,EDDM,LEMD,LFPG
```

~~**Central Europe / Germany:**~~

*Superseded block:*
```text superseded
EDDF,EDDM,EDDB,EDDL,EDDP,EHAM,EBCI,EBBR,LSZH,LOWW,EPWA
```

~~**Southern Europe / Mediterranean:**~~

*Superseded block:*
```text superseded
LIRF,LIML,LEMD,LEBL,LFMN,LGAV,LTBA,LLBG,OMDB,OEJN
```

~~**Middle East:**~~

*Superseded block:*
```text superseded
OMDB,OMAA,OEJN,OERK,OTHH,OKBK,OBBI,LLBG,HECA
```

**~~Asia-Pacific (ICAO 4-letter codes)~~** *(former heading)*


### CTDI Regionalization Guide › Regional airport reference › Asia-Pacific (ICAO 4-letter codes)

~~**Japan:**~~

*Superseded block:*
```text superseded
RJTT,RJAA,RJBB,RJOO,RJFF,RJSS,RJSN,RJCK,ROAH
```

~~(Tokyo Haneda, Narita, Kansai, Itami, Fukuoka, Sendai, Niigata, Kushiro, Okinawa)~~

~~**Korea:**~~

*Superseded block:*
```text superseded
RKSI,RKSS,RKPK,RKJJ,RKPC,RKTN
```

~~(Incheon, Gimpo, Busan, Gwangju, Jeju, Daegu)~~

~~**Australia / Pacific:**~~

*Superseded block:*
```text superseded
YSSY,YMML,YBBN,YPER,NZAA,NZWN,NZCH,WSSS,WMKK
```

~~(Sydney, Melbourne, Brisbane, Perth, Auckland, Wellington, Christchurch, Singapore, Kuala Lumpur)~~

~~**China / East Asia:**~~

*Superseded block:*
```text superseded
ZBAA,ZSSS,ZGSZ,ZGGG,ZSPD,RPLL,VTBS,VHHH
```

~~(Beijing, Shanghai Hongqiao, Shenzhen, Guangzhou, Pudong, Manila, Bangkok, Hong Kong)~~

**~~Weather API equivalents by region~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region

~~The DC deployment uses two NWS feeds: `api.weather.gov/alerts` (REST polling) and NWWS-OI XMPP (push). Outside the US, replace these with regional equivalents that feed into the same poller slots.~~

**~~European weather~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region › European weather

~~**EUROCONTROL / Eurocontrol Network Operations Portal**~~

- ~~OPMET data (METARs, TAFs, SIGMETs, AIRMETs) via NM B2B SOAP/REST API~~
- ~~Portal: [https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services](https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services)~~
- ~~Access request: [https://www.eurocontrol.int/contact/nm-b2b-access-request](https://www.eurocontrol.int/contact/nm-b2b-access-request)~~
- ~~Credentials: `EUROCONTROL_NM_B2B_USER` / `EUROCONTROL_NM_B2B_PASS` (stub in dispatch-secrets.env)~~
- ~~Note: NM B2B requires organizational affiliation; ANSPs and licensed aviation operators qualify~~

~~**National meteorological services** (METARs — free, no credentials)~~

- ~~UK: [https://www.aviationweather.gov/](https://www.aviationweather.gov/) covers UK ICAO codes, no changes needed~~
- ~~France (Météo-France): [https://donneespubliques.meteofrance.fr/](https://donneespubliques.meteofrance.fr/) — free API, registration at developer portal~~
- ~~Germany (DWD): [https://opendata.dwd.de/](https://opendata.dwd.de/) — open data, no credentials~~

> ~~For European deployments, the simplest path is to keep `AviationWeather.gov ADDS` for METARs (it covers ICAO codes globally) and replace the NWS alerts URL with EUROCONTROL or a national MET service alert feed for severe weather warnings.~~

**~~Japan weather~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region › Japan weather

~~**JMA (Japan Meteorological Agency)** — open data, no credentials required~~

- ~~Aviation weather bulletins: [https://www.jma.go.jp/bosai/](https://www.jma.go.jp/bosai/)~~
- ~~Open data API: [https://opendata.jma.go.jp/gpv/](https://opendata.jma.go.jp/gpv/)~~
- ~~Aviation-specific XML feeds (METAR, TAF, SIGMET): [https://www.data.jma.go.jp/developer/index.html](https://www.data.jma.go.jp/developer/index.html)~~
- ~~METARs in ICAO format — AviationWeather.gov ADDS covers Japanese airport codes; no API change needed for basic METAR~~
- ~~Env var stub: `JMA_API_KEY` (see dispatch-secrets.env — currently not required for open feeds)~~

~~**JASDAT (Japan AIS Data Tool)** — the Japanese equivalent of FAA SWIM for aeronautical information~~

- ~~Operated by: JCAB (Japan Civil Aviation Bureau), Ministry of Land, Infrastructure, Transport and Tourism~~
- ~~Provides: NOTAMs, AIS data, SIGMET/AIRMET, airspace information~~
- ~~Portal: [https://www.jasdat.go.jp/en/](https://www.jasdat.go.jp/en/)~~
- ~~Access: Requires JCAB authorization; approved aviation operators and ANSPs~~
- ~~Env var stub: `JASDAT_USER` / `JASDAT_PASS` (see dispatch-secrets.env)~~

**~~Australia / New Zealand weather~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region › Australia / New Zealand weather

~~**Bureau of Meteorology (BoM)** — free, no credentials required~~

- ~~Aviation weather: [http://www.bom.gov.au/aviation/](http://www.bom.gov.au/aviation/)~~
- ~~Open data: [https://open-data.bom.gov.au/](https://open-data.bom.gov.au/)~~

~~**Airservices Australia** — NAIPS (National Aeronautical Information Processing System)~~

- ~~Portal: [https://www.airservicesaustralia.com/](https://www.airservicesaustralia.com/)~~
- ~~NAIPS access: Registration required; primarily for pilots and operators~~
- ~~Env var stub: `NAIPS_USER` / `NAIPS_PASS` (see dispatch-secrets.env)~~

**~~Korea weather~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region › Korea weather

~~**KMA (Korea Meteorological Administration)**~~

- ~~Open API: [https://data.kma.go.kr/](https://data.kma.go.kr/)~~
- ~~English portal: [https://www.kma.go.kr/en/](https://www.kma.go.kr/en/)~~
- ~~API key registration: [https://apihub.kma.go.kr/](https://apihub.kma.go.kr/)~~
- ~~Env var stub: `KMA_API_KEY` (see dispatch-secrets.env)~~

**~~India weather~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region › India weather

~~**IMD (India Meteorological Department)**~~

- ~~Aviation weather: [https://mausam.imd.gov.in/](https://mausam.imd.gov.in/)~~
- ~~Open data: [https://www.imdpune.gov.in/](https://www.imdpune.gov.in/)~~

**~~China weather~~** *(former heading)*


### CTDI Regionalization Guide › Weather API equivalents by region › China weather

~~**CMA (China Meteorological Administration)**~~

- ~~Data portal: [https://data.cma.cn/](https://data.cma.cn/) — registration required~~
- ~~Note: International access is limited; aviation operators typically obtain data via CAAC channels~~
- ~~Env var stub: `CMA_API_KEY` (see dispatch-secrets.env)~~

**~~Aviation data equivalents by region~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region

**~~US: FAA SWIM NMS~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › US: FAA SWIM NMS

~~The reference deployment uses FAA SWIM (System Wide Information Management) NMS for push-primary feeds of flight plans, tracks, TFRs, and NAS programs. See [docs/DATA_SOURCES.md](DATA_SOURCES.md) for the FAA SWIM access request process.~~

**~~Europe: EUROCONTROL NM B2B~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › Europe: EUROCONTROL NM B2B

~~The EUROCONTROL Network Manager B2B API is the closest European equivalent to FAA SWIM. It provides:~~

- ~~Flight plans and ATC clearances (FDPS equivalent)~~
- ~~ATFM measures (GDP/GS equivalent — CTOT, regulations, MCIs)~~
- ~~OPMET (weather: METARs, TAFs, SIGMETs)~~
- ~~NOTAMs~~
- ~~Airspace status~~

~~Portal: [https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services](https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services)~~

~~The feed uses SOAP/XML or REST depending on the service; the ingest container's SWIM slots can be adapted to consume NM B2B output using the same message-queue architecture.~~

**~~Japan: JASDAT and SWIM-JAPAN~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › Japan: JASDAT and SWIM-JAPAN

~~Japan implements ICAO SWIM through JASDAT, operated by JCAB. It provides:~~

- ~~NOTAMs (equivalent to FAA AIM SWIM feed)~~
- ~~SIGMET/AIRMET~~
- ~~AIS data publications~~
- ~~Aerodrome information~~

~~Japan's domestic SWIM implementation (`SWIM-JAPAN`) is expanding — access is primarily through JCAB-affiliated aviation operators. See [docs/DATA_SOURCES.md](DATA_SOURCES.md) for the access request process and email template.~~

**~~Australia: NAIPS and SWIM-AU~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › Australia: NAIPS and SWIM-AU

~~Airservices Australia's NAIPS provides NOTAMs, PIREPs, and AIS data. An ICAO SWIM initiative is in development for Australia. For current deployments, NAIPS REST API is the practical path.~~

**~~Korea: AIS Korea~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › Korea: AIS Korea

~~AIS Korea (operated by MOLIT) provides NOTAMs and aeronautical information.~~

- ~~Portal: [https://aiskorea.molit.go.kr/](https://aiskorea.molit.go.kr/)~~

**~~India: AAI AIS~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › India: AAI AIS

~~Airports Authority of India AIS portal.~~

- ~~Portal: [https://aim.aai.aero/](https://aim.aai.aero/)~~

**~~China: CAAC AIS~~** *(former heading)*


### CTDI Regionalization Guide › Aviation data equivalents by region › China: CAAC AIS

~~Civil Aviation Administration of China AIS services are primarily accessible to domestic operators and ICAO members. International access is via AFTN/AMHS or through approved aviation service providers.~~

**~~Aircraft registry equivalents by region~~** *(former heading)*


### CTDI Regionalization Guide › Aircraft registry equivalents by region

~~Separate from the live flight-plan/NOTAM feeds above, aircraft *ownership/registration* lookups (N-number <-> Mode S hex <-> registrant) are country-specific -- there's no single global registry.~~

| ~~Region~~ | ~~Service~~ | ~~Access~~ |
|---|---|---|
| ~~US~~ | ~~FAA Aircraft Registry (N-Number)~~ | ~~Free bulk download, daily refresh -- see [DATA_SOURCES.md](DATA_SOURCES.md)~~ |
| ~~UK~~ | ~~CAA G-INFO~~ | ~~Paid product (email/Excel delivery), monthly or quarterly -- see [DATA_SOURCES.md](DATA_SOURCES.md)~~ |
| ~~Global fallback~~ | ~~OpenSky Network Aircraft Database~~ | ~~Free bulk CSV, 127 countries, irregular updates -- see [DATA_SOURCES.md](DATA_SOURCES.md)~~ |
| ~~Europe (other)~~ | ~~No single EU-wide registry -- each country's CAA maintains its own (DGAC in France, LBA in Germany, ENAC in Italy, etc.)~~ | ~~Integrate per-country following the FAA/UK CAA pattern if needed~~ |

~~The local registry lookup endpoint (`GET /api/v1/aircraft/<N-NUMBER-or-HEX>`) serves US N-number data plus the locally-imported OpenSky registry cross-check. A non-US deployment that needs fast local hex/registration lookups would need to import a regional registry (UK CAA G-INFO, etc.) into the same table structure — note the reference deployment resolves identity **local-only** as of 2026-08-27 (own ADS-B → ingested SWIM → locally-imported FAA/OpenSky registry tables) and no longer queries airplanes.live programmatically; a deployment choosing a live third-party lookup instead is departing from that rule deliberately.~~

**~~Ops brief naming conventions~~** *(former heading)*


### CTDI Regionalization Guide › Ops brief naming conventions

~~The ops brief sections that use DC-specific names (`DC METRO`, `NORTHEAST`, `TRANSCON HUBS`) are labels in the ops-brief persona's system prompt — which, since the 2026-08-27 llama.cpp cutover, lives in **`src/common/personas.py`** (the `ops-brief` entry), not in a Modelfile or in `ops_brief.py` itself. They're cosmetic — rename them to match your operating context (edits take effect on the next request, no rebuild):~~

~~**European example:**~~

*Superseded block:*
```text superseded
SYSTEM_PROMPT = """...
HOME BASE: Current conditions at [your primary airports] — ceiling, vis, wind, precip.

REGIONAL: [Your regional airports] — flag gusty winds, convection, or approaching systems.

INTL CONNECTIONS: LHR/CDG/FRA/AMS/MAD/FCO — one line each unless a significant delay or
closure is active. Flag Eurocontrol flow management measures (CTOT, regulation, MCI).
...
```

~~**Asia-Pacific example:**~~

*Superseded block:*
```text superseded
SYSTEM_PROMPT = """...
HOME BASE: Current conditions at [your primary airports].

REGIONAL: [Regional airports].

INTL CONNECTIONS: NRT/HND/ICN/HKG/SIN/BKK/SYD — one line each unless ATFM measures
or significant weather active. Note JMA SIGMET issuances for typhoon/frontal activity.
...
```

~~No code changes outside the persona text (remember to re-sign the manifest — `personas.py` and the Modelfiles are integrity-covered). The local model doesn't care what the sections are called — it follows the structure you define.~~

**~~Rail feed equivalents~~** *(former heading)*


### CTDI Regionalization Guide › Rail feed equivalents

~~The reference deployment monitors Amtrak via [amtraker.com](https://api.amtraker.com/) (unofficial, no credentials). Regional equivalents:~~

| ~~Region~~ | ~~Service~~ | ~~API/Source~~ |
|---|---|---|
| ~~US Northeast~~ | ~~Amtrak NEC~~ | ~~amtraker.com (public)~~ |
| ~~UK~~ | ~~National Rail~~ | ~~[https://www.nationalrail.co.uk/developers/](https://www.nationalrail.co.uk/developers/) — Darwin push feed~~ |
| ~~Germany~~ | ~~Deutsche Bahn~~ | ~~[https://developer.deutschebahn.com/](https://developer.deutschebahn.com/)~~ |
| ~~Japan~~ | ~~JR / Shinkansen~~ | ~~No public real-time API; delay info via regional apps~~ |
| ~~France~~ | ~~SNCF~~ | ~~[https://numerique.sncf.com/startup/api](https://numerique.sncf.com/startup/api)~~ |
| ~~Australia~~ | ~~Various state operators~~ | ~~State transit authority APIs vary~~ |

~~The sole live Amtrak path is the ingest-core push-primary loop in `src/ingest/amtrak.py` — it makes a single REST call per cycle and parses train delay/status fields; wire any JSON-returning transit API into that module's slot. (The poller-side fetcher file `src/poller/fetchers/amtrak.py` exists but is currently *unscheduled dead code* — it has no `FETCH_SCHEDULE` entry and is never invoked; the poller only runs a 300 s watchlist sweep against amtraker. Don't adapt the fetcher file expecting it to run.)~~

**~~CUI handling in non-US deployments~~** *(former heading)*


### CTDI Regionalization Guide › CUI handling in non-US deployments

~~The CUI handling rules in the repository apply specifically to US classified radio programs (SHARES, HEARS, HEART). Non-US deployments operating outside the US government radio framework do not have these specific credential types, but analogous rules apply to any CUI-equivalent data in your jurisdiction. The audit log and empty placeholder pattern should still be followed for any credentialed or restricted data sources.~~
