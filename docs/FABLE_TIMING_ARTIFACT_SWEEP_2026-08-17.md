# Fable Timing / Artifact Sweep — 2026-08-17

Branch: `fable-timing-artifact-sweep-2026-08-17` (all changes staged/uncommitted
— commit authority is the operator's). Written during the sweep, ~14:45–19:00
EDT, on the live box under genuine production contention (no synthetic load,
no idle measurements). Follow-on to the Phase-3/4 timing baseline (plan
`joyful-mapping-crown`; ~53 s persona-only worst-case reference, per-skill
measured timeouts of 2026-08-15).

Everything below is verified against live state, journals, `brief_archive`,
the second-brain vault, or a real re-run — file/ID citations throughout.
Mirrored to vault `Docs/fable-timing-artifact-sweep-2026-08-17/`.

---

## 0. Headline reality check: the "just-rebuilt models" were NOT rebuilt

The task premise was that all 21 `corporatetraveldc-pi5-*` models had just
been rebuilt from the freshly-edited Modelfiles (the "never write code"
persona rule). Live state at 14:46 EDT said otherwise, and it never changed
during this sweep:

- `ollama list`: `ep-advance` model is **19 h old** (built ~19:46 08-16,
  BEFORE today's 13:14 EDT Modelfile edits); the other 20 are **40–46 h old**
  (the 08-15 Phase-4 builds). `ollama show --modelfile` on `ep-advance` and
  `ops-brief` confirms **no live model contains the new persona rule**.
- The edits sat uncommitted and unsigned until the operator committed
  `b3a914b` + re-signed the manifest at **14:48 EDT** (landing the commit on
  THIS branch, which I had already checked out — noted, not my commit).
- A rebuild is STILL blocked at this writing: `build-models.sh`'s GUARD-0
  collective check fails on `docs/LIVE_STATE_CHECK_2026-08-17.md`, which a
  parallel live-state-check session edited after the 14:48 re-sign (1 file
  mismatch), and re-signing requires the operator's GPG passphrase (verified:
  loopback signing fails, `security/signing.env` key `3B2975…C1631`).
- What IS live since ~14:49 EDT: the **Python-side content guard** in
  `ep_advance_brief.py` (`b3a914b`), baked into the 14:48:35 poller image
  (verified by grep inside the running container).

So: every timing/artifact observation below is against the **current live
(pre-persona-fix) models** — which is also exactly what production has been
pushing all day. Deploy steps for the operator are in §7.

## 1. The EP-advance failure class is ~28 %, not 1/40 — five distinct shapes

Automated sweep of all 653 archived briefs since 2026-08-01 (code-fence /
meta-commentary / duplicate-line / degenerate-run detectors, plus manual
reads), full detail per specimen in the vault mirror:

| id | when (Z) | shape |
|---|---|---|
| 1561, 1568 | 08-16 05:54 / 08:55 | meta-commentary ("The provided text does not…") inside the brief |
| 1587 | 08-16 20:54 | `=== EXTENDED VENUE MATRIX ===` repeated ×20 to the token cap |
| 1595 | 08-17 01:45 | the operator's specimen: "doesn't contain a programming tutorial" + broken Python block, pushed as a real brief |
| 1597 | 08-17 16:58 | "Branson, MO \| Branson, MO (~50mi W via I-44)" ×6 — invented venue (grafts Middleburg's real W99 airstrip onto Branson), repetition loop |
| 1598 | 08-17 17:46 | "The Grove at Monticello \| Monticello, VA (~35mi NW via I-66)" ×9 — invented venue + loop, the very next run after 1597 |
| 1600 | 08-17 18:43 | brief tail devolves into empty `- [ ]` checkboxes repeated to the cap |
| 1602 | 08-17 20:10 | one real line, then ~370 empty `- [ ]` checkboxes to the cap — pushed to the phone mid-sweep |

That is 8 degenerate outputs out of ~26 ep-advance runs in the 44 h window —
**~30 % overall, and 4 of 4 (100 %) of today's completed runs** (1597, 1598,
1600, 1602). The model serving all four is last night's ~19:46 EDT rebuild
from commit f57744d's "attempt EP-advance leak fix" — whose own message
already records "Confirmed via two live tests this does NOT stop the model
from echoing section instructions — needs a deeper prompt restructure, not
a fix." The degenerate shapes predate that rebuild (1587 is older), but
today's rate is the worst observed, and the empty-checkbox shape first
appears after it. The "Branson ×6" and mixed-geography hotel matrix the operator saw
were not a one-off: the SAME loop shape recurred on the next two consecutive
runs today (1597, 1598), and none of these venues exist in the skill's
`EXTENDED_VENUES_50MI` input — they are hallucinated in the *format* of the
input matrix. The already-applied code-fence guard catches ONLY shape 1595.

**And it is not only ep-advance:** the gig-economy watch's vault note
`04-Syntheses/daily/gig-economy-watch-daily-2026-08-17.md` (18:01Z today)
ends with `### Instruction (More Diff0.5, with added constraints):` followed
by a near-verbatim regurgitation of the entire shared dispatcher persona —
a **system-prompt leak persisted permanently into the second-brain vault**,
truncated mid-rule at the token cap.

### Fix applied (staged): shared response guard, `common/llm.py`

`sanitize_llm_response()` — persona-echo cut markers (the persona text is
identical across all 21 models, so its distinctive phrases are reliable),
≥3× identical long-line repetition → discard to deterministic fallback,
≥5× identical consecutive short-line runs (blank-transparent) → trim, and
(on `done_reason == "length"` only) trailing incomplete-sentence trim.
Wired into `_ollama()` (every `generate()` caller) and both of
`ep_advance_brief.py`'s direct call sites.

**Verified against real data, not hypothetically:** replayed over all 653
archived briefs + 23 HIGH OSINT narratives + today's vault notes — catches
exactly the four garbage briefs (1587, 1597, 1598, 1600 — 1595 is caught by
the existing code-fence guard) and trims the gig persona leak at char 848
keeping the 844 chars of real content; **zero false positives** on the other
649 briefs.

## 2. ops-brief NWS/Amtrak prose gap — actual root cause (three stacked causes)

Investigated from real archived output (ids 1577–1601), live journals, the
live DB, and the Modelfile — not from the task's framing. The operator's
"prose drops NWS and Amtrak" is real and has **three independent causes**,
in descending order of impact:

**(a) 500-token output cap + input-order echo → NWS/Amtrak always fall off
the end.** Every sampled brief ends mid-sentence at the cap: 1599 stops at
"Closure SAN at", 1601 (15:13 EDT today) at "LGA/JFK ground stop", 1596/1586
mid-NAS-line. phi3:mini does not follow the briefing structure; it walks the
data pull in INPUT order (CPS → TFR → METAR → NAS → ATCSCC → NWS → AMTRAK,
several briefs literally echo the "OPS BRIEF DATA PULL" header as their first
line) and exhausts 500 tokens (~375 words) on the aviation blocks before
reaching the NWS/Amtrak blocks at the tail. The commit-f57744d anti-echo
trailing instruction (08-16) changed verbatim echo into paraphrased echo but
neither the ordering nor the truncation. Fix applied (staged):
`max_tokens`/`num_predict` 500 → 900, `OLLAMA_TIMEOUT` 1200 → 2000 (same
Phase-3/4 derivation formula, documented at the constant), unit
`TimeoutStartSec` 2600 → 3600 in parity. A/B-verified — §3.

**(b) The upstream fetches themselves fail, with no local fallback.**
Real logged instances TODAY: `api.weather.gov` alerts read-timeout (10 s) at
14:00 and again at 15:00 EDT; `aviationweather.gov` METAR timeout at 14:00.
When that happens the prompt says "NWS alerts unavailable." even though the
platform's own ingest keeps fresh caches: `nws_alerts` had 5 active alerts
(≈2 h old) and `amtrak_status` had a 1-minute-old row carrying **real NEC
delays (#95 NE Regional +88 min, #2155 Acela +72 min, #2118 Acela +60 min)**
at test time. METAR already had a local-DB fallback; NWS/Amtrak did not.
Fix applied (staged): `_nws_alerts_from_db()` / `_amtrak_from_db()` (30-min
freshness cap on Amtrak), same pattern as `_metar_section()`.
**Verified by real re-run** with `_fetch` forced dead: both sections produce
real cached data (`[Severe] Flood Watch — …(local-db)` / `9/51 trains
delayed … Acela +72min (local-db, 1min old)`).

**(c) The live Modelfile task layer never asks for NWS at all.** The rich
12-section SYSTEM_PROMPT in `ops_brief.py` is dead code (skills pass
`system=None` since Phase 4); the actual instructions are
`corporatetraveldc.ops-brief`'s SYSTEM, whose task layer enumerates TFRs /
NAS / METAR-CPS / Amtrak — **no NWS, no ATCSCC, no route, no section
structure, no word target**. Fixing this means changing the production
prompt → per operator directive this is a **staged proposal with a real A/B/C
demo, NOT applied** — §3.

Also fixed in passing (same truncation class, each verified against a real
truncated artifact): `weekly-summary` 400 → 700 tokens (id 1589 ends
mid-word "- Train"), `ep-advance` 750 → 1000 (4 of 8 recent briefs end
mid-sentence; unit 3600 → 4500), `second-brain-weekly` 500 → 700 (vault note
`04-Syntheses/weekly/2026-W33.md` ends mid-sentence). All with re-derived
timeouts using the skills' own documented formulas. `aam-daily/weekly` also
end mid-word but state no word target — left at current caps (graceful
sentence-trim now covers them), flagged for the operator.

## 3. A/B/C demo — current prompt vs token-cap fix vs enrichment proposal

One real data pull (built by the skill's own `build_brief_content()` at
15:12 EDT, 6 993 chars — richer than the hour's production prompt because
the new DB fallbacks recovered the NWS/Amtrak data the 15:00 production run
lost to fetch timeouts), three generations against the live
`corporatetraveldc-pi5-ops-brief` model, each acquiring the production
`ollama_slot` (priority=report) so real skills queue fairly; no ntfy pushes,
no archive writes.

<!-- ABC_RESULTS -->

## 4. Timing / health scorecard — all 21 models

Method: real invocations via each skill's own normal path — the day's
production timer runs (all on the current live models, under the afternoon's
genuine 6-deep contention), plus manual `systemctl --user start` fires for
skills without a natural trigger today, plus clearly-labeled direct smoke
probes for the two models that CANNOT be exercised through their skill path
without a live VIP/POTUS TFR. Wall times from journal `Consumed …` lines.

Columns: how the model was exercised today · wall time (systemd `Consumed`)
· narrative vs fallback · py-timeout / unit-ceiling parity verdict.

| # | model / skill | exercised via | wall time | outcome | timeout parity (py / unit) |
|---|---|---|---|---|---|
| 1 | aam-daily-watch | prod timers 12:25 & 13:55 | 44m47s; **1h25m04s** | ok — but in BOTH runs one of the two framing calls lost the 150 s slot wait (12:39, 14:49) and fell back; vault note written | 1890 / 8600 — own budget fine; queue-wait is the risk (watch class) |
| 2 | aam-weekly-watch | manual fire this session (last prod 08-16 09:00, 20m18s) | see §4a addendum | prod run ok but note truncated mid-word (cap, flagged) | 2250 / 7600 ok |
| 3 | aviation-daily-watch | prod timers 13:16 & 14:46 | 32m35s; second run see addendum | ok — narrative, 6/6 items, clean note | 1380×3 / 7000 ok |
| 4 | concierge-travel-daily-watch | prod timer 13:16 | 59m07s | ok — first framing call lock-fell-back (13:31), second generated; note ends mid-sentence (cap class) | 1470×3 / 7300 ok |
| 5 | dispatch-desk-memo | manual fire this session (last prod 08-16 09:30, 41m06s) | see addendum | prod W33 memo complete and clean | 4770 / 10400 ok |
| 6 | disruption-weather-digest | manual fire 15:27 (04:35 prod run died on the integrity race) | 29m51s (**KILLED at the 1700 s ceiling**) | flagged "tightest margin of the fleet" when this row was first drafted — proved 30 min later: ~16 min of slot-wait/load before the generate call, ceiling hit 15:55:58, and **the narrative that completed during stop-sigterm (15:57:27) was SIGKILLed before the vault write** — a finished generation thrown away | 810 / 1700 — **insufficient, live kill today** → staged 3000 |
| 7 | ep-advance | prod timers 12:30 / 13:30 / 14:30 / 15:30 | 29m46s / 16m15s / 13m49s / 41m00s | narrative all four runs — but **all four briefs were degenerate** (1597 loop, 1598 loop, 1600 & 1602 checkbox runs; §1) | 2220+540 / 3600 was marginal → staged 2800+540 / 4500 |
| 8 | ep-advance-trend | inside the 12:30 prod run (12 h boundary) | (within run 1) | trend narrative coherent — the one healthy part of 1597 | 540 shared budget ok |
| 9 | executive-protection-daily-watch | prod timers 13:16 & 14:46 | 12m19s; addendum | ok — clean (0-item day) | 1560×3 / 7600 ok |
| 10 | gig-economy-daily-watch | prod timers 13:16 & 14:46 | 45m14s; 17m27s | narrative both — but the 13:16 run's vault note carries the **persona leak** (§1) | 1380×3 / 7000 ok |
| 11 | ops-brief | prod timers 14:00 & 15:00 | 28m53s; 13m57s | narrative both — both echo-truncated before NWS/AMTRAK (1599, 1601; §2) | 1200+510 / 2600 → staged 2000+510 / 3600 |
| 12 | ops-brief-trend | last real: 08-16 18:14 boundary run (1590) | (within run) | coherent trend narrative; next natural boundary 18:00 ET today | 510 ok |
| 13 | osint-monitor | poller scheduler, ~15-min cadence | sweep completions 12:18→14:24 every ~30 m | real HIGH narratives today 11:45 & 12:08 EDT — clean prose, 23/23 HIGH narratives artifact-free | 300×2-per-item / 2000 subprocess cap ok |
| 14 | route-impact | **cannot be exercised via its skill path** — LLM fires only on VIP/POTUS TFRs; none active in ≥7 days (125 routine). Direct smoke probe: see addendum | — | deterministic summaries every ~10 min by design | 480 / 2000 ok |
| 15 | tfr-enrichment | same VIP-only gate — smoke probe, see addendum | — | deterministic path healthy every ~5 min | 540 / 2000 ok |
| 16 | secondbrain-daily | prod timer 13:45 | 12m12s | ok — narrative, day-file written | 870 / 2600 ok |
| 17 | secondbrain-weekly | manual fire this session (last prod 08-16 18:15, 20m02s) | see addendum | prod W33 note truncated mid-sentence (cap → staged fix) | 2340→2940 / 5500 ok |
| 18 | trains-yachts-daily-watch | prod timer 13:16 — **KILLED at the 7000 s unit ceiling 15:14 after 1h58m; vault note lost** (§5a) | 1h58m10s (killed) | first call lock-fell-back 13:35; never finished | 1380×3 / 7000 — **insufficient under queue-wait; live kill today** |
| 19 | transport-digest | prod timer 12:25 (00:25 run died on integrity race) | 17m30s | ok — narrative, 4 425-byte vault note | 600 / 1600 ok |
| 20 | weekly-summary | manual fire this session (last prod 08-16 18:00, 5m20s) | see addendum | prod 1589 narrative truncated mid-word (cap → staged fix) | 990→1530 / 2800 ok |
| 21 | chat | real `/api/ask` POST 15:17 EDT | 112 s wall | LLM produced no first token inside its 110 s ceiling under the queue → local-resolver fallback (answered half the question); busy-probe dead code (§5e) | 110 s read / nginx 120 s — by design fail-fast, but effectively LLM-less during contention |

<!-- SCORECARD_ADDENDUM -->

## 5. Systemic timing findings (the real production pain)

**(a) The six daily-watch timers converge into lockstep and stay there.**
`OnCalendar` anchor + `OnUnitActiveSec=90min` does not preserve the 15-min
sibling stagger the timer comments assume: re-arm is from each unit's own
activation time and systemd's default `AccuracySec=1min` coalesces nearby
wakeups, so one bunching event is permanent. Observed live today: five watch
timers fired at literally the same second — 13:16:35 and again 14:46:36
(exactly 90 min later) — producing a 6-deep Ollama queue, four real 150-s
lock-timeout fallbacks (aam-daily 12:39 & 14:49, concierge 13:31,
trains-yachts 13:35), and **trains-yachts-daily-watch KILLED at its 7 000 s
`TimeoutStartSec` ceiling at 15:14 EDT after 1 h 58 m — today's vault note
lost**. Fix staged (NOT applied live — blocked by the permission classifier,
operator applies): each watch timer replaced with a fixed 24 h 90-min
calendar grid (two `OnCalendar` lines, `systemd-analyze calendar`-validated,
same cadence/anchors, stagger structurally guaranteed; a slot arriving while
the previous run is active is skipped — the load-shedding the old comments
believed they already had). Until applied, the lockstep recurs tonight.

**(b) Unit start-ceilings don't budget for slot-wait time.** The Phase-4
`TimeoutStartSec` derivations sum fixed overhead + the skill's own LLM
timeouts, but under queue contention a run can spend 1-2 h WAITING for the
slot before its own budget even starts (trains-yachts today). Parity table
per skill in the scorecard; the killed run is the live proof. The timer-grid
fix removes most of the cause; the ceilings I touched (ops-brief, ep-advance)
are re-derived in their unit files.

**(c) The 22:07 08-16 image shipped internally inconsistent (src edited
after last manifest sign) and self-failed every skill run for ~14 h** —
transport-digest 00:25, disruption-weather 04:35, freshness-audit 06:00,
second-brain-daily 09:45/11:45, second-brain-rss 22:10→12:10 (8 consecutive),
ops-brief gap 22:15→14:00 (no ops brief archived for ~16 h). All cleared by
the 12:20 rebuild. This is the second integrity-vs-deploy race today (the
12:19 re-sign race was the first); worth folding into deploy tooling: build
images only from a tree that passes the collective check.

**(d) second-brain-rss:** first post-fix run (14:10, ~16 h backlog) was
killed at its 300 s `TimeoutStartSec` at 6 m 31 s. Staged: 300 → 900
(normal runs are 1–70 s; backlog-recovery runs need the headroom).

**(e) chat's Ollama-busy probe is dead code on this box:** it checks
`size_vram > 0`, never true on CPU-only Ollama — the "model busy → local
data" fast path can never trigger; instead every chat rides its 110 s
first-token timeout. Real request at 15:17 EDT: 112 s wall, LLM never
produced a token under the afternoon queue, fell back to the local resolver
(which answered the Amtrak half of a two-part question and dropped the DCA
half). Flagged only — interactive path, operator's call.

**(f) Repo template drift:** `config/dispatch.env` (template) still ships
`OLLAMA_OSINT_MODEL=corporatetraveldc-pi5-osint` (model doesn't exist
post-Phase-4 naming; live env uses `…-osint-monitor`) and
`OLLAMA_TIMEOUT=240` (the class the 08-15 re-baseline found "could not
complete a single cold-slot call"). A fresh deployment from the template
would silently fall back on every brief. Flagged; not changed (template
values are operator policy).

**(g) `tfrs.enriched_text`/`enriched_at` columns are dead** (0 of 125 rows
ever enriched) — tfr-enrichment writes `hot_alerts` rows instead. Cosmetic,
flagged.

## 6. Everything applied vs staged vs flagged

**Applied to the working tree (staged, uncommitted, NOT live — needs
operator sign + image/model rebuild, §7):**
- `src/common/llm.py`: `sanitize_llm_response()` + wiring (persona-echo,
  repetition loops, degenerate runs, cap-hit sentence trim).
- `src/poller/skills/ep_advance_brief.py`: guard wiring both call sites;
  num_predict 750→1000; timeout 2220→2800.
- `src/poller/skills/ops_brief.py`: NWS/Amtrak local-DB fallbacks;
  max_tokens 500→900; timeout 1200→2000.
- `src/poller/skills/weekly_summary.py`: 400→700 / 990→1530.
- `src/poller/skills/second_brain_weekly.py`: 500→700 / 2340→2940.
- Modelfiles `corporatetraveldc.{ops-brief,ep-advance,weekly-summary,secondbrain-weekly}`:
  matching `num_predict` parity.
- `.config/containers/systemd/corporatetraveldc-{ops-brief,ep-advance}.container`:
  TimeoutStartSec parity (3600/4500);
  `…second-brain-rss.container`: 300→900.
- `.config/systemd/user/corporatetraveldc-*-daily-watch.timer` (×6):
  calendar-grid stagger fix.

**Staged proposal (NOT to be promoted without review + post-promotion
retest, per operator directive):** ops-brief Modelfile task-layer enrichment
— `corporatetraveldc.ops-brief.PROPOSED-enrichment-2026-08-17` (repo root),
with the §3 A/B/C evidence. Same lane: the shared persona's stale "Up to
sixteen automated skills" (21 now) across all 21 Modelfiles, and a
`repeat_penalty` experiment for the ep-advance loop shapes.

**Flagged only:** §5 (e)(f)(g), aam daily/weekly caps, MCP half-retirement
(pre-existing, see LIVE_STATE_CHECK), runner-demo crash-loop (pre-existing).

## 7. Deploy path for the operator (nothing below done by me)

1. Review this branch's diff; commit what you accept (your signature).
2. `scripts/sign-manifest.sh` (requires the LIVE_STATE doc's parallel edit
   to be settled too — it is the current GUARD-0 blocker).
3. `build-models.sh corporatetraveldc-pi5-ops-brief corporatetraveldc-pi5-ep-advance corporatetraveldc-pi5-weekly-summary corporatetraveldc-pi5-secondbrain-weekly`
   (or all 21 to pick up the persona rule everywhere — 20 smoke tests,
   plan for hours).
4. `build-images.sh` + restart poller/web/pusher + the changed quadlets/
   timers (`cp` to `~/.config/…` + `systemctl --user daemon-reload`).
5. Re-run one ops-brief and one ep-advance cycle and check
   `brief_archive` for: complete AMTRAK/NWS sections, no mid-sentence tail,
   and `llm:` guard log lines staying quiet.
