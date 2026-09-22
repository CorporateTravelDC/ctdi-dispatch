# Live State Check — 2026-08-19

Written ~05:33–05:40 EDT against HEAD `f3ad588` (`main`, 05:31:53,
"Reconcile CLAUDE.md; wire drift-checker into sign-manifest.sh + daily
backstop" — 7 files, +440/−50: `CLAUDE.md`, `scripts/sign-manifest.sh`,
`scripts/scrub-public-tree.py`, new `scripts/check-claude-md-drift.sh`,
new `scripts/claude-md-drift-daily.sh`, manifest pair). HEAD moved once
under the check, to `595e85b` (05:33:49, tracks the drift-daily units +
report) — covered in finding 1. Same rule as the
08-12…08-18 checks: does anything README.md, CLAUDE.md, docs/,
`src/ingest/README.md`, or `src/shared/watchlist_README.md` currently claim
no longer match the live box *because of this commit*? Verified live
(`systemctl --user`, `journalctl`, the checker itself, `verify-manifest.sh`,
curl), not against prior docs. Nothing staged, committed, enabled, restarted,
or otherwise changed live by me; this file is the only write.

## Live snapshot verified

- **Manifest clean.** `scripts/verify-manifest.sh` → rc 0; the 15-min
  `corporatetraveldc-integrity-sweep` fired 05:30:53 → `sweep OK … all 686
  files match` (its 05:21:17 run had failed on the unsigned in-progress
  edits, exactly as CLAUDE.md's "Manifest / integrity-sweep" paragraph now
  says to expect). Working tree: `docs/LIVE_STATE_CHECK_2026-08-18.md`
  modified (+179, yesterday's addenda, manifest-excluded) and three
  untracked files — see finding 1.
- **New checker works as documented.** `scripts/check-claude-md-drift.sh`
  (full set, no `--pre-sign`) → rc 0, all nine checks `[OK]`: no retired
  terms, no hardcoded unit counts, model count 21 = `build-models.sh`
  MODELS map (22 `corporatetraveldc.*` files on disk incl. the
  `.PROPOSED-enrichment-2026-08-17` draft, correctly not counted), single
  base `phi3:mini`, Modelfile scrub coverage satisfied by the new
  `name.startswith("corporatetraveldc.")` pattern rule, Known bad 1 day old,
  manifest+signature clean, `/healthz` 200. `sign-manifest.sh` wiring
  confirmed in source (`--pre-sign`, `SKIP_DRIFT_CHECK`, exit 3 on drift;
  `docs/CLAUDE_MD_DRIFT_REPORT.md` added to the hash exclusion alongside
  `LIVE_STATE_CHECK_*`).
- **Known bad section — still accurate** (re-verified, not trusted):
  `second-brain-weekly` `failed` (only `failed` unit);
  `runner-demo` crash-looping — `NRestarts=3647`, ~8 s cycle,
  `sqlite3.OperationalError: unable to open database file`, exit 3, :8005
  refuses connections; `mcpo*` → 0 units, `mcp.example.com`
  vhost still answers **502**; `ingest-tbfm` not failed; `docs-drift-weekly`
  timer active (next Mon 2026-08-24 09:00 EDT). Six daily-watch/ep-advance
  units were `activating` at check time — normal 05:xx run window, not
  failures.
- CLAUDE.md's 2026-08-19 claim that `sign-manifest.sh`'s check 5 needs the
  Known-bad paragraph to name `corporatetraveldc-integrity-sweep` is
  correct — check 5 is a `grep -qF <unit>` against CLAUDE.md.

## Drifted

1. **Daily backstop — drifted at `f3ad588`, resolved by the operator at
   `595e85b` while this check ran.** At `f3ad588` the commit message and
   `sign-manifest.sh`'s new comment ("overwrites it every 05:15 ET run")
   described `corporatetraveldc-claude-md-drift-daily` as running daily,
   but live the timer was `disabled; inactive (dead)`, absent from
   `timers.target.wants/`, had never fired, and both unit files plus
   `docs/CLAUDE_MD_DRIFT_REPORT.md` were untracked (`??`) though hashed
   into `MANIFEST.sha256`. Two minutes later HEAD moved to **`595e85b`**
   (05:33:49, "Add claude-md-drift-daily systemd unit + initial drift
   report", 3 files / +28) tracking all three, and the timer is now
   **`enabled`**, fired once (`Persistent=true` catch-up — report
   regenerated 05:34:08, "No drift found"), next fire Thu 2026-08-20 05:15
   EDT. The regenerated report shows as ` M` in the working tree, which is
   the designed steady state (manifest-excluded, rewritten daily).
   Nothing in CLAUDE.md/README/INFRA_MAP mentions this unit yet — not
   wrong, just undocumented outside the scripts' own headers.
2. **CLAUDE.md header contradicts the same commit** (was still true at
   `595e85b`). Lines 8–11 said
   `scripts/check-claude-md-drift.sh` "does not exist yet as of this
   reconciliation — install it before relying on that part of the story."
   It exists, is executable, is wired into `sign-manifest.sh`, and passed
   on this very commit. The checker has no self-check for this sentence, so
   nothing automated will flag it.
   > **RESOLVED 2026-08-19** by the reconciliation pass later that day.
   > CLAUDE.md's opening now describes the checker accurately — and goes
   > further, documenting two blind spots that pass discovered: check 5
   > only greps for `failed`/`auto-restart`, so cleanly-stopped units
   > (`inactive`, `Result=success`) are invisible to it; and check 9 only
   > asserts `/healthz` returns HTTP 200, never reading the body, so it
   > prints `[OK] API healthy` while the response says
   > `{"status":"degraded"}`. The 05:34 "No drift found" cited in item 1
   > above was produced under exactly those blind spots and should not be
   > read as a clean bill of health.
3. **`docs/PHASE4_FIXES_VALIDATION_2026-08-16.md` §"Fix 2" is superseded.**
   It validated "All 21 Modelfile basenames present at
   `scrub-public-tree.py:66-86`" via an `ast` set-diff. Post-commit,
   `DROP_FILES` enumerates **one** Modelfile (`…ops-brief.PROPOSED-enrichment-
   2026-08-17`) and coverage comes from the `scrub_tree()` basename-prefix
   rule instead — the 08-16 invariant ("every Modelfile basename appears in
   DROP_FILES") is intentionally no longer true. The file is a dated
   snapshot and was accurate when written; I did not edit it. If anyone
   re-runs that `ast` check as a regression test it will now "fail" by
   design — the checker's section 4 is the replacement.

## Still accurate / not affected

- README.md (`sign-manifest.sh` note at :435, `verified-exec.sh` exists),
  `docs/INFRA_MAP.md` §timers (`docs-drift-weekly` description, post-commit
  hook — hook is installed at `.git/hooks/post-commit`, 2026-08-11),
  `src/ingest/README.md`, `src/shared/watchlist_README.md` — nothing this
  commit touched is claimed there. The already-known stale `mcpo`/MCP
  bridge entries in README.md:52/161 and INFRA_MAP.md:222/305–318 are
  unchanged and already listed in CLAUDE.md's Known bad; not new drift.

> **Two corrections to the paragraph above, 2026-08-19:**
>
> 1. **The post-commit hook should not have been listed as "still
>    accurate" — that is a regression from the previous day's check.**
>    `docs/LIVE_STATE_CHECK_2026-08-18.md:954-967` had explicitly flagged
>    `.git/hooks/post-commit` (2,840 B, 2026-08-11) as differing from
>    `scripts/post-commit-doc-verify.sh` (3,510 B, 2026-08-18); this
>    document silently reclassified it as accurate. Re-verified today: they
>    still differ, and the installed hook is 7 days older than the script it
>    is supposed to mirror. Note the hook does reference
>    `post-commit-doc-verify` (3 occurrences), so it is not simply an
>    unrelated file — it is a stale copy. **NEEDS OPERATOR DECISION:**
>    reinstall the hook from `scripts/post-commit-doc-verify.sh`, or make
>    the hook a symlink/thin wrapper so it cannot drift again. Note
>    `.git/hooks/` is untracked and outside signed-manifest coverage, so
>    nothing will detect this automatically.
> 2. The `mcpo`/MCP bridge entries in README.md and INFRA_MAP.md were
>    **corrected in the 2026-08-19 reconciliation pass**, along with
>    `docs/REFERENCE_INFRA.md` (the public-facing counterpart, which had the
>    same staleness and was not previously listed). INFRA_MAP had six stale
>    locations, not the two cited here. The `mcp.example.com`
>    nginx vhost still exists and still 502s — that part remains open, since
>    removing it is a live config change rather than a docs fix.
- Minor, not doc-drift: the checker's section 5 derives failed units from a
  one-shot `list-units` snapshot, so a ~8 s crash-looper like `runner-demo`
  (≈2 s `active running` / ≈6 s `activating auto-restart`) is caught only
  on the majority of runs, not all — today's run happened to miss it.
  CLAUDE.md names the unit, so even when caught it passes; noting only so
  a future "runner-demo isn't in the report" isn't read as "fixed".
- Comment-level nit: checker section 4 says the prefix rule was "added
  2026-08-18"; `scrub-public-tree.py`'s comment dates the exposure to
  08-18 and the rule/entry to 08-19 (commit date). Harmless.

Net: the gate half of the commit is live and correct; the daily backstop
was installed-but-disabled and untracked at `f3ad588` and is now tracked,
enabled and has fired once as of `595e85b`; the only outstanding doc drift
is CLAUDE.md's own header still saying the checker doesn't exist (plus the
superseded 08-16 DROP_FILES invariant). No live action taken by me.

## Addendum — second pass, 05:34–05:38 EDT, against HEAD `595e85b` (delta only)

Second session, independent check of the follow-on commit `595e85b`
(05:33:49, "Add claude-md-drift-daily systemd unit + initial drift report" —
3 files, +28: the `.service`/`.timer` pair under `.config/systemd/user/` and
`docs/CLAUDE_MD_DRIFT_REPORT.md`). Everything above was written against
`f3ad588` with `595e85b` landing mid-check; this pass only covers what
`595e85b` itself could have invalidated. Nothing staged, committed, enabled,
restarted or otherwise changed live; this addendum is the only write.

**Verified live — matches what the commit claims**

- Repo unit files are byte-identical to the live copies in
  `~/.config/systemd/user/` (`diff` clean, both mtime 05:24). Timer
  `enabled`, `active (waiting)`, next fire Thu 2026-08-20 05:15:00 EDT;
  the service ran once at 05:34:08 (`Persistent=true` catch-up after
  enable) and `Finished` cleanly, exit 0.
- The timer's `Description` makes four live-state claims about neighbouring
  slots; all four match real `OnCalendar` values: `second-brain-index-scan`
  04:00, `second-brain-demo-archiver-daily` 04:15,
  `disruption-weather-digest` 04:35, `freshness-audit` 06:00 (all
  `America/New_York`). Nothing else is scheduled at 05:15.
- No re-sign owed for this commit: the `.service`/`.timer` and
  `scripts/claude-md-drift-daily.sh` were already hashed at `f3ad588`
  (`MANIFEST.sha256:108-109`, `:341`), and `docs/CLAUDE_MD_DRIFT_REPORT.md`
  is excluded from hashing by design (`scripts/sign-manifest.sh:98-103`).
  `scripts/verify-manifest.sh` → rc 0, 686 files; `integrity-sweep` 05:30:53
  `sweep OK`, next 05:45:53.
- `scripts/check-claude-md-drift.sh` (full set, no `--pre-sign`) re-run
  after the commit → rc 0, all `[OK]`; the regenerated report (05:34:08)
  reads "No drift found" and shows as ` M` in the working tree, which is its
  designed steady state.
- `docs/INFRA_MAP.md` §"Timer highlights" does not list the new 05:15 timer,
  but that section is explicitly non-exhaustive ("full list: `systemctl
  --user list-timers`"), so this is not drift. If it is ever extended, the
  "New 2026-08-11 docs-drift-weekly" sentence at `:174-178` is the natural
  place to add the daily backstop. README.md, `src/ingest/README.md` and
  `src/shared/watchlist_README.md` make no claims this commit touches.

**Drifted — nothing new from `595e85b`**

- Finding 2 above (CLAUDE.md:8-11 "does not exist yet") is unchanged by
  this commit and remains the only open CLAUDE.md drift.
- One note, not drift: the *committed* copy of
  `docs/CLAUDE_MD_DRIFT_REPORT.md` (generated 05:24:12) says
  `corporatetraveldc-integrity-sweep` is "absent from CLAUDE.md's Known bad
  section", which was already false at commit time — CLAUDE.md at `f3ad588`
  names the unit twice (the paragraph added for exactly this reason). The
  tracked copy is a stale first-run snapshot that the daily unit overwrites;
  don't read `git show HEAD:docs/CLAUDE_MD_DRIFT_REPORT.md` as current
  state.

Net for `595e85b`: the daily backstop is installed, enabled, tracked and
manifest-clean, matching its commit message; no doc it could have
invalidated has drifted. The only open item for the next CLAUDE.md pass is
still finding 2 (one sentence in the header). No live action taken.

## Addendum — third pass, 05:47–05:50 EDT, against HEAD `f914f30` (delta only)

Third session, checking commit `f914f30` (05:46:04, "Promote ops-brief
enrichment task layer from staged proposal" — 3 files, +47/−108:
`corporatetraveldc.ops-brief` task layer rewritten to the 8-section
LEAD/DC METRO/NAS PROGRAMS/ATCSCC FORECAST/TFRs/NWS ALERTS/AMTRAK NEC/
BOTTOM LINE shape with a 500-word cap; `…ops-brief.PROPOSED-enrichment-
2026-08-17` deleted; its `DROP_FILES` literal removed from
`scripts/scrub-public-tree.py`). Same rule as the two passes above: only
what this commit could have invalidated, verified live (`ollama show`,
`systemctl --user`, `journalctl`, `verify-manifest.sh`, the drift checker,
`brief_archive`). Nothing staged, committed, built, restarted or otherwise
changed live; this addendum is the only write.

**Drifted**

1. **The promotion is repo-only — the live Ollama model still runs the
   pre-commit prompt.** `corporatetraveldc-pi5-ops-brief:latest`
   (`ollama list`: modified "30 hours ago", i.e. the 08-17 rebuild) has a
   `SYSTEM` block byte-identical to `git show f3ad588:corporatetraveldc.
   ops-brief` (diffed live vs. pre-commit: clean) and differs from HEAD's
   Modelfile exactly where the commit changed it (no `LEAD:`…`BOTTOM LINE:`
   labels, no 500-word cap, old "Generate a concise operational briefing"
   paragraph). `build-models.sh corporatetraveldc-pi5-ops-brief` has not
   been run since the commit (no `ollama create` in the journal, no
   `:candidate` tag present). So the Modelfile's own new header claim
   ("2026-08-19: task-layer promoted") and the commit title are true of the
   repo and not yet of the box; `docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md`
   §7 step 3 is the still-unexecuted deploy step. Consequences worth
   stating plainly:
   - The "mandatory post-promotion retest" the new Modelfile comment calls
     for cannot be satisfied yet. An ops-brief run started manually at
     **05:46:40** (not the :00 timer — timer's last fire was 05:00; trigger
     dir empty) and was in the load-gate wait (`load 8.37 > 7.00`) at check
     time — it is exercising the **old** prompt, so whatever it writes to
     `brief_archive` is not evidence either way for the enrichment. The
     last completed brief (id 1668, 05:32 EDT, 421 words) has 0 of the 8
     new section labels, as expected for the old model.
   - Neither gate catches this. `llm.py:_verify_before_inference()`
     hashes the *repo* Modelfile against `MANIFEST.sha256` (passes — see
     2), it does not compare the live model's baked `SYSTEM` to the file;
     `check-claude-md-drift.sh` checks model count and base only (21/21,
     `phi3:mini`, `[OK]`). A Modelfile-vs-live-model divergence is
     invisible to both by design; only `ollama show --modelfile` shows it.
2. **`MANIFEST.sha256` was re-signed after the commit but the commit
   doesn't carry it.** Working tree: `verify-manifest.sh` → rc 0, 685 files
   (686 → 685, the deleted proposal); `MANIFEST.sha256`/`.asc` mtime 05:46,
   both ` M`. `git show HEAD:MANIFEST.sha256` still lists
   `…PROPOSED-enrichment-2026-08-17` and the pre-commit hashes for
   `corporatetraveldc.ops-brief` and `scripts/scrub-public-tree.py` — i.e.
   the *committed* manifest does not verify against the *committed* tree.
   Working tree is the truth; the next commit should pick up the pair.
   `corporatetraveldc-integrity-sweep` shows `failed` from its 05:45:53 fire
   (landed in the 11 s between the tree edits and the 05:46 re-sign: "1
   listed file could not be read, 2 checksums did NOT match") — exactly the
   case CLAUDE.md's "Manifest / integrity-sweep" paragraph says to expect;
   self-clears at 06:00:53 absent further unsigned edits.
3. **Dangling references to the deleted proposal file** (all dated
   snapshots, none edited by me): `docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md:281-283`
   still describes it as a "staged proposal (NOT to be promoted without
   review + post-promotion retest)" and `:160`'s `<!-- ABC_RESULTS -->` is
   still unfilled — the new Modelfile header says this out loud, so it is
   documented, not hidden; `docs/LIVE_STATE_CHECK_2026-08-17.md` and the
   first pass above ("22 `corporatetraveldc.*` files on disk incl. the
   `.PROPOSED…` draft") now describe a file that no longer exists (21 on
   disk = 21 in `MODELS` = 21 in `ollama list`). Finding 3 of the first
   pass (PHASE4 §Fix 2 superseded) tightens from "DROP_FILES enumerates one
   Modelfile" to "zero" — still superseded, nothing new.

**Still accurate / not affected**

- CLAUDE.md §Local LLM (21 models, all `FROM phi3:mini`, persona baked
  per-Modelfile, candidate/smoke/promote build) — all re-verified live.
  README.md:319/:496 and `docs/INFRA_MAP.md:163` ops-brief rows make no
  prompt-shape claim. `src/ingest/README.md`, `src/shared/watchlist_README.md`
  untouched by this commit. `scripts/check-claude-md-drift.sh` full set →
  rc 0, all `[OK]`, section 4 (Modelfile scrub coverage) still satisfied by
  the `corporatetraveldc.` prefix rule with the literal entry gone.
  Public mirror `/opt/corporatetraveldc/public/ctdi-dispatch` (last
  sanitized 2026-07-12) never contained the proposal file.
- Pre-existing working-tree state not from this commit and not mine:
  `docs/CLAUDE_MD_DRIFT_REPORT.md` ` M` (designed daily overwrite, "No
  drift found" 05:34:08), `docs/LIVE_STATE_CHECK_2026-08-18.md` ` M`
  (+179 addenda), untracked `docs/COGS_VENDOR_COMPARISON_2026-08-18.md`
  and `docs/DEPLOYMENT_COST_PROJECTION_2026-08-18.md`.
- Open from the first pass, unchanged: CLAUDE.md:8-11 still says
  `check-claude-md-drift.sh` "does not exist yet".

Net for `f914f30`: the docs this commit could invalidate are mostly dated
snapshots that now reference a deleted file (noted, not drift-worthy on
their own); the real live/repo gap is that **the enriched prompt is signed
and committed but not built** — `corporatetraveldc-pi5-ops-brief:latest`
is still the 08-17 model, the in-flight 05:46 retest runs the old prompt,
and the committed manifest lags the working-tree one. No live action taken.

## Addendum — fourth pass, 06:59–07:05 EDT, against HEAD `447dd4a` (delta only)

Fourth session, checking commit `447dd4a` (06:58:37, "Sync repo with live
state: research-board-mirror + 3 opsplan/freshness/summary timers; re-sign
manifest" — 9 files, +928/−27: CLAUDE.md header rewritten (checker now
exists + a "Needs confirmation" note), `scripts/stack-boot-ctl.sh` re-adds
ultrafeeder/acarsrouter/acarshub/dumpvdl2 to the boot ORDER, regenerated
`docs/CLAUDE_MD_DRIFT_REPORT.md`, and four docs newly tracked/extended —
`COGS_VENDOR_COMPARISON_2026-08-18.md`, `DEPLOYMENT_COST_PROJECTION_2026-08-18.md`,
the 08-18 check's +179 addenda, and this file; manifest pair re-signed).
Same rule as the three passes above: only what this commit could have
invalidated, verified live (`systemctl --user`, `journalctl`, `podman ps`,
`lsusb`, `sqlite3`, curl, `verify-manifest.sh`, the drift checker,
`ollama list/show`). Nothing staged, committed, started, restarted or
otherwise changed live; this addendum is the only write. Tree was clean at
start (`git status` empty); this file is manifest-excluded by design
(`sign-manifest.sh:103`), so my edit alone leaves `verify-manifest.sh` at
rc 0 — but see the coda at the end: a concurrent session edited a
*hashed* doc at 07:04:52 while this pass was being written, and the
manifest is now failing on that file, not this one.

**Verified live — matches what the commit claims**

- **`stack-boot-ctl.sh` SDR re-add: every factual claim in the new ORDER
  comment checks out.** `lsusb` → two `0bda:2838 RTL2838` (Bus 001 Dev 017,
  Bus 003 Dev 005). `corporatetraveldc-ultrafeeder`/`acarsrouter`/`dumpvdl2`
  `active (running)` since 22:13:26–28 EDT 08-18 (manual starts ~53 min
  after the 21:19 boot); `acarshub` `active` since **06:08:13** today
  (`WantedBy=` empty, exactly as the comment says — it was the one missed
  across the reboot). `acars.example.com` vhost now answers
  **200** (was the standing 502 the comment describes). The
  `sdr-crashloop-guard` timer (5-min) has been reporting both
  `ultrafeeder`/`dumpvdl2` `healthy … device_present=true` (07:00:03 run);
  its own log shows it was the thing that stopped ultrafeeder at 21:35:01
  08-18 ("ADSB1090 no longer enumerated … dongle physically gone") before
  the hardware swap — consistent with the commit's narrative.
- **The re-add is repo-only until the next boot.** The last
  `corporatetraveldc-stack-boot-stagger.service` run (21:20:04 08-18, this
  boot) executed the *pre-commit* script: "starting 15 units", no SDR units.
  The new ORDER has 19 entries and has not been exercised; the four SDR
  units currently run only because they were started by hand. Not drift —
  just don't read "re-added 2026-08-19" as "boot-tested".
- **CLAUDE.md "Needs confirmation" note — half-confirmable now, half not.**
  What can be confirmed: `MANIFEST.sha256`/`.asc` were signed 06:58:37
  (`gpg --verify` good) with the *new* CLAUDE.md hash, and
  `scripts/check-claude-md-drift.sh --pre-sign` re-run now against the
  committed CLAUDE.md → rc 0 (full set also rc 0, all `[OK]`, "CLAUDE.md
  matches live state"). What cannot: `sign-manifest.sh` logs the gate
  result to stdout only (no file/journal record), so there is no artifact
  proving `SKIP_DRIFT_CHECK` was *not* set on the 06:58 pass; and the daily
  unit has not fired since (last 05:34:08, next Thu 2026-08-20 05:15 EDT).
  Leave the note in place until tomorrow's run — I did not edit CLAUDE.md.
- **Third-pass finding 2 (committed manifest lagging the tree) is
  resolved by this commit**: `git status` clean + `verify-manifest.sh` rc 0
  (685 files) ⇒ the committed manifest now verifies against the committed
  tree. `integrity-sweep` failed once at 06:57:42 (the 55 s window of
  unsigned edits before the re-sign) and its re-run at 06:58:39 succeeded —
  exactly the CLAUDE.md "Manifest / integrity-sweep" case.
- **Known bad section — re-verified, mostly still accurate** (one
  exception, below): `second-brain-weekly` `failed` (only `failed` unit,
  still the 08-18 21:19:59 exit 1); `runner-demo` crash-looping
  (`NRestarts=4304`, :8005 refuses); `mcp.example.com` → 502;
  `transport-pattern-digest` and `second-brain-demo-archiver-daily` both
  `Result=success` on their latest fires (00:42:26 / 04:15:06).
  `/healthz` 200, `cps GREEN/GO`.

**Drifted**

1. **Five of the seven ingest containers are `inactive (dead)` — by
   design, but CLAUDE.md and `src/ingest/README.md` currently read as if
   all six SWIM feeds are up.** Not caused by this commit, but this commit
   re-signed CLAUDE.md with the claims in it, so recording it here.
   `corporatetraveldc-ingest-{fdps,itws,stdds,tbfm,tfms}` were stopped
   22:58:26 (tfms, stdds) and 23:08:26–28 (fdps, tbfm, itws) EDT 08-18 by
   `scripts/thermal-ingest-guard.py` — `tripped tier 2 (load 14.72)`,
   `guard_label: "Load Guard"` in `thermal_ingest_guard_state.json` — and
   have stayed shed for ~8 h because resume needs load1 < 6.0 **held for
   300 s** (`THERMAL_GUARD_RESUME_LOAD` default; `RESUME_DWELL_S=300`) and
   load1 has sat at 5–8 all morning (9.79 at 07:01). `/healthz` says so:
   `"status":"degraded","reason":"Stale feeds: notam, push:fdps,
   push:itws, push:stdds, push:tbfm, push:tfms"`; `feed_state` push rows
   for those five are frozen at 22:55–23:07 08-18. Only `ingest-core` and
   `ingest-notam` (fns) are running. Consequences for the docs:
   - CLAUDE.md:126 "All six SWIM feeds live since 2026-07-20" and :234
     "all six push feeds are live" are provisioning statements and remain
     true in that sense, but a reader checking them against `podman ps`
     right now sees 2/7.
   - CLAUDE.md Known bad: "`corporatetraveldc-ingest-tbfm.service` is not
     failed … live state shows it `active (running)`, writing sequences
     normally" — it is *still* not `failed` (`Result=success`), but it is
     `inactive (dead)`, not running. The paragraph's point (don't expect
     `failed`) holds; its evidence sentence is stale.
   - `src/ingest/README.md:32` "All 7 are **running and live** as of
     2026-08-11" — dated, and the guard's shedding behaviour is not
     mentioned anywhere in that README or in CLAUDE.md (only
     `docs/INFRA_MAP.md:170` names the timer). Suggest one sentence in
     each pointing at `thermal-ingest-guard.py` + `ingest-feed-ctl.sh start`
     so a future reader doesn't treat a Load-Guard shed as an outage.
   - Checker blind spot, not drift: `check-claude-md-drift.sh` reports
     `[OK] API healthy` on HTTP 200 alone (the body says `degraded`) and
     its section 5 only looks at `failed` units, so a guard-shed
     `inactive` ingest fleet passes as "CLAUDE.md matches live state".

   > **RESOLVED 2026-08-19 (later that day).** Every recommendation in this
   > finding was actioned in the reconciliation pass, and the finding itself
   > proved understated — the shed lasted until **07:17 EDT**, roughly eight
   > hours, and the guard then re-tripped to tier 1 within the hour, so the
   > fleet oscillates rather than settling.
   > - CLAUDE.md now has a dedicated **"Ingest load-shedding"** section with
   >   the full tier/restore threshold table, the state-file path, and an
   >   explicit "do not restart these to fix it" warning; :126 and :234 were
   >   reworded from liveness claims to provisioning claims.
   > - The stale tbfm evidence sentence in Known bad was replaced — the new
   >   text refuses to record a per-unit expectation at all, since the state
   >   flips on a 2-minute cadence.
   > - Both checker blind spots are now documented at the top of CLAUDE.md,
   >   with a NEEDS OPERATOR DECISION on whether to tighten checks 5 and 9.
   > - `src/ingest/README.md` got the suggested pointer sentence.
   > - One correction to this finding: it attributes the restore threshold
   >   to `THERMAL_GUARD_RESUME_LOAD` "default", which is right, but worth
   >   making explicit — `TIER1_LOAD`, `TIER2_LOAD` and `RESUME_LOAD` are
   >   **script-only defaults present in no env file**, so unlike the
   >   temperature thresholds they cannot be tuned from `dispatch.env` at
   >   all. `docs/DATA_SOURCES.md` claimed otherwise and was corrected.
2. **Commit message vs. commit content.** The title says "Sync repo with
   live state: research-board-mirror + 3 opsplan/freshness/summary
   timers", but none of those unit files are in the diff (the 9 files are
   listed above), and the repo/live unit dirs still differ on exactly those
   names: repo tracks `.config/systemd/user/corporatetraveldc-research-board-mirror.timer`
   (since `75ca192`, 08-09) which is **not installed live** — no timer
   unit, `list-timers` empty for it, the generated
   `research-board-mirror.service` has **never run** (`journalctl` "No
   entries", `WantedBy=`/`TriggeredBy=` empty); and repo carries duplicate
   `corporatetraveldc-{daily-opsplan,freshness-audit,weekly-summary}.timer`
   in *both* `.config/containers/systemd/` and `.config/systemd/user/`
   (byte-identical, since `fee09bb` 06-08) while live has them only in
   `~/.config/systemd/user/` (the quadlet dir copies would be ignored by
   systemd anyway). So the "sync" this title describes did not land in
   this commit; the repo is still ahead of live on research-board-mirror
   and carries three dead duplicate timer files. `docs/INFRA_MAP.md` makes
   no claim about research-board-mirror, so no doc drift — git-history
   drift only.
3. **Stale "18 units" count for the boot stagger** — pre-existing, but
   this commit moved the real number again: ORDER was 15 before
   (`stack-boot-stagger` logged "starting 15 units" at 21:20 08-18) and is
   **19** now, while `corporatetraveldc-stack-boot-stagger.service`'s
   `Description=` (repo == live), `scripts/stack-boot-ctl.sh:12`, and
   `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:21` all still say **18**. Also
   `scripts/sdr-crashloop-guard.sh:6` cites "see stack-boot-ctl.sh's ORDER
   comment" for the 08-11 casualty reasoning — that comment was replaced
   by this commit and now itself defers to git history. Minor; fix on the
   next pass that touches either script (both are tracked ⇒ re-sign).
4. **Open from the third pass, unchanged:** `corporatetraveldc-pi5-ops-brief:latest`
   is still the 08-17 build (`ollama list` "31 hours ago"; baked `SYSTEM`
   has 0 of the `LEAD:`/`BOTTOM LINE:` labels vs 2 in HEAD's
   `corporatetraveldc.ops-brief`) — the promoted prompt remains signed and
   committed but not built. No `ollama create` in the journal since 05:46.

**Still accurate / not affected**

- README.md:55 and `docs/INFRA_MAP.md:360-364` (UltraFeeder "restored
  2026-08-11", `lsusb` shows both dongles) are dated entries that happen to
  be true again today; neither mentions the 08-11→08-19 exclusion window,
  so nothing to retract. README.md:106 (`acarshub` :9081) and
  INFRA_MAP.md:108 — container back up, accurate. `docs/ALERT_REFERENCE.md`,
  `docs/ALERT_ARCHITECTURE.md`, `src/shared/watchlist_README.md` — no
  claims touched by this commit.
- The two newly tracked cost docs are dated planning estimates with their
  own caveat banners; three lines in them are already overtaken but are
  not drift in the sense of this check: both say "intentionally left
  **uncommitted**; the operator commits personally or not at all" (the
  operator did just that — self-consistent, but the sentence now reads
  oddly in a tracked file); `DEPLOYMENT_COST_PROJECTION_2026-08-18.md:55`
  "22 repo Modelfiles (+1 PROPOSED variant)" is 21 since `f914f30`; and
  `:58` "7 SWIM push feeds" disagrees with CLAUDE.md/README's six (the
  seventh container, `core`, carries zero SWIM feeds). Left as-is — dated
  snapshots, operator's call.
- Regenerated `docs/CLAUDE_MD_DRIFT_REPORT.md` ("No drift found",
  05:34:08) is now the committed copy and matches the checker's current
  output; the earlier "stale first-run snapshot" note from the second pass
  is moot.
- Still accurate from the first pass and not re-litigated: ntfy topics,
  auth tiers, 21 models all `FROM phi3:mini` (`[OK] model count matches
  (21)`, single base), `mcpo*` → 0 units.

Net for `447dd4a`: the SDR re-add is factually correct and live (but
boot-untested until the next reboot), the CLAUDE.md header edit is
signed-and-gate-checked as far as the evidence allows (daily-run
confirmation still pending), and the committed manifest now matches the
committed tree. The one thing a reader of CLAUDE.md would get wrong *right
now* is the ingest fleet: five SWIM containers are Load-Guard-shed, not
failed, not broken — a designed state the docs don't describe. The commit
title over-claims a unit-file sync that isn't in the diff. No live action
taken by me.

**Coda — concurrent edit landed during this pass (07:04:52 EDT)**

While this addendum was being written, another session (a
`claude --remote-control` process, not this one) appended a 53-line
"INDEPENDENT RE-VERIFICATION — 2026-08-19 ~07:00 EDT" block to
`docs/COGS_VENDOR_COMPARISON_2026-08-18.md` (` M`, +53). That file **is**
hashed in `MANIFEST.sha256`, so `scripts/verify-manifest.sh` now reports
`docs/COGS_VENDOR_COMPARISON_2026-08-18.md: FAILED … INTEGRITY FAILURE`
and `corporatetraveldc-integrity-sweep` will show `failed` from its next
fire until someone re-signs — the CLAUDE.md "Manifest / integrity-sweep"
case, not a compromise, and not from this file. Two notes on its content,
since it overlaps finding 1 above:

- It reaches the same 5-of-6-SWIM-feeds-down observation independently
  (same `healthz`/`feed_state`/`list-units` evidence), which is useful
  corroboration — but it characterises them as "stopped and never
  restarted after the 21:19 host reboot". The journal says otherwise: all
  five *were* restarted post-boot (`ingest-feed-ctl.sh` 21:23:46–21:25:50,
  then the guard's own `restored (tfms,stdds,fdps,tbfm,itws)` at 22:49:26)
  and were then re-shed by `thermal-ingest-guard.py` at 22:58 / 23:08 on
  load (`tripped tier 2 (load 14.72)`), and have stayed shed because the
  resume dwell (load1 < 6.0 for 300 s) has not been met. "Load-Guard-shed,
  awaiting resume" is the accurate state; "dead since the reboot" would
  send someone chasing a boot-order or Quadlet bug that isn't there.
- Its ACARS/VDL and METAR "recovered" findings agree with the SDR-stack
  verification above.

Neither session staged or committed anything; both edits are working-tree
only. Whoever next runs `scripts/sign-manifest.sh` picks up the COGS edit;
this file needs no re-sign.

## Addendum — fifth pass, 09:50–09:56 EDT, the weekly timer job itself (working tree, not a new commit)

Fifth session. **This pass is `corporatetraveldc-docs-drift-weekly.service`
running** — started by hand at 09:49:48 (`ExecMainStartTimestamp`; the timer's
own next fire is still Mon 2026-08-24 09:00), and this shell is inside the
unit's cgroup
(`/user.slice/…/corporatetraveldc-docs-drift-weekly.service`). HEAD is still
`447dd4a`; what changed since the fourth pass is the **uncommitted
reconciliation pass** (19 files, +4282/−538, CLAUDE.md rewritten) — so this
pass verifies *that* pass's live-state claims, not a commit. Everything below
re-verified live (`systemctl --user`, `journalctl`, `podman`, `sqlite3
-readonly`, curl, `ollama` via the tailnet host, the drift checker,
`verify-manifest.sh`). Nothing staged, committed, started, restarted or
changed live by me; this addendum is the only write.

**The weekly unit: PATH fix is live and works — CLAUDE.md's header is now
stale in one specific way.** The repo and live `.service` are byte-identical
(`Environment=PATH=/home/corporatetraveldc/.local/bin:…`), `NeedDaemonReload=no`
(so the reload CLAUDE.md says is still owed has been done), and `claude` was
found — the proof is that this text exists; the 09:49 log file was 0 bytes at
start instead of the old 65-byte `nohup: failed to run command 'claude'`.
CLAUDE.md's opening still says the fix is "**unproven** — … has not been
exercised, and the unit needs `systemctl --user daemon-reload`" and "has
never once completed successfully"; after this run both sentences should be
re-read against `systemctl --user show corporatetraveldc-docs-drift-weekly
-p Result -p ExecMainStatus` — I cannot see my own exit status from inside
the run, so I am not claiming "completed successfully" here; whoever reads
this next should. One correction to the 08-17 narrative while at it: the
journal shows the 08-17 09:00 run's `status=1` came from a *second* cause as
well — `weekly-doc-drift-check.sh: line 16: cd:
/home/corporatetraveldc/mcp/dispatch-mcp: No such file or directory` (the
since-removed dispatch-mcp `run_check`; `nohup`'s own failure was the first
repo's). Both causes are gone (script comment dated 2026-08-17 removes the
second repo; PATH line fixes the first). The 65-byte log is still the only
*artifact*, so CLAUDE.md's sentence is true but incomplete.

**Reconciliation-pass claims re-verified live — all hold** (listing so the
next pass need not): `ollama.service` ungoverned (`DropInPaths` = `10-binding`
+ distro `10-timeout-abort` only, `CPUWeight=[not set]`,
`MemoryHigh/Max=infinity`, cgroup `cpu.weight 100`, `cpu.max max`; journal
has 28 × `prompt cache is enabled` this boot); `auth_tokens` 15 rows / 15
`expires_at IS NULL` / 5 active, incl. `ctdc_admin_` ·
`mcpo-corporatetraveldc-dispatch-mcp` (admin); `audit_log` 12 rows;
`dispatch.env:200 ANTHROPIC_FALLBACK_ENABLED=false`, `:9 TZ=America/New_York`,
`grep -rn America/New_York src/` → 0; `DEMO_MODE` in no env file and no
runner-demo Quadlet (repo == live), `runner/main.py:47` default false,
`:1612 CHAT_DB_PATH`; `Containerfile.{web,poller,pusher,ingest}` reference
`verify-manifest` 3× each but never invoke it, `Containerfile.runner` 0×,
web `Cmd=[uvicorn …]` `Entrypoint=[]`; 31 `verified-exec.sh` skill Quadlets
(repo == live); `SCHEMA_V33` top, `PRAGMA user_version` 0;
`/admin/approval-requests/{request_id}/resolve` at `main.py:2370`, 23 ×
`Depends(require_admin)`, the only `db.audit(` at `:1773`;
`mcp.example.com` → 502, `:8005/:8082/:8083` refuse;
`claude_desktop_config.json` still points at `/home/corporatetraveldc/mcp/
dispatch-mcp/…` (only `…archived-20260817` exists) and still holds
`FLIGHTAWARE_API_KEY`; `second-brain-weekly` `failed` (08-18 21:19:59, WebDAV
502 on `01-Sources/daily`); `transport-pattern-digest` / `demo-archiver-daily`
latest runs `success`; `runner-demo` `auto-restart`, `NRestarts=5507`; all
four SDR units active, `acars.example.com` → 200; 21 models, one
base blob, `check-claude-md-drift.sh` full set rc 0; README's 118 units / 61
repo Quadlets / RSS 11·32·33 all re-count the same (containers now 36, not
34 — the guard restored three ingest units; README already says not to
hardcode it). Ingest at check time: `stdds`,`tfms` `inactive (dead)` /
`Result=success` (guard **tier 1** since 08:13:52, `peak_load1 10.02`),
the other five running; `/healthz` → `degraded` / `Stale feeds: push:stdds,
push:tfms`; load1 13.3. The guard trace since the fourth pass: tier-2 shed
23:08 → restore **08:10:52** (not 07:17 — 07:17 was a tier-1 *trip* while
still shed, the restore came 53 min later) → re-trip tier 1 at 08:13:52
(three minutes later). CLAUDE.md's "~8 h" and "oscillates" characterisation
stands; its "07:17" should read 08:10 if anyone quotes it as the restore.

**Drifted / new**

1. **CLAUDE.md:446-449 overstates the load-threshold gotcha.** It says
   `THERMAL_GUARD_TIER1_LOAD / TIER2_LOAD / RESUME_LOAD` "are script-only
   defaults … so the thresholds that actually fire **cannot be tuned via
   `dispatch.env`**". They can: `thermal-ingest-guard.py::_cfg()` parses
   `dispatch.env` + `dispatch-secrets.env` and `:276-279` read
   `cfg.get("THERMAL_GUARD_TIER1_LOAD")` etc. with 10.0/14.0/6.0/300 as
   *fallbacks*; the script's own docstring (`:56-67`) lists all three as
   `dispatch.env` tunables. What is true is that `dispatch.env:278-284`
   currently sets only `ENABLED`, the three `*_TEMP_C`, `RESUME_DWELL_S` and
   the two `*_FEEDS` — the load knobs are *absent*, not *unsupported*.
   `docs/DATA_SOURCES.md:1021-1030` gets this exactly right ("tunable in
   principle … an operator working from `dispatch.env` will not find them");
   the fourth pass's RESOLVED block above (“cannot be tuned from
   `dispatch.env` at all”) repeats the overstatement and I am correcting it
   here rather than editing that block. Fix is one clause in CLAUDE.md:
   "present in no env file — add them to `dispatch.env` to tune" instead of
   "cannot be tuned".
2. **A third checker blind spot, worse than the two CLAUDE.md documents.**
   Right now `scripts/verify-manifest.sh` → **rc 1, 18 checksums do not
   match** (the reconciliation pass's edits, unsigned since ~07:05;
   `integrity-sweep` has failed every fire since 07:13:38, 11 in a row), yet
   `check-claude-md-drift.sh` prints `[OK] manifest and signature both clean`
   and exits 0 "CLAUDE.md matches live state". Check 8 (`:155-165`) is only
   `git diff --quiet MANIFEST.sha256 MANIFEST.sha256.asc` — it asks whether
   the *manifest files* are modified, never whether the *tree* verifies
   against them. So the one thing its label promises ("manifest … clean") is
   the one thing it doesn't test; and check 5 sees `integrity-sweep` failed
   but passes it because CLAUDE.md names the unit (by design). The first pass
   above even transcribed this as "manifest+signature clean". Suggest the
   top-of-CLAUDE.md blind-spot list grow a third bullet, and/or check 8 run
   `scripts/verify-manifest.sh` (cheap, ~1 s) — **NEEDS OPERATOR DECISION**
   like the other two, since it changes the signing gate.
3. **"A stale manifest … will block skill runs and inference" is only true
   for the tree the process runs in — which for the 31 timer skills is the
   image, not the host checkout.** With the host tree failing verification
   all morning, `ep-advance` still generated via Ollama at 09:04:16 and
   `ops-brief` at 08:00/09:20 (its 08:30 deterministic fallback was "could
   not acquire Ollama lock within 1800.0s", not a manifest refusal —
   `brief-fallback-monitor` alerted on that at 08:50 and 09:50). Reason:
   the skill Quadlets run `localhost/corporatetraveldc-poller:latest`
   (built 2026-08-18 02:27 EDT) with only `/var/lib/corporatetraveldc`
   mounted, and `Containerfile.poller:25-29,38` `COPY`s `src/`, the verify
   scripts, `MANIFEST.sha256{,.asc}` *and the Modelfiles* into the image —
   so `verified-exec.sh` and `llm.py` verify the image's own baked snapshot.
   Host-side, `scripts/verified-exec.sh` *would* refuse right now
   (`src/ingest/README.md: FAILED` under its `src/` scope). Consequences:
   (a) unsigned host edits do not block the running timer fleet — they only
   block host-invoked runs and the sweep, and would be baked in at the next
   `build-images.sh`; (b) conversely, the image still carries the
   *pre-`f914f30`* `corporatetraveldc.ops-brief` Modelfile and manifest, which
   is consistent with the third/fourth-pass finding that the promoted prompt
   is unbuilt (`ollama list`: `ops-brief` "34 hours ago", 0 of the
   `LEAD:`/`BOTTOM LINE:` labels vs 2 in HEAD — **still open**). CLAUDE.md's
   "Enforced / NOT enforced" paragraph is accurate as far as it goes; it
   should add "against the copy baked into the image at build time".
4. **Unit-file dirs: one live-only public-ish surface the docs don't know
   about, two retired units still tracked.** Live-only (not in repo, not in
   README/CLAUDE.md/INFRA_MAP/any doc): `corporatetraveldc-ccw-demo.container`
   + `ccw-demo-webdev-expiry.{service,timer}` (all 2026-08-18 00:10) —
   `nginx:alpine` serving `~/demos/corporate-car-worldwide/site` on
   `127.0.0.1:8085`, `active (running)` since boot, answers **401** (HTTP
   Basic Auth, so gated); expiry timer next fires 2026-08-25 00:10. Its
   header claims a Cloudflare Tunnel route at
   `ccw-preview.example.com` — **that route does not exist**:
   no DNS record (`getent hosts` empty, public curl → 000), no such
   `hostname:` in `~/.cloudflared/config.yml`, no nginx vhost. So it is a
   tailnet/localhost-only preview whose own comment overstates its
   exposure; not a security finding, but the "Key paths" contract (repo
   `.config/…` ↔ live `~/.config/…`) is one unit short. Repo-only:
   `.config/systemd/user/corporatetraveldc-mcpo{,-public}.service` are still
   tracked (`git ls-files`) 1 day after the retirement — no doc claims them
   live (INFRA_MAP §mcpo is already in "ran / Unit (retired)" framing), so
   this is repo hygiene, not doc drift. Unchanged from the fourth pass:
   `research-board-mirror.timer` repo-only / never installed; the three
   duplicate `{daily-opsplan,freshness-audit,weekly-summary}.timer` copies
   under `.config/containers/systemd/`.
5. Trivia, recorded only so they aren't re-found: CLAUDE.md "`audit_log`
   … newest 2026-08-17" — newest `event_time` is 2026-08-17 01:07 **UTC** =
   2026-08-16 21:07 EDT, i.e. the file breaks its own "present in Eastern"
   rule by one calendar day; `NRestarts` 4359 → 5507 (snapshot, fine);
   running `ollama list` from a plain shell needs
   `OLLAMA_HOST=100.x.x.x:11434` (the `10-binding` drop-in moves it off
   localhost; `~/.bashrc:37` exports it, the systemd unit environment does
   not — the checker and CLAUDE.md commands that assume `ollama` just works
   will say "could not connect" from a unit).

**Not drift, but the state of the tree matters to whoever signs next:**
the reconciliation pass is ~3 h old and unsigned; `integrity-sweep` will
keep failing every 15 min until `scripts/sign-manifest.sh` runs, and per
finding 2 the checker's own gate will not notice. `docs/LIVE_STATE_CHECK_*`
is manifest-excluded, so this addendum adds nothing to the 18.

Net for the fifth pass: the reconciliation pass's factual claims all check
out live — the only wrong sentence is the load-threshold "cannot be tuned"
clause (finding 1), and the only *stale* sentences are the ones this very
run just obsoleted (the weekly unit's "unproven / needs daemon-reload"
header). Two precision gaps in the integrity story are new (checker check 8
doesn't verify the tree; the skill gate verifies the image, not the host
checkout). The enriched ops-brief prompt remains unbuilt. No live action
taken by me.
