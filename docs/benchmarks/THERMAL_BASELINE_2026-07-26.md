# Thermal Baseline — 2026-07-26 (Argon ONE case, pre-DIY-case-revert)

Captured before the operator reverts to the DIY case tonight, for direct
comparison once the DIY case is back in with all the same software fixes
(thermal-ingest-guard, CPU governor=schedutil, argonone-cli aggressive fan
curve committed to the board, N829AW static-reg scrub, RSS retry fix).

## Baseline A — "Thursday night through this morning" (pre-fix window)

Requested window: Thu 2026-07-23 22:46 EDT -> Sun 2026-07-26 08:45 EDT
(journald boot -3, confirmed via `journalctl --list-boots`).

**No data available.** `ollama-governor.service` has zero journald entries
in this entire boot -- its first-ever "Started" event in journald history
is this morning, 2026-07-26 09:45:49 EDT (right when the Argon case was
reseated / i3-autologin work began). Whatever governor activity happened
before this morning, it wasn't running as a systemd-managed unit that
logged to journald, so there's no automated cycling telemetry to pull for
that window. Not fabricating numbers here -- if a Thu-Sat baseline matters,
it'll have to come from whatever the operator remembers/reported at the
time, not from a log.

## Baseline B — Today, full session in Argon case (with fixes), boot 0

Window: 2026-07-26 10:24:47 -> 20:03:24 EDT (~9h39m), ollama-governor
pause/resume cycles:

| # | Alert (paused) | Resumed | Pause duration |
|---|---|---|---|
| 1 | 10:25:46 | 10:52:36 | 26m50s |
| 2 | 11:06:58 | 11:18:25 | 11m27s |
| 3 | 11:30:33 | 12:41:23 | 1h10m50s |
| 4 | 12:41:31 | 13:43:02 | 1h01m31s |
| 5 | 13:43:08 | 14:34:11 | 51m03s |
| 6 | 14:34:17 | 16:59:58 | 2h25m41s |
| 7 | 17:01:44 | 18:25:28 | 1h23m44s |
| 8 | 18:27:16 | 18:42:24 | 15m08s |
| 9 | 18:42:33 | 18:55:01 | 12m28s |
| 10-27 | 18:56:11 -> 20:03:24 | (see below) | mostly 1-4 min each |

**Clear inflection point around 18:27** (right when the aggressive
argonone-cli fan curve was committed to the board and the case was
handled/reseated during this session): cycling frequency goes from
"11 minutes to 2.5 hours between pauses" to "pausing again every
1-10 minutes, continuously." 27 total ALERT events in 9h39m, with roughly
two-thirds of them packed into the last 90 minutes of that window.

## Baseline C — Last ~75 minutes specifically (the requested "last hour to
## hour and a half"), WITH all current fixes fully engaged

Window: 2026-07-26 18:50:59 -> 20:05:29 EDT, from
`thermal-ingest-guard.service`'s own 2-minute-interval temp polling
(independent, continuous, not just governor trip points):

- 40 readings, 2-min interval
- **min 66.1C / max 83.7C / avg 74.07C**
- Guard state: tier 2 (all 5 SWIM feeds shed) for 23/40 readings (57.5%),
  tier 1 for 16/40, tier 0 (normal) for only 1/40
- This is WITH: CPU governor=schedutil, argonone fan pinned at rated max
  (5000 RPM, confirmed via `sensors`), thermal-ingest-guard actively
  shedding ingest load at both tiers, and Ollama's own governor pausing on
  top of all of it -- and the box still oscillated into the 80s repeatedly.

**This is the number to beat once the DIY case is back in with the same
software stack.** If DIY-case avg/max come in meaningfully lower and
tier-2 dwell time drops, that confirms the case itself (not software) was
the bottleneck -- consistent with the sensors reading of the fan already
at its physical rated max RPM while temps kept climbing.

## Raw source commands (for re-pulling if needed)

```
journalctl -u ollama-governor.service --since '2026-07-26 10:24:44' \
  -o short-iso | grep -E 'ALERT|Resuming|initialized'

journalctl --user -u corporatetraveldc-thermal-ingest-guard.service \
  -o short-iso | grep 'temp='
```

## Addendum — Ollama process load itself (2026-07-26 20:17 EDT, still Argon case)

Operator flagged the `llama-server` process in htop showing an "absurd"
multi-hour CPU time. Checked -- it's real, and it tells its own story
independent of the SWIM-feed thermal data above.

**Process:** `llama-server` (the actual inference child of `ollama serve`),
started ~11:30:12 today.

| Metric | Value |
|---|---|
| Wall-clock lifetime so far | 8h47m25s |
| Cumulative CPU time | 2h05m29s |
| Live %CPU (snapshot) | 23.7% |
| RSS | 2.9 GB (18.0% of 15 GiB total) |
| Threads requested (`-t`) | 2 (llama.cpp spawns more for batch/prompt work) |

**Cross-referenced against `ollama_governor.py`'s own pause/resume log for
that same window:** the process was SIGSTOP'd (thermally paused) for
**486.8 of its 527 minutes of wall-clock life -- ~92% of the time.** It
was only actually allowed to run for about **40.6 minutes total.**

That means: in the ~40 minutes it *was* running, it burned 2h05m of CPU
time -- **~309% average CPU utilization while active**, i.e. saturating
3+ of the Pi's 4 cores flat out every time the governor let it resume.

**Correction, added 2026-07-27:** this section originally read "this is
not a leak or runaway bug... getting stopped and started over and over by
the thermal governor, exactly as designed." That characterization was
wrong. Live-diagnosed the next day: `scripts/ollama-keepwarm.sh` (the
process named in passing above) had a residency-check bug that made it
misread the model as never-resident and fire an uncapped `/api/generate`
warm-up call on every single 2-minute cycle, forever -- combined with
`ollama.service` running `-np 1` (one processing slot), this built an
ever-growing backlog of never-finishing generations. That backlog, not
"legitimately CPU-heavy inference workload... exactly as designed," is
what was actually keeping `llama-server` saturated across this entire
window. See `docs/GUARDRAILS_JUSTIFICATION.md` section 5 and the
2026-07-27 commit fixing `ollama-keepwarm.sh` for the full root-cause
writeup and live verification (CPU dropped from continuous ~95-190% to
0.0% within ~90 seconds of the fix, no service restart needed). This also
means the 92%-paused / ~309%-while-active figures above were measuring a
process partly cooking itself with its own bug, not purely "many skills
calling Ollama" as originally described.

**Why this still matters for the case comparison, with that correction
applied:** the SWIM-feed numbers in Baselines B/C above stand on their
own and are not affected by the keepwarm bug (SWIM ingest is a separate
set of containers). The Ollama-load portion of this Addendum means
Baseline C's 83.7°C max / 74.07°C avg / 57.5% tier-2 figure should be read
as an upper bound rather than a clean number -- some of that load was the
bug, not organic demand. That said, the same bug was also active during
Baseline D below (`ollama.service` restarted at the exact moment of the
case swap, and the bug resumed misfiring immediately in the new boot), so
the two samples were compared against each other under equally-inflated
Ollama load, and Baseline D still recovered meaningfully faster between
governor trips. The case-comparison conclusion -- recovery time, not peak
temperature, is where DIY wins -- holds regardless of this correction; see
`docs/GUARDRAILS_JUSTIFICATION.md` section 4 for the full writeup. The
fan-at-rated-max-RPM finding and the governor pause/resume mechanics
themselves are unaffected either way.

## Baseline D — DIY case, post-revert, FULL ingest load (2026-07-26 23:06-23:56)

Captured right after the physical case swap + reboot. **Correction to an
earlier same-night verbal summary:** initially reported this looked like
a clean win on raw temps -- it isn't, once the numbers are pulled
properly. The real, defensible improvement is in cycle frequency, not
peak/average temperature.

**What was actually running:** all 7 SWIM ingest containers, confirmed
via `podman ps` (~48min uptime each) -- full load, not shed. This
matters because `thermal-ingest-guard.py`'s persisted state file still
said "tier 2" (shed) from before the reboot the entire time (a stale-
state bug, since fixed -- see below), so this data point is genuinely
full-load, not a lighter comparison than the Argon baseline.

**Governor pause/resume cycles since reboot (23:06:21-23:56:10):**

| Alert | Resumed | Duration |
|---|---|---|
| 23:12:07 | 23:27:32 | 15m25s |
| 23:29:37 | 23:36:18 | 6m41s |
| 23:36:22 | 23:49:39 | (4s gap before this alert re-tripped) 13m17s |
| 23:49:45 | (ongoing) | -- |

Two near-instant re-trips worth reporting honestly (not cherry-picking
the good gaps): resume-to-next-alert gaps of ~5s and ~6s occurred twice,
meaning the board was right at the edge and Ollama's next burst pushed it
straight back over. Still, **compared to the Argon case's terminal state
(1-4 minutes between EVERY cycle, continuously, for the last 90+ minutes
of that session)**, gaps of 6-15 minutes between most cycles here is a
real, meaningful drop in cycling *frequency*.

**Raw temp readings from thermal-ingest-guard's own polling, same window,
steady-state (excludes the first 3 readings during the cold-boot ramp):**

- n=24, 2-min interval
- **min 69.4C / max 82.6C / avg 75.34C**

**Honest comparison against Baseline C (Argon case, 75min sample):** min
66.1C / max 83.7C / avg 74.07C, but that sample had real tier-2 shedding
active 57.5% of the time (ingest genuinely reduced). This DIY sample
carried the *full* ingest load the entire time. So on raw temperature
terms, DIY (75.34C avg, more load) vs. Argon (74.07C avg, partial load
shed) is not a clean win -- it's roughly comparable, arguably slightly
worse in raw average, but under a heavier load. **The peak (82.6C vs.
83.7C) is essentially the same ballpark.**

**Where the real improvement is:** not the ceiling, the recovery. The
higher-RPM PWM fan in the DIY case appears to bring the board back down
past the resume threshold meaningfully faster and more often holds a
longer stable stretch before re-tripping, which is why cycle-to-cycle
gaps stretched to 6-15 minutes instead of the Argon case's terminal 1-4
minutes -- even while carrying more simultaneous load. That's a real,
useful difference. It is not evidence that the DIY case runs meaningfully
cooler at the peak.

## Bug found and fixed during this comparison

`thermal-ingest-guard.py` had no mechanism to reconcile its persisted
tier against actual container state after a host reboot -- a reboot
brings every enabled Quadlet back up regardless of what tier the guard
last recorded, so the state file said "tier 2, shed" for the entire
23:06-23:56 window while all 7 containers were, in fact, running. This
silently made the guard inert (neither the trip logic nor the resume
logic would fire with tier stuck at a stale non-zero value). Fixed: the
guard now checks whether the feeds implied by its persisted tier are
actually running, and resets to tier 0 if so, before evaluating this
run's conditions. Verified live -- correctly detected and corrected the
stale tier=2, then resumed normal operation.
