# Argon 40 Support Ticket — Draft

**[Fill in before sending: order number, exact case model/SKU — referred to
generically below as "Argon ONE" since two units were tested and neither
model number is in hand here. Attach a photo of the case label if you have
one.]**

**Subject:** Sustained thermal throttling on Raspberry Pi 5 under Fedora — fan confirmed at rated max RPM, case still can't hold safe temps under sustained CPU load

Hi Argon team,

I'm running an Argon ONE case (V3M2 form factor, active fan + heatsink) on
a Raspberry Pi 5 Model B Rev 1.1, and I'm seeing sustained thermal
throttling that the case's own cooling can't resolve, even with the fan
confirmed running at its physical maximum.

**What I'm reporting, not asking you to fix on the spot:** I know Fedora
is not this case's supported/tested OS (that's on me, and I'm not
expecting official Fedora support). I'm filing this because the failure
mode — fan already at 100% and still unable to hold temp — is something
your team may want on record regardless of OS, since it points at the
case's heatsink/airflow design under sustained load rather than anything
fan-curve or driver related.

**Hardware / OS:**
- Raspberry Pi 5 Model B Rev 1.1
- Fedora Linux 44 (Workstation Edition), aarch64
- Argon ONE case (fan + heatsink), argononed v0.4.1, controlled via `argonone-cli`
- Two separate physical Argon ONE units tested, same result on both

**What I tried before filing this:**
1. Set an aggressive fan curve via `argonone-cli --temp0/1/2 --fan0/1/2 --hysteresis --commit` (persisted to the board itself, confirmed surviving reboot).
2. Confirmed via `sensors` that the fan was running at **5000 RPM — its own documented rated maximum** (`gpio_fan-isa-0000: fan1: 5000 RPM (max=5000 RPM)`).
3. Tuned the host CPU governor (`performance` → `schedutil`).
4. Reduced background CPU load by ~68% (stopped five background data-ingest processes consuming the largest CPU share).

None of the above meaningfully changed the outcome.

**Observed behavior (real measurements, not simulated):**
- Sampled 40 SoC temperature readings at 2-minute intervals over a 75-minute window (18:50–20:05 local) while the fan was confirmed pinned at 5000 RPM: **min 66.1°C, max 83.7°C, average 74.07°C.**
- The board's own thermal management (our software-side pause/resume governor, independent of Argon's fan control) tripped **27 times over a 9-hour, 39-minute session**, with the interval between trips collapsing from roughly 1–2.5 hours apart early in the session to **1–4 minutes apart** by the end, despite the fan already at max and background load already reduced.
- A CPU-bound background process (LLM inference) was actually stopped (thermally paused) for **92% of its runtime** during this window and still couldn't bring the average down — whenever it was allowed to resume even briefly, temps climbed right back into the high 70s/low 80s.

**Comparison point:** the same Pi 5, same workload, same OS, moved into a
different (DIY/open-frame, higher-RPM PWM fan) enclosure the same evening
and immediately showed longer intervals between thermal events (tens of
minutes instead of single-digit minutes) under comparable load. I'm happy
to share the full before/after numbers if useful for your engineering
team — I have them logged.

**Ask:** No specific resolution requested — flagging this as a real-world
data point for a sustained-load use case (24/7 background compute, not
bursty desktop use) that this case's cooling doesn't appear built for,
independent of any Fedora-specific driver issue. If your team has
guidance on airflow/heatsink contact for sustained-load scenarios, or
wants the full data set, I'm glad to provide it.

Thanks,
the operator
[operator LLC], LLC
