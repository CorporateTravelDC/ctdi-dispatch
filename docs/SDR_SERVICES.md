# SDR Services — Current State & Enabling Disabled Containers

**Rewritten 2026-08-11 against the live Quadlet directory.** The previous
revision (2026-06-14) claimed *all* SDR decode/watch services ship as
`.disabled` — no longer true: most of the SDR stack has been enabled and
running for weeks. `docs/SDR_SERVICES_README.md` was an identical duplicate
of this file and is now just a pointer here.

**Re-verified 2026-08-23** against `podman ps`, `lsusb`,
`/etc/udev/rules.d/99-rtlsdr-adsb.rules`, `ls /dev/rtl_sdr_*`, and the actual
`.disabled` files (`find . -name '*.disabled'`). The enabled table below held
up unchanged. Corrected below: the **dongle serials in the disabled table
were stale**, and the `.disabled` file paths were understated.

> **Why the serials were wrong, and the conflict to be aware of.** The old
> table's `hfdl0HF` / `ais0AIS` were not invented — they are real strings, but
> they only appear in the *setup comments* of two early staged quadlets,
> `systemd/corporatetraveldc-dumphfdl.container.disabled` and
> `systemd/corporatetraveldc-ais.container.disabled`. Those two predate the
> `systemd/quadlets/` generation and predate the live udev rules file, which
> uses an entirely different, documented convention:
> `{TYPE}{CENTERFREQ_4DIGIT}` → `HFDL0008`/`HFDL0011`/`HFDL0017` and
> `AIS0162`. **The udev rules file and the `systemd/quadlets/` variants are
> the ones to follow** — they are what a new dongle would actually be
> programmed and matched against. Serializing a dongle as `hfdl0HF` per those
> two older comments would produce a device no live udev rule matches. Worth
> reconciling or deleting the two stale quadlets when this hardware is
> actually acquired.

## Live state (enabled set verified 2026-08-11, re-confirmed 2026-08-23)

**Enabled and running:**

| Quadlet | Role | Notes |
|---|---|---|
| `corporatetraveldc-ultrafeeder` | ADS-B 1090 MHz decode + tar1090 (:8080), feeds FlightAware/FR24/adsbhub/airplanes.live/OpenSky MLAT | ✅ Restored midday 2026-08-11 after a hardware reseat — the `ADSB1090` dongle had stopped enumerating on USB (~2026-08-10, container crash-looping on missing `/dev/rtl_sdr_adsb`); `adsb-feed-silence-watchdog` alerted correctly throughout. Both dongles enumerate again; live decode confirmed. |
| `corporatetraveldc-acarsrouter` | ACARS/VDL2 message router (:9080) | |
| `corporatetraveldc-dumpvdl2` | VDL Mode 2 decoder — shares the `ACARS0130` dongle (`Environment=RTL_SERIAL=ACARS0130`, whole-bus `AddDevice=/dev/bus/usb`, not the `/dev/rtl_sdr_acars` symlink) | |
| `corporatetraveldc-acarshub` | ACARS web UI (127.0.0.1:9081) | |
| `corporatetraveldc-acars-watcher` | Dual-source watcher — local UDP 5005 + airframes.io REST | custom local image |
| `corporatetraveldc-piaware` / `-fr24feed` / `-planefinder` / `-airnavradar` | Aggregator feeders (FlightAware / FR24 / PlaneFinder / RadarBox) | |

**Still `.disabled` (hardware not yet acquired).** Serials below are the real
`RTL_SERIAL=` values in the Quadlets and the real placeholder serials in
`/etc/udev/rules.d/99-rtlsdr-adsb.rules` — re-derive with
`grep -rn RTL_SERIAL systemd/` rather than trusting a doc:

| Quadlet (repo path) | Dongle serial | Freq | Watcher |
|---|---|---|---|
| `.config/containers/systemd/corporatetraveldc-acarsdec.container.disabled` (duplicate copy at `systemd/`) | `ACARS0130` | 129–131 MHz | acars-watcher (UDP 5005) |
| `systemd/quadlets/corporatetraveldc-dumphfdl-{0008,0011,0017}.container.disabled` | `HFDL0008` / `HFDL0011` / `HFDL0017` (real `Environment=RTL_SERIAL=`) | 8 / 11 / 17 MHz | acars-watcher (UDP 5005) |
| `systemd/corporatetraveldc-dumphfdl.container.disabled` — **superseded** by the three above | comments say `hfdl0HF`; no live udev rule matches it | 2–22 MHz HF | acars-watcher (UDP 5005) |
| `systemd/quadlets/corporatetraveldc-ais-catcher.container.disabled` (AIS-catcher; NMEA/JSON on :8110) | `AIS0162` (commented `AddDevice=/dev/rtl_sdr_ais0162`) | 161–162 MHz | `systemd/corporatetraveldc-ais-watcher.container.disabled` (UDP 5006) |
| `systemd/corporatetraveldc-ais.container.disabled` — **superseded** by ais-catcher above | comments say `ais0AIS`; no live udev rule matches it | 161–162 MHz | same |

Note the three locations: **only** the `acarsdec` one is staged in
`.config/containers/systemd/`; everything else sits under `systemd/` or
`systemd/quadlets/`. None of them exists in the live
`~/.config/containers/systemd/` (confirmed 2026-08-23:
`ls ~/.config/containers/systemd/*.disabled` → no matches), so "disabled"
here means "staged in the repo, never installed", not "installed and
switched off".

## Enabling a disabled service

```bash
# note the repo-side source path differs per service — see the table above
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
(`rtl-eeprom-reserialize.sh`, run with only the target dongle connected):

Live today, both confirmed 2026-08-23 (`lsusb` shows two Realtek RTL2838s;
`ls -l /dev/rtl_sdr_*` shows both symlinks resolved): **`ADSB1090`** →
`/dev/rtl_sdr_adsb`, and **`ACARS0130`** → `/dev/rtl_sdr_acars`. The naming
convention, stated in the rules file itself, is `{TYPE}{CENTERFREQ_4DIGIT}`.
Planned serials, already written as commented placeholders in the live rules
file: `HFDL0008`, `HFDL0011`, `HFDL0017`, `AIS0162` — **use these, not the
`hfdl0HF`/`ais0AIS` forms in the two superseded staged quadlets** (see the
callout at the top of this file). There is **no** separate VDL2 dongle
planned — `dumpvdl2` shares `ACARS0130`.

Live rules, verbatim from `/etc/udev/rules.d/99-rtlsdr-adsb.rules` (note the
`idProduct` match and `MODE="0666"`, both required — an earlier revision of
this doc omitted them):

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", ATTRS{serial}=="ADSB1090", SYMLINK+="rtl_sdr_adsb", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", ATTRS{serial}=="ACARS0130", SYMLINK+="rtl_sdr_acars", MODE="0666"
# HFDL0008 / HFDL0011 / HFDL0017 / AIS0162 lines are present but commented out

sudo udevadm control --reload-rules && sudo udevadm trigger
```

(The repo `udev/` directory carries the reference rules file. Bus paths are
**not** stable across reboots — the serial→symlink mapping is the contract;
see `docs/HARDWARE_GUIDANCE.md` for the current live bus map.)

## Notes

- ACARS, VDL2, and HFDL all feed the same `acars-watcher` via UDP 5005 —
  same message shape (registrations, OOOI, flight data).
- AIS uses MMSI vessel identifiers and its own `ais-watcher` on UDP 5006;
  the watchlist's vessel sweep (AISHub) works independently of local AIS
  receive — see `src/shared/watchlist_README.md`.
- HFDL needs an HF antenna (long wire / end-fed), not the VHF stub.
- `AIS_STATIC_MMSI` and `ACARS_STATIC_REGS` can pin specific
  vessels/registrations independent of the OOOI watchlist — but **neither is
  actually set today** (verified 2026-08-23:
  `grep -c 'AIS_STATIC_MMSI\|ACARS_STATIC_REGS'
  /etc/corporatetraveldc/dispatch.env` → `0` for both, and neither appears in
  the live `corporatetraveldc-acars-watcher.container`). Both default to the
  empty string in code (`src/acars_watcher/acars_watcher.py:49`,
  `src/ais_watcher/ais_watcher.py:32`), so the feature is supported and
  wired but currently inert. Add them to `dispatch.env` (bare values, no
  quotes — see CLAUDE.md's `EnvironmentFile` quoting gotcha) to use it.
