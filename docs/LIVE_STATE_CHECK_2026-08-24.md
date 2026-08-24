# Live State Check — 2026-08-24

Written ~06:58–07:15 EDT against HEAD `447dd4a` plus the large
still-unstaged working-tree changeset from the 2026-08-23/24 overnight
session (GPS/receiver-location fix, visibility-aware polling intervals,
`remember --dest-subdir`, the delay-extension deploy, chronology
unification). This is the **designated drift benchmark state**,
superseding `docs/LIVE_STATE_CHECK_2026-08-23.md` per CLAUDE.md's
pointer convention. Produced by a single independent live-system-first
audit pass (Pass 1: verify the overnight change batch end-to-end against
the live system; Pass 2: re-verify CLAUDE.md and the docs tree against
live state), same methodology as the 08-12…08-23 checks. Every number
here is a point-in-time reading — re-query rather than trust, per this
file series' standing convention.

## Live snapshot verified

- **Manifest clean.** `scripts/verify-manifest.sh` → `OK — signature
  valid, all 706 files match` (up from 698/701 on 08-23). Manifest +
  signature were re-signed 06:56–06:57 EDT, minutes before this check.
- **The integrity-sweep self-clearing cycle was observed live end to
  end this morning**: sweep `failed` at 06:47:50 on exactly one unsigned
  edit (`src/second_brain/remember.py`, edited 06:20), sign at
  06:56–06:57, next fire 07:02:51 → `Result=success`. Textbook
  behavior per CLAUDE.md's manifest entry; no incident.
- **Test suite: 217 passed, 1 failed** (218 total) — the one failure is
  still the pre-existing, unrelated
  `tests/ingest/test_marine_one_detection.py::test_smes_parser_basic`.
  Up from 216/217 the night before.
- **Schema: `SCHEMA_V37`** is the current top in `src/common/db.py`
  (`watchlist_entries.departure_delay_min`).
- **Zero `failed` units at 07:05** other than the `runner-demo` crash
  loop (which never shows `failed` — `SubState=auto-restart`,
  `NRestarts` **56,461** and climbing; root cause unchanged since
  2026-08-15). `semantic-compile-daily` — `failed` since 16:22 on 08-23
  — **cleared**: the poller image was rebuilt post-sign
  (`build-date=20260824T043307Z`) and the 03:47:00 EDT scheduled fire
  succeeded (`Result=success`, 8.6 s wall, real compile output:
  v1.1.0, 51,317 assignments, 71 derivations, 26,377 chronology edges).
- **`/healthz` → `"status":"ok"`**, snapshot age 1 s, 6 active tokens,
  CPS RED/NO-GO (a score, not a health state). All feeds fresh in
  `/api/v1/feeds` — every `push:*` heartbeat under 60 s, only
  `eurocontrol`/`jasdat` on `awaiting_credentials` (long-standing).
- **Scale numbers**: 124 `corporatetraveldc-*` user units, 48 timers;
  `podman ps` 33 running / 33 total; 64 live Quadlets vs 63 in the repo
  (the gap of one is still `ccw-demo`, untracked); 33 quadlets carry
  `verified-exec`; `audit_log` 3,777 rows; `auth_tokens` 19 rows /
  6 active, all `expires_at IS NULL` — the retired mcpo **admin** token
  (`ctdc_admin_` / `mcpo-corporatetraveldc-dispatch-mcp`) is **still
  active/unrevoked**, open since 08-19.
- **`corporatetraveldc-claude-md-drift-daily`** ran 05:15 →
  `No drift found`. Weak evidence, per CLAUDE.md's blind-spot list — the
  checker itself is byte-unchanged since 2026-08-19; none of its
  NEEDS-OPERATOR-DECISION fixes (silence-proofing, Known-bad-scoped
  grep, `/healthz` body parse) have been made. `docs-drift-weekly`'s
  first *scheduled* post-fix fire is today 09:00 EDT (after this check).

## Overnight change batch (2026-08-23/24) — verified end to end

1. **GPS/receiver-location fix: fully live.** `useReceiverLocation.js`
   fetches `GET /api/v1/frontend-config`; live response is the real
   receiver coordinates (matching `ULTRAFEEDER_LAT`/`_LON`, not the
   generic placeholder — literal values redacted from this file by the
   08:2x post-commit addendum below; this is a tracked, publish-eligible
   file and the scrubber has no coordinate coverage).
   `buildGlobeUrl()` sends `SiteLat`/`SiteLon`/`SiteClear=1`
   (`MapView.jsx:47`). The runner image (created 04:35 UTC) postdates
   every source mtime (23:19–23:24 EDT), the running container (started
   06:46 EDT after the last LOCKDOWN restore) serves a bundle that
   contains `SiteClear` and `frontend-config` — no stale-image drift.
   `docs/GPS_COORDINATE_CONFIGURATION.md` exists and matches reality
   (`ULTRAFEEDER_LAT` confirmed present in live secrets; the referenced
   ultrafeeder Quadlet is repo-tracked). README + `dispatch-secrets.env.template`
   both carry the self-hoster callout.
2. **Timer-pause fix: complete, no stragglers.** Raw `setInterval` under
   `components/`+`hooks/` remains only in `useTailnet.js`,
   `useDemoStatus.js`, `useVisibilityAwareInterval.js` itself, and
   `useWakeLock.js`'s 30 s health check — exactly the sanctioned set. 12
   files import `useVisibilityAwareInterval` (10 components +
   `useWatchlist`/`useWakeLock`). `useWakeLock` has the
   `pageshow`/`focus` listeners + health check.
3. **`remember_text(dest_subdir=)`**: added with CLI `--dest-subdir`
   (default `manual`). The REST wrapper `src/web/routes/remember.py`
   calls by keyword and needs no change — it deliberately does not
   expose `dest_subdir` and keeps writing `01-Sources/manual/`.
4. **`.claude/skills/personal-export-analysis/SKILL.md`**: every claim
   checked — `export_analysis.py`'s no-verbatim policy, `db.board_insert`
   (`db.py:334`)/`board_query` (`:352`), the board-mirror precedent's
   exact call shape, and the external repo
   `/opt/corporatetraveldc/public/agentic-management-tooling-mcp`: the
   extended `_PLATFORM_MAPS["uber"]` candidates, `fromisoformat(...Z→+00:00)`
   first-try parsing, and `_DATE_FORMATS` leading with
   `"%Y-%m-%d %H:%M:%S"` are all really there, uncommitted in that
   repo's working tree (unstaged — the SKILL.md said "staged", corrected
   this pass). `.venv` exists.
5. **Delay-extension (`extend_auto_remove_for_delay`, SCHEMA_V37) is now
   DEPLOYED** — CLAUDE.md said "not yet deployed"; the overnight rebuild
   (all images 04:33–04:35 UTC) shipped it, confirmed inside the running
   `ingest-tfms` and `poller` containers. First real confirmation still
   pending: `departure_delay_min` is NULL on all 317 watchlist entries —
   no watchlisted flight has reported `airlineOffTime` since deploy.
   `flight_ooooi_times` has its first 2 real rows.

## LOCKDOWN cadence + watchdog fix, production data

**Ten LOCKDOWNs since 2026-08-23 12:00**, seven of them overnight
(19:35:39, 21:05:53, 23:20:17, 00:20:40, 02:20:49, 04:18:59, 06:35:27 —
one roughly every ~2 h), **all** triggered by `N load-attributed brief
fallbacks/300s` (never temp/load), each ~9–11 min, each restoring
cleanly. The `FALLBACK_TRIGGER_COUNT=2`/`FALLBACK_WINDOW_S=300`
calibration question is now the highest-leverage open tuning item
(~1.5 h/day of full-stack shed on ordinary timer bunching).

**The watchdog guard-state suppression fix is production-proven**: on
every 90 s cycle inside all seven overnight LOCKDOWNs the watchdog
logged `[OK] … down but guard tier=2 (deliberate shed, not a fault)` and
performed zero restarts. The 08-23 17:13 mid-LOCKDOWN collision has not
recurred since the fix shipped.

## Re-verified open items (unchanged unless noted)

- `nas_programs.key_scheme`: GDP 3×v1/0×v2, GS 558×v1/10×v2 — drifting
  exactly as predicted; the `find_legacy_nas_program()` REST-window
  defect remains open (all v2 rows still correlate to REST-shape ids).
- `watchlist_sessions` still 0 rows (vs 317 `watchlist_entries`);
  vessel path still fully dormant (0 entries); `runsheet` frozen at
  7,282 rows (duplicate-insert fix holding); `acars_messages` still 0
  (upstream router silent, per the deployed instrumentation).
- 4 `approval_requests` rows at `status='pending'`, all past
  `expires_at` — correct read-enforced-expiry behavior, not a backlog.
- `ccw-demo`: 401 on :8085 (auth active); `webdev-expiry.timer` armed
  for 2026-08-25 00:10 EDT. Repo copies still absent.
- Repo-structure items still open: mcpo `.service` files still in
  `.config/systemd/user/` (not moved to a `retired-20260818/`);
  `.git/hooks/post-commit` still the stale 2026-08-11 copy;
  `research-board-mirror.timer` still repo-only, not installed live;
  the fictional `_WATCHLIST_PATHS`/`_is_tailnet_request` comment still
  in `src/runner/main.py`.
- Ollama drop-in still active (`CPUWeight=500`, `MemoryMax=7250M`).

## Fixed in this pass (docs only — no code, no live state touched)

- CLAUDE.md: drift-benchmark pointer → this file; semantic-compile-daily
  🔴 block RESOLVED + removed from Known bad (dated 2026-08-24);
  integrity-sweep morning cycle recorded; delay-extension marked
  deployed; SCHEMA_V36→V37; test counts 218/217; manifest 701→706;
  Quadlet counts 63/62→64/63; overnight LOCKDOWN cadence + watchdog
  production proof added; runner frontend conventions (receiver-location
  endpoint, visibility-aware intervals) and `--dest-subdir` documented;
  research-board-mirror note re-verified/reworded.
- `.claude/skills/personal-export-analysis/SKILL.md`: "staged" →
  unstaged (verified toolkit-repo `git status`).

## Still needs operator decision (carried forward, none new)

Fallback-trigger calibration / daily-watch timer spreading (now with
hard cadence data); drift-checker check 5/8/9 fixes; `runner-demo`
`DEMO_MODE` + crash fix; mcpo token revocation + default TTL; mcpo unit
file relocation; `find_legacy_nas_program()` narrowing; post-commit hook
refresh (`cp scripts/post-commit-doc-verify.sh .git/hooks/post-commit`);
research-board-mirror timer install-or-drop; ccw-demo track-or-declare;
Amtrak fallback wire-or-reword; ITWS `_LOW_PRIORITY_FEEDS` membership;
`to_eastern()` helper; verify-manifest preflight for core containers;
`POST /api/v1/watchlist` deprecation.

---

## Addendum — post-commit drift check, ~08:10–08:25 EDT

Independent targeted pass run right after the overnight batch above was
committed as `cb2cd58` (08:08:41 EDT); a second, small commit `a443478`
(scrubber FAA-UUID allowlist entry + one-line `grant-agent-session.sh`
change, 08:19:24) landed mid-check and is covered too. Scope per the
check's mandate: did the commit invalidate anything the current docs
claim, verified against the live system — not a from-scratch rewrite.
This pass's own changes are working-tree edits only; nothing staged or
committed by it.

### 🔴 Pre-push blocker introduced by `cb2cd58` — real GPS coordinates in tracked, publish-eligible files

- `src/runner/main.py:118-134` (the `DEFAULT_LAT` comment block, new in
  this commit) hardcodes **both** the operator-confirmed current
  receiver coordinates **and** the operator's former-residence
  coordinates, labeled in the comment as exactly that.
  `src/web/main.py:1799`'s twin comment handles the same history
  correctly — it cites the runner writeup without repeating any real
  value; only the runner copy leaks. (The `38.8521/-77.0377` and
  `38.8816/-77.0910` literals elsewhere are the pre-existing generic
  placeholders, fine.)
- This file's own "Overnight change batch" item 1 quoted the live
  current pair verbatim — redacted by this pass; the claim stands.
- Why it's a blocker, all three legs verified against
  `scripts/scrub-public-tree.py` (including the `a443478` version):
  (1) `src/runner/main.py` transits to the public mirror normally and
  `LIVE_STATE_CHECK_*.md` is **not** in `DROP_FILES`; (2) neither
  coordinate pair has a `SUBSTITUTIONS` entry; (3) `verify_scrubbed()`
  scans only forbidden literals / emails / UUIDs / IPv4s — a
  coordinate-shaped decimal passes every gate silently. The next
  `public` push would publish the receiver's real location and the
  operator's former home address area with nothing tripping. This is
  the exact "new value silently bypasses a fixed literal list" failure
  mode CLAUDE.md's conventions section warns about — introduced,
  ironically, by the same change set that removed hardcoded frontend
  coordinates.
- Not fixed in code by this pass (docs pass; the comment's history is
  operationally valuable). **Before any `public` push:** reword the
  `runner/main.py` comment to name the env vars without values
  (mirror `web/main.py:1799`'s form), and/or add both pairs to the
  scrubber's `SUBSTITUTIONS`. NEEDS OPERATOR DECISION on which.

### New since the 06:58 check — ops-brief/ep-advance restagger (uncommitted, installed live)

A concurrent session moved `corporatetraveldc-ops-brief.timer` :00→:05
and `corporatetraveldc-ep-advance.timer` :30→:35 at 07:59–08:00 (repo
edits, uncommitted; live copies byte-identical; daemon-reload done —
ep-advance took a `Persistent=true` catch-up fire at 08:08:37; systemd
confirms the new specs loaded via `TimersCalendar`). First real
application of the timer-bunching lesson to the brief family; the six
daily-watch skills themselves are still on the :00/:15/:30/:45 grid.
Doc lines this invalidated, both corrected by this pass:
`docs/INFRA_MAP.md:451` ("Hourly ops-brief (:00) / ep-advance (:30)")
and CLAUDE.md's watchdog-section claim that the bunching lesson "has
simply not been applied to the long-running daily-watch/brief family".
Reading note: at check time both timers showed `NextElapse` blank —
only because both services were mid-run (a timer recomputes after its
service exits), not a scheduling fault.

### Manifest / integrity-sweep — the self-clear cycle observed twice more

- 08:02:53 sweep fire failed on exactly the two then-unsigned timer
  edits; sign 08:09 cleared it. 08:17:52 fire failed on the two
  then-staged script edits (since committed as `a443478`); sign
  08:19:01/:15 cleared that — `verify-manifest.sh` → OK, all 706
  files, *including this pass's first three doc edits* (the 08:19 hash
  postdates them, confirmed by verify passing against the edited
  tree). This addendum itself landed after that sign, so this pass
  ends with its own `sign-manifest.sh --agent` attempt per convention;
  until one succeeds, the 15-min sweep failing on this file (and only
  this file) is the documented normal cycle, not an incident. Three
  full failure→sign→clear observations today already.
- The agent sign-attempt loop was watched live: two
  `sign-manifest.sh --agent` attempts pending on approval-gate taps
  during this check (started 08:07:21 / 08:13:00; requests
  `5d3599cf…`/`937f1de4…`), the first failing closed at its 08:17:24
  expiry, the second approved ~08:19. This pass deliberately did not
  launch a third concurrent sign. Each in-flight attempt leaves a
  `MANIFEST.sha256.<mktemp>` stray that its own trap cleans on
  fail-closed exit; one orphan (`MANIFEST.sha256.Cobrrm`, 08:13:02)
  remained on disk at check end — harmless (the 2026-08-20 exclusion-
  regex fix demonstrably kept every stray out of the signed manifest),
  safe to delete, left in place.

### Post-commit spot re-verification — this file's claims all hold

Re-derived live at 08:1x, none from docs: manifest 706/clean;
`SCHEMA_V37` top; Quadlets 63 repo / 64 live, gap still exactly
`ccw-demo`; 33 `verified-exec` quadlets; `/healthz` `"status":"ok"`;
guard `tier: 0`; `semantic-compile-daily` 03:47 fire `Result=success`;
`entity-tracking-digest` next fire 12:12; `runner-demo` still
crash-looping (`NRestarts` 56,910→56,911 across 8 s — and, exactly as
CLAUDE.md documents, it did NOT appear in a one-shot
`failed|auto-restart` grep of `list-units`); `departure_delay_min`
still NULL on all 317 watchlist entries (delay-extension first live
confirmation still pending); ccw-demo :8085 → 401, `webdev-expiry.timer`
armed for 2026-08-25; `research-board-mirror.timer` still
repo-only/not installed; the runner's served bundle still carries
`SiteLat` + `visibilitychange` handling; `claude-md-drift-daily` 05:15
"No drift found" (pre-commit, weak evidence per its blind-spot list);
`docs-drift-weekly`'s first scheduled post-fix fire still pending at
09:00 EDT today. Full `pytest` deliberately not re-run (verified
217/218 at ~07:00 against this exact change set; re-running under two
live brief runs would only feed the fallback-count LOCKDOWN trigger).

### Verdict

Commit `cb2cd58` introduced **no stale-claim doc drift** — CLAUDE.md
and this file were written against this exact change set and every
spot-checked claim held. Three real findings, none of which existed at
07:15: the GPS-literal pre-push blocker above (introduced *by* the
commit, undocumented anywhere until now), the post-commit timer
restagger that invalidated two doc lines (corrected this pass), and
the cosmetic orphaned manifest temp file. Files edited by this pass,
all left uncommitted: this file (coordinate redaction + this
addendum), `docs/INFRA_MAP.md` (one cadence line), CLAUDE.md (one
watchdog-section paragraph).
