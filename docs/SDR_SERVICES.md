# SDR Services — Enabling Disabled Containers

All SDR decode/watch services ship as `.disabled` Quadlets. Systemd ignores these files automatically. To activate a service when the required hardware is available:

```
cp <service>.container.disabled <service>.container
systemctl --user daemon-reload
systemctl --user enable --now <service>
```

That's it. No other changes needed for the decoder containers (they pull upstream images). The watcher containers require a one-time image build first — see each section below.

---

## Service map

| Quadlet | Dongle serial | Freq | Aggregator | Watcher |
|---|---|---|---|---|
| `corporatetraveldc-acarsdec` | `acars0130` | 129–131 MHz | airframes.io | `corporatetraveldc-acars-watcher` (UDP 5005) |
| `corporatetraveldc-dumpvdl2` | `vdl20130` | 136–137 MHz | airframes.io | `corporatetraveldc-acars-watcher` (UDP 5005) |
| `corporatetraveldc-dumphfdl` | `hfdl0HF` | 2–22 MHz | airframes.io | `corporatetraveldc-acars-watcher` (UDP 5005) |
| `corporatetraveldc-ais` | `ais0AIS` | 161–162 MHz | MarineTraffic / AISHub | `corporatetraveldc-ais-watcher` (UDP 5006) |

ADS-B (dongle `adsb1090`) is handled by UltraFeeder (separate Quadlet, always active on primary Pi).

---

## Dongle serialization order

Run `rtl-eeprom-reserialize.sh` once per dongle. Each dongle should be the only one connected (or identified by device index) when reserialized.

Recommended order as hardware arrives:

1. `acars0130` — existing dongle, already serialized (ACARS VHF)
2. `vdl20130` — existing dongle, reserialize from `acars0130` via `rtl-eeprom-reserialize.sh` (VDL Mode 2)
3. `hfdl0HF` — new dongle #3 when acquired
4. `ais0AIS` — new dongle #4 when acquired

After serializing, add udev rules so containers address dongles by serial:

```
# /etc/udev/rules.d/99-rtlsdr.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="adsb1090",  SYMLINK+="adsb1090"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="vdl20130",  SYMLINK+="vdl20130"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="acars0130", SYMLINK+="acars0130"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="hfdl0HF",   SYMLINK+="hfdl0HF"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="ais0AIS",   SYMLINK+="ais0AIS"

sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## Watcher containers (require image build)

`corporatetraveldc-acars-watcher` and `corporatetraveldc-ais-watcher` are custom images built locally:

```bash
# ACARS/VDL2/HFDL watcher
mkdir -p /opt/corporatetraveldc/src/acars_watcher
cp acars_watcher.py Containerfile.acars-watcher /opt/corporatetraveldc/src/acars_watcher/
cd /opt/corporatetraveldc/src/acars_watcher
cp Containerfile.acars-watcher Containerfile
podman build -t localhost/corporatetraveldc-acars-watcher:latest .

# AIS vessel watcher
mkdir -p /opt/corporatetraveldc/src/ais_watcher
cp ais_watcher.py Containerfile.ais-watcher /opt/corporatetraveldc/src/ais_watcher/
cd /opt/corporatetraveldc/src/ais_watcher
cp Containerfile.ais-watcher Containerfile
podman build -t localhost/corporatetraveldc-ais-watcher:latest .
```

---

## Notes

- ACARS, VDL2, and HFDL all feed into the same `acars-watcher` via UDP port 5005 — they carry the same message type (aircraft registrations, OOOI, flight data).
- AIS uses MMSI (vessel identifiers), not aircraft registrations — it has its own `ais-watcher` on UDP port 5006.
- HFDL requires an HF antenna (long wire or end-fed), not the VHF stub used for ACARS/VDL2.
- Set `AIS_STATIC_MMSI` in `/etc/corporatetraveldc/dispatch.env` to watch specific vessels.
- Set `ACARS_STATIC_REGS` in the same file to pin aircraft registrations independent of the OOOI watchlist.
