# SDR Services — Current State & Enabling Disabled Containers

**Rewritten 2026-08-11 against the live Quadlet directory.** The previous
revision (2026-06-14) claimed *all* SDR decode/watch services ship as
`.disabled` — no longer true: most of the SDR stack has been enabled and
running for weeks. `docs/SDR_SERVICES_README.md` was an identical duplicate
of this file and is now just a pointer here.

## Live state (verified 2026-08-11)

**Enabled and running:**

| Quadlet | Role | Notes |
|---|---|---|
| `corporatetraveldc-ultrafeeder` | ADS-B 1090 MHz decode + tar1090 (:8080), feeds FlightAware/FR24/adsbhub/airplanes.live/OpenSky MLAT | ✅ Restored midday 2026-08-11 after a hardware reseat — the `ADSB1090` dongle had stopped enumerating on USB (~2026-08-10, container crash-looping on missing `/dev/rtl_sdr_adsb`); `adsb-feed-silence-watchdog` alerted correctly throughout. Both dongles enumerate again; live decode confirmed. |
| `corporatetraveldc-acarsrouter` | ACARS/VDL2 message router (:9080) | |
| `corporatetraveldc-dumpvdl2` | VDL Mode 2 decoder (dongle `ACARS0130`-family) | |
| `corporatetraveldc-acarshub` | ACARS web UI (127.0.0.1:9081) | |
| `corporatetraveldc-acars-watcher` | Dual-source watcher — local UDP 5005 + airframes.io REST | custom local image |
| `corporatetraveldc-piaware` / `-fr24feed` / `-planefinder` / `-airnavradar` | Aggregator feeders (FlightAware / FR24 / PlaneFinder / RadarBox) | |

**Still `.disabled` (hardware not yet acquired):**

| Quadlet | Dongle serial | Freq | Watcher |
|---|---|---|---|
| `corporatetraveldc-acarsdec.container.disabled` | `acars0130` | 129–131 MHz | acars-watcher (UDP 5005) |
| `corporatetraveldc-dumphfdl.container.disabled` (+ `-0008`/`-0011`/`-0017` variants under `systemd/quadlets/`) | `hfdl0HF` | 2–22 MHz HF | acars-watcher (UDP 5005) |
| `corporatetraveldc-ais.container.disabled` (+ `corporatetraveldc-ais-catcher.container.disabled`) | `ais0AIS` | 161–162 MHz | `corporatetraveldc-ais-watcher.container.disabled` (UDP 5006) |

## Enabling a disabled service

```bash
cp <service>.container.disabled ~/.config/containers/systemd/<service>.container
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
(`rtl-eeprom-reserialize.sh`, run with only the target dongle connected):

Live today: `ADSB1090` (ADS-B — enumerating again after the 2026-08-11
reseat, see above) and the ACARS dongle. Planned: `vdl20130`, `hfdl0HF`,
`ais0AIS`.

```
# /etc/udev/rules.d/99-rtlsdr.rules (pattern)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="ADSB1090", SYMLINK+="rtl_sdr_adsb"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="ACARS0130", SYMLINK+="rtl_sdr_acars"
# add hfdl0HF / ais0AIS lines as hardware arrives

sudo udevadm control --reload-rules && sudo udevadm trigger
```

(The repo `udev/` directory carries the reference rules file.)

## Notes

- ACARS, VDL2, and HFDL all feed the same `acars-watcher` via UDP 5005 —
  same message shape (registrations, OOOI, flight data).
- AIS uses MMSI vessel identifiers and its own `ais-watcher` on UDP 5006;
  the watchlist's vessel sweep (AISHub) works independently of local AIS
  receive — see `src/shared/watchlist_README.md`.
- HFDL needs an HF antenna (long wire / end-fed), not the VHF stub.
- `AIS_STATIC_MMSI` and `ACARS_STATIC_REGS` in
  `/etc/corporatetraveldc/dispatch.env` pin specific vessels/registrations
  independent of the OOOI watchlist.
