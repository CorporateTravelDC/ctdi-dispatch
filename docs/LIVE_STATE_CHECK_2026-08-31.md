# Live-state check 2026-08-31 post-2999633 (disruption detectors, UTM/drone, report-1 on-demand, CPU rework)

Post-commit doc-drift pass for `2999633`, run ~06:30–07:00 EDT 2026-08-31.
Scope per operator directive: does anything the current docs claim get
invalidated by THIS commit — not a from-scratch rewrite. Verified against
live state (`systemctl --user show`, `podman ps`/`image inspect`, installed
quadlet diffs, `curl`, poller journal), not against the docs themselves.

Prior art consulted first (second brain): `20260831T065821Z.md` (report-1
on-demand rollout, pre-commit, one open item: ep-advance 3600s recalibration
"STAGED, needs re-sign + poller rebuild") and `20260831T102515Z.md` (the
post-commit session consolidation — records that *everything* from the
day's passes, including RVR-into-CPS, was deliberately deployed, with the
CFR operator-verification still flagged open). This check builds on both.

## Drifted — invalidated by this commit

1. **`src/shared/watchlist_README.md` — "Three entry types" is now four.**
   Line 13 ("Three entry types: flight, train, vessel"), the file-map
   table (lines 24–28), the id-convention list (line 62), and the ntfy
   topic table (lines 173–177) all predate the `drone` entry type this
   commit added (`shared/watchlist.py` `EntryType` + `permanent_drones.json`
   in `_FILE_MAP`, verified in code). The REST/API tables in the same file
   remain accurate — no drone add/remove endpoints were created
   (`web/routes/watchlist.py` untouched; utm_watcher only *reads*
   `/api/v1/watchlist`), so only the entry-type enumeration drifted, not
   the API surface.

2. **`docs/GUARDRAILS_JUSTIFICATION.md` §3 — ingest CPU numbers stale.**
   Lines 128–129 claim "The seven ingest containers carry `CPUWeight=30`";
   this commit raised all seven to **5000** (verified live: all seven units
   report CPUWeight=5000). Three rows of the CPUQuota table also drifted:
   tbfm 80% → **100%**, tfms 90% → **110%**, stdds 120% → **140%**
   (verified live: 1s/1.1s/1.4s CPUQuotaPerSecUSec). Still correct:
   itws/notam 60%, core 80%, fdps 150%. (The section's Ollama-era
   `ollama.service.d` narrative was already on the 08-29/30 passes' open
   edit list — pre-existing, not this commit's.)

3. **`docs/ALERT_REFERENCE.md:164` — fdps-alerts family undercounts its
   contributing feed_names.** The row enumerates exactly two (`fdps`,
   `fdps_notam`); this commit added two more `fire_family_alert("fdps", …)`
   call sites, both new in 2999633 (absent from 385a5f6):
   `fdps_alt_saturation` (`fdps_parser.py:1173`) and
   `fdps_diversion_continuation` (`fdps_parser.py:1380`) — both base
   priority 4, `escalating_only=False`, `isolate=True`. Same doc's
   `flight-alerts` publisher row lists the undeployed `ais_watcher` default
   topic but not the new (equally undeployed) `utm_watcher`, whose
   `NTFY_TOPIC` also defaults to `flight-alerts` (`utm_watcher.py:64`).

4. **`CLAUDE.md` — the RVR-into-CPS "deliberately NOT deployed" paragraph
   is now false.** The 2026-08-30 SWIM-audit entry says the RVR scoring
   half was "staged, deliberately NOT deployed … (image not rebuilt)" and
   "needs operator verification against the actual CFR text before this
   scoring half goes live." This pass's poller image rebuild (10:14 UTC,
   for the report-1/detector deploy) carried the RVR code along: the
   running poller container is on that image, `cps_recompute.py`'s RVR
   path has no feature gate, and the skill runs hourly. **The 14 CFR
   91.175(h) correlation values are therefore live while the flagged
   operator verification remains open.** This is *known and intentional*
   per the post-commit consolidation note (20260831T102515Z: "everything
   above is deployed … operator-verification flagged, not independently
   confirmed") — the drift is CLAUDE.md's stale text, not a rogue deploy.
   Mitigating by design: RVR can only ever tighten a score, never loosen
   one. CLAUDE.md is a write-only scratchpad per its own header, so this
   is recorded here rather than edited there. Note the daily
   `CLAUDE_MD_DRIFT_REPORT.md` ("No drift found", generated 05:15 EDT —
   pre-commit) cannot see this class of semantic drift.

## Watch items (not drift)

- **First `cps-recompute` run on the new image failed**: 06:18:29 EDT,
  `compute error: database is locked` (poller journal). Every prior run
  on the old image was `rc=0`; the failure coincided with the daily-watch
  herd (4 poller-image containers running). Looks like transient SQLite
  contention, not the RVR change — but it means the RVR scoring path has
  **not yet completed a live run**. Verify the next hourly fire.
- ep-advance's in-flight run (started 05:35 EDT) predates the 10:14 UTC
  image rebuild, so it still carries the old 2800s timeout. The
  consolidation note's backlog item 1 ("watch the next real ep-advance
  fire end-to-end") stands; the *next* fire is the first real test of
  3600s. Trivial: `ep_advance_brief.py:1000`'s comment still says
  "2800s" though the constant at :119 is 3600.
- `docs/dispatch-runner-design.md` doesn't list `/api/utm/drones` or the
  `/utm` view, and its undeployed-hardware list (line 204) predates
  utm-watcher — but that doc self-describes as covering "roughly half the
  current API surface," so this is noted, not counted as drift.

## Checked, still accurate

- **report-1 on-demand wiring**: tracked == installed for all three
  consumer quadlets (ep-advance/dispatch-desk-memo/second-brain-weekly),
  `production.slice`, and `corporatetraveldc-llama-report-1.service`.
  Hooks point at the repo's `scripts/llama-report-ondemand.sh` (no
  /usr/local/bin copy expected). report-1 was *active* during this check —
  correctly: ep-advance was `activating` (mid-run), the exact case the
  stop-if-idle guard exists for. Unit is `disabled` (on-demand posture) ✓.
- **The 06:58 UTC note's open item is CLOSED**: manifest re-signed in the
  commit (verify-manifest OK, 837 files) and the poller image rebuilt
  10:14 UTC with `OLLAMA_TIMEOUT=3600` inside (verified in-image);
  ep-advance quadlet installed with `TimeoutStartSec=5000`.
- **`src/ingest/README.md`** (rewritten by this commit): spot-verified new
  claims against code — `EntryType` drone + `permanent_drones.json`
  hot-reload, `db.init_db_v43()`/`update_watchlist_uas_phase()` dedicated
  uas_phase columns, utm_watcher UDP :5007 default, `UTM_STATIC_IDS` pins,
  quadlet shipped `.disabled`. All accurate. (Its line-58 LOCKDOWN "host
  `ollama.service`" row remains on the pre-existing I4 open edit list.)
- **UTM posture matches docs**: no utm quadlet installed live (ships
  `.disabled` — same class as ais-watcher), `permanent_drones.json` is an
  empty watchlist, and `/api/utm/drones` on the live runner (:8001)
  returns the honest fallback shape
  (`{"source":"none","drones":[],"count":0,"detail":"no_source_configured"}`) ✓.
- **Ingest CPU rework deployed**: live CPUWeight/CPUQuota on all seven
  ingest units match the tracked quadlets exactly (see item 2 values).
- **`README.md`**: nothing this commit touched invalidates any claim not
  already on the standing I4 (Ollama-era) edit list. Model/skill table at
  :663 and governed-`ollama.service` note at :643 are pre-existing items.

Net: three real doc-file drifts (watchlist_README entry types,
GUARDRAILS ingest CPU numbers, ALERT_REFERENCE fdps family/utm topic) plus
one safety-relevant CLAUDE.md staleness (RVR scoring live with CFR
verification still open — already vault-recorded as intentional). Live
deploy state itself matches the tracked tree everywhere checked.
