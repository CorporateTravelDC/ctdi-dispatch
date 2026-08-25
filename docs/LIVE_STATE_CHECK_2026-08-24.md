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

---

## Addendum 2 — post-commit `0a7f643` drift check (~18:30–18:50 EDT)

Independent pass run right after commit `0a7f643` (18:26 EDT, "Security
remediation: redact leaked NWWS password + GPS coords, harden
scrub-public-tree.py, drop CLAUDE.md from public") and its 18:27 push to
`public/main` (`e02c134`). Scope per the task: did *this specific
change* invalidate anything the current docs claim — not a from-scratch
rewrite. Everything below was verified against the live system
(`systemctl --user`, `podman`, real curls, journal reads, public-remote
tree reads), not against the docs' own text. This pass edited only this
file; nothing was staged or committed.

### ACTIVE now, not doc drift — 19 units `failed`, one root cause:
### the sign→build ordering trap, fourth occurrence

`systemctl --user list-units 'corporatetraveldc-*' --all` at 18:31
showed **19 failed units** (every daily-watch, ops-brief, ep-advance,
board-sweep, entity-tracking-digest, integrity-sweep, second-brain-daily,
tbfm-arrival-enrichment, feed-db-integrity-check, ingest-feed-watch,
personal-notes-import, pull-path-verify, second-brain-rss,
docs-drift-weekly — see below for that last one, it is a *different*
cause). Do not debug these individually. Evidence chain:

- All five images carry `build-date=20260824T211010Z` = **17:10 EDT**;
  `MANIFEST.sha256`/`.asc` were signed at **18:23** EDT. Build predates
  sign.
- Internally inconsistent image, proven directly: inside
  `corporatetraveldc-poller:latest`, `/app/src/web/main.py` hashes
  `9a6ee3c3…` while the baked `/app/MANIFEST.sha256` records `d39e8cdc…`
  for that path — the image baked this pass's *new* source against the
  *pre-sign* manifest. Every `verified-exec.sh`-gated skill fire since
  ~17:10 has failed exactly as CLAUDE.md's standing entry predicts
  (`ep-advance` 17:35: `src/runner/main.py`, `src/web/main.py`,
  `src/web/routes/sectors.py` FAILED).
- The repo tree itself is fine: `scripts/verify-manifest.sh` → `OK —
  signature valid, all 762 files match` (up from 706 this morning; the
  new investor-materials tree, LICENSE, requirements.txt,
  `src/second_brain/doc_generation.py` + tests account for the growth).
  The `integrity-sweep` failure on record fired at 18:18 — *before* the
  18:23 sign — and is the documented normal pre-sign cycle; it
  self-clears on its next 15-min fire.
- **Fix is the documented one: `bash build-images.sh` again, now, after
  the 18:23 sign — then the skill timers clear themselves on their next
  fires.** Not done by this pass (live deploy, out of scope for a
  drift-check pass, same precedent as the 08-23 semantic-compile entry).
  Until rebuilt, every verified-exec skill fire keeps failing, and the
  running `web`/`poller`/`pusher`/`runner`/`runner-demo` containers
  carry the same dormant baked-manifest inconsistency (harmless to
  their own startup — they don't run the gate — same as the 08-23
  occurrence).

### `docs-drift-weekly`'s first scheduled post-fix fire FAILED — new,
### undocumented failure mode

CLAUDE.md's top-of-file entry says this unit "was broken, is now fixed
and proven" with next scheduled fire 2026-08-24 09:00. The 09:00 fire
**failed** (`status=1`, 9 s wall clock) — not the PATH or archived-dir
bugs. The run log
(`/var/lib/corporatetraveldc/docs-drift-check/ctdi-dispatch-internal-2026-08-24.log`,
67 bytes) contains only: `You've hit your session limit · resets
10:40am (America/New_York)` — the Claude CLI account was at its usage
limit at fire time. So the unit's plumbing is fixed (it ran, invoked
the agent, captured output, exited nonzero honestly) but the scheduled
path has still never produced a scheduled-fire report, and CLAUDE.md's
"fixed and proven" should be read as "proven manually, scheduled path
now blocked by a third cause (API usage limits) nobody has documented."
The 18:26 commit didn't cause this; recorded because this file is the
drift benchmark and the claim is now misleading as written.

### Doc drift 1 (largest): the runner-demo crash loop is FIXED — five
### docs still describe it as the current state

Commit `0a7f643` rewrote
`.config/containers/systemd/corporatetraveldc-runner-demo.container`:
the 2026-08-14 F6 mount removal (the root cause) is reverted the right
way — a dedicated `/var/lib/corporatetraveldc-demo` host dir (root-
created, `700 corporatetraveldc:corporatetraveldc`, confirmed live with
a real `dispatch-chat.db` in it) is mounted at the container-internal
`/var/lib/corporatetraveldc` path, sharing nothing with production.
Live-verified this pass: `ActiveState=active SubState=running`,
`NRestarts=0` and **stable across an 8 s re-read** (the doc's own
detection method), `:8005` → HTTP 200, repo and live quadlet copies
byte-identical. The container's header comment now tells the whole
story, including the two same-day pentest-found missteps (production DB
briefly mounted RW; shared `dispatch-chat.db` inode readable/deletable
via unauthenticated `/api/chat/history` on the public hostname) and
their fixes.

Now stale, all describing the crash loop / 502 / "masked only by
accident" state as current:

- CLAUDE.md "Core containers" warning block (~line 342): "has been
  crash-looping since 2026-08-15", ":8005 refuses connections and the
  public hostname 502s", the NRestarts figures, and the F6-mount root
  cause framed as unfixed.
- CLAUDE.md "Known bad" table (~line 1911): runner-demo listed as the
  sole currently-failed unit, "unchanged long-running crash loop" —
  now false on every column (and superseded by the 19 *actually*
  failed units above).
- CLAUDE.md "Crash-looping" entry + the NRestarts/curl detection
  how-to (~lines 1966–2003): now historical; the method itself is
  still good general advice.
- `README.md` lines 70 ("DOWN — crash-looping"), 121, 249 ("Currently
  502").
- `docs/INFRA_MAP.md` line 350 (full crash-loop paragraph) and the
  count-flapping notes at lines 26/34/168.

**The security half of those warnings is now LIVE, not resolved.**
`DEMO_MODE` is still set nowhere (confirmed: not in the quadlet, not in
either env file, not in the running container's env), and
`https://dispatch-runner.example.com/` now returns **200**
publicly. The exact scenario CLAUDE.md's NEEDS OPERATOR DECISION warned
about — "fixing the crash without also setting `DEMO_MODE=true` would
put an ungated demo surface live" — has happened. Partially mitigated
by the same commit: `frontend_config()` is now trust-gated (untrusted
callers get a DC-area placeholder + empty `mt_widget_key` — confirmed
live from the demo instance), the layer-config PUT is trust-gated
(404), and the chat DB is demo-scoped. But `proxy_dispatch()`'s
DEMO_MODE hard gate, signal sanitization, and ntfy suppression remain
inert (`main.py` default `false`), and the same commit's own
`PENTEST_REVERIFICATION_2026-08-24.md` still lists "Set `DEMO_MODE`
explicitly" as an open MEDIUM. The operator decision is no longer
deferrable-by-accident — the crash loop that masked it is gone.

### Doc drift 2: CLAUDE.md is now IN `DROP_FILES` — three CLAUDE.md
### passages claim the opposite

`scripts/scrub-public-tree.py`'s `DROP_FILES` now contains
`"CLAUDE.md"` (added by this commit, with a long rationale comment),
and the 18:27 push confirms it worked: `git show public/main:CLAUDE.md`
→ file **gone from the public mirror**. Three passages in CLAUDE.md now
assert the opposite as present-tense fact:

- ~line 493 (approval-requests note): "`CLAUDE.md` is **not** in
  `scripts/scrub-public-tree.py`'s `DROP_FILES` (confirmed by reading
  the set)… A real id added here would hard-fail the next `public`
  push." Both halves now stale — and the *consequence* is inverted: a
  UUID pasted into CLAUDE.md would no longer block the push, because
  the file never reaches `verify_scrubbed()` at all (dropped files
  aren't scanned). The don't-paste-ids rule itself still stands (reason
  1, the id-is-the-credential argument, is unchanged).
- ~line 3544 (secrets convention): "CLAUDE.md itself is not in the
  scrubber's `DROP_FILES` — it publishes with only content
  substitutions applied." False; it no longer publishes at all.
- ~line 3555 (NWWS correction): same claim, plus "nothing in that
  script substitutes credential-shaped example text" — the commit also
  added a live-secret-value check (`_load_live_secret_values()` reads
  `dispatch-secrets.env` at scrub time and hard-fails on any live
  value appearing anywhere in the output tree), so that sentence is
  accurate only as history of how the leak happened, not as a
  description of the current scrubber.

### Real finding, introduced AND published by this commit: a stray
### manifest temp file is tracked and on the public mirror

`MANIFEST.sha256.3zbNoX` — a 762-line `sign-manifest.sh` mktemp orphan
(the same class as the morning's `MANIFEST.sha256.Cobrrm`, which was
correctly left untracked) — got **committed** in `0a7f643` and is now
git-tracked AND present on `public/main` (verified:
`git show public/main:MANIFEST.sha256.3zbNoX` succeeds). The 2026-08-20
exclusion-regex fix keeps the whole `MANIFEST.sha256(\..*)?` family out
of the *signed manifest* (confirmed: it is not a manifest entry and
`verify-manifest.sh` passes), but `DROP_FILES` is an exact-name set —
`MANIFEST.sha256` and `.asc` are dropped, the mktemp family is not — so
the stray sailed through the scrubber and published.

Measured severity, checked against the actual public blob rather than
assumed: the content **substitutions did apply** (the real domain is
rewritten — the public copy lists `nginx/conf.d/cloud.example.com.conf`
/ `dav.example.com.conf`, not the real hostnames), so no hostname or
credential leaked. What it does disclose: the private tree's complete
file inventory — including the existence and names of every file
`DROP_FILES` deliberately withholds (the vault vhost pair, CLAUDE.md,
HEADLESS_ACCESS.md, etc.) — plus real SHA-256 hashes of the *private*
(pre-substitution) content of every tracked file. That is precisely the
"real trust material… must NOT transit" class the `MANIFEST.sha256`
DROP_FILES entry's own comment describes. Low severity, but real, live
now, and it also permanently confirms to a public reader that the
public tree's content differs from the signed private one.

One more wrinkle, confirmed via `git status` at 18:4x: the file is
**already deleted from disk** (working tree shows an unstaged ` D
MANIFEST.sha256.3zbNoX`) — it vanished sometime after the 18:26 commit,
consistent with mktemp-orphan cleanup. So the tracked copy is now a
phantom in a second sense: HEAD carries a file the working tree doesn't
have.

**Operator actions** (not done by this pass — both violate its
no-git-writes rule): stage the already-pending deletion (`git rm`
reduces to `git add -u` on that path) and commit it; add the
`MANIFEST.sha256.*` family to the scrubber's drop logic (DROP_FILES is
exact-name; either enumerate-by-glob at scrub time or add a prefix
rule) so the next orphan can't repeat this; then re-scrub and push to
clear it from `public/main`.

### Doc drift 3: admin-audit endpoint count is now 37, not 32

The same commit added `require_admin(...)` gating to the five
mute/throttle endpoints in `src/web/routes/sectors.py`
(`sectors.sector.silence` etc.). Re-derived:
`grep -rc 'require_admin("' src/web/…` → main.py 23 + watchlist.py 8 +
remember.py 1 + **sectors.py 5** = **37**. Stale: CLAUDE.md's auth-tiers
section ("Real coverage is **32 endpoints, not 23**") and
`docs/COMPLIANCE_SECURITY.md:369` (same 32 claim; its action-name map
also has no `sectors.*` entries). Same undercount mechanism both docs
themselves describe — a new route file gained `require_admin` and the
main.py-scoped count wasn't re-derived.

### Smaller notes, checked and either fine or minor

- **Timers**: `ops-brief` fired 18:05, next 19:05; `ep-advance` fired
  17:35, next 18:35 — matches CLAUDE.md's ":05/:35 restagger" entry;
  repo and live timer files byte-identical. (Both *services* then
  failed — that's the image/manifest root cause above, not the timers.)
- **Test suite: 223 tests, 222 pass** — up from this morning's 218/217
  (+5 from the new `tests/second_brain/test_doc_generation.py`), same
  single pre-existing `test_smes_parser_basic` failure. Run live this
  pass (guard `tier: 0`, Ollama idle, 12 s wall).
- **GPS/receiver-location**: `ULTRAFEEDER_LAT/LON` moved to
  `dispatch-secrets.env` (dispatch.env keeps a pointer comment).
  `docs/GPS_COORDINATE_CONFIGURATION.md`'s normative section already
  says `dispatch-secrets.env` — correct; its "Incident this codified"
  narrative says `dispatch.env`, accurate as history of the pre-move
  state. CLAUDE.md's frontend-conventions bullet ("components fetch
  them from `GET /api/v1/frontend-config`") is still true but now
  incomplete: the endpoint is trust-gated, so only trusted (tailnet/
  loopback) callers get the real coordinate; untrusted callers get a
  fixed DC placeholder and empty widget key.
- **`src/shared/watchlist_README.md`**: updated in-commit with the
  delay-extension entry; checked against `extend_auto_remove_for_delay()`
  — accurate, no drift.
- **Board reads are now redacted** (`_redact_board_body()` in
  `src/web/main.py` masks the vault hostname + app-password mechanism
  references before serving Tier-0 board reads). New behavior; no
  existing doc contradicts it, none describes it yet either.
- **New surface with no doc coverage at all** (gap, not falsified
  claims): `src/second_brain/doc_generation.py` (+ its tests,
  `requirements.txt`, `LICENSE`, and the whole
  `docs/investor-materials/v1.5/` tree). Nothing in the living docs
  mentions the doc-generation pipeline.
- **`/healthz`** → `"status":"ok"`, `token_count_active: 5` — matches
  CLAUDE.md's 2026-08-24 five-active-token accounting. Guard state
  `{"tier": 0}`; load1 ~9 (normal band, informational only).
- **Manifest file counts**: 762 now; every doc citing 698/701/706
  already carries the re-derive-don't-trust caveat, so not drift.

### Verdict

Commit `0a7f643` **did** invalidate current doc claims, in three
clusters — the runner-demo crash-loop state (CLAUDE.md ×3 sections,
README ×3 lines, INFRA_MAP ×2 places), the CLAUDE.md-in-DROP_FILES
claims (CLAUDE.md ×3 passages, one with an inverted safety
consequence), and the 32-admin-endpoint count (CLAUDE.md +
COMPLIANCE_SECURITY.md) — plus one new live exposure it introduced
itself (the published stray manifest temp file) and one urgent
operational follow-through it left undone (all five images must be
rebuilt post-sign; 18 skill units are failing on the stale bake right
now, and the public demo runs with `DEMO_MODE` still unset). The
morning half of this file remains accurate except where this addendum
supersedes it (runner-demo, file counts, test counts,
docs-drift-weekly's "still pending" fire). This pass edited only this
file and committed/staged nothing.
