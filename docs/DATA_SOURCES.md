# CTDI Data Sources & Access Guide

This document covers every integrated data source, how to request access, and email templates for sources that require it. It also serves as the canonical reference for wiring in new sources — any new source that requires API signup or an email request should have an entry added here when it's integrated.

> **Maintenance:** When a portal URL, email address, or signup process changes, update this file in the same commit that updates the code. The last-verified date in each section header tracks when the information was confirmed current.

---

## US Sources

### FAA SWIM / NMS (System Wide Information Management — Network Management Server)

**Last verified:** 2025-12

**What it provides:** Push-primary flight plan data, real-time tracks, TFRs, NAS flow programs (GDP, GS, AFP, AAR), digital NOTAMs, terminal weather, and arrival sequencing. The highest-fidelity aviation data feed available in the US.

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

**Credentials location:**
```bash
FAA_NOTAM_API_KEY=
```

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

## Adding a new data source

When integrating a new feed:

1. Add the credential stub(s) to `dispatch-secrets.env.example` with a comment block that includes the signup URL and a brief description.

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
