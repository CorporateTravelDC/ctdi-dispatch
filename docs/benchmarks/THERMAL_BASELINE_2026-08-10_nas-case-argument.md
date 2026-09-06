# Case Argument — 52Pi NAS Case vs. Current DIY Case (2026-08-10)

Follow-on to `THERMAL_BASELINE_2026-07-26.md` (Argon ONE vs. DIY case). That
doc's conclusion was narrow and honest: DIY didn't run cooler at the peak
than Argon, but it recovered from thermal trips faster (6-15min gaps vs.
Argon's terminal 1-4min cycling), and that recovery-time win was real even
under full ingest load. This entry adds a different kind of data point —
not another case-vs-case peak/recovery comparison, but a read on how much
headroom the *current* DIY case + stock fan actually has, captured
incidentally during an unrelated incident.

## What happened

While debugging a completely separate Ollama reliability problem (client-
side request timeouts not cancelling server-side generation), a chain of
four back-to-back model-load attempts left multiple orphaned `llama-server`
processes running concurrently, one for 20+ minutes straight at ~84% CPU.
Host load average peaked at **51.92** (15-min: 39.97) on a 4-core box —
roughly 13x a normal ~4 baseline, and the worst CPU-contention event this
box has seen recorded since the DIY case went in.

This was not a synthetic thermal benchmark. It was a genuine, unplanned
worst-case CPU storm, which makes it a more honest stress test than
anything deliberately staged.

## Temperature during the spike

Two independent loggers bracket the event at tight intervals (data pulled
directly from `thermal-ingest-guard.service` and `thermal-sample.service`
journal/CSV history, 16:00-16:24 EDT window):

| Source | Interval | Readings (°C) |
|---|---|---|
| thermal-ingest-guard | ~2 min | 62.8, 63.9, 65.55, 67.2, 65.55, 63.9, 65.55, 63.9, 64.45, 66.1, 63.9, 65.55 |
| thermal-sample | 5 min | 65.0, 63.9, 65.0, 64.5, 64.5, 63.4 |

**Peak: 67.2°C.** That's 7-8°C below `thermal-ingest-guard`'s own Tier-1
shed threshold (74°C) and Ollama governor's SIGSTOP threshold (75°C) —
neither system had to intervene at all. Zero governor ALERT entries, zero
kernel throttle messages, for the entire window.

Compare against the 2026-07-26 numbers for scale: Argon ONE case, under
software-assisted load (schedutil governor, active tier-2 ingest shedding
57% of the time, fan pinned at rated max), still averaged 74.07°C with an
83.7°C peak. Today's number — no shedding needed, full load, higher CPU
stress by any reasonable measure — never got within 7°C of that old
average.

## Fan behavior

The stock/existing PWM fan (hwmon name `pwmfan`, device node
`cooling_fan` — the real, working cooler) handled the spike without any
visible strain; post-incident live readings during a comparable 66-67°C
period showed it running around 3400 RPM / ~69% duty, well short of its
apparent ceiling. (Historical fan-speed data for the exact spike minute
doesn't exist — neither logger captured RPM before today; see "Changes
made alongside this entry" below.)

Separately, and unrelated to the spike itself: a second hwmon entry
(`gpio_fan`, hwmon3) has been reporting a constant 5000 RPM / 100% duty
this entire time. That's a dead sensor — a leftover `dtoverlay=gpio-fan`
entry from the original Argon ONE case attempt, with no physical fan
attached to the GPIO pins it thinks it's driving. It was never providing
real cooling and never will; it was only ever polluting `sensors`-style
readouts with a fabricated 100%-duty number. Removal is in progress (see
below).

## NVMe

Three NVMe temp sensors read during this same period: two at 64°C, one
(the composite/controller-die sensor) at 84°C. That's a live snapshot, not
a spike-vs-baseline comparison (no historical NVMe logging exists yet to
compare against), but two things are worth stating plainly:
- 64°C on two of three sensors, after several months of continuous
  multi-container I/O plus today's CPU storm, is a genuinely fine number.
- 84°C on the third is the one real hot spot in the whole system right
  now, and it's the one thing today's data does *not* say is fine — it's
  simply the one component the current case's airflow was never designed
  around.

## The actual argument

This is deliberately not "we need a NAS case because we're thermally
struggling." Today's data says the opposite: a real 52-load CPU storm,
worse than anything in the 07-26 comparison, stayed 7°C below every
intervention threshold using the case and fan already installed. The CPU/
SoC side of this system has proven headroom.

The honest case for the 52Pi NAS case is narrower and, for that reason,
more defensible:

1. **NVMe airflow, specifically.** The current case was never built around
   drive cooling — it inherited whatever the CPU-cooling design left over.
   84°C on one sensor is exactly the kind of number a case with deliberate
   drive-bay airflow (which is the entire premise of a NAS enclosure)
   would address directly, where the current setup addresses it not at
   all.
2. **Clean multi-drive mounting and expansion**, as a mechanical/practical
   upgrade over an ad-hoc DIY enclosure — not a thermal rescue.
3. **Retiring case-history debt.** Between the Argon ONE remnants (a dead
   fan overlay fabricating sensor readings, a disabled-but-still-installed
   `argononed.service`) and the DIY case being an interim fix rather than
   a considered enclosure, there's real accumulated cruft. A single
   purpose-built case is a chance to end that, not patch around it again.
4. **Made from a position of headroom, not desperation.** Every prior case
   decision on this box (Argon in, Argon out, DIY in) was reactive to an
   active thermal problem. This would be the first case change made while
   the current setup is already working — which is a better basis for a
   considered hardware decision than any of the ones before it.

## Changes made alongside this entry (2026-08-10)

- `scripts/thermal-ingest-guard.py`: now reads the real fan (`pwmfan` by
  hwmon *name*, not a fixed path) and logs RPM on every 2-min sample and
  in both tier-trip ntfy alerts — so the next spike, thermal or load-
  driven, has real fan data attached to it, not just temperature.
- `scripts/thermal-sample.sh`: same fan-by-name resolution, added
  `fan_rpm`/`fan_pwm_pct` columns to `thermal-samples.csv`.
- Both deliberately resolve by hwmon *name* rather than a hardcoded
  `hwmonN` path, because removing the dead `gpio-fan` overlay and
  rebooting will shift hwmon numbering — path-based lookups would have
  silently started reading the wrong sensor (or nothing) the moment that
  reboot happens.
- `/boot/config.txt`: `dtoverlay=gpio-fan,temp=65000` commented out
  (backup at `/boot/config.txt.bak-20260810`). Requires a reboot to fully
  clear the `gpio_fan` hwmon entry; not yet rebooted as of this writing.
