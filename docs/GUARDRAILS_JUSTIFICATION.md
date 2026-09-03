# Resource Guardrails — What's In Place and Why

This platform runs several resource guardrails -- network, memory, CPU,
and thermal -- that cost some throughput/flexibility in exchange for not
taking the box down. This document exists so a future "can we just remove
this cap, it's slowing things down" conversation starts from real,
captured data instead of a guess. Every number below is from this
deployment's actual logs/telemetry, not a simulation or a vendor spec.

No formal "network congestion" writeup existed before this document --
the earlier finding lived only in operator notes/session memory. This
supersedes that and folds it in alongside the memory, CPU, and thermal
guardrails as one place to look before touching any of them.

---

## 1. Network guardrail — why SWIM ingest needs bandwidth scoping

**The finding (2026-07-19, still open):** the FAA SWIM ingest container
pulls the *unscoped nationwide* feed for each subscribed domain
(TFMS/FDPS/STDDS/ITWS/NOTAM), not a DC-region-scoped subset, and discards
almost everything client-side. Measured at the time: ~4 GB in 43 minutes
from that one container (~5-6 GB/hour).

**Real vnstat data, this deployment, July 2026:**

| Period | Total (rx+tx) |
|---|---|
| Daily, 2026-07-19 | 140.18 GiB |
| Daily, 2026-07-22 | 248.44 GiB |
| Daily, 2026-07-24 | 294.90 GiB |
| Daily, 2026-07-25 | 295.48 GiB (peak) |
| Month-to-date, July 2026 (through 7/26) | 3.40 TiB |
| ~~**July 2026, estimated full-month**~~ → **actual, `vnstat -m` 2026-08-23** | ~~4.30 TiB~~ → **4.37 TiB** (4.26 TiB rx + 114.14 GiB tx) — the estimate landed within 2% |

Daily usage climbed roughly 2x over the week as more SWIM domains came
online -- from ~140 GiB/day to ~295 GiB/day. At the July peak-day rate
sustained for a month, that's closer to **8.8 TiB/month** than the
originally-estimated 4-6 TB; the actual estimated-July figure (4.30 TiB)
reflects the lighter early-month days blended in, not a ceiling.

**Why this matters:** this is a residential/small-business network link,
not a datacenter uplink. Sustained multi-hundred-GiB/day usage risks ISP
throttling, contention with every other device on the link, and -- per
the separate Wi-Fi congestion finding -- directly correlates with pi-to-
gateway RTT spikes to 1-2 seconds under load, which cascades into ingest
timeouts, DNS failures, and tunnel instability that look like unrelated
application bugs until traced back to link saturation.

**Current guardrail:** no *designed* network-layer guardrail is deployed
-- topic-scoping the SWIM subscription to DC-region facilities (or gating
ingest to active-use windows) remains the open fix. The CPU/memory caps
on each ingest container (below) exist independently of this and don't
address bandwidth directly. **Don't remove the plan to scope this** on
the theory that "it's just background data" -- the vnstat numbers above
are what "unscoped" actually costs on this link.

**Update, 2026-08-23 (revised the same day with the fuller series — the
first version of this paragraph quoted only the last four days and read
as though all of August had been light, which the data does not
support).** The July figures above are history, not the current rate,
but the drop is **recent and abrupt, not a gradual trend**. Full
`vnstat -d` totals:

| Date | Total | Note |
|---|---|---|
| 2026-08-11 → 08-15 | 289.87 / 318.18 / 255.12 / 132.20 / 150.08 GiB | still at July-like rates |
| 2026-08-16 → 08-18 | 3.02 / 11.29 / 10.64 GiB | the SWIM-feed outage window + the 08-18 reboot — not a guardrail effect |
| 2026-08-19 → 08-22 | 63.70 / 48.67 / 34.49 / 18.92 GiB | the current regime |
| 2026-08-23 (partial, to 14:00) | 2.99 GiB | — |
| August month-to-date | 3.38 TiB (`vnstat -m`) | vs. July's 4.37 TiB actual |

So the honest read is: mid-August still ran at 130-320 GiB/day, and only
since ~08-19 has the rate settled an order of magnitude below July's
140-295 GiB/day. The most likely cause is the per-feed ingest split plus
`thermal-ingest-guard`'s load-based shedding keeping SWIM containers
stopped for large parts of the day (2026-08-23 alone had four separate
shed periods before 12:30 — see `docs/INFRA_MAP.md` §4.1). That is a
de-facto network guardrail, not a designed one: the drop is a side
effect of load management, not deliberate topic scoping, and would
vanish if the box stopped shedding. Note the causal attribution is
inferred from the timing, not measured per-feed — the open fix
(scoping the SWIM subscription) is still the only *designed* answer.

---

## 2. Memory guardrails — per-container `Memory=` caps

Every container in this stack runs with an explicit `Memory=` cap in its
Quadlet (`~/.config/containers/systemd/*.container`). Real values from
this deployment:

| Container class | `Memory=` | Rationale |
|---|---|---|
| Ingest (core/itws/notam) | 256m | Lightest feeds, smallest footprint |
| Ingest (tbfm) | 320m | |
| Ingest (tfms) | 320m | |
| Ingest (stdds) | 384m | |
| Ingest (fdps) | 448m | Largest ingest payload |
| Web/poller/pusher/runner/ultrafeeder/ntfy/openwebui/acars* | 1536m | General app-container ceiling |
| demo-api | 512m | Lightweight demo endpoint |
| nextcloud-app | 2048m | PHP app server, heavier footprint |
| nextcloud-db | 768m | |

**Why this matters:** this Pi has 15 GiB usable RAM, and a leaking or
runaway container without a hard cap doesn't just slow down -- it can
consume enough RAM to trigger swap thrashing (this Pi runs zram swap)
across the *entire* stack, taking down unrelated containers that had
nothing to do with the leak. The `acars-watcher.container`'s own comment
states the intent directly: `Memory=1536m` with
`PodmanArgs=--memory-swap=1536m` (no additional swap beyond the RAM cap)
is deliberate -- it "forces a clean OOM-kill instead of swap-thrashing
(zram) if a container leaks past Memory=." One container failing loudly
and restarting (it's `Restart=always`) is a contained, visible, self-
healing failure. One container swap-thrashing the whole box silently
degrades everything at once.

**Don't remove these caps** even to "give a container more headroom" --
raise the specific container's cap deliberately and document why, don't
uncap it.

---

## 3. CPU guardrails — `CPUWeight=` / `CPUQuota=`

Every container also carries a `CPUWeight=` (proportional share, not a
hard pin -- only matters under real contention) and a `CPUQuota=`
ceiling. The seven ingest containers carry `CPUWeight=30`; the app-class
containers are at `CPUWeight=100` (nextcloud-app excepted, below). Real
values:

| Container class | `CPUQuota=` | Notes |
|---|---|---|
| Ingest (itws/notam) | 60% | |
| Ingest (core/tbfm) | 80% | |
| Ingest (tfms) | 90% | |
| Ingest (stdds) | 120% | |
| Ingest (fdps) | 150% | Heaviest single ingest domain |
| demo-api | 100% | |
| nextcloud-db | 200% | |
| General app containers (web/poller/pusher/runner/acars*/ultrafeeder/ntfy/openwebui) | 300% | Hard ceiling: never more than 3 of this Pi's 4 cores for one container |
| nextcloud-app | 300%, `CPUWeight=150` | Slightly favored under contention |

**Why this matters -- directly demonstrated 2026-07-26:** the Ollama
inference process (native host process, not a container) DOES carry its
own guardrail -- `/etc/systemd/system/ollama.service.d/20-resource-limits.conf`
(CPU caps in place since 2026-07-09/11 — confirmed via the file timestamp
at the time this was written; the drop-in was since **replaced 2026-08-19**
by the expanded version adding memory bounds/`OLLAMA_KEEP_ALIVE`/
`LLAMA_ARG_CACHE_RAM=0`, so its on-disk mtime now reads 2026-08-19 — don't
re-derive the July date from mtime) sets
`CPUQuota=300%` and `CPUWeight=500`, mirroring the same "never more than
3 of 4 cores for one thing" ceiling every container gets, with a 5x
priority edge under contention (documented rationale in the drop-in
itself). Corrected here: an earlier draft of this section claimed Ollama
had no CPU cap at all -- it always has, this was a documentation error,
not a missing guardrail.

What the cap does NOT do, also directly demonstrated: it doesn't protect
against a bug that keeps a process busy but never exceeds the ceiling.
2026-07-27's incident (see section 5) had `llama-server` sustained at
~95-190% CPU -- comfortably under the 300% quota -- continuously for its
entire ~10.5 hour uptime, and the CPU cap had nothing to say about it,
because the problem was duration and an unbounded generation, not
instantaneous rate. Real observed rate WAS higher on 2026-07-26 (~309%
average whenever allowed to run, which is why that 300% ceiling exists at
all and does get hit under legitimate load) -- these are two different
failure shapes and both real: the quota bounds *how hard* Ollama can hit
the CPU at any instant, thermal-ingest-guard/governor bound *how long*
the rest of the stack tolerates sustained load, and neither substitutes
for fixing an actual application bug that generates a runaway workload in
the first place. The per-container `CPUQuota=` caps on the ingest
containers are what prevented THEM from also running uncapped and
compounding the 2026-07-26 problem; `thermal-ingest-guard.py`'s shedding
is only effective because those containers already have a bounded CPU
footprint to fully stop, not an unbounded one to fight.

**Ollama memory guardrail (added 2026-08-19; superseded 2026-08-27 — the
drop-in and `ollama.service` are gone, and the equivalent hard
ceiling/no-swap limits now live inside each `corporatetraveldc-llama-*`
unit, e.g. llama-hot `MemoryMax=4608M`):** the
`20-resource-limits.conf` drop-in bounded Ollama's memory:
`MemoryLow=4850M`, `MemoryHigh=6050M`, `MemoryMax=7250M`,
`MemorySwapMax=0`, plus `OLLAMA_KEEP_ALIVE=10m` and
`LLAMA_ARG_CACHE_RAM=0` -- installed to
`/etc/systemd/system/ollama.service.d/` and verified live via
`systemctl show ollama.service` the same day. Same philosophy as the
container `Memory=`/`--memory-swap` pairs: a hard ceiling with no swap
escape hatch, so a runaway model load OOM-kills cleanly instead of
zram-thrashing the whole box.

**Don't remove or raise these ceilings** without re-running the same
kind of sustained-load thermal test used for the case comparison below --
a CPU cap increase on any container is effectively a thermal-load
increase on this hardware, not just a performance change. Also don't
assume a CPU/memory cap alone will catch every future application-level
runaway -- see section 5's incident, where the fix that actually mattered
was in application code, not a cgroup limit.

---

## 4. Thermal + load guardrails — `thermal-ingest-guard.py` (the Ollama governor is retired)

> **2026-08-27 update (verified 2026-09-03):** with the Ollama → llama.cpp
> cutover, `ollama.service`, its `20-resource-limits.conf` drop-in, and the
> `ollama-governor` unit were all retired (no such unit files exist; the
> repo's `systemd/` copies are gone). The per-tier
> `corporatetraveldc-llama-{hot,chat,report-1}` user units carry their own
> `CPUWeight`/`MemoryMax` limits, llama-hot is documented in its own unit as
> "never thermally paused", and a daily
> `corporatetraveldc-llama-restart.timer` (03:00 ET) provides the freshness
> cycle. `thermal-ingest-guard.py` also no longer touches any LLM service,
> and its third LOCKDOWN trigger (contention-attributed fallbacks) was
> demoted to informational-only the same day. The governor description and
> the LOCKDOWN table below are kept as the record of the Ollama era and of
> the 2026-08-23 design they justified.

Two independent, non-interacting safety mechanisms (one thermal-only,
one thermal *and* CPU-load):

- **`ollama_governor.py`** (**RETIRED 2026-08-27** — historically ran as a
  managed systemd unit, `ollama-governor.service`; the stale script copy may
  linger at `/usr/local/bin/ollama_governor.py` but no unit runs it):
  SIGSTOP/SIGCONT on
  the Ollama inference process at 75.0°C pause / 68.0°C resume.
- **`thermal-ingest-guard.py`** (added 2026-07-26, `docs/benchmarks/
  THERMAL_BASELINE_2026-07-26.md` has the full build rationale): a
  thermal **and CPU-load** guard, not temperature-only. **Redesigned
  2026-08-23 by operator directive — the tiers below replace the old
  symmetric 10.0/14.0-trip, 6.0-resume load ladder an earlier revision of
  this section described.** Verified live against
  `scripts/thermal-ingest-guard.py` (defaults at lines **499-510** — an
  earlier revision said 499-508, which clipped both `*_FEEDS` lines;
  decision logic ~line 555 onward. Line numbers drift — re-derive with
  `grep -n 'THERMAL_GUARD_' scripts/thermal-ingest-guard.py`):

  | Trip | Condition | What's shed |
  |---|---|---|
  | Temp tier 1 (mild) | `temp ≥ 74.0 °C` (temperature only — load no longer participates here) | `tfms,stdds` |
  | **LOCKDOWN** | `temp ≥ 79.0 °C` **or** `load1 ≥ 40.0` *(the third, fallback-count trigger was demoted to informational-only 2026-08-27)* | the entire stack except `web` — all 6 SWIM feeds, `ingest-core`, `poller`, `pusher`, `runner` *(no LLM service since 2026-08-27)* |
  | Informational only (logged, no ntfy, no shed) | `temp` 70–74 °C, or `load1` 15–40, or any fallback count | — |
  | Restore | `temp < 65.0 °C` **and** `load1 < 15.0`, held 300 s *(fallback count no longer blocks resume)* | tier 1 restores `tfms,stdds`; LOCKDOWN restores the whole stack |

  Why the asymmetry: **every real trip on this box's recorded history has
  been load-driven, never temperature-driven** (peak temp ever observed
  across the guard's journal is ~71 °C, under the 74 °C line), and an
  independent auto-ramping PWM fan already regulates thermally underneath
  this script. Temperature therefore keeps its original two-stage trigger
  as an unattended-hardware backstop; load collapses to a single
  informational/lockdown split because it was the actual, avoidable cause
  of feed downtime. The third trigger — repeated Ollama-contention-driven
  brief fallbacks, logged by `common/llm.py::_record_load_fallback()` to
  `/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl` — catches
  contention the 1-minute load average smooths over.

  Since 2026-08-21 the restore path verifies each restarted feed
  (`_feed_is_active()`) and fires a priority-5 "RESTORE FAILED" alert
  instead of silently declaring success. Tunable via `THERMAL_GUARD_*`
  vars in `dispatch.env` — **but note only the enable flag and the
  temperature/dwell/feed-list vars are actually set there today**
  (`dispatch.env:278-284`: `ENABLED`, `TIER1_TEMP_C`, `TIER2_TEMP_C`,
  `RESUME_TEMP_C`, `RESUME_DWELL_S`, `TIER1_FEEDS`, `TIER2_FEEDS` — seven
  lines, re-confirmed live 2026-08-23; an earlier revision here listed six
  and omitted `THERMAL_GUARD_ENABLED`); every new load/fallback threshold
  (`LOAD_INFO_MIN`, `LOAD_LOCKDOWN`, `RESUME_LOAD`, `TEMP_INFO_C`,
  `FALLBACK_TRIGGER_COUNT`, `FALLBACK_WINDOW_S`) runs on the script's own
  hardcoded defaults. That is the intended pattern (`_cfg()` reads
  `dispatch.env` first, falls back to defaults), not a misconfiguration.
  Deliberately independent of the Ollama governor (never touches it).

  **First real trip under the new model, observed 2026-08-23** (from
  `journalctl --user -u corporatetraveldc-thermal-ingest-guard`, recorded
  here 14:00 EDT — until this, the redesign was deployed but unexercised):

  ```
  Aug 23 12:18:22  tripped LOCKDOWN (2 load-attributed brief fallbacks/300s) fan=2313rpm
  Aug 23 12:29:35  restored (the whole stack (all 6 SWIM feeds, ingest-core,
                   poller, pusher, runner, ollama.service)) at 56.75C load=3.73
  ```

  Three findings from that single event: the **third trigger** (Ollama
  contention, not temperature and not `load1`) is what actually fired; the
  full-stack scope really does reach the host `ollama.service`
  (independently corroborated at the time — `systemctl show ollama.service
  -p ActiveEnterTimestamp` then read 2026-08-23 12:29:29 EDT, the restore);
  and the 2026-08-21 restore-verification path completed with no `RESTORE
  FAILED` alert. Same-day trips at 00:11 / 07:32 / 08:08 still logged
  `tripped tier 2 (load 16.30 / 14.10 / 14.17)` under the *old* ladder, so
  the cutover landed between the 10:28 restore and 12:18 — useful if
  anyone reconciles that journal later and wonders why one day's lines use
  two different formats.

  **Second LOCKDOWN the same day, added 2026-08-23 ~15:00 EDT.** The event
  above was the first, not the only one: the guard tripped LOCKDOWN again on
  the same third trigger at **14:34:42** (`2 load-attributed brief
  fallbacks/300s`, fan 3424 rpm) and restored the whole stack cleanly at
  **14:45:51** (`56.75C load=1.29`), again with no `RESTORE FAILED`. Two
  clean full-stack cycles in one afternoon, so the mechanism is exercised
  rather than proven on a single data point. Consequence for the
  cross-check above: `ActiveEnterTimestamp` on `ollama.service` now reads
  **2026-08-23 14:45:46 EDT**, the *second* restore — that command tracks
  the most recent LOCKDOWN restore, so re-derive it from the guard's own
  journal rather than expecting the 12:29 value.

**Real data justifying both existing, and neither being loosened:**

- **Argon ONE case (2026-07-26, before the case swap):** 75-minute sample
  with the fan confirmed at its rated max (5000 RPM) and every other
  mitigation already engaged: min 66.1°C / max **83.7°C** / avg 74.07°C,
  in tier-2 (all ingest shed) for **57.5%** of the sample. Governor pause
  cycles compressed to 1-4 minutes apart by the end of a 9h39m session (27
  total trips). The Ollama process itself was paused 92% of its runtime
  and still couldn't hold the average down. See
  `docs/benchmarks/THERMAL_BASELINE_2026-07-26.md` for the full pull.
- **Same night, DIY case, same workload:** materially longer gaps between
  governor trips (tens of minutes, not single digits) within the first
  couple hours post-swap -- full comparison numbers to be finalized once
  that window is complete, same benchmarks doc.

**Why this matters:** this is direct, timestamped evidence that even
*with* both guardrails active, this hardware (in an undersized-cooling
enclosure) still spent well over half its time in the most aggressive
shedding tier and still hit temperatures the SoC datasheet flags as
throttle territory. Removing or loosening either threshold would not free
up "wasted" headroom -- there was no headroom. It would trade a visible,
managed degradation (ingest paused, alert fired, auto-restored) for
silent throttling or a thermal shutdown.

**Keepwarm-bug confound, flagged 2026-07-27, and why it does not change the
case verdict:** the Argon-case sample above and
`docs/HARDWARE_GUIDANCE.md` / `docs/tickets/argon-support-ticket-2026-07-26.md`'s
conclusions were captured while `scripts/ollama-keepwarm.sh`'s
residency-check bug was active (see section 5) -- that bug had `ollama`
running its log file back to 2026-07-26 14:22, meaning it was almost
certainly inflating the load during this same 75-minute sample. The exact
83.7°C max / 74.07°C avg / 57.5% tier-2 figures should be read as upper
bounds, not clean baseline numbers -- some portion of that load was the
bug, not organic brief-generation demand.

That said, this does **not** put the Argon hard-pass verdict in question,
for one specific reason: the same bug was active during the DIY-case
comparison sample too (`ollama.service` restarted at 23:06:36 that night,
right when the case was physically swapped, and the buggy script resumed
its every-2-minute misfire immediately in the new boot). Both cases were
therefore carrying the same kind of bug-inflated load when compared
against each other, and the DIY case still recovered meaningfully faster
between governor trips (Baseline D's 6-15 minute gaps vs. Baseline C's
terminal 1-4 minute gaps) -- which is the finding `docs/HARDWARE_GUIDANCE.md`
already leans on ("that's the real, useful difference: recovery, not
ceiling"), not the absolute peak/average numbers. An apples-to-apples
comparison under equally-inflated load still favored the DIY case, which
is why the operator has already retired *both* physical Argon units in
favor of the DIY enclosure -- that decision predates this write-up and
isn't waiting on a clean re-measurement to be finalized.

`docs/tickets/argon-support-ticket-2026-07-26.md` stays staged as-written
and unsent -- it documents the real data that was in hand at the time and
is being kept as the record of what was actually measured, not as a
pending action item. Whether it's ever sent to Argon's support team, and
whether a clean re-measurement happens first, is the operator's call to
make later; it isn't blocking anything in this commit.

---

## 5. Approval-gated sudo grants — real-world justification, 2026-07-27

`docs/SUDO_JUSTIFICATION_PROPOSAL.md` documents a passwordless-sudo model
for two narrow grants (`ollama.service` start/stop/restart, `dnf remove`/
`autoremove`), each gated behind an explicit ntfy Allow/Deny tap -- never
freely usable just because the sudoers entry exists. The keepwarm
incident this same day is the concrete case that motivated wanting the
`ollama.service restart` grant in the first place: a stuck inference
backlog (see the "keepwarm" fix in the pending commit) was diagnosed live,
and restarting the service was considered as a clean-slate option before
the underlying script fix alone was confirmed to have already resolved it
(CPU and temp both dropped to normal within minutes, without a restart).

A real restart request was pushed through the mechanism anyway per
operator instruction, as a live test of the actual approval flow rather
than a synthetic one: request created, ntfy Allow/Deny pushed, polled for
its full 600-second TTL, received no tap, and correctly expired --
treated as a denial, `ollama.service` was NOT restarted, fail-closed
exactly as designed. This is the first real (non-synthetic) invocation of
the mechanism since the sudoers file was installed and verified via
`sudo -n -l`.

---

## Bottom line

Every guardrail on this list -- network scoping (pending), memory caps,
CPU quotas, and the two thermal governors -- is backed by a real incident
or a real sustained-load measurement on this exact hardware, not a
theoretical worst case. If a future change proposes removing or loosening
any of them, the bar is: show a comparable sustained-load measurement
(not an idle/light-load test) demonstrating the guardrail is no longer
needed, the same way the DIY-case comparison is being used to validate a
hardware change here.
