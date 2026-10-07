Verified against HEAD db64018 and live state on 2026-10-06 18:10Z / 14:10 ET.

# Timer `[Unit] Requires=` boot storm — root cause and standing rule

**Standing rule: a `.timer` unit must never carry `Requires=` or `Wants=`
pointing at its own `.service` in the `[Unit]` section.** The timer→service
binding is implicit from the filename (`[Timer] Unit=` defaults to the
same basename with `.service`), so the line buys nothing and costs a boot
storm. `scripts/check-timer-requires.sh` enforces this — **but see the
Findings section below: its coverage has a real gap, and two live root
timers still violate the rule today.**

## What it actually does

`Requires=`/`Wants=` in a timer's `[Unit]` section is an ordinary
dependency on that unit: **starting the timer starts the service.** It has
nothing to do with the schedule. So every such timer fires its service the
instant `timers.target` is reached — on every boot — and again on any
`systemctl --user restart <name>.timer`, regardless of `OnCalendar=` or
`Persistent=`.

Proven live 2026-09-05 18:33:16 EDT on `corporatetraveldc-thermal-sample`:
restarting **only** the timer ran the service immediately, while the
timer's own next scheduled fire was 18:35:00.

## The incident (2026-09-05 reboot, ~17:47 EDT)

61 of 67 timers carried the line. At `timers.target` (17:48:10) all of them
pulled their services in simultaneously. 14 were LLM report-tier skills
(the LLM stack at the time still ran separate hot/chat/report-tier
`ollama`-era units — since replaced by the single `corporatetraveldc-llama.service`,
see `docs/PI5-BOOT-CONFIG.md`), so their quadlet `ExecStartPre` hooks also
started the on-demand report tier alongside the resident hot and chat
tiers. Three model servers held ~9.8 GB RSS of 15 GB, forcing hard swap
thrashing (~20k blocks/s, run queue 39–62). Load1 hit 43–58 while CPU
temperature stayed unremarkable at 65°C.

`thermal-ingest-guard.py` trips LOCKDOWN at load1 ≥ 40 and sheds
`poller`/`pusher`/`runner` plus all ingest feeds. It did exactly that at
17:57–17:59 — **tearing down the stack that the boot-stagger units had
just finished bringing up.** Recovery was impossible without intervention:
resume needed load1 < 15 *and* temp < 65°C held 300s, and the 14 LLM jobs
were grinding at ~1.8 tok/s against a 2-thread server. The platform sat
down for ~30 minutes: no alerts, no PWA, no ingest.

## Why the boot-stagger units did not prevent it

They worked correctly and are not at fault. `corporatetraveldc-stack-boot-stagger`
(19 units) finished 17:56:05 and `corporatetraveldc-boot-stagger` (7 SWIM
ingest units, 20s apart) finished 17:58:16 — both clean.

The staggers only own units they start themselves. Timer-triggered services
are a **separate, uncoordinated activation path** that fires at
`timers.target`, before and during the staggered sequences, and outside
their pacing entirely.

## Prior partial diagnosis

On 2026-08-30 this exact mechanism was found on **one** timer —
`corporatetraveldc-llama-restart.timer` was self-triggering an unplanned
restart the instant the timer unit activated. The fix applied was
`Requires=` → `Wants=`. **That fix was wrong and the finding was never
generalized at the time.** `Wants=` is a weaker dependency for *failure
propagation* only — it pulls the unit in at start exactly like `Requires=`.

## Not the cause: `Persistent=true`

`corporatetraveldc-aam-daily-watch.timer` sets `Persistent=false`
explicitly, yet still fired at 17:48:11, one second after `timers.target`.
Only the `Requires=` pull-in explains that. `Persistent=true` remains a
real but secondary concern (post-outage catch-up bunching) — see
`docs/NETWORK_FAILOVER.md` "Still open" for the related note.

## Correct timer shape

```ini
[Unit]
Description=...

[Timer]
OnCalendar=...
Persistent=false

[Install]
WantedBy=timers.target
```

## Enforcement: every timer, every level (2026-10-06)

Operator standing rule: the guard captures all timers regardless of level.
`scripts/check-timer-requires.sh` scans

- every tracked `*.timer` in the repo (`git ls-files`, `retired-*` dirs excluded):
  user timers under `.config/systemd/user/` and root timers under `systemd/`
  and `systemd/system/`;
- every live user timer (`~/.config/systemd/user/*.timer`);
- every live system timer (`/etc/systemd/system/*.timer`, world-readable, so
  no privilege is needed).

It runs in the signing pass and fails it on any hit.
`tests/scripts/test_timer_requires_all_levels.py` holds the scan scope and
asserts that no tracked timer requires its own service.

Before 2026-10-06 the guard scanned user timers only, so two root timers
carried the banned line, tracked and live: `corporatetraveldc-watchdog.timer`
and `corporatetraveldc-tailscale-cert-renew.timer`. Both lines were removed in
the same change, and the installed copies are replaced by
`scripts/install-root-copies.sh`. Every other self-referential hit is inside a
`retired-*` directory, kept as history.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-ops.md`.


### Timer `[Unit] Requires=` boot storm — root cause and standing rule

~~**Standing rule: a `.timer` unit must never carry `Requires=` or `Wants=` pointing at its own `.service` in the `[Unit]` section.** The timer→service binding is implicit from the filename (`[Timer] Unit=` defaults to the same basename with `.service`), so the line buys nothing and costs a boot storm. `scripts/check-timer-requires.sh` enforces this.~~


### Timer `[Unit] Requires=` boot storm — root cause and standing rule › The incident (2026-09-05 reboot, ~17:47 EDT)

~~61 of 67 timers carried the line. At `timers.target` (17:48:10) all of them pulled their services in simultaneously. 14 were LLM report-tier skills, so their quadlet `ExecStartPre` hooks also started `corporatetraveldc-llama-report-1` — the deliberately-`disabled`, on-demand report tier — alongside the resident hot and chat tiers. Three llama-servers held ~9.8 GB RSS of 15 GB, forcing hard swap thrashing (~20k blocks/s, run queue 39–62). Load1 hit 43–58 while CPU temperature stayed unremarkable at 65 °C.~~

~~`thermal-ingest-guard.py` trips LOCKDOWN at load1 ≥ 40 and sheds `poller`/`pusher`/`runner` plus all ingest feeds. It did exactly that at 17:57–17:59 — **tearing down the stack that the boot-stagger units had just finished bringing up.** Recovery was impossible without intervention: resume needs load1 < 15 *and* temp < 65 °C held 300 s, and the 14 LLM jobs were grinding at ~1.8 tok/s against a 2-thread server. The platform sat down for ~30 minutes: no alerts, no PWA, no ingest.~~


### Timer `[Unit] Requires=` boot storm — root cause and standing rule › Why the boot-stagger units did not prevent it

~~They worked correctly and are not at fault. `corporatetraveldc-stack-boot-stagger` (19 units) finished 17:56:05 and `corporatetraveldc-boot-stagger` (7 SWIM ingest units, 20 s apart) finished 17:58:16 — both clean.~~

~~The staggers only own units they start themselves. Timer-triggered services are a **separate, uncoordinated activation path** that fires at `timers.target`, *before and during* the staggered sequences, and outside their pacing entirely. The stagger then had its output shed out from under it by the guard. Staggering more of the same set would not have helped; the unstaggered path had to be closed.~~


### Timer `[Unit] Requires=` boot storm — root cause and standing rule › Prior partial diagnosis

~~On 2026-08-30 this exact mechanism was found on **one** timer — `corporatetraveldc-llama-restart.timer` was "self-triggering an unplanned restart the instant the timer unit activated, not just at scheduled fire" (confirmed live; it killed an in-flight `aviation-daily-watch` run). The fix applied was `Requires=` → `Wants=`.~~

~~**That fix was wrong and the finding was never generalized.** `Wants=` is a weaker dependency for *failure propagation* only — it pulls the unit in at start exactly like `Requires=`. `llama-restart.timer` therefore kept firing at every boot, and the other 60 timers were never examined.~~


### Timer `[Unit] Requires=` boot storm — root cause and standing rule › Not the cause: `Persistent=true`

~~The obvious suspect is post-boot catch-up (37 tracked timers set `Persistent=true`). It is not what happened here. `corporatetraveldc-aam-daily-watch.timer` sets `Persistent=false` — explicitly, with a 2026-08-06 comment about avoiding exactly this boot race — yet still fired at 17:48:11, one second after `timers.target`. Only the `Requires=` pull-in explains that.~~

~~`Persistent=true` remains a real but secondary concern: it can still bunch catch-up runs after a long outage. That is worth a separate look, and only `cowork-coord-24h`/`cowork-coord-7d` currently set `RandomizedDelaySec=`.~~
