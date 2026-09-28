# GPS coordinate configuration — self-hosting guidance

## The short version

If you're running this platform against a real ADS-B receiver
(UltraFeeder/readsb), **set your receiver's real GPS coordinates before
going live.** Two places, both required:

1. `/etc/corporatetraveldc/dispatch-secrets.env`:
   ```
   ULTRAFEEDER_LAT=<your latitude>
   ULTRAFEEDER_LON=<your longitude>
   ```
2. The UltraFeeder Quadlet's own receive/decode config — `READSB_LAT`,
   `READSB_LON`, `TAR1090_DEFAULTCENTERLAT`, `TAR1090_DEFAULTCENTERLON`
   (also sourced from `dispatch-secrets.env` via `EnvironmentFile=` —
   see `.config/containers/systemd/corporatetraveldc-ultrafeeder.container`).
   Point these at the same real coordinates.

Left unset, the platform still runs — `runner/main.py`'s `DEFAULT_LAT`/
`DEFAULT_LON` and `web/main.py`'s `_ADSB_DEFAULT_LAT`/`_ADSB_DEFAULT_LON`
fall back to a generic Washington-DC-area placeholder — but:

- Every distance-from-you calculation, the ARIA compass-quadrant summary,
  and the tactical map's range rings will be wrong for your actual site.
- The embedded globe.airplanes.live iframe's native **"H"/home key and
  per-aircraft distance columns** will be wrong (see "Why the H button
  specifically" below).
- **MLAT positioning accuracy depends on the receiver's own site position
  being correct** — this isn't just a display issue, multilateration math
  uses the configured site coordinates as a fixed timing reference.
- Your feed registration with aggregators (airplanes.live, FlightAware,
  FR24, PlaneFinder, AirNav Radar, OpenSky) will carry the wrong location.

## Why this needs two separate settings, not one

`ULTRAFEEDER_LAT`/`ULTRAFEEDER_LON` feed the **display/UI layer** — the
runner's own React frontend (map centers, compass bearings, the
globe.airplanes.live embed's `SiteLat`/`SiteLon`). `READSB_LAT`/
`READSB_LON`/`TAR1090_DEFAULTCENTERLAT`/`TAR1090_DEFAULTCENTERLON` feed
the **receiver itself** — readsb's own MLAT math and tar1090's own
`receiver.json`. They should always agree, but they're read by different
processes at different times, so both need setting explicitly; setting
one does not imply the other.

## The one place the frontend is allowed to read this from

**`GET /api/v1/frontend-config`** (`src/runner/main.py`) is the sole
source of truth for the React frontend — it returns `receiver_lat`/
`receiver_lon`, sourced from `DEFAULT_LAT`/`DEFAULT_LON`, which read
`ULTRAFEEDER_LAT`/`ULTRAFEEDER_LON`. `src/runner/frontend/src/hooks/
useReceiverLocation.js` is the one hook that fetches it; every map/compass
component consumes the coordinate through that hook, never a literal.

**If you're extending the UI: never hardcode a GPS literal into a
`.jsx`/`.js` file.** This repo has a real public GitHub mirror
(`scripts/scrub-public-tree.py`), and that script's fixed-literal
substitution list is a backstop for values it already knows about — it
cannot catch a new or rotated coordinate pasted directly into a tracked
source file. Read from `useReceiverLocation()` (or, server-side, from
`DEFAULT_LAT`/`DEFAULT_LON` / `_ADSB_DEFAULT_LAT`/`_ADSB_DEFAULT_LON`,
which themselves only ever read the env var, never hardcode a real
address) instead.

## Why the "H" button specifically

tar1090 (which powers both the embedded globe.airplanes.live iframe and
UltraFeeder's own local web UI) determines its **"H"/home key and
per-aircraft distance-from-you columns** from a `SiteLat`/`SiteLon`
value — **not** from `centerlat`/`centerlon`, which only move the initial
map camera on load. Passing only `centerlat`/`centerlon` (an easy mistake
— it's the more obviously-named pair) leaves the H button and distance
columns keyed off whatever tar1090's own `geoFindMe()` browser-geolocation
last cached in that iframe origin's `localStorage`, which can silently
disagree with your real receiver location for a long time before anyone
notices. `src/runner/frontend/src/components/MapView.jsx`'s
`buildGlobeUrl()` sends `SiteLat`/`SiteLon` **and** `SiteClear=1` (to
purge any such stale cached value) on every embed load, specifically to
close this gap.

If you navigate to a local tar1090 instance directly (bypassing the
runner's own embed — e.g. a vhost proxying straight to the UltraFeeder
container's port 80/8080) and it's showing a wrong "home" location despite
correct `READSB_LAT`/`READSB_LON`, the same `localStorage`-persistence
mechanism is almost certainly why: visit it once with `?SiteClear=1`
appended to the URL to purge the stale cached value.

## Incident this codified (2026-08-24)

Before this was centralized, four independent places disagreed about the
receiver's location: `ULTRAFEEDER_LAT`/`LON` in `dispatch.env` (the true
current address), `READSB_LAT`/`LAT`/`TAR1090_DEFAULTCENTERLAT` in
`dispatch-secrets.env` (a stale former address), and two more hardcoded
literal placeholders baked directly into `MapView.jsx` and
`useCompassSummary.js` (neither of which matched either real address).
Separately, the runner's embedded map was only ever setting `centerlat`/
`centerlon` on its globe.airplanes.live embed, never `SiteLat`/`SiteLon`
— so even after the location values were reconciled, the "H" button and
distance columns kept showing a stale, browser-geolocation-derived
location (reported live as "it looks like I'm in New York") until the
embed URL itself was fixed. See `CLAUDE.md`'s dated entries around
2026-08-24 for the full narrative if you need it; this document is the
durable reference going forward.
