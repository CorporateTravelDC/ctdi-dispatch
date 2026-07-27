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
| **July 2026, estimated full-month** | **4.30 TiB** |

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

**Current guardrail:** none is fully deployed yet at the network layer --
this is the one item on this list still flagged as an open fix (topic-
scope the SWIM subscription to DC-region facilities, or gate the ingest
containers to active-use windows). The CPU/memory caps on each ingest
container (below) exist independently of this and don't address
bandwidth directly. **Don't remove the plan to scope this** on the theory
that "it's just background data" -- the vnstat numbers above are what
"unscoped" actually costs on this link.

---

## 2. Memory guardrails — per-container `Memory=` caps

Every container in this stack runs with an explicit `Memory=` cap in its
Quadlet (`~/.config/containers/systemd/*.container`). Real values from
this deployment:

| Container class | `Memory=` | Rationale |
|---|---|---|
| Ingest (core/itws/notam) | 256m | Lightest feeds, smallest footprint |
| Ingest (tbfm) | 320m | |
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

Every container also carries `CPUWeight=100` (proportional share, not a
hard pin -- only matters under real contention) and a `CPUQuota=`
ceiling. Real values:

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
(in place since 2026-07-09/11, confirmed via file timestamp) sets
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

**Don't remove or raise these ceilings** without re-running the same
kind of sustained-load thermal test used for the case comparison below --
a CPU cap increase on any container is effectively a thermal-load
increase on this hardware, not just a performance change. Also don't
assume a CPU/memory cap alone will catch every future application-level
runaway -- see section 5's incident, where the fix that actually mattered
was in application code, not a cgroup limit.

---

## 4. Thermal guardrails — `ollama_governor.py` + `thermal-ingest-guard.py`

Two independent, non-interacting thermal safety mechanisms:

- **`ollama_governor.py`** (root-owned, native host process, not
  git-tracked): SIGSTOP/SIGCONT on the Ollama inference process at
  ~75-77°C pause / ~67-68°C resume.
- **`thermal-ingest-guard.py`** (added 2026-07-26, `docs/benchmarks/
  THERMAL_BASELINE_2026-07-26.md` has the full build rationale): sheds
  the heaviest SWIM ingest containers in two tiers (74°C / 79°C), restores
  once temps hold below 65°C for 5 minutes. Deliberately tunable via
  `THERMAL_GUARD_*` vars in `dispatch.env`, deliberately independent of
  the Ollama governor (never touches it).

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
