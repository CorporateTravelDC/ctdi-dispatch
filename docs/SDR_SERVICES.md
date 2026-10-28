~~Verified against HEAD db64018 and live state on 2026-10-06 18:10Z / 14:10 ET.~~
(corrected 2026-10-15: re-verified against main 1491bf5a plus the batch staged
for the next relay, and against `podman ps`, the live quadlets, udev rules and
timers on 2026-10-15 ~01:10Z / 2026-10-14 21:10 ET. New sections: data flow,
outbound sharing, monitoring, host setup.)

# SDR Services — Current State & Enabling Disabled Containers

**Re-verified 2026-10-06** against `podman ps`, `lsusb`,
`/etc/udev/rules.d/99-rtlsdr-adsb.rules`, `ls /dev/rtl_sdr_*`, and the live
`.disabled` files (`find . -name '*.disabled'`). The enabled set is
unchanged since 2026-08-11/2026-08-23; `docs/SDR_SERVICES_README.md` stays
a pointer here.

> **Serial-naming convention reminder.** `systemd/corporatetraveldc-dumphfdl.container.disabled`
> and `systemd/corporatetraveldc-ais.container.disabled` still carry the
> superseded `hfdl0HF`/`ais0AIS` comment-serials and do not match any live
> udev rule. The live rule file and `systemd/quadlets/` variants use
> `{TYPE}{CENTERFREQ_4DIGIT}` (`HFDL0008`/`HFDL0011`/`HFDL0017`, `AIS0162`)
> — follow those, not the two superseded quadlets, when this hardware is
> acquired.

## Live state (re-confirmed 2026-10-06)

**Enabled and running** (`systemctl --user list-units`, `podman ps`, all
`active`/`Up` at check time):

| Quadlet | Role | Notes |
|---|---|---|
| `corporatetraveldc-ultrafeeder` | ADS-B 1090 MHz decode + tar1090 (:8080), ~~feeds FlightAware/FR24/airplanes.live/OpenSky MLAT~~ (corrected 2026-10-25: its own feed list sends only to airplanes.live, ADS-B and MLAT; FlightAware, FR24, OpenSky, PlaneFinder and AirNav Radar are fed by their own containers, which read its Beast output) (ADSBHub dropped 2026-10-15) | running (2026-10-15: tar1090 on `100.x.x.x:8080` and `:8081`; Beast `30005` published on all interfaces; SBS `127.0.0.1:30003`. Full feed list under "Outbound sharing") |
| `corporatetraveldc-acarsrouter` | ACARS/VDL2 message router (:9080) | running (2026-10-15: serves plain ACARS on TCP `9080` and VDL2 on TCP `15555`, both published on all interfaces; receives VDL2 from dumpvdl2 over ZMQ `45555`) |
| `corporatetraveldc-dumpvdl2` | VDL Mode 2 decoder — shares the `ACARS0130` dongle (`Environment=RTL_SERIAL=ACARS0130`, ~~whole-bus `AddDevice=/dev/bus/usb`, not the `/dev/rtl_sdr_acars` symlink~~ (corrected 2026-10-15: `AddDevice=/dev/rtl_sdr_acars` plus a read-only `/dev/bus/usb` volume, scoped since 2026-08-26; the image's start script is overlaid with the tracked `containers/dumpvdl2/launch.sh`), `FEED_ID` set in the quadlet) | running |
| `corporatetraveldc-acarshub` | ACARS web UI (~~127.0.0.1:9081~~ `100.x.x.x:8092`, tailnet only; corrected 2026-10-15 from the quadlet's `PublishPort`) | running |
| `corporatetraveldc-acars-watcher` | Triple-source watcher — local UDP 5005 + airframes.io REST + ACARS Drama Jumpseat REST. ~~The Jumpseat poller queries ACARS Drama by registration for every watched registration from `GET /api/v1/watchlist`~~ (2026-10-16, operator: keep the LADD list for Jumpseat -- the Jumpseat poller's list is ONLY `GET /api/v1/watchlist/registrations`, the flight-watch identifiers with every LADD-listed one removed by the dispatch API's production LADD filter (`common.db.faa_is_ladd` / `faa_lookup_by_hex`; the container has no database); a lookup error is 503 and the thread polls nothing, with a warning, rather than fall back to the full set; the local UDP and airframes.io threads keep the full set; filtered registrations are counted, never returned or logged) | custom local image, running. (2026-10-15: all three sources exist in code, but in practice only airframes.io produces watchlist matches: 3 `MATCH [AIRFRAMES` in the last 24 h; the 2026-10-12 audit counted 3 airframes, 0 local, 0 Jumpseat over 7 days. The router sends only VDL2 to UDP 5005, ~~and the watcher does not parse the registration nested inside a VDL2 message, so local VDL2 rarely matches a watched registration~~ (superseded; corrected 2026-10-25: since 2026-10-18 the watcher reads the registration nested in a dumpvdl2 message, `vdl2.avlc.acars.reg`, `src/acars_watcher/acars_watcher.py:194-201`; the journal shows no `MATCH` line from any source in the 24 h to 2026-10-26 02:36Z)) |
| `corporatetraveldc-piaware` / `-fr24feed` / `-planefinder` / `-airnavradar` | Aggregator feeders (FlightAware / FR24 / PlaneFinder / RadarBox) | running (2026-10-25: so is a fifth, `corporatetraveldc-opensky`, the OpenSky Network feeder; all five read Beast from the aggregator) |
| `corporatetraveldc-sdr-crashloop-guard.timer` | Every 5 min — checks ultrafeeder/dumpvdl2 for lost USB device or restart-loop, auto-stops either | active (its `.service` is `inactive dead` between runs, as expected for a oneshot) |

The local VDL2 messages also reach the database by a second path that does
not go through the watcher (see "Data flow" below).

**Still `.disabled` (hardware not yet acquired).** Serials below are the
real `RTL_SERIAL=` values in the Quadlets and the real placeholder serials
in `/etc/udev/rules.d/99-rtlsdr-adsb.rules`:

| Quadlet (repo path) | Dongle serial | Freq | Watcher |
|---|---|---|---|
| `.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled` (~~duplicate copy at `systemd/`,~~ plus an archived copy under `systemd/retired-20261003/`; corrected 2026-10-15: there is no copy at `systemd/`) | `ACARS0130` | 129–131 MHz | acars-watcher (UDP 5005) |
| `systemd/quadlets/corporatetraveldc-dumphfdl-{0008,0011,0017}.container.disabled` | `HFDL0008` / `HFDL0011` / `HFDL0017` | 8 / 11 / 17 MHz | ~~acars-watcher (UDP 5005)~~ none yet (corrected 2026-10-15: acarsrouter has no HFDL input, so HFDL would not reach UDP 5005 without a router change) |
| `systemd/corporatetraveldc-dumphfdl.container.disabled` — superseded by the three above | comments say `hfdl0HF`; no live udev rule matches it | 2–22 MHz HF | ~~acars-watcher (UDP 5005)~~ none yet (corrected 2026-10-15: same as the row above, acarsrouter has no HFDL input) |
| `systemd/quadlets/corporatetraveldc-ais-catcher.container.disabled` (AIS-catcher; NMEA/JSON on :8110) | `AIS0162` | 161–162 MHz | `systemd/corporatetraveldc-ais-watcher.container.disabled` (UDP 5006) |
| `systemd/corporatetraveldc-ais.container.disabled` — superseded by ais-catcher above | comments say `ais0AIS`; no live udev rule matches it | 161–162 MHz | same |
| `systemd/corporatetraveldc-utm-watcher.container.disabled` (OpenDroneID / Remote-ID drone watcher, `src/utm_watcher/`) | n/a — listens for UDP Remote-ID broadcasts | 2.4/5.8 GHz Remote-ID | utm-watcher itself |

(2026-10-15) The staged acarsdec quadlet uses `ACARS0130`, the dongle that
`dumpvdl2` already holds. Enabling it as written would fight `dumpvdl2` for
the device. It also uses whole-bus `AddDevice=/dev/bus/usb` and loads
`dispatch-secrets.env`.

None of the above exists in the live `~/.config/containers/systemd/`
(re-confirmed 2026-10-06: no `*.disabled` file there) — "disabled" means
"staged in the repo, never installed," not "installed and switched off."
~~A new, unrelated `.disabled` file now also exists at
`.config/containers/systemd/amtrak-tracker.container.disabled` — out of
scope for this doc (not an SDR service); not otherwise investigated here.~~
(2026-10-20: that stale 2026-07 amtrak draft is retired to
`.config/containers/systemd/retired-20261020/`; the live unit is `amtrak-tracker.container`.)
(Re-confirmed 2026-10-15: same staged set, none installed live.)

(2026-10-15) Two more staged watchers, for the record: the **ais-watcher**
(`src/ais_watcher/ais_watcher.py`) watches only the MMSIs in
`AIS_STATIC_MMSI` and does not read the platform's vessel watchlist. The
**utm-watcher** (`src/utm_watcher/`) is inert: no Remote ID receiver exists,
and its USS REST poller idles until `USS_API_BASE` and `USS_API_KEY` are set.

## Data flow (added 2026-10-15)

```
ADSB1090 dongle -> ultrafeeder (readsb/tar1090) -> aircraft.json
    -> ingest-core local_airspace.py (15 s poll) -> proximity alerts, local positions
    -> outbound feeds (below)
ACARS0130 dongle -> dumpvdl2 --ZMQ 45555--> acarsrouter
    -> TCP 15555 (VDL2) and TCP 9080 (plain ACARS; nothing produces it here)
         -> ingest-core local_airspace.py -> acars_messages -> ACARS/OOOI evidence
    -> UDP 5005 (VDL2 only) -> acars-watcher -> ntfy on a watched registration
    -> outbound feeds (below)
```

`acars_messages` held 93,190 rows on 2026-10-15, 9,986 of them from the last
24 h (2026-10-25: about 185,300 live rows on 2026-10-26 02:19Z, the
`pg_stat_user_tables` estimate). ACARS phase evidence is read by `common/acars.py` (Jumpseat, then
airframes.io, then local) for the pusher and poller OOOI logic. ADS-B never
asserts ON or IN. The watcher's own Jumpseat polling is LADD-filtered since
2026-10-16 (`GET /api/v1/watchlist/registrations`, row above); `common/acars.py`
already LADD-checked its Jumpseat lookups (`db.faa_is_ladd`, 2026-10-16).

## Outbound sharing (added 2026-10-15)

The receivers share data with third parties. MLAT networks receive the
receiver's precise position; feeder accounts carry station identifiers.
Credentials and feeder keys live in per-feeder files under
`/etc/corporatetraveldc/` (`piaware-`, `fr24feed-`, `planefinder-`,
`airnavradar-`, `ultrafeeder-secrets.env`). Names only here.

| From | Destination | What |
|---|---|---|
| ~~ultrafeeder~~ (corrected 2026-10-25: not the aggregator; FlightAware gets ADS-B from the `piaware` container, whose own endpoint was not read) | ~~`piaware.flightaware.com:30005`~~ | ADS-B (Beast reduced) |
| ~~ultrafeeder~~ (corrected 2026-10-25: not the aggregator; FR24 gets ADS-B from the `fr24feed` container, whose own endpoint was not read) | ~~`data-out.flightradar24.com:10001`~~ | ADS-B |
| ~~ultrafeeder~~ | ~~`data.adsbhub.org:5002`~~ | ~~ADS-B~~ (removed 2026-10-15: operator dropped ADSBHub; its server had dropped the feed every ~16 s since 10-14) |
| ultrafeeder | `feed.airplanes.live:30004` | ADS-B |
| ~~ultrafeeder~~ (corrected 2026-10-25: not in the aggregator's feed list; OpenSky is fed by the `opensky` container, whose own endpoint was not read) | ~~`collector.opensky-network.org:30003`~~ | ~~MLAT (receiver position)~~ |
| ultrafeeder | `feed.airplanes.live:31090` | MLAT (receiver position) |
| piaware, fr24feed, planefinder, airnavradar containers (2026-10-25: and the `opensky` container) | FlightAware, FR24, PlaneFinder, AirNav Radar (2026-10-25: and OpenSky Network) | their own feeder clients |
| acarsrouter | `feed.airframes.io:5553` (TCP) | VDL2 |
| acarsrouter | `feedthe.acarsdrama.com:5555` (UDP) | VDL2 |
| acarsrouter | `feedthe.acarsdrama.com:5550`, `feed.airframes.io:5550` (UDP) | plain ACARS (none produced today) |

## Monitoring (added 2026-10-15)

| Timer | State 2026-10-15 |
|---|---|
| `corporatetraveldc-sdr-crashloop-guard.timer` | enabled, active |
| `corporatetraveldc-adsb-feed-silence-watchdog.timer` | ~~**disabled**~~ (superseded; corrected 2026-10-25: enabled, active, every 5 min at :01, 2026-10-26 02:15Z) |
| `corporatetraveldc-acars-feed-silence-watchdog.timer` | ~~**disabled**~~ (superseded; corrected 2026-10-25: enabled, active, every 5 min, 2026-10-26 02:15Z) |
| `corporatetraveldc-adsb-link-watchdog.timer` | ~~**disabled**~~ (superseded; corrected 2026-10-25: enabled, active, every 5 min, 2026-10-26 02:15Z) |

~~Only the crash-loop guard runs. A receiver that stays up but goes quiet
(antenna, cable, feed silence) raises no alert today.~~ (superseded; corrected
2026-10-25: all four run. The two feed-silence watchdogs and the link
watchdog page `ops-health` when a receiver stays up but goes quiet or its
link flaps.)

## Host setup (added 2026-10-15)

A new deployment needs, besides the udev rules below:

- `/etc/modprobe.d/99-rtlsdr-blacklist.conf` (repo: `config/99-rtlsdr-blacklist.conf`),
  which keeps the kernel DVB driver off the dongles; installed here.
- The SELinux module `selinux/corporatetraveldc-sdr-usb.te` (built by
  `selinux/apply-selinux-policy.sh`) for container USB access.

## Enabling a disabled service

> (2026-10-15) Read before using the commands below. The `podman build` lines
> bypass the build policy (no `--platform`, no provenance record). The staged
> ais-watcher and utm-watcher quadlets load the master `dispatch-secrets.env`
> and join `corporatetraveldc.network`, which does not exist. The acarsdec
> quadlet conflicts with dumpvdl2 (see above). Each one needs its quadlet
> brought up to the current conventions first: scoped env file, scoped
> device, an existing network, a policy build.

```bash
# note the repo-side source path differs per service -- see the table above
cp <repo-path>/<service>.container.disabled \
   ~/.config/containers/systemd/<service>.container
systemctl --user daemon-reload
systemctl --user start <service>
```

Decoder containers pull upstream images — no build needed. The watcher
containers are custom local builds:

```bash
# ACARS/VDL2/HFDL watcher (already built & running)
podman build -t localhost/corporatetraveldc-acars-watcher:latest src/acars_watcher/

# AIS vessel watcher (build when AIS dongle arrives)
podman build -t localhost/corporatetraveldc-ais-watcher:latest src/ais_watcher/
```

## Dongle serialization

Each dongle gets a unique EEPROM serial so udev can create stable symlinks
(~~`rtl-eeprom-reserialize.sh`, run with only the target dongle connected~~
corrected 2026-10-15: `scripts/rtl-eeprom-reserialize.sh` is a historical
one-off. It renames the ACARS dongle to `vdl20130`, which no live udev rule
or quadlet uses, so running it as written would break the VDL2 chain. Program
a new dongle with `rtl_eeprom -s <SERIAL>` by hand, with only that dongle
connected. `scripts/deploy-acars-stack.sh` is obsolete as well, and is not
to be run: it builds from inputs that no longer exist and ignores the boot
stagger).

Live 2026-10-06 (`lsusb` shows two Realtek RTL2838s; `ls -l /dev/rtl_sdr_*`
shows both symlinks resolved; same on 2026-10-15): **`ADSB1090`** → `/dev/rtl_sdr_adsb`, and
**`ACARS0130`** → `/dev/rtl_sdr_acars` — both still correctly keyed, though
the underlying bus paths have moved again since 2026-08-23 (see
`docs/HARDWARE_GUIDANCE.md`'s live USB table — bus path is not the
stable contract, the serial is). Naming convention: `{TYPE}{CENTERFREQ_4DIGIT}`.
Planned serials, already written as commented placeholders in the live
rules file: `HFDL0008`, `HFDL0011`, `HFDL0017`, `AIS0162` — use these, not
the `hfdl0HF`/`ais0AIS` forms in the two superseded staged quadlets. There
is no separate VDL2 dongle planned — `dumpvdl2` shares `ACARS0130`.

Live rules, verbatim from `/etc/udev/rules.d/99-rtlsdr-adsb.rules`
(re-confirmed 2026-10-06, unchanged; identical to the repo copy on 2026-10-15):

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", ATTRS{serial}=="ADSB1090", SYMLINK+="rtl_sdr_adsb", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", ATTRS{serial}=="ACARS0130", SYMLINK+="rtl_sdr_acars", MODE="0666"
# HFDL0008 / HFDL0011 / HFDL0017 / AIS0162 lines are present but commented out

sudo udevadm control --reload-rules && sudo udevadm trigger
```

(The repo `udev/` directory carries the reference rules file.)

## Notes

- ~~ACARS, VDL2, and HFDL all feed the same `acars-watcher` via UDP 5005 —
  same message shape (registrations, OOOI, flight data).~~ (corrected
  2026-10-15: only VDL2 reaches UDP 5005 (`AR_SEND_UDP_VDLM2`). The router
  sends plain ACARS only to the outbound feeds, and it has no HFDL input.)
- AIS uses MMSI vessel identifiers and its own `ais-watcher` on UDP 5006;
  the watchlist's vessel sweep (AISHub) works independently of local AIS
  receive — see `src/shared/watchlist_README.md`.
- HFDL needs an HF antenna (long wire / end-fed), not the VHF stub.
- `AIS_STATIC_MMSI` and `ACARS_STATIC_REGS` can pin specific
  vessels/registrations independent of the OOOI watchlist, but neither is
  set today — re-confirmed live 2026-10-06
  (`grep -c '^AIS_STATIC_MMSI=\|^ACARS_STATIC_REGS=' /etc/corporatetraveldc/dispatch.env`
  → `0`; names only, values never read). Both default to the empty string
  in code (~~`src/acars_watcher/acars_watcher.py:49`~~ (corrected 2026-10-25:
  `src/acars_watcher/acars_watcher.py:76` at 5f7fca01),
  `src/ais_watcher/ais_watcher.py:32`).

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-ops.md`.


### SDR Services — Current State & Enabling Disabled Containers

~~**Rewritten 2026-08-11 against the live Quadlet directory.** The previous revision (2026-06-14) claimed *all* SDR decode/watch services ship as `.disabled` — no longer true: most of the SDR stack has been enabled and running for weeks. `docs/SDR_SERVICES_README.md` was an identical duplicate of this file and is now just a pointer here.~~

~~**Re-verified 2026-08-23** against `podman ps`, `lsusb`, `/etc/udev/rules.d/99-rtlsdr-adsb.rules`, `ls /dev/rtl_sdr_*`, and the actual `.disabled` files (`find . -name '*.disabled'`). The enabled table below held up unchanged. Corrected below: the **dongle serials in the disabled table were stale**, and the `.disabled` file paths were understated.~~

> ~~**Why the serials were wrong, and the conflict to be aware of.** The old table's `hfdl0HF` / `ais0AIS` were not invented — they are real strings, but they only appear in the *setup comments* of two early staged quadlets, `systemd/corporatetraveldc-dumphfdl.container.disabled` and `systemd/corporatetraveldc-ais.container.disabled`. Those two predate the `systemd/quadlets/` generation and predate the live udev rules file, which uses an entirely different, documented convention: `{TYPE}{CENTERFREQ_4DIGIT}` → `HFDL0008`/`HFDL0011`/`HFDL0017` and `AIS0162`. **The udev rules file and the `systemd/quadlets/` variants are the ones to follow** — they are what a new dongle would actually be programmed and matched against. Serializing a dongle as `hfdl0HF` per those two older comments would produce a device no live udev rule matches. Worth reconciling or deleting the two stale quadlets when this hardware is actually acquired.~~

**~~Live state (enabled set verified 2026-08-11, re-confirmed 2026-08-23)~~** *(former heading)*


### SDR Services — Current State & Enabling Disabled Containers › Live state (enabled set verified 2026-08-11, re-confirmed 2026-08-23)

~~**Enabled and running:**~~

| ~~Quadlet~~ | ~~Role~~ | ~~Notes~~ |
|---|---|---|
| ~~`corporatetraveldc-ultrafeeder`~~ | ~~ADS-B 1090 MHz decode + tar1090 (:8080), feeds FlightAware/FR24/adsbhub/airplanes.live/OpenSky MLAT~~ | ~~✅ Restored midday 2026-08-11 after a hardware reseat — the `ADSB1090` dongle had stopped enumerating on USB (~2026-08-10, container crash-looping on missing `/dev/rtl_sdr_adsb`); `adsb-feed-silence-watchdog` alerted correctly throughout. Both dongles enumerate again; live decode confirmed.~~ |
| ~~`corporatetraveldc-acarsrouter`~~ | ~~ACARS/VDL2 message router (:9080)~~ |  |
| ~~`corporatetraveldc-dumpvdl2`~~ | ~~VDL Mode 2 decoder — shares the `ACARS0130` dongle (`Environment=RTL_SERIAL=ACARS0130`, whole-bus `AddDevice=/dev/bus/usb`, not the `/dev/rtl_sdr_acars` symlink)~~ |  |
| ~~`corporatetraveldc-acarshub`~~ | ~~ACARS web UI (127.0.0.1:9081)~~ |  |
| ~~`corporatetraveldc-acars-watcher`~~ | ~~Triple-source watcher — local UDP 5005 + airframes.io REST + ACARS Drama Jumpseat REST~~ | ~~custom local image~~ |
| ~~`corporatetraveldc-piaware` / `-fr24feed` / `-planefinder` / `-airnavradar`~~ | ~~Aggregator feeders (FlightAware / FR24 / PlaneFinder / RadarBox)~~ |  |

~~**Still `.disabled` (hardware not yet acquired).** Serials below are the real `RTL_SERIAL=` values in the Quadlets and the real placeholder serials in `/etc/udev/rules.d/99-rtlsdr-adsb.rules` — re-derive with `grep -rn RTL_SERIAL systemd/` rather than trusting a doc:~~

| ~~Quadlet (repo path)~~ | ~~Dongle serial~~ | ~~Freq~~ | ~~Watcher~~ |
|---|---|---|---|
| ~~`.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled` (duplicate copy at `systemd/`)~~ | ~~`ACARS0130`~~ | ~~129–131 MHz~~ | ~~acars-watcher (UDP 5005)~~ |
| ~~`systemd/quadlets/corporatetraveldc-dumphfdl-{0008,0011,0017}.container.disabled`~~ | ~~`HFDL0008` / `HFDL0011` / `HFDL0017` (real `Environment=RTL_SERIAL=`)~~ | ~~8 / 11 / 17 MHz~~ | ~~acars-watcher (UDP 5005)~~ |
| ~~`systemd/corporatetraveldc-dumphfdl.container.disabled` — **superseded** by the three above~~ | ~~comments say `hfdl0HF`; no live udev rule matches it~~ | ~~2–22 MHz HF~~ | ~~acars-watcher (UDP 5005)~~ |
| ~~`systemd/quadlets/corporatetraveldc-ais-catcher.container.disabled` (AIS-catcher; NMEA/JSON on :8110)~~ | ~~`AIS0162` (commented `AddDevice=/dev/rtl_sdr_ais0162`)~~ | ~~161–162 MHz~~ | ~~`systemd/corporatetraveldc-ais-watcher.container.disabled` (UDP 5006)~~ |
| ~~`systemd/corporatetraveldc-ais.container.disabled` — **superseded** by ais-catcher above~~ | ~~comments say `ais0AIS`; no live udev rule matches it~~ | ~~161–162 MHz~~ | ~~same~~ |
| ~~`systemd/corporatetraveldc-utm-watcher.container.disabled` (OpenDroneID / Remote-ID drone watcher, `src/utm_watcher/`)~~ | ~~n/a — listens for UDP Remote-ID broadcasts (no RTL-SDR serial assigned yet)~~ | ~~2.4/5.8 GHz Remote-ID (WiFi/BT beacons)~~ | ~~utm-watcher itself (its UDP parser is defensive/best-effort pending real captures; the USS REST poller is an honest inert stub)~~ |

~~Note the three locations: **only** the `acarsdec` one is staged in `.config/containers/systemd/`; everything else sits under `systemd/` or `systemd/quadlets/`. None of them exists in the live `~/.config/containers/systemd/` (confirmed 2026-08-23: `ls ~/.config/containers/systemd/*.disabled` → no matches), so "disabled" here means "staged in the repo, never installed", not "installed and switched off".~~


### SDR Services — Current State & Enabling Disabled Containers › Enabling a disabled service

*Superseded block:*
```text superseded
# note the repo-side source path differs per service — see the table above
cp <repo-path>/<service>.container.disabled \
   ~/.config/containers/systemd/<service>.container
systemctl --user daemon-reload
systemctl --user start <service>
```


### SDR Services — Current State & Enabling Disabled Containers › Dongle serialization

~~Each dongle gets a unique EEPROM serial so udev can create stable symlinks (`rtl-eeprom-reserialize.sh`, run with only the target dongle connected):~~

~~Live today, both confirmed 2026-08-23 (`lsusb` shows two Realtek RTL2838s; `ls -l /dev/rtl_sdr_*` shows both symlinks resolved): **`ADSB1090`** → `/dev/rtl_sdr_adsb`, and **`ACARS0130`** → `/dev/rtl_sdr_acars`. The naming convention, stated in the rules file itself, is `{TYPE}{CENTERFREQ_4DIGIT}`. Planned serials, already written as commented placeholders in the live rules file: `HFDL0008`, `HFDL0011`, `HFDL0017`, `AIS0162` — **use these, not the `hfdl0HF`/`ais0AIS` forms in the two superseded staged quadlets** (see the callout at the top of this file). There is **no** separate VDL2 dongle planned — `dumpvdl2` shares `ACARS0130`.~~

~~Live rules, verbatim from `/etc/udev/rules.d/99-rtlsdr-adsb.rules` (note the `idProduct` match and `MODE="0666"`, both required — an earlier revision of this doc omitted them):~~

~~(The repo `udev/` directory carries the reference rules file. Bus paths are **not** stable across reboots — the serial→symlink mapping is the contract; see `docs/HARDWARE_GUIDANCE.md` for the current live bus map.)~~


### SDR Services — Current State & Enabling Disabled Containers › Notes

- ~~ACARS, VDL2, and HFDL all feed the same `acars-watcher` via UDP 5005 — same message shape (registrations, OOOI, flight data).~~
- ~~AIS uses MMSI vessel identifiers and its own `ais-watcher` on UDP 5006; the watchlist's vessel sweep (AISHub) works independently of local AIS receive — see `src/shared/watchlist_README.md`.~~
- ~~HFDL needs an HF antenna (long wire / end-fed), not the VHF stub.~~
- ~~`AIS_STATIC_MMSI` and `ACARS_STATIC_REGS` can pin specific vessels/registrations independent of the OOOI watchlist — but **neither is actually set today** (verified 2026-08-23: `grep -c 'AIS_STATIC_MMSI\|ACARS_STATIC_REGS' /etc/corporatetraveldc/dispatch.env` → `0` for both, and neither appears in the live `corporatetraveldc-acars-watcher.container`). Both default to the empty string in code (`src/acars_watcher/acars_watcher.py:49`, `src/ais_watcher/ais_watcher.py:32`), so the feature is supported and wired but currently inert. Add them to `dispatch.env` (bare values, no quotes — see CLAUDE.md's `EnvironmentFile` quoting gotcha) to use it.~~
