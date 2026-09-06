# Hardware Guidance — corporatetraveldc dispatch platform

What's been tested on this platform's actual 24/7 workload (SWIM ingest +
Ollama inference + everything else in the container stack), and what to
avoid. All findings below are from real captured data on this deployment,
not vendor specs or simulated load.

---

## Enclosure / cooling — Argon ONE case: **hard pass for this workload**

**Do not use an Argon ONE case (any of the active-fan models tested) for
a Pi 5 running this platform's workload.** Two separate physical units
tested, both with the same failure mode: the fan hits its own rated
maximum RPM (confirmed via `sensors`) and the case still cannot hold safe
temperatures under this platform's sustained CPU load.

**Real data, captured 2026-07-26** (see
`docs/benchmarks/THERMAL_BASELINE_2026-07-26.md` for the full log and raw
pull commands):

- 75-minute sample window, fan confirmed pinned at 5000 RPM (its rated
  max): min 66.1°C, max **83.7°C**, average **74.07°C**. In tier-2 thermal
  shedding (all background SWIM ingest paused) for 57.5% of that window.
- Software thermal governor (independent SIGSTOP/SIGCONT pause on the
  Ollama inference process) tripped 27 times over 9h39m, with the gap
  between trips collapsing from 1-2.5 hours apart early on to **1-4
  minutes apart** by the end of the session -- despite the fan already
  maxed and background ingest load already cut ~68%.
- The Ollama inference process itself was thermally paused for **92% of
  its runtime** and still couldn't bring the average down.
- This happened *with* every available software mitigation already
  engaged: `schedutil` CPU governor, an aggressive fan curve committed
  directly to the Argon board via `argonone-cli`, and this platform's own
  `thermal-ingest-guard.py` shedding the heaviest ingest containers at
  74C/79C thresholds.

**Keepwarm-bug note, added 2026-07-27:** this sample was captured while
`scripts/ollama-keepwarm.sh` had a since-fixed bug (broken residency check
+ uncapped warm-up call) that kept Ollama's inference process needlessly
saturated for its entire uptime, independent of any real brief-generation
demand -- so the exact 83.7°C max / 74.07°C avg / 57.5% tier-2 figures
above should be read as upper bounds, some of that load wasn't organic
demand. This does not soften the hard-pass verdict, though: the same bug
was active during the DIY-case comparison sample as well (same night,
`ollama.service` restarted right at the case swap and the bug resumed
immediately), so both cases were compared against each other under
equally-inflated load -- and the DIY case still recovered meaningfully
faster between thermal events. That relative result, not the absolute
peak/average numbers, is the actual basis for this section's
recommendation. Both physical Argon units have since been retired in
favor of the DIY enclosure on the strength of that comparison; a clean
Argon re-test isn't planned. See `docs/GUARDRAILS_JUSTIFICATION.md`
section 4 for the full writeup. The draft support ticket
(`docs/tickets/argon-support-ticket-2026-07-26.md`) is kept staged as a
record of the data in hand at the time, not as a pending action.

**Why this workload is harder on cooling than a typical Pi 5 desktop/NAS
use case:** this platform runs continuous background CPU load from seven
parallel ingest containers (six SWIM feeds plus a `core` container carrying
NWWS-OI/Amtrak/local RF — `core` is not a SWIM feed) plus on-demand LLM
inference (Ollama),
essentially 24/7, not bursty. A case sized for occasional desktop-style
load spikes doesn't have the sustained thermal headroom this needs.
(Note: that is the *un-shed design load*. `thermal-ingest-guard` sheds
containers under pressure, so the observed steady state is often lighter
than the design load. **Corrected 2026-08-23:** this paragraph used to
describe hours-long "tier-2" sheds leaving just ingest-core +
ingest-notam running — that tier no longer exists. The guard was
redesigned the same day to a single-stage model, then refined 2026-08-27:
a mild temperature trip
at 74 °C sheds only `tfms`/`stdds`, and a LOCKDOWN (79 °C, or 1-min load
≥ 40 — the LLM-contention-fallback trigger was demoted to
informational-only 2026-08-27) sheds the *entire* stack
except `web` — all six SWIM feeds, `ingest-core` included, plus poller,
pusher and runner. The guard no longer touches any LLM service (Ollama
itself was retired for per-tier llama.cpp units the same week). Expect
materially fewer and
shorter sheds than the figures in the sections above were captured under.
See `docs/INFRA_MAP.md` §4.1 and CLAUDE.md "Ingest load-shedding".)

**What worked better, and what didn't improve (same night, same Pi, same
workload, full ingest load in both samples):** a DIY/open-frame enclosure
with a higher-RPM PWM fan showed materially longer intervals between
thermal governor cycles -- 6-15 minutes apart vs. the Argon case's
terminal state of 1-4 minutes apart, continuously. **That's the real,
useful difference: recovery, not ceiling.** Raw peak/average temperature
was NOT meaningfully better -- DIY sample: min 69.4C/max 82.6C/avg
75.34C vs. Argon sample: min 66.1C/max 83.7C/avg 74.07C (Argon's sample
had partial load-shedding active at the time; DIY's did not, so if
anything DIY carried more load for a similar temperature range). Full
numbers and the honest before/after comparison are in
`docs/benchmarks/THERMAL_BASELINE_2026-07-26.md`.

**Filed with Argon:** see the support-ticket draft for the reasoning and
data shared with their team -- filed as a real-world sustained-load data
point, not a defect claim, since Fedora is not this case's supported OS.

**Recommendation going forward:** neither enclosure tested so far holds
the SoC meaningfully below the mid-70s average under this platform's full
sustained load -- so the bar for a future enclosure isn't "beats these
two," it's demonstrating a real drop in both cycle frequency *and*
average/peak temperature under a comparable full-load test, not just a
better recovery curve. Open-frame / high-airflow designs with a PWM (not
fixed-curve) fan controller remain the current working direction on the
strength of the recovery-time data, but this isn't a solved problem yet.

## Board

- Raspberry Pi 5 Model B Rev 1.1 -- confirmed working reference platform
  for the rest of this stack (NVMe boot, Tailscale, Podman Quadlets, etc.)
  See `docs/PI5-BOOT-CONFIG.md` for the NVMe boot recovery reference.

## RAM

15 GiB usable on this deployment's Pi 5 -- sized for the current container
count (see `docs/GUARDRAILS_JUSTIFICATION.md` for the per-container memory
caps that assume this). An 8 GB Pi 5 is workable only with a reduced model
selection (see README.md's LLM model table for the "8 GB Pi 5" row) and
likely a reduced ingest-container footprint.

## USB port assignment (full physical map)

Current enumeration, re-derived from live sysfs on **2026-08-23**
(`for d in /sys/bus/usb/devices/*/serial; do echo "$d $(cat $d)";
done` plus `ls -l /dev/rtl_sdr_*`). An earlier revision of this table
(from the 2026-08-06 reseat) recorded ACARS0130 at `1-1`, ADSB1090 at
`3-1`, and a mouse/keyboard at `3-2`/`1-2` — none of that matches live
state anymore: **bus positions change across reboots and replugs**;
the `/dev/rtl_sdr_*` symlinks were relinked on the 2026-08-18 reboot.
The serial→symlink mapping is the stable contract, not the bus path.

| Device | Identifier | Protocol / freq | Bus path (live 2026-08-23) | Symlink |
|---|---|---|---|---|
| RTL-SDR dongle | serial `ADSB1090` | ADS-B (ultrafeeder/readsb, 1090 MHz) | `1-2` | `/dev/rtl_sdr_adsb` → `bus/usb/001/017` |
| RTL-SDR dongle | serial `ACARS0130` | VDL-M / ACARS (dumpvdl2, 136.650–136.975 MHz) | `3-2` | `/dev/rtl_sdr_acars` → `bus/usb/003/005` |
| Multifunction Composite Gadget | no serial | — | `1-1` | — |

There is no `3-1` device currently, and no mouse or keyboard is
enumerated at all — the previously-tabled mouse/keyboard rows are gone
from live state.

The two RTL-SDR dongles are identical hardware (Realtek, vendor `0bda`
product `2838`), distinguished only by their programmed serial (see
`/etc/udev/rules.d/99-rtlsdr-adsb.rules` for the udev rule that keys
off this serial to create a stable device symlink). Bus path and
symlink are included as a direct cross-reference for `lsusb` /
`journalctl -k` output — a fresh `journalctl -k --since "..."` after
any reseat will show `usb <bus-path>: ...` lines with a
`SerialNumber:` field matching the table above, which is the fastest
way to confirm both are seated correctly and staying connected (no
repeated `new ... USB device number N` / `USB disconnect` pairs for
the same bus path, no `device descriptor read/64, error -71` — that
error specifically indicates a marginal physical connection, not a
software/udev problem).

If the RTL-SDR dongles are ever moved to different physical ports (or
the box simply reboots), the *bus path* column goes stale — the
serial-to-protocol mapping (`ACARS0130` = VDL-M, `ADSB1090` = ADS-B)
doesn't change, since that's a property of the dongle itself (its
programmed EEPROM serial), not the port it's plugged into. Re-derive
bus paths from sysfs rather than trusting this table's snapshot.

## Network

Wired or a strong Wi-Fi link is required, not optional -- this platform's
unscoped SWIM ingest alone can sustain multi-GiB/hour bandwidth (see
`docs/GUARDRAILS_JUSTIFICATION.md`). A weak or congested link will surface
as ingest timeouts and RSS/API fetch failures that look like application
bugs but are actually link-layer congestion -- check `ping <gateway>`
RTT before debugging the application layer.
