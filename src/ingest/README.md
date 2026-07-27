# corporatetraveldc — Ingest Container

The ingest container connects to FAA SWIM data feeds and pushes events
into the shared SQLite database. The poller's REST fallback activates
automatically whenever ingest is not stamping heartbeats.

## NMS/Solace credentials -- LIVE as of 2026-07-20

Credentials arrived and are configured; this section's "pending" framing is
stale and kept only for the setup steps below (still accurate if ever
reprovisioning from scratch). All six SWIM feeds (fdps, stdds, tfms, tbfm,
itws, and fns/aim for NOTAMs) are actively connected and receiving live
traffic. **Real per-parser status, 2026-07-20** (see each parser's own
module docstring for full schema details):

| Feed  | Parser            | Status                                                                 |
|-------|-------------------|-------------------------------------------------------------------------|
| TBFM  | tbfm_parser.py    | FIXED, live -- tbfm_sequences populating, real metering ntfy alerts     |
| STDDS/TAIS | smes_parser.py (TAIS path) | FIXED, live -- terminal_tracks populating          |
| STDDS/SMES | smes_parser.py (SMES path) | FIXED, live -- surface_tracks populating (real `asdexMsg` schema, confirmed against FAA's FIXM-Mediated STDDS Data Overview doc + a live captured sample) |
| TFMS  | tfms_parser.py    | PARTIALLY fixed -- RSTR (restriction) msgType fully live in nas_programs (real ground stops/MIT restrictions). TMI_FLIGHT_LIST/trackInformation/flightPlanInformation/APTC/GADV msgTypes are confirmed-schema but stubbed, pending design decisions (OOOI-coupling for per-flight data, CPS integration for APTC, new table for GADV) |
| ITWS  | itws_parser.py    | Parse-error bug fixed (stray unescaped `<` in text content); 13+ product types confirmed and taxonomized (simple alert-style vs. raster-heavy), dispatcher/handlers not yet built |
| FDPS  | fdps_parser.py    | NOT fixed -- confirmed the live feed is FIXM 3.0, not the FIXM 4.2 the parser was written against (a genuinely different message model, confirmed against the official SFDPS Data Consumer Reference Manual). Version-detection scaffold in place (`_detect_fixm_version`), old 4.2 logic preserved as `_parse_fdps_message_fixm42_legacy`, FIXM 3.0 field mapping is a stub (`_parse_fdps_message_fixm30`) pending a dedicated rewrite session |

**To enable live SWIM from a fresh setup (credentials lost/reprovisioning):**

1. Add credentials to `/etc/corporatetraveldc/dispatch-secrets.env`:
   ```
   SWIM_NMS_USER_FDPS=<your-fdps-username>
   SWIM_NMS_PASS_FDPS=<your-fdps-password>
   SWIM_NMS_QUEUE_FDPS=<your-queue-name>

   SWIM_NMS_USER_STDDS=<your-stdds-username>
   SWIM_NMS_PASS_STDDS=<your-stdds-password>
   SWIM_NMS_QUEUE_STDDS=<your-queue-name>
   ```

2. Verify the host and VPN names in `/etc/corporatetraveldc/dispatch.env`
   match what FAA provisioned:
   ```
   SWIM_NMS_HOST=tcps://ems2.swim.faa.gov:55443
   SWIM_NMS_VPN_FDPS=FDPS
   SWIM_NMS_VPN_STDDS=STDDS
   ```

3. Rebuild and restart the ingest container:
   ```bash
   cd /opt/corporatetraveldc
   bash build-images.sh
   systemctl --user daemon-reload
   systemctl --user restart corporatetraveldc-ingest
   systemctl --user status corporatetraveldc-ingest
   ```

4. Confirm the heartbeat is stamping:
   ```bash
   curl http://localhost:8000/api/v1/feeds | jq '.feeds[] | select(.feed_name | startswith("push:"))'
   ```

## Feed heartbeat contract

- Ingest stamps `push:fdps` and `push:stdds` in `feed_state` every 30s while connected.
- Poller checks `push_is_healthy(feed, max_age=90s)` before each REST poll.
- If ingest disconnects → heartbeat ages out → poller resumes REST automatically.
- **Do NOT stamp** `push:metar`, `push:nws`, `push:tfr`, `push:nas`, `push:ops_plan`,
  `push:amtrak` — those are poller-owned feeds.

## Owned feed names

`fdps`, `stdds`

## Message types parsed

`FH`/`TH`/`CL`/`HP`/`OH`/`HZ` below are the FIXM 4.2 legacy source-type
model fdps_parser.py was originally written against -- kept for reference
in `_parse_fdps_message_fixm42_legacy`, but NOT what's actually live (see
table above: live feed is FIXM 3.0, a different message model entirely,
still stubbed as of 2026-07-20).

| Source | Feed  | What it carries                            | Live? |
|--------|-------|---------------------------------------------|-------|
| `FH`   | FDPS  | Full flight plan (origin, dest, type)        | No -- legacy 4.2 model |
| `TH`   | FDPS  | Track position (lat, lon, alt, speed)        | No -- legacy 4.2 model |
| `CL`   | FDPS  | Cancellation                                 | No -- legacy 4.2 model |
| `HP/OH`| FDPS  | Handoff events                               | No -- legacy 4.2 model |
| `HZ`   | FDPS  | Heartbeat position (altitude skipped)        | No -- legacy 4.2 model |
| `asdexMsg` (positionReport/mlatReport/adsbReport) | STDDS/SMES | ASDE-X surface tracks at DCA/IAD/BWI | **Yes**, live 2026-07-20 |
| `TATrackAndFlightPlan` | STDDS/TAIS | Terminal radar tracks (PCT TRACON, though captures so far have mostly shown other TRACONs) | **Yes**, live 2026-07-20 |
| `RSTR` (restrictionMessage) | TFMS | GDP/GS/MIT restrictions (program_id, facility, airports, category, mit value, reason) | **Yes**, live 2026-07-20 |
| `TMI_FLIGHT_LIST`/`FlightModify`/`trackInformation`/`flightPlanInformation` | TFMS | Per-flight TMI/reroute/track/flight-plan data | No -- stubbed, OOOI-coupling planned |
| `APTC`/`GADV` | TFMS | Airport config/rates; ATCSCC general advisories | No -- stubbed, design pending |
| various (Microburst/Wind Shear/Tornado ATIS, Terminal Weather Text, Precipitation raster, etc.) | ITWS | Terminal weather products, 13+ distinct types | No -- taxonomized, not parsed |

---

## Local airspace monitoring (UltraFeeder ADS-B + ACARS)

`local_airspace.py` runs inside this container and handles two local RF feeds.
It starts automatically alongside SWIM/NWWS; sources degrade gracefully if unavailable.

### Hardware prerequisites

**Step 1 — Tag dongles by serial number** (one-time, dongles must be idle):

```bash
# Stop any rtl-tcp / dump1090 processes first
rtl_eeprom -d 0 -s ADSB1090
rtl_eeprom -d 1 -s ACARS0130
# Unplug and replug both dongles, then verify:
rtl_test -d ADSB1090 -t
rtl_test -d ACARS0130 -t
```

**Step 2 — Stable udev symlinks** (requires sudo):

Create `/etc/udev/rules.d/99-rtlsdr.rules`:

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", \
  ATTRS{serial}=="ADSB1090", SYMLINK+="rtl_sdr_adsb", MODE="0664", GROUP="plugdev"

SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", \
  ATTRS{serial}=="ACARS0130", SYMLINK+="rtl_sdr_acars", MODE="0664", GROUP="plugdev"
```

Then:

```bash
sudo udevadm control --reload && sudo udevadm trigger
ls -la /dev/rtl_sdr_adsb /dev/rtl_sdr_acars
sudo usermod -aG plugdev corporatetraveldc
```

### Deploying UltraFeeder (ADS-B)

```bash
systemctl --user daemon-reload
systemctl --user start corporatetraveldc-ultrafeeder
# Verify tar1090 web UI:
curl http://localhost:8080/data/aircraft.json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(len(d.get('aircraft',[])), 'aircraft')"
```

`ULTRAFEEDER_URL=http://host.containers.internal:8080` is already set in `dispatch.env`.
Restart the pusher after UltraFeeder is confirmed running:

```bash
systemctl --user restart corporatetraveldc-pusher
```

The pusher flight monitor will automatically prefer UltraFeeder over airplanes.live.

### Deploying ACARS (VHF)

```bash
systemctl --user start corporatetraveldc-acarsrouter
systemctl --user start corporatetraveldc-acarsdec
# Verify TCP router accessible from ingest:
# (inside ingest container or from host)
nc -zv host.containers.internal 9080
```

`local_airspace.py` connects to the ACARS router via TCP on port 9080
(`ACARS_ROUTER_HOST` / `ACARS_ROUTER_PORT` in `dispatch.env`).
ACARS is bursty — messages may take minutes to appear; check `acars_messages` table:

```bash
sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db \
  "SELECT received_at, tail, flight, label, msg_text FROM acars_messages \
   ORDER BY id DESC LIMIT 10;"
```

### Verifying heartbeats

After startup, heartbeat files appear within 30 seconds of each feed being reachable:

```bash
ls -la /var/lib/corporatetraveldc/feed_state/ultrafeeder.heartbeat \
       /var/lib/corporatetraveldc/feed_state/acars.heartbeat
```

### Alert routing

| Event | ntfy topics | Priority |
|-------|-------------|---------|
| Watchlist aircraft in range (≤30nm) | `flight-alerts` + `dispatch` | 4 |
| Marine One / VIP callsign (≤50nm) | `dispatch` only | 5 |
| Emergency squawk 7700/7500/7600 | `dispatch` | 4 |
| ACARS OOOI event for watched flight | `flight-alerts` + `dispatch` | 3–4 |

5-minute deduplication prevents re-firing the same alert per ICAO hex.

### Owned feed names

`ultrafeeder`, `acars`
