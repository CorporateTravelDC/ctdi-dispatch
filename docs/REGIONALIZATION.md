# CTDI Regionalization Guide

This document covers everything you need to deploy Corporate Travel Dispatch Intelligence outside Washington, DC. The core architecture is identical everywhere — only the geographic filters and data source credentials change.

> **Key principle:** The feed credentials themselves don't change when you move regions. You're pointing the same credential infrastructure at different geographic filters. No code restructuring required.

---

## What to change

Three files contain all DC-specific geography. Everything else is region-agnostic.

### `src/poller/skills/ops_brief.py` — Airport hubs and NWS area

```python
# Primary + regional hub airports (ICAO 4-letter codes)
HUB_AIRPORTS = "KDCA,KIAD,KBWI,KJFK,KEWR,KLGA,KBOS,KPHL,KORD,KATL,KLAX,KSFO,KSEA,KDEN,KDFW"

# NWS alerts geographic filter
NWS_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?area=VA,MD,DC,NY,NJ,CT,MA,PA,DE,RI&status=actual&severity=Extreme,Severe,Moderate"
)
```

Replace both with your region. See airport and weather office tables below.

### `dispatch.env` — NWS weather field office filter

```bash
# WFO codes for the ingest container's NWWS-OI filter
# DC reference: LWX (Sterling VA), AKQ (Wakefield VA), CTP (State College PA)
NWWS_WFO_FILTER=LWX,AKQ,CTP
```

Find your WFO codes at [weather.gov/srh/nwsoffices](https://www.weather.gov/srh/nwsoffices). Replace with the 3-letter codes for the offices covering your operating area. For non-US deployments, leave this blank and configure a regional weather API instead (see below).

### `src/ingest/config.py` — DC static airspace

The static airspace definitions (P-56A/B, DC FRZ, DC SFRA) in `src/common/airspace_static.py` are DC-specific. Replace or remove these polygons for deployments where different restricted areas apply. Non-DC deployments will still receive TFR data for their region; only the static "always-on" areas need updating.

---

## Regional airport reference

Replace the `HUB_AIRPORTS` string with whatever makes operational sense for your area. The first three entries should be your primary operating airports; the remainder are the major connecting hubs you want weather and delay data for.

### US regions (sample configurations)

**Chicago / Great Lakes:**
```
KORD,KMDW,KGYY,KMKE,KDTW,KSTL,KDEN,KLAS,KLAX,KJFK,KBOS
```

**Pacific Northwest / Seattle:**
```
KSEA,KPDX,KBFI,KAOO,KGTF,KSFO,KLAX,KDEN,KORD,KJFK
```

**Texas / Southwest:**
```
KDFW,KAUS,KIAH,KSAT,KELP,KDAL,KHOU,KLAS,KPHX,KLAX,KDEN
```

**Southeast / Atlanta:**
```
KATL,KBNA,KCLT,KMCO,KMIA,KFLL,KTPA,KPNS,KBHM,KORD,KJFK
```

### Europe (ICAO 4-letter codes)

AviationWeather.gov ADDS provides METAR data for European airports using standard ICAO codes — no API change required, just swap the codes.

**UK / Northern Europe:**
```
EGLL,EGKK,EGGW,EGSS,EGCC,EHAM,EDDH,EDDF,EDDM,LEMD,LFPG
```

**Central Europe / Germany:**
```
EDDF,EDDM,EDDB,EDDL,EDDP,EHAM,EBCI,EBBR,LSZH,LOWW,EPWA
```

**Southern Europe / Mediterranean:**
```
LIRF,LIML,LEMD,LEBL,LFMN,LGAV,LTBA,LLBG,OMDB,OEJN
```

**Middle East:**
```
OMDB,OMAA,OEJN,OERK,OTHH,OKBK,OBBI,LLBG,HECA
```

### Asia-Pacific (ICAO 4-letter codes)

**Japan:**
```
RJTT,RJAA,RJBB,RJOO,RJFF,RJSS,RJSN,RJCK,ROAH
```
(Tokyo Haneda, Narita, Kansai, Itami, Fukuoka, Sendai, Niigata, Kushiro, Okinawa)

**Korea:**
```
RKSI,RKSS,RKPK,RKJJ,RKPC,RKTN
```
(Incheon, Gimpo, Busan, Gwangju, Jeju, Daegu)

**Australia / Pacific:**
```
YSSY,YMML,YBBN,YPER,NZAA,NZWN,NZCH,WSSS,WMKK
```
(Sydney, Melbourne, Brisbane, Perth, Auckland, Wellington, Christchurch, Singapore, Kuala Lumpur)

**China / East Asia:**
```
ZBAA,ZSSS,ZGSZ,ZGGG,ZSPD,RPLL,VTBS,VHHH
```
(Beijing, Shanghai Hongqiao, Shenzhen, Guangzhou, Pudong, Manila, Bangkok, Hong Kong)

---

## Weather API equivalents by region

The DC deployment uses two NWS feeds: `api.weather.gov/alerts` (REST polling) and NWWS-OI XMPP (push). Outside the US, replace these with regional equivalents that feed into the same poller slots.

### European weather

**EUROCONTROL / Eurocontrol Network Operations Portal**
- OPMET data (METARs, TAFs, SIGMETs, AIRMETs) via NM B2B SOAP/REST API
- Portal: [https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services](https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services)
- Access request: [https://www.eurocontrol.int/contact/nm-b2b-access-request](https://www.eurocontrol.int/contact/nm-b2b-access-request)
- Credentials: `EUROCONTROL_NM_B2B_USER` / `EUROCONTROL_NM_B2B_PASS` (stub in dispatch-secrets.env)
- Note: NM B2B requires organizational affiliation; ANSPs and licensed aviation operators qualify

**National meteorological services** (METARs — free, no credentials)
- UK: [https://www.aviationweather.gov/](https://www.aviationweather.gov/) covers UK ICAO codes, no changes needed
- France (Météo-France): [https://donneespubliques.meteofrance.fr/](https://donneespubliques.meteofrance.fr/) — free API, registration at developer portal
- Germany (DWD): [https://opendata.dwd.de/](https://opendata.dwd.de/) — open data, no credentials

> For European deployments, the simplest path is to keep `AviationWeather.gov ADDS` for METARs (it covers ICAO codes globally) and replace the NWS alerts URL with EUROCONTROL or a national MET service alert feed for severe weather warnings.

### Japan weather

**JMA (Japan Meteorological Agency)** — open data, no credentials required
- Aviation weather bulletins: [https://www.jma.go.jp/bosai/](https://www.jma.go.jp/bosai/)
- Open data API: [https://opendata.jma.go.jp/gpv/](https://opendata.jma.go.jp/gpv/)
- Aviation-specific XML feeds (METAR, TAF, SIGMET): [https://www.data.jma.go.jp/developer/index.html](https://www.data.jma.go.jp/developer/index.html)
- METARs in ICAO format — AviationWeather.gov ADDS covers Japanese airport codes; no API change needed for basic METAR
- Env var stub: `JMA_API_KEY` (see dispatch-secrets.env — currently not required for open feeds)

**JASDAT (Japan AIS Data Tool)** — the Japanese equivalent of FAA SWIM for aeronautical information
- Operated by: JCAB (Japan Civil Aviation Bureau), Ministry of Land, Infrastructure, Transport and Tourism
- Provides: NOTAMs, AIS data, SIGMET/AIRMET, airspace information
- Portal: [https://www.jasdat.go.jp/en/](https://www.jasdat.go.jp/en/)
- Access: Requires JCAB authorization; approved aviation operators and ANSPs
- Env var stub: `JASDAT_USER` / `JASDAT_PASS` (see dispatch-secrets.env)

### Australia / New Zealand weather

**Bureau of Meteorology (BoM)** — free, no credentials required
- Aviation weather: [http://www.bom.gov.au/aviation/](http://www.bom.gov.au/aviation/)
- Open data: [https://open-data.bom.gov.au/](https://open-data.bom.gov.au/)

**Airservices Australia** — NAIPS (National Aeronautical Information Processing System)
- Portal: [https://www.airservicesaustralia.com/](https://www.airservicesaustralia.com/)
- NAIPS access: Registration required; primarily for pilots and operators
- Env var stub: `NAIPS_USER` / `NAIPS_PASS` (see dispatch-secrets.env)

### Korea weather

**KMA (Korea Meteorological Administration)**
- Open API: [https://data.kma.go.kr/](https://data.kma.go.kr/)
- English portal: [https://www.kma.go.kr/en/](https://www.kma.go.kr/en/)
- API key registration: [https://apihub.kma.go.kr/](https://apihub.kma.go.kr/)
- Env var stub: `KMA_API_KEY` (see dispatch-secrets.env)

### India weather

**IMD (India Meteorological Department)**
- Aviation weather: [https://mausam.imd.gov.in/](https://mausam.imd.gov.in/)
- Open data: [https://www.imdpune.gov.in/](https://www.imdpune.gov.in/)

### China weather

**CMA (China Meteorological Administration)**
- Data portal: [https://data.cma.cn/](https://data.cma.cn/) — registration required
- Note: International access is limited; aviation operators typically obtain data via CAAC channels
- Env var stub: `CMA_API_KEY` (see dispatch-secrets.env)

---

## Aviation data equivalents by region

### US: FAA SWIM NMS

The reference deployment uses FAA SWIM (System Wide Information Management) NMS for push-primary feeds of flight plans, tracks, TFRs, and NAS programs. See [docs/DATA_SOURCES.md](DATA_SOURCES.md) for the FAA SWIM access request process.

### Europe: EUROCONTROL NM B2B

The EUROCONTROL Network Manager B2B API is the closest European equivalent to FAA SWIM. It provides:
- Flight plans and ATC clearances (FDPS equivalent)
- ATFM measures (GDP/GS equivalent — CTOT, regulations, MCIs)
- OPMET (weather: METARs, TAFs, SIGMETs)
- NOTAMs
- Airspace status

Portal: [https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services](https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services)

The feed uses SOAP/XML or REST depending on the service; the ingest container's SWIM slots can be adapted to consume NM B2B output using the same message-queue architecture.

### Japan: JASDAT and SWIM-JAPAN

Japan implements ICAO SWIM through JASDAT, operated by JCAB. It provides:
- NOTAMs (equivalent to FAA AIM SWIM feed)
- SIGMET/AIRMET
- AIS data publications
- Aerodrome information

Japan's domestic SWIM implementation (`SWIM-JAPAN`) is expanding — access is primarily through JCAB-affiliated aviation operators. See [docs/DATA_SOURCES.md](DATA_SOURCES.md) for the access request process and email template.

### Australia: NAIPS and SWIM-AU

Airservices Australia's NAIPS provides NOTAMs, PIREPs, and AIS data. An ICAO SWIM initiative is in development for Australia. For current deployments, NAIPS REST API is the practical path.

### Korea: AIS Korea

AIS Korea (operated by MOLIT) provides NOTAMs and aeronautical information.
- Portal: [https://aiskorea.molit.go.kr/](https://aiskorea.molit.go.kr/)

### India: AAI AIS

Airports Authority of India AIS portal.
- Portal: [https://aim.aai.aero/](https://aim.aai.aero/)

### China: CAAC AIS

Civil Aviation Administration of China AIS services are primarily accessible to domestic operators and ICAO members. International access is via AFTN/AMHS or through approved aviation service providers.

---

## Ops brief naming conventions

The ops brief sections that use DC-specific names (`DC METRO`, `NORTHEAST`, `TRANSCON HUBS`) are labels in the Ollama system prompt inside `ops_brief.py`. They're cosmetic — rename them to match your operating context:

**European example:**
```python
SYSTEM_PROMPT = """...
HOME BASE: Current conditions at [your primary airports] — ceiling, vis, wind, precip.

REGIONAL: [Your regional airports] — flag gusty winds, convection, or approaching systems.

INTL CONNECTIONS: LHR/CDG/FRA/AMS/MAD/FCO — one line each unless a significant delay or
closure is active. Flag Eurocontrol flow management measures (CTOT, regulation, MCI).
...
```

**Asia-Pacific example:**
```python
SYSTEM_PROMPT = """...
HOME BASE: Current conditions at [your primary airports].

REGIONAL: [Regional airports].

INTL CONNECTIONS: NRT/HND/ICN/HKG/SIN/BKK/SYD — one line each unless ATFM measures
or significant weather active. Note JMA SIGMET issuances for typhoon/frontal activity.
...
```

No code changes outside the prompt string. The Ollama model doesn't care what the sections are called — it follows the structure you define.

---

## Rail feed equivalents

The reference deployment monitors Amtrak via [amtraker.com](https://api.amtraker.com/) (unofficial, no credentials). Regional equivalents:

| Region | Service | API/Source |
|---|---|---|
| US Northeast | Amtrak NEC | amtraker.com (public) |
| UK | National Rail | [https://www.nationalrail.co.uk/developers/](https://www.nationalrail.co.uk/developers/) — Darwin push feed |
| Germany | Deutsche Bahn | [https://developer.deutschebahn.com/](https://developer.deutschebahn.com/) |
| Japan | JR / Shinkansen | No public real-time API; delay info via regional apps |
| France | SNCF | [https://numerique.sncf.com/startup/api](https://numerique.sncf.com/startup/api) |
| Australia | Various state operators | State transit authority APIs vary |

The amtrak fetcher in `src/poller/fetchers/amtrak.py` is straightforward to adapt — it makes a single REST call and parses train delay/status fields. Wire any JSON-returning transit API into the same slot.

---

## CUI handling in non-US deployments

The CUI handling rules in the repository apply specifically to US classified radio programs (SHARES, HEARS, HEART). Non-US deployments operating outside the US government radio framework do not have these specific credential types, but analogous rules apply to any CUI-equivalent data in your jurisdiction. The audit log and empty placeholder pattern should still be followed for any credentialed or restricted data sources.
