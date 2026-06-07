# corporatetraveldc — Ingest Container

The ingest container connects to FAA SWIM data feeds and pushes events
into the shared SQLite database. The poller's REST fallback activates
automatically whenever ingest is not stamping heartbeats.

## NMS/Solace credentials (pending FAA provisioning)

The NMS feeds (FDPS, STDDS) require credentials issued by the FAA SWIM
program office. Until they arrive the container starts cleanly, logs
`pending_credentials`, and the poller continues polling REST.

**To enable live SWIM once credentials arrive:**

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

| Source | Feed  | What it carries                            |
|--------|-------|--------------------------------------------|
| `FH`   | FDPS  | Full flight plan (origin, dest, type)      |
| `TH`   | FDPS  | Track position (lat, lon, alt, speed)      |
| `CL`   | FDPS  | Cancellation                               |
| `HP/OH`| FDPS  | Handoff events                             |
| `HZ`   | FDPS  | Heartbeat position (altitude skipped)      |
| SMES   | STDDS | ASDE-X surface tracks at DCA/IAD/BWI       |
| TAIS   | STDDS | Terminal radar tracks (PCT TRACON)         |

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
