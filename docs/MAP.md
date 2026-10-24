# Map — operator and deployer reference

Written 2026-10-24 against the operator-approved map rework (aviation wave 1,
spec: one aircraft map, local receiver and SWIM drawn together) and the same
pattern applied to vessels, trains and drones. Facts below were read from the
code named in each section on 2026-10-24, not from a running instance.

## 1. What the map is

One Leaflet map per domain in the runner SPA (`src/runner/frontend/src/`):

| Route | Component | Data |
|---|---|---|
| `/map` | `components/aircraftMap/AircraftMap.jsx` (`MapView.jsx` is a thin wrapper) | runner `/api/map/*` (local receiver + SWIM, merged server-side) |
| `/ais` | `components/AisMapView.jsx` | runner `/api/ais/vessels` |
| `/trains` | `components/TrainMapView.jsx` | dispatch `/api/v1/amtrak` via the runner proxy |
| `/utm` | `components/UtmMapView.jsx` | runner `/api/utm/drones` |

The aircraft map replaces the earlier two-mode view: the **GLOBE** mode (a
`globe.airplanes.live` iframe) and the GLOBE / TACTICAL toggle are gone, and
so is any "local only" toggle. Everything the deployment has is drawn on the
one map. The vessel map no longer embeds MarineTraffic; the only MarineTraffic
reference left is an external link for the selected vessel (`AisMapView.jsx`).
The trains tab, list-only since 2026-07-21, is a real map again.

The shared, non-aviation parts (marker layer, trails, list panel, legend,
watchlist chips, click-to-focus) live in `components/mapKit/`; the aviation
specifics (altitude ramp, source styles, conflict ring, leg line) in
`components/aircraftMap/`.

Basemap: OpenStreetMap tiles through `src/runner/frontend/src/mapTiles.js`.
Every tile layer asks for `referrerPolicy: strict-origin` because OSM answers
a request with no Referer with a blocked-placeholder tile, and the public
runner domain sends `Referrer-Policy: no-referrer` from nginx (kept for
everything else). OSM sees the site origin only, never a path or query.

## 2. Sources and how they are told apart (aircraft)

The runner merges two sources in `/api/map/aircraft` (`src/runner/main.py`,
section "Aircraft map"):

- **Local receiver** — ultrafeeder / readsb `aircraft.json` (`ULTRAFEEDER_URL`),
  the same read as `/api/adsb/live`.
- **SWIM** — stored FDPS flight positions and TAIS terminal tracks, from
  dispatch-web's `/api/v1/swim/positions` (`src/web/routes/swim_positions.py`),
  fetched server-side with the runner's service token. The browser never
  calls dispatch-web for this.

Merge rule: by ICAO hex first, else by normalised callsign. A matched aircraft
takes the LOCAL position (fresher); SWIM fills origin, destination and
registration. When both sides carry a callsign or registration and they
disagree, the aircraft is flagged `conflict` with a note; SWIM is the tail
authority and the tooltip shows both values. If dispatch-web is unreachable
the map still renders, local only, with `swim_available: false`.

Each aircraft carries `src`:

| `src` | Meaning | List column shows |
|---|---|---|
| `matched` | receiver AND SWIM have it | `R+S` |
| `local` | receiver only | `RCV` |
| `swim` | SWIM only (typically beyond receiver range) | `SWM` |

## 3. The styling legend, in words

The on-map legend (`aircraftMap/Legend.jsx`, collapsible, collapsed by default
on a phone) encodes two things independently so neither hides the other:

**Fill colour = altitude.** The stop tables are tar1090's default `ColorByAlt`
ramp (wiedehopf/tar1090, `html/defaults.js`, GPL-2.0), copied on 2026-10-24
so this map reads exactly like the receiver's own tar1090 page: orange near
the ground, through yellow and green around 9-11 kft, to blue and purple at
cruise. Only the numbers are tar1090's; the interpolation is ours
(`aircraftMap/altitudeColor.js`). Aircraft on the ground are dark grey;
unknown altitude is light grey. A **stale** position (older than 60 s) uses
tar1090's lighter, desaturated variant of the same hue.

**Outline = source** (`aircraftMap/markers.js`), never changing the fill:

- `matched` — solid fill with a thin white outline.
- `local` — solid fill, no outline (tar1090's own look).
- `swim` — fill at about 55 % opacity with a dashed outline in the altitude
  colour; the tooltip shows the position age.
- `conflict` (any source) — a red ring around the glyph.
- A SWIM-only aircraft has no ground track (FDPS does not carry one), so it
  is drawn as a heading-less round glyph instead of an arrow pointing north.

Watchlist (tracked) and selected aircraft get a CSS glow and a larger glyph;
these do not change fill or outline.

**Trails** are built client-side from successive polls (`trailsStore.js`): one
ring buffer per aircraft, about 10 minutes deep, dropped 5 minutes after the
aircraft is last seen, segments coloured by altitude with the same ramp.
The runner API is a snapshot; there is no server-side history behind trails.

**Leg line.** The selected aircraft shows its current origin-to-destination
line when both airports resolve to coordinates (the CIFP airport reference
table via `/api/v1/swim/last-leg`), or its most recent leg within 24 h when
it is not airborne.

## 4. Interaction

- Poll: `/api/map/aircraft` for the current viewport every 5 s while the tab
  is visible, paused when hidden, and 600 ms after each pan or zoom.
- `/api/map/scope` is fetched once and sets the pan limit and minimum zoom.
- Click-to-focus from a marker, a row in the aircraft list, a watchlist chip,
  or the search box; all go through the same focus path. An off-screen or
  landed target resolves through `/api/map/focus`.
- Aircraft list (`AircraftList.jsx`): sortable by callsign, hex, altitude,
  ground speed, source and age; filter box; sort choice persists per device.
- "Open full receiver map" (trusted callers only, see §6) opens the local
  tar1090 with `?icao=<hex>` appended when an aircraft is selected.

## 5. Endpoints and tiers

Runner (`src/runner/main.py`, port 8001):

| Path | Returns | Trust rule |
|---|---|---|
| `GET /api/map/aircraft?south=&west=&north=&east=&max_age_s=120` | merged aircraft, `counts` by source, `swim_available`, `local_available`, `swim_truncated` | on the live runner, trusted origins only (`live_trusted_only` middleware) |
| `GET /api/map/scope` | `receiver` (centre + `range_nm`), `swim` (dispatch scope), `bounds`, `tar1090_url` | real receiver centre, SWIM centre and `tar1090_url` go to trusted callers only; an untrusted caller gets the same public-airport placeholder `/api/v1/frontend-config` has always handed out, a null SWIM centre and no tar1090 link |
| `GET /api/map/focus?ident=<hex or callsign>` | `{"live": true, ...aircraft}` if live anywhere in scope, else `{"live": false, "last": {...}}` from the most recent SWIM leg within 24 h, else 404 | as above |

`DEMO_MODE` returns synthetic data only from all three and makes no network
calls (covered by `tests/runner/test_demo_never_touches_local_receivers.py`).

Dispatch web API (`src/web/routes/swim_positions.py`, port 8000):

| Path | Returns |
|---|---|
| `GET /api/v1/swim/positions?south=&west=&north=&east=&max_age_s=120&limit=2000` | stored FDPS / TAIS positions inside the box, newer than `max_age_s` (bbox and age filter run in SQL) |
| `GET /api/v1/swim/scope` | `swim_enabled` (a SWIM push feed reported within the last hour), `center`, `radius_nm`, `airports`, `label` |
| `GET /api/v1/swim/last-leg?ident=` | most recent `flight_events` row within 24 h, with origin / destination coordinates when the airport table resolves them |

Tier and privacy rule (documented in that module's docstring): the runner
sends `RUNNER_ENRICHED_TOKEN` (Tier 1) for these paths — they are in the
runner's `_TIER1_PATHS`. A Tier-1 or higher caller is the internal
operational display and receives every stored position, the same rule the
internal watchlist and ETA display already follow. **Any caller that resolves
to Tier 0** (no token, or the tunnel's public stamp) is treated as a public
exposure: LADD-listed aircraft are removed by hex, registration, callsign and
flight id with the production filters, a filter failure is a 503 rather than
an unfiltered list, `/scope` returns a null centre, and `/last-leg` answers
404 for a listed aircraft so the response does not confirm it exists. PIA
aircraft carry a temporary hex that is not in the registry; their callsign is
checked against the LADD list like any other identifier, and there is no
separate PIA list in the platform today.

## 6. Configuration knobs

| Setting | Where | Effect |
|---|---|---|
| `ULTRAFEEDER_URL` | runner env | the local receiver's `aircraft.json` (the local source) |
| `ULTRAFEEDER_LAT` / `ULTRAFEEDER_LON` | secrets file, scoped env (`docs/GPS_COORDINATE_CONFIGURATION.md`) | receiver centre for the range circle, range rings and distances |
| `DISPATCH_BASE_URL` + `RUNNER_ENRICHED_TOKEN` | runner env | where the SWIM layer comes from and the token that makes it Tier 1 |
| `TAR1090_URL` | `dispatch.env` (added 2026-10-24, default empty) | the local tar1090 UI behind "Open full receiver map"; empty means no link; handed to trusted callers only because it names a box-local address |
| SWIM storage area | `src/ingest/parsers/geo_filter.py` constants (`_RADIUS_NM`, `CORE_AIRPORTS`), exposed by `storage_scope()` | the SWIM half of the map extent — see §7 |
| `DEMO_MODE` | runner env | synthetic aircraft only |

Map extent (pan limit, minimum zoom, the area the server will answer for) is
the union of the receiver range circle (`DEFAULT_DIST`, 250 nm) and the SWIM
storage area. It is **not** nationwide: FDPS storage is filtered by
`geo_filter.py` (250 nm around the primary airport plus the core-airport
list), so only what is stored can be drawn. A deployment that wants a wider
map widens that storage filter, which is a load and storage decision on small
hardware, not a map setting.

## 7. Public deployment rules

For anyone deploying the public repo:

1. **Default.** The map shows that deployer's own receiver (readsb /
   ultrafeeder `aircraft.json`) plus whatever else they have configured — a
   tar1090-style experience on their own data.
2. **Two ways to extend, both by configuration.**
   (A) Link out to a public aggregator, hex-only (`?icao=<hex>`; never a tail,
   flight number or callsign).
   (B) Their own FAA SWIM subscriptions (and any other subscriptions), set in
   their config files. With (B) they get the same map we run internally.
3. **SWIM redistribution.** A standard FAA SWIM data agreement does not permit
   redistribution or public display of the data, so SWIM-fed positions must
   not be pushed out publicly. That limit comes from the agreement terms.
   Separately, LADD/PIA blocking has a federal-law basis (FAA Reauthorization
   Act of 2024, sec. 803), which is why the dispatch endpoints filter any
   Tier-0 caller fail-closed. Anyone running a public-facing deployment must
   review their subscription's terms and, if they want public display, obtain
   a tier or agreement that allows it.
4. **Receiver-location protection for anything public-facing.** Do not expose
   exact receiver coordinates: in tar1090 / readsb set
   `READSB_RX_LOCATION_ACCURACY=0` (or a rounded value), `SiteShow=false`, no
   heywhatsthat panorama, range outline off, and strip `r_dst` / `r_dir` from
   any aircraft JSON served publicly. The runner already hands untrusted
   callers a public-airport placeholder instead of the receiver centre and
   withholds `tar1090_url`.
5. **Extent follows storage.** The map covers the deployer's SWIM storage
   area (`geo_filter.py`) plus receiver range; nationwide means widening the
   FDPS storage filter first (§6).

## 8. Known limits

- SWIM-only aircraft have no heading (round glyph) and update more sparsely
  than ADS-B; their position age is shown in the tooltip and list.
- Trails and "age" for vessels and drones are client-side (time since this
  client last saw the position change); the drone feed carries no timestamp.
- Trains: position age and heading come from the feed only when the writer
  passes them. `src/ingest/amtrak.py` now keeps amtraker's `heading`,
  `velocity_mph` and `position_updated` (2026-10-24), but the deployed push
  writer on this box is the `amtrak-tracker` container
  (`docs/DATA_SOURCES.md` §3), ~~which does not pass them~~ (2026-10-24: it
  now passes the same three fields, `src/amtrak_tracker/main.py`). A record
  written before that change has none of them; for it the train map derives
  direction from successive positions and has no per-train fix time.
- Trails are lost on page reload (no server history).
- The identity-conflict ring says the two sources disagree; it does not say
  which is wrong. SWIM is the tail authority by platform rule.
