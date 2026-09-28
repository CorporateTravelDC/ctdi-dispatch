# Live State Check — 2026-08-23

Written ~09:55–10:00 EDT against HEAD `447dd4a` ("Sync repo with live
state: research-board-mirror + 3 opsplan/freshness/summary timers;
re-sign manifest" — the operator's own commit, made mid-session), with a
further 74-file staged-but-uncommitted changeset on top of it representing
this session's documentation reconciliation work (see below). This is the
**designated drift benchmark state** — the reference point future drift
checks (automated or agent-driven) should diff against, not a passive
historical record only. Produced by three sequential independent Fable
passes over the course of the overnight 2026-08-22/23 session: (1) a
live-system-first sweep across ~29 repo doc files, (2) an independent
re-verification of that sweep against live state, (3) a cross-consistency
pass checking CLAUDE.md and the other docs agree *with each other*, not
just each against live state. Full detail of what each pass found and
fixed is in the conversation record and CLAUDE.md's own dated entries —
this file is the compact, checkable snapshot of the resulting state, in
the same format as the 08-12…08-19 checks before it.

## Live snapshot verified

- **Manifest clean.** `scripts/verify-manifest.sh` → `OK — signature
  valid, all 698 files match`. The 15-min `corporatetraveldc-integrity-sweep`
  timer's most recent run (09:47:26) predates the final sign and shows
  `failed` — expected, self-clearing at its next fire (~10:02 EDT), the
  same designed steady-state this file's 08-19 predecessor documented.
- **Test suite: 194 passed, 1 failed** — the one failure is the
  pre-existing, unrelated `test_smes_parser_basic` (marine-detection
  parser), confirmed unrelated to anything touched this session.
- **`scripts/check-claude-md-drift.sh` (full set) → all 9 checks `[OK]`,
  `CLAUDE.md matches live state`.** No retired terms, no hardcoded unit
  counts, model count 21 (matches `build-models.sh`), single base
  `phi3:mini`, Modelfile scrub coverage satisfied, Known bad section 0
  days old, manifest+signature clean, API healthy.
- **Schema: `SCHEMA_V36`** is the current top in `src/common/db.py`.
- **Scale numbers** (all logged here as a point-in-time baseline —
  re-query rather than trust, per this file's own convention and
  CLAUDE.md's repeated warning that these move fast): 122
  `corporatetraveldc-*` user units total, 47 timers; `audit_log` 1,104
  rows; `auth_tokens` 19 rows / 6 active (all `expires_at IS NULL`,
  unchanged open item); manifest covers 698 tracked+untracked files.
- **Ingest/thermal state at check time**: thermal-ingest-guard at
  **tier 2**, all five SWIM containers (`fdps`/`stdds`/`tfms`/`tbfm`/`itws`)
  cleanly shed, `~106 min` into the shed with `below_resume_since: null`
  — load has been oscillating 5–13 for the last half hour without
  holding the required 5-minute sub-6.0 window, driven by this session's
  own heavy background agent activity (three sequential Fable
  reconciliation passes). This is the guard working as designed under
  genuinely sustained load, not a bug — see CLAUDE.md's "Ingest
  load-shedding" section. Being actively monitored for the actual
  restore as this file is written; not yet resolved at write time.
- **`corporatetraveldc-integrity-sweep`** is the only currently-`failed`
  unit, for the stale-pre-sign reason above — matches CLAUDE.md's Known
  bad section, which already names it for exactly this self-clearing
  reason.

## What changed to reach this state (since the 2026-08-19 check)

Compressed pointer, not a repeat of the full history — see CLAUDE.md's
own dated entries for detail on each:

- Four parser/fallback bugs found by a blind audit and fixed: NWWS
  WFO-filter, NWWS WPC key mismatch, `runsheet` duplicate-insert, ACARS
  zero-rows instrumentation.
- TFMS AFP handler added; GDP/GS `program_id` ElementTree-truthiness bug
  fixed, with a new `key_scheme`/`legacy_correlate_id` versioning system
  (`SCHEMA_V36`) so the fix doesn't silently re-key or lose historical
  `nas_programs` data.
- A real overnight incident: ITWS/STDDS/TBFM/notam silenced for hours
  during genuine severe weather, root-caused to the
  `bandwidth_priority=ollama`/`weather` backpressure valve stacking
  under chronic high load, compounded by a hung skill run. Mitigated
  live (Ollama stopped/restarted, hung skill killed, flag cleared) and
  documented in depth.
- A separate, real `swim_client.py` bug found during that incident —
  `SWIM_NMS_SKIP_FEEDS` was clobbering the *shared* `feed_state` table
  for feeds a container doesn't own, a stale pattern from before the
  per-feed-container split — fixed (log-only now).
- New skill `entity_tracking_digest.py` built, deployed, and confirmed
  firing on its real 6-hourly schedule (06:12 EDT run succeeded, 14
  findings).
- Three-pass documentation reconciliation (this file's own subject):
  ~29 files brought into parity with live state, independently
  re-verified, then cross-checked against each other and CLAUDE.md —
  concrete example finds: `docs/COMPLIANCE_SECURITY.md`'s `audit_log`
  row count was stale by two orders of magnitude (12 vs. now 1,000+);
  `docs/INFRA_MAP.md` had a whole section describing the Ollama
  resource-governance drop-in as "never installed" days after it
  actually was; CLAUDE.md itself had an internal self-contradiction
  about whether `ollama_governor.py` is a cron job or a systemd unit
  (it's a unit); a "prompt cache disabled" resolution note cited
  `journalctl --user` evidence for a root-scope **system** unit, which
  is vacuously always-empty regardless of truth — replaced with the
  real system-journal evidence.

## Known bad — still accurate, re-verified

Matches CLAUDE.md's own Known bad section at check time: only
`corporatetraveldc-integrity-sweep` shows `failed`, for the documented
stale-pre-sign reason. `runner-demo` still crash-looping
(`sqlite3.OperationalError`, `:8005` refuses connections) — same
unresolved root cause as every prior check, `NRestarts` in the tens of
thousands and climbing, not worth citing an exact figure per CLAUDE.md's
own re-query-don't-trust convention for that counter.

## How to use this file as a benchmark

A future drift check — scripted or agent-driven — should read this file
first and diff *specific claims* against current live state, rather
than starting from zero: does `verify-manifest.sh` still say `OK`? Does
`SCHEMA_V36` still lead `src/common/db.py`, or has it moved further? Are
the same units still the only `failed` ones? Has the ITWS/bandwidth-priority
incident's root cause recurred? This file is accurate as of the commit
and timestamp at its top — treat anything beyond that boundary as
unverified by this snapshot, the same discipline every `LIVE_STATE_CHECK_*`
file before it has followed.
