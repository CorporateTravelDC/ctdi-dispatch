Verified against HEAD db64018 and live state on 2026-10-06 18:10Z / 14:10 ET.

# Hardware Guidance — corporatetraveldc dispatch platform

What's been tested on this platform's actual 24/7 workload (SWIM ingest +
local LLM inference + everything else in the container stack), and what to
avoid. The thermal findings below are from real captured data on this
deployment, not vendor specs or simulated load; the current live-state
sections (USB map, RAM) were re-derived directly on 2026-10-06.

---

## Enclosure / cooling — Argon ONE case: **hard pass for this workload**

**Do not use an Argon ONE case (any of the active-fan models tested) for
a Pi 5 running this platform's workload.** Two separate physical units
tested, both with the same failure mode: the fan hits its own rated
maximum RPM and the case still cannot hold safe temperatures under this
platform's sustained CPU load.

**Real data, captured 2026-07-26** (see
`docs/benchmarks/THERMAL_BASELINE_2026-07-26.md` for the full log and raw
pull commands):

- 75-minute sample window, fan confirmed pinned at 5000 RPM (its rated
  max): min 66.1°C, max **83.7°C**, average **74.07°C**.
- The software thermal mitigation in place at the time (SIGSTOP/SIGCONT
  pause of the then-current Ollama inference process) tripped 27 times
  over 9h39m, with the gap between trips collapsing to **1-4 minutes
  apart** by the end of the session despite the fan already maxed.
- That inference process was thermally paused for **92% of its runtime**
  and the average temperature still did not come down.

**Keepwarm-bug note (2026-07-27):** this sample was captured while a
since-fixed keepwarm script bug kept the inference process needlessly
saturated independent of real demand, so the 83.7°C/74.07°C/57.5% figures
above should be read as upper bounds. The DIY-case comparison sample ran
under the same inflated load, so the two cases were still compared fairly
against each other — and the DIY case recovered meaningfully faster
between thermal events. **That relative result (recovery, not peak), is
the basis for this section's recommendation.** Both Argon units have
since been retired in favor of the DIY enclosure; a clean Argon re-test
isn't planned. Full writeup: `docs/GUARDRAILS_JUSTIFICATION.md` §4.

**Why this workload is harder on cooling than a typical Pi 5 desktop/NAS
use case:** this platform runs continuous background CPU load from seven
parallel ingest containers (six SWIM feeds plus a `core` container
carrying NWWS-OI/Amtrak/local RF — confirmed live 2026-10-06: `ingest-notam`,
`ingest-itws`, `ingest-tbfm`, `ingest-tfms`, `ingest-stdds`, `ingest-fdps`,
`ingest-core`, all `Up`) plus on-demand local LLM inference, essentially
24/7, not bursty.

**Inference mitigation has changed since the 2026-07-26 sample and is no
longer a SIGSTOP/SIGCONT governor.** Ollama is retired. There is now a
single `corporatetraveldc-llama.service` (llama.cpp, currently serving
Qwen3-4B-Instruct-2507 q4_0 — swapped from phi3-mini on 2026-09-21 after
an on-box bake-off) hard-capped to two cores and two slots by design
(`common/llama_pool.py`), confirmed live 2026-10-06. `thermal-ingest-guard`
no longer touches any LLM service at all — see the 2026-08-23/08-27
redesign below. Current live snapshot (2026-10-06 18:08 UTC, for scale,
not a verdict): CPU temp 62.8°C, fan ~2325 RPM, load average 10.96/10.45/11.52
with all seven ingest containers and the llama service running.

(Note: that seven-container figure is the *un-shed design load*.
`thermal-ingest-guard` sheds containers under pressure, so the observed
steady state is often lighter than the design load. **Corrected
2026-08-23, still current:** the guard is a single-stage model (refined
2026-08-27): a mild trip at 74°C sheds only `tfms`/`stdds`, and a LOCKDOWN
(79°C, or 1-min load ≥ 40) sheds the *entire* stack except `web` — all six
SWIM feeds, `ingest-core` included, plus poller, pusher and runner. See
`docs/INFRA_MAP.md` §4.1 and CLAUDE.md "Ingest load-shedding".)

**What worked better, and what didn't improve (same night, same Pi, same
workload):** a DIY/open-frame enclosure with a higher-RPM PWM fan showed
materially longer intervals between thermal governor cycles — 6-15 minutes
apart vs. the Argon case's terminal state of 1-4 minutes apart. Raw
peak/average temperature was NOT meaningfully better (DIY: min 69.4/max
82.6/avg 75.34°C vs. Argon: min 66.1/max 83.7/avg 74.07°C — Argon's sample
had partial load-shedding active; DIY's did not). Full comparison:
`docs/benchmarks/THERMAL_BASELINE_2026-07-26.md`.

**Filed with Argon:** see the support-ticket draft
(`docs/tickets/argon-support-ticket-2026-07-26.md`) for the data shared
with their team — a real-world sustained-load data point, not a defect
claim (Fedora is not this case's supported OS). [UNVERIFIED: whether
Argon ever responded — not tracked in this repo.]

**Recommendation going forward:** neither enclosure tested holds the SoC
meaningfully below the mid-70s average under full sustained load. Open-frame
/ high-airflow designs with a PWM (not fixed-curve) fan controller remain
the current working direction on the strength of the recovery-time data;
this isn't a solved problem.

## Board

- Raspberry Pi 5 Model B Rev 1.1, 4 cores (`arm_cortex_a76`, BogoMIPS 108
  per core) — confirmed live 2026-10-06 (`/proc/device-tree/model`,
  `nproc`). Reference platform for the rest of this stack (NVMe boot,
  Tailscale, Podman Quadlets). See `docs/PI5-BOOT-CONFIG.md` for the NVMe
  boot recovery reference.

## RAM

15 GiB usable, confirmed live 2026-10-06 (`free -h`: 15Gi total). Sized
for the current container count (see `docs/GUARDRAILS_JUSTIFICATION.md`
for the per-container memory caps that assume this). An 8 GB Pi 5 is
workable only with a reduced model selection and likely a reduced
ingest-container footprint. [UNVERIFIED: no live 8GB Pi 5 to test against;
this remains guidance, not measured.]

Live snapshot 2026-10-06 18:04 UTC: 8.2Gi used, 8.2Gi buff/cache, 312Mi
free, 7.6Gi "available"; swap in use: zram0 (8 GiB compressed RAM swap,
858.8M used) plus the disk-backed swap file `/var/swap/nvme-overflow.swap`
(8 GiB, priority 10, 0B used at the time of this check). The swap file is a
deliberate OOM safety margin: zram + file = 16 GiB of swap, with the NVMe file
used only after zram fills. See `docs/PI5-BOOT-CONFIG.md`.

## USB port assignment (full physical map)

**Bus paths change across reboots and replugs — re-derive, don't trust a
snapshot.** Current enumeration, re-derived live 2026-10-06
(`for d in /sys/bus/usb/devices/*/serial; do echo "$d $(cat $d)"; done`
plus `lsusb` and `ls -l /dev/rtl_sdr_*`). This table has changed from the
2026-08-23 revision — both the bus paths AND the symlink targets moved,
exactly as the standing caveat below predicts:

| Device | Identifier | Protocol / freq | Bus path (live 2026-10-06) | Symlink |
|---|---|---|---|---|
| RTL-SDR dongle | serial `ACARS0130` | VDL-M / ACARS (dumpvdl2, 136.650–136.975 MHz) | `1-2` | `/dev/rtl_sdr_acars` → `bus/usb/001/003` |
| RTL-SDR dongle | serial `ADSB1090` | ADS-B (ultrafeeder/readsb, 1090 MHz) | `3-2` | `/dev/rtl_sdr_adsb` → `bus/usb/003/002` |
| Multifunction Composite Gadget (GCT Semiconductor, `1076:a005`) | no serial | USB-tethered cellular modem (Verizon hotspot backup uplink, see `docs/NETWORK_FAILOVER.md`) | `1-1` | — |

No `3-1` device, and no mouse or keyboard enumerated — unchanged from the
2026-08-23 finding.

The two RTL-SDR dongles are identical hardware (Realtek, vendor `0bda`
product `2838`), distinguished only by their programmed serial (see
`/etc/udev/rules.d/99-rtlsdr-adsb.rules` for the udev rule that keys off
this serial to create a stable device symlink). **Bus path and symlink
target are a point-in-time cross-reference only** — the serial-to-protocol
mapping (`ACARS0130` = VDL-M, `ADSB1090` = ADS-B) is the stable contract,
not the port or symlink target. Re-derive from sysfs rather than trusting
this table.

## Network

Wired or a strong Wi-Fi link is required, not optional — this platform's
unscoped SWIM ingest alone can sustain multi-GiB/hour bandwidth (see
`docs/GUARDRAILS_JUSTIFICATION.md`). A weak or congested link will surface
as ingest timeouts and RSS/API fetch failures that look like application
bugs but are actually link-layer congestion. See `docs/NETWORK_FAILOVER.md`
for the current primary/backup uplink topology and the active
failover watchdog — as of 2026-10-06 this box runs on two independent
uplinks (Wi-Fi primary, cellular-over-USB backup), not a single link.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-ops.md`.


### Hardware Guidance — corporatetraveldc dispatch platform

~~What's been tested on this platform's actual 24/7 workload (SWIM ingest + Ollama inference + everything else in the container stack), and what to avoid. All findings below are from real captured data on this deployment, not vendor specs or simulated load.~~


### Hardware Guidance — corporatetraveldc dispatch platform › Enclosure / cooling — Argon ONE case: **hard pass for this workload**

~~**Do not use an Argon ONE case (any of the active-fan models tested) for a Pi 5 running this platform's workload.** Two separate physical units tested, both with the same failure mode: the fan hits its own rated maximum RPM (confirmed via `sensors`) and the case still cannot hold safe temperatures under this platform's sustained CPU load.~~

- ~~75-minute sample window, fan confirmed pinned at 5000 RPM (its rated max): min 66.1°C, max **83.7°C**, average **74.07°C**. In tier-2 thermal shedding (all background SWIM ingest paused) for 57.5% of that window.~~
- ~~Software thermal governor (independent SIGSTOP/SIGCONT pause on the Ollama inference process) tripped 27 times over 9h39m, with the gap between trips collapsing from 1-2.5 hours apart early on to **1-4 minutes apart** by the end of the session -- despite the fan already maxed and background ingest load already cut ~68%.~~
- ~~The Ollama inference process itself was thermally paused for **92% of its runtime** and still couldn't bring the average down.~~
- ~~This happened *with* every available software mitigation already engaged: `schedutil` CPU governor, an aggressive fan curve committed directly to the Argon board via `argonone-cli`, and this platform's own `thermal-ingest-guard.py` shedding the heaviest ingest containers at 74C/79C thresholds.~~

~~**Keepwarm-bug note, added 2026-07-27:** this sample was captured while `scripts/ollama-keepwarm.sh` had a since-fixed bug (broken residency check~~

+ ~~uncapped warm-up call) that kept Ollama's inference process needlessly saturated for its entire uptime, independent of any real brief-generation demand -- so the exact 83.7°C max / 74.07°C avg / 57.5% tier-2 figures above should be read as upper bounds, some of that load wasn't organic demand. This does not soften the hard-pass verdict, though: the same bug was active during the DIY-case comparison sample as well (same night, `ollama.service` restarted right at the case swap and the bug resumed immediately), so both cases were compared against each other under equally-inflated load -- and the DIY case still recovered meaningfully faster between thermal events. That relative result, not the absolute peak/average numbers, is the actual basis for this section's recommendation. Both physical Argon units have since been retired in favor of the DIY enclosure on the strength of that comparison; a clean Argon re-test isn't planned. See `docs/GUARDRAILS_JUSTIFICATION.md` section 4 for the full writeup. The draft support ticket (`docs/tickets/argon-support-ticket-2026-07-26.md`) is kept staged as a record of the data in hand at the time, not as a pending action.~~

~~**Why this workload is harder on cooling than a typical Pi 5 desktop/NAS use case:** this platform runs continuous background CPU load from seven parallel ingest containers (six SWIM feeds plus a `core` container carrying NWWS-OI/Amtrak/local RF — `core` is not a SWIM feed) plus on-demand LLM inference (Ollama), essentially 24/7, not bursty. A case sized for occasional desktop-style load spikes doesn't have the sustained thermal headroom this needs. (Note: that is the *un-shed design load*. `thermal-ingest-guard` sheds containers under pressure, so the observed steady state is often lighter than the design load. **Corrected 2026-08-23:** this paragraph used to describe hours-long "tier-2" sheds leaving just ingest-core + ingest-notam running — that tier no longer exists. The guard was redesigned the same day to a single-stage model, then refined 2026-08-27: a mild temperature trip at 74 °C sheds only `tfms`/`stdds`, and a LOCKDOWN (79 °C, or 1-min load ≥ 40 — the LLM-contention-fallback trigger was demoted to informational-only 2026-08-27) sheds the *entire* stack except `web` — all six SWIM feeds, `ingest-core` included, plus poller, pusher and runner. The guard no longer touches any LLM service (Ollama itself was retired for per-tier llama.cpp units the same week). Expect materially fewer and shorter sheds than the figures in the sections above were captured under. See `docs/INFRA_MAP.md` §4.1 and CLAUDE.md "Ingest load-shedding".)~~

~~**What worked better, and what didn't improve (same night, same Pi, same workload, full ingest load in both samples):** a DIY/open-frame enclosure with a higher-RPM PWM fan showed materially longer intervals between thermal governor cycles -- 6-15 minutes apart vs. the Argon case's terminal state of 1-4 minutes apart, continuously. **That's the real, useful difference: recovery, not ceiling.** Raw peak/average temperature was NOT meaningfully better -- DIY sample: min 69.4C/max 82.6C/avg 75.34C vs. Argon sample: min 66.1C/max 83.7C/avg 74.07C (Argon's sample had partial load-shedding active at the time; DIY's did not, so if anything DIY carried more load for a similar temperature range). Full numbers and the honest before/after comparison are in `docs/benchmarks/THERMAL_BASELINE_2026-07-26.md`.~~

~~**Filed with Argon:** see the support-ticket draft for the reasoning and data shared with their team -- filed as a real-world sustained-load data point, not a defect claim, since Fedora is not this case's supported OS.~~

~~**Recommendation going forward:** neither enclosure tested so far holds the SoC meaningfully below the mid-70s average under this platform's full sustained load -- so the bar for a future enclosure isn't "beats these two," it's demonstrating a real drop in both cycle frequency *and* average/peak temperature under a comparable full-load test, not just a better recovery curve. Open-frame / high-airflow designs with a PWM (not fixed-curve) fan controller remain the current working direction on the strength of the recovery-time data, but this isn't a solved problem yet.~~


### Hardware Guidance — corporatetraveldc dispatch platform › Board

- ~~Raspberry Pi 5 Model B Rev 1.1 -- confirmed working reference platform for the rest of this stack (NVMe boot, Tailscale, Podman Quadlets, etc.) See `docs/PI5-BOOT-CONFIG.md` for the NVMe boot recovery reference.~~


### Hardware Guidance — corporatetraveldc dispatch platform › RAM

~~15 GiB usable on this deployment's Pi 5 -- sized for the current container count (see `docs/GUARDRAILS_JUSTIFICATION.md` for the per-container memory caps that assume this). An 8 GB Pi 5 is workable only with a reduced model selection (see README.md's LLM model table for the "8 GB Pi 5" row) and likely a reduced ingest-container footprint.~~


### Hardware Guidance — corporatetraveldc dispatch platform › USB port assignment (full physical map)

~~Current enumeration, re-derived from live sysfs on **2026-08-23** (`for d in /sys/bus/usb/devices/*/serial; do echo "$d $(cat $d)"; done` plus `ls -l /dev/rtl_sdr_*`). An earlier revision of this table (from the 2026-08-06 reseat) recorded ACARS0130 at `1-1`, ADSB1090 at `3-1`, and a mouse/keyboard at `3-2`/`1-2` — none of that matches live state anymore: **bus positions change across reboots and replugs**; the `/dev/rtl_sdr_*` symlinks were relinked on the 2026-08-18 reboot. The serial→symlink mapping is the stable contract, not the bus path.~~

| ~~Device~~ | ~~Identifier~~ | ~~Protocol / freq~~ | ~~Bus path (live 2026-08-23)~~ | ~~Symlink~~ |
|---|---|---|---|---|
| ~~RTL-SDR dongle~~ | ~~serial `ADSB1090`~~ | ~~ADS-B (ultrafeeder/readsb, 1090 MHz)~~ | ~~`1-2`~~ | ~~`/dev/rtl_sdr_adsb` → `bus/usb/001/017`~~ |
| ~~RTL-SDR dongle~~ | ~~serial `ACARS0130`~~ | ~~VDL-M / ACARS (dumpvdl2, 136.650–136.975 MHz)~~ | ~~`3-2`~~ | ~~`/dev/rtl_sdr_acars` → `bus/usb/003/005`~~ |
| ~~Multifunction Composite Gadget~~ | ~~no serial~~ | ~~—~~ | ~~`1-1`~~ | ~~—~~ |

~~There is no `3-1` device currently, and no mouse or keyboard is enumerated at all — the previously-tabled mouse/keyboard rows are gone from live state.~~

~~The two RTL-SDR dongles are identical hardware (Realtek, vendor `0bda` product `2838`), distinguished only by their programmed serial (see `/etc/udev/rules.d/99-rtlsdr-adsb.rules` for the udev rule that keys off this serial to create a stable device symlink). Bus path and symlink are included as a direct cross-reference for `lsusb` / `journalctl -k` output — a fresh `journalctl -k --since "..."` after any reseat will show `usb <bus-path>: ...` lines with a `SerialNumber:` field matching the table above, which is the fastest way to confirm both are seated correctly and staying connected (no repeated `new ... USB device number N` / `USB disconnect` pairs for the same bus path, no `device descriptor read/64, error -71` — that error specifically indicates a marginal physical connection, not a software/udev problem).~~

~~If the RTL-SDR dongles are ever moved to different physical ports (or the box simply reboots), the *bus path* column goes stale — the serial-to-protocol mapping (`ACARS0130` = VDL-M, `ADSB1090` = ADS-B) doesn't change, since that's a property of the dongle itself (its programmed EEPROM serial), not the port it's plugged into. Re-derive bus paths from sysfs rather than trusting this table's snapshot.~~


### Hardware Guidance — corporatetraveldc dispatch platform › Network

~~Wired or a strong Wi-Fi link is required, not optional -- this platform's unscoped SWIM ingest alone can sustain multi-GiB/hour bandwidth (see `docs/GUARDRAILS_JUSTIFICATION.md`). A weak or congested link will surface as ingest timeouts and RSS/API fetch failures that look like application bugs but are actually link-layer congestion -- check `ping <gateway>` RTT before debugging the application layer.~~
