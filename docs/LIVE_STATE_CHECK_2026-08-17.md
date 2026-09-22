# Live State Check — 2026-08-17

Written ~12:20–12:25 EDT, immediately after `be4cb62` landed as HEAD
(12:18:59 EDT, branch `smoke-test-harness-2026-08-17`): "Smoke-test harness:
FDPS HF/RH allowlist fix, ntfy header-encoding fix, real-sample test
coverage" — 15 files: `docs/SMOKE_TEST_HARNESS_2026-08-17.md` (new),
`scripts/smoke-test-platform.sh` (new), `scripts/weekly-doc-drift-check.sh`
(dispatch-mcp `run_check` removed), `src/ingest/parsers/fdps_parser.py`
(`HF`,`RH` added to `_KNOWN_SOURCES_FIXM30`), `src/shared/watchlist.py`
(collapse newlines in ntfy title, both at `watchlist_event_hit` and at the
HTTP boundary in `_fire_ntfy_dual`), 9 real FIXM 3.0 fixtures +
`tests/ingest/test_fdps_fixm30_real_samples.py`. Same rules as the
08-12…08-16 checks: does THIS commit invalidate anything README.md,
CLAUDE.md, docs/, `src/ingest/README.md`, or `src/shared/watchlist_README.md`
currently claim? Verified against the live box, not prior docs. Nothing
staged, committed, or changed live.

The operator was actively deploying while this ran (images rebuilt
12:20:20–12:20:49 EDT; poller/web/pusher restarted 12:21:46) — timestamps
below are as observed.

## Live snapshot verified

- `scripts/verify-manifest.sh`: **OK, 667 files** — the manifest was
  re-signed at **12:19 EDT** (right after the commit). But `MANIFEST.sha256`
  and `MANIFEST.sha256.asc` are **uncommitted working-tree edits** (`git
  status`: ` M MANIFEST.sha256`, ` M MANIFEST.sha256.asc`; last committed
  manifest is `cc863d2`, 2026-08-16 14:35). HEAD's committed manifest does
  not match HEAD's own tree — any checkout/stash of this branch elsewhere
  breaks every `verified-exec.sh` skill again. Presumably intended to ride
  in the next commit; noting because the prior checks' commits included
  the re-sign in the same commit.
- Re-sign confirmed effective end-to-end: `personal-notes-import` (2-min
  timer) failed `INTEGRITY CHECK FAILED` at 12:19:12 (raced the re-sign),
  ran clean at 12:21:14 and left the failed list. Failed units **20 → 19**
  during this check; the remaining 19 are stale `failed` state that clears
  at each unit's next trigger (all their `ExecMainExitTimestamp`s predate
  12:19; nothing has re-failed since). `boot-stagger` (failed 08-14 08:48)
  and `docs-drift-weekly` (09:00 today, `cd` to the renamed dispatch-mcp
  dir — fixed by this commit, next run Monday 08-24) are the two
  non-manifest failures, matching the new doc's Finding 1/2.
- `scripts/smoke-test-platform.sh` run now: `SMOKE-TEST: FAIL (1 failing
  category)` — 19 failed units; manifest OK; all 4 endpoints 200; ACARS
  `KNOWN-FAIL` (`acars_messages` = 0 rows, re-confirmed).
- 115 `corporatetraveldc-*` user units loaded (117 on 08-16; the two mcpo
  units are still among them — see drift item 1); 30 containers up.
- **FDPS `HF`/`RH` fix is NOT yet live**: `systemd-corporatetraveldc-ingest-fdps`
  is still the 2026-08-16 22:07 container (image `13780ac18cef`) — `grep`
  inside it finds no `"HF", "RH"`. The freshly built
  `localhost/corporatetraveldc-ingest:latest` (12:20:20 EDT, `dbbeb8fc2676`)
  does contain it (line 155). Needs `scripts/ingest-feed-ctl.sh restart
  fdps` (or the staggered `all`). Not restarted by me.
- **Watchlist ntfy-title fix IS live** in the running poller — both halves
  (`title_summary = " ".join(event_summary.split())` and the
  `safe_title` collapse in `_fire_ntfy_dual`) present in
  `/app/src/shared/watchlist.py` of the container that was running from
  07:55 and again in the 12:21:46 restart.
- Test suite on this HEAD: **17 failed, 114 passed** (1.5 s) — matches the
  new doc's Finding 10 exactly (same 11 watchlist schema-chain failures,
  5 `_dispatch_proxy_headers` orphans, 1 `test_smes_parser_basic`).
- `/var/lib/corporatetraveldc/fdps_debug_fixm30/` rotates (13 files
  rewritten 08-16 22:07, 12 at 11:36 today, 1 at 12:22 during this check).
  Current 25 files by primary `source`: TH 4, HP 4, HX 4, CL 3, AH 2, HF 2,
  HZ 2, OH 2, RH 2 — HF+RH = 4/25 (16 %), vs. the new doc's "5 of 25
  (20 %)". Not a doc error (the batch it counted has partly rotated) but
  the figure isn't reproducible from a dated capture; fine as written.

## Drift found

### 1. "MCP is fully retired from this platform" (this commit) — live, it isn't

`scripts/weekly-doc-drift-check.sh` (this commit) says: "repo archived
(GitHub repo archived read-only, local dir renamed
`dispatch-mcp.archived-20260817`) — MCP is fully retired from this platform,
nothing left to drift-check." `docs/SMOKE_TEST_HARNESS_2026-08-17.md`
Finding 2 says the same ("MCP is fully retired"). Live:

- `gh repo view CorporateTravelDC/corporatetravel-dispatch-mcp` →
  `isArchived: true`, archived 2026-08-17T06:33Z. ✅ true.
- `/home/corporatetraveldc/mcp/dispatch-mcp` → gone; only
  `dispatch-mcp.archived-20260817/` exists. ✅ true.
- **`corporatetraveldc-mcpo.service` (:8082) and
  `corporatetraveldc-mcpo-public.service` (:8083) are both `active
  running`** since the 08-14 08:41 boot (PIDs 1737/1740), each with a live
  `dispatch-mcp` child from `/opt/corporatetraveldc/corporatetravel-dispatch-mcp/venv/`
  (the wrapper `exec`s that venv; the venv still exists). Both unit files
  are still enabled in `~/.config/systemd/user/` and in the repo's
  `.config/systemd/user/`.
- `/etc/nginx/conf.d/mcp.example.com.conf` still proxies to
  :8083; `https://mcp.example.com/openapi.json` → **200, 26
  paths**. `http://127.0.0.1:8083/openapi.json` → 200.
- **Latent breakage:** both units' `ExecStart` point at
  `/home/corporatetraveldc/mcp/dispatch-mcp/dispatch-mcp-wrapper.sh`, which
  no longer exists after the rename. They keep running only because the
  processes predate the rename; the next `systemctl --user restart`,
  daemon-reload+restart, or reboot will fail both units (and 502 the public
  `mcp.` hostname). `~/.config/Claude/claude_desktop_config.json` line 11
  points at the same dead path.

So the retirement is half-done: source archived, but the runtime layer
(2 units, 2 ports, 1 public vhost, 1 desktop client config) is still up
and now unrestartable. Two consistent ways to close it — operator's call:
(a) finish the retirement: stop/disable both mcpo units, remove them from
`.config/systemd/user/` in the repo, retire the `mcp.` vhost (same
`_RETIRED_HOSTNAMES`-style treatment as `ops.` on 08-02), then update the
docs below; or (b) if MCP is *not* actually retired, restore the wrapper
path (symlink or point `ExecStart` at the archived dir / the `/opt` venv
directly), reinstate the `dispatch-mcp` `run_check` (pointing at the
archived dir, or drop it deliberately with a different justification), and
soften the "fully retired" wording in the two places above.

Docs whose MCP claims are still *literally* true today (the bridges are
serving) but contradict the commit's stated intent — all need touching
under option (a), only §9's checkout path under option (b):

- `README.md:52,161` — "Public MCP (OpenAPI bridge) … 26 read-only tools".
- `CLAUDE.md:15` — auxiliary services list ends "…MCP bridges)".
- `docs/INFRA_MAP.md:37,300-302` — "Runtime checkout:
  `/home/corporatetraveldc/mcp/dispatch-mcp`" — **already false live**
  (dir renamed) under either option; §9 as a whole (mcpo ×2 table, `mcp.`
  vhost row at :222 "Live (deployed 2026-08-11)").
- `docs/REFERENCE_INFRA.md:36,73-81,121-128,149` — MCP layer §9.
- `docs/INFRA_MAP.md:175-178` — see item 2 (invalidated regardless).

### 2. `docs/INFRA_MAP.md:175-178` — docs-drift-weekly "against both this repo and `dispatch-mcp`"

Invalidated by this commit: `weekly-doc-drift-check.sh` now runs one
`run_check` (this repo only). Same sentence should drop "and
`dispatch-mcp`". The script's own header comment (line 3-5, "covering BOTH
repos") is likewise stale after the edit made two lines below it — a
one-word fix in the same file.

### 3. `src/ingest/README.md:94` — FDPS "Sources handled" list is short by two

Says: "`FH` (flight plan), `TH` (track), `CL` (cancel), `HP`/`OH`
(handoff), `HZ` (heartbeat), plus `AH`/`BA`/`LH`/`HX` (generic
extraction)". This commit adds `HF`/`RH` to `_KNOWN_SOURCES_FIXM30` (same
generic path). Add "`HF`/`RH`" to the generic-extraction group. (Only takes
effect live after the ingest-fdps restart noted above.)

### 4. `docs/SECOND_BRAIN_STATUS.md:289` — "narrower real gap remaining" is now closed (and mis-named)

"…source types `HT`/`RH` seen in live captures aren't in
`_KNOWN_SOURCES_FIXM30` and are currently silently skipped". Two things:
(a) the type is `HF`, not `HT` — no `HT` source exists in any current
capture (`HT` matches only inside unrelated element text); (b) as of this
commit `HF` and `RH` are both in the allowlist. Strike-through/annotate the
same way the earlier part of that bullet already was on 2026-07-23. The
second half of the bullet (`registration` extracted but no DB column) was
not checked here.

### 5. The new doc's own "Branch state" is stale the moment it was committed

`docs/SMOKE_TEST_HARNESS_2026-08-17.md` header: "un-pushed, uncommitted";
"Branch state: 19 changed/new files, uncommitted". Now: committed as
`be4cb62`, 15 files in the commit (the 19 presumably counted the
then-unsigned manifest pair and other working-tree items). Also Finding 1
("The operator needs to run `scripts/sign-manifest.sh` themselves") is
done as of 12:19 — worth a one-line "resolved 12:19 EDT, manifest
re-signed (uncommitted)" note so the next reader doesn't re-diagnose. And
"Live-verified: running it right now shows `SMOKE-TEST: FAIL (2 failing
categories)`" is now 1 (manifest OK). None of this is wrong as a
point-in-time record; flagging because this repo's dated docs are usually
annotated when their headline finding closes.

## Still accurate (checked because this commit could have touched them)

- `src/shared/watchlist_README.md` — nothing there describes the title
  truncation, header encoding, or `_fire_ntfy_dual` internals; the
  `TRN`/`VSL` title-prefix notes (lines ~149-151) still match the code.
  No change needed for the newline fix.
- `docs/ALERT_REFERENCE.md` / `docs/ALERT_ARCHITECTURE.md` — describe
  topics/priorities/throttles, not header sanitisation; unaffected.
- `README.md`/`CLAUDE.md` `pytest` invocation, SR-1/SR-2 rules, signed-
  manifest instructions ("re-sign with `scripts/sign-manifest.sh` … or
  rebuilt containers/skills will refuse to run") — the outage in the new
  doc's Finding 1 is exactly the case that sentence warns about; the
  wording is right, no change.
- `docs/INFRA_MAP.md:79,175` — `docs-drift-weekly` timer pair exists and
  is Mondays 09:00 ET (fired 09:00 today; failed only on the dead `cd`).
- `docs/SDR_SERVICES.md` / `docs/INFRA_MAP.md:108-110` — describe
  `acars-watcher`/`acarshub` as present/active, which they are; neither
  claims rows are flowing. `acars_messages` = 0 is the new smoke script's
  `KNOWN-FAIL`, already recorded there; no doc claims it works.
- New fixtures/test file: 9 fixtures = one per source seen (AH, CL, HF,
  HP, HX, HZ, OH, RH, TH), no `FH` — matches the doc's Finding 4 and the
  current capture dir.

## Pre-existing, still open, not caused by this commit (one line each)

- `CLAUDE.md:17` / `README.md:14` "145 loaded units" → 115 today (flagged
  08-12/13/14/15; both files already say don't hardcode it).
- `CLAUDE.md:165` / `README.md:475` "V31 exists as of this snapshot" →
  `SCHEMA_V33` (flagged 08-12).
- `corporatetraveldc-runner-demo.service` still crash-looping —
  **`NRestarts=36728`** (8549 on 08-15), same startup traceback,
  `:8005` refused, `https://dispatch-runner.example.com/healthz`
  → **502**. `CLAUDE.md:99-100` still presents the :8005 demo as public
  and live. Flagged 08-15; not in the 08-16 check; still broken.

## Bottom line

Three doc claims are invalidated by this commit and are one-line fixes each
(`INFRA_MAP.md:175-178` "and `dispatch-mcp`"; `src/ingest/README.md:94`
add `HF`/`RH`; `SECOND_BRAIN_STATUS.md:289` gap closed + `HT`→`HF`). The
substantive finding is item 1: the commit records MCP as "fully retired"
while both mcpo bridges and the public `mcp.` vhost are still live and
serving — and are now unrestartable because their `ExecStart` wrapper path
was renamed away. Decide (a) finish retiring or (b) un-retire, then the
README/CLAUDE/INFRA_MAP/REFERENCE_INFRA MCP sections follow from that
decision. Also: the manifest re-sign is uncommitted, and the FDPS `HF`/`RH`
fix is built but not yet running (ingest-fdps container predates it).

---

# Addendum — 13:10–13:20 EDT, after `4eb3d2c` / `40e3eec` / `3fcc1f5`

Three commits landed on `main` after the check above: `4eb3d2c` (merge of
`smoke-test-harness-2026-08-17` — tree is **byte-identical** to `be4cb62`,
`git diff be4cb62 4eb3d2c` is empty; the branch was already on top of
`171f7e4`, so the merge brought nothing new), `40e3eec` (commits the
re-signed `MANIFEST.sha256`/`.asc`), and `3fcc1f5` (commits the check
above). Same question, same rules: does anything the docs now claim —
including what the 12:20 check itself claims — no longer match the live
box? Nothing staged, committed, or changed live. `main` is `ahead 4` of
`origin/main` (un-pushed); the merged branch ref is gone locally.

## Items from the 12:20 check that have since closed (verified live)

- **Manifest is now committed and matches HEAD.** `git diff HEAD --
  MANIFEST.sha256` empty; `scripts/verify-manifest.sh` → `OK, 667 files`.
  The "HEAD's committed manifest does not match HEAD's own tree" concern
  is resolved by `40e3eec`.
- **FDPS `HF`/`RH` fix is now live.** `systemd-corporatetraveldc-ingest-fdps`
  was restarted at **12:24:27 EDT** onto image `dbbeb8fc2676` (the 12:20:20
  build); `grep` inside the running container finds `"HF", "RH"` at
  `fdps_parser.py:155`.
- **Failed units 19 → 0.** `systemctl --user --failed` is empty; every
  stale manifest-race failure cleared on its next trigger, and the two
  non-manifest ones (`boot-stagger`, `docs-drift-weekly`) were reset too.
  115 `corporatetraveldc-*` units loaded (unchanged); **31** containers up
  (30 at 12:25 — the difference is ingest-fdps back after its restart
  window).
- **`scripts/smoke-test-platform.sh` → `SMOKE-TEST: PASS (0 failing
  categories)`** (was FAIL/1 at 12:25, FAIL/2 when the doc was written).
  Manifest OK, all 4 endpoints 200, ACARS still `KNOWN-FAIL` (0 rows,
  carve-out as designed).
- Test suite unchanged: **17 failed, 114 passed** — same set as before
  (11 watchlist schema-chain, 5 `_dispatch_proxy_headers`, 1
  `test_smes_parser_basic`); the merge didn't touch tests.

## Drift introduced by `3fcc1f5` itself (the 12:20 check is now the stale doc)

The committed check is a correct point-in-time record, but four of its
"Live snapshot" bullets are already false and a reader tomorrow will
re-diagnose them: the manifest-uncommitted paragraph, the "FDPS `HF`/`RH`
fix is NOT yet live" bullet, "Failed units 20 → 19", and the smoke-test
FAIL line. All resolved as listed above; this addendum is the annotation.
Nothing else in that file has changed status:

- **Drift items 1–5 are all still open, none touched** (working tree was
  clean at HEAD): `weekly-doc-drift-check.sh:3-5` still says "covering
  BOTH repos"; `docs/INFRA_MAP.md:175-178` still says "against both this
  repo and `dispatch-mcp`"; `src/ingest/README.md:94` still lists sources
  without `HF`/`RH`; `docs/SECOND_BRAIN_STATUS.md:289` still says
  `HT`/`RH` are skipped; `docs/SMOKE_TEST_HARNESS_2026-08-17.md:3,71,263-265`
  still says "un-pushed, uncommitted", "2 failing categories", "19
  changed/new files, uncommitted" — now merged to `main` (still un-pushed),
  smoke test PASS.
- **MCP half-retirement (item 1) unchanged:** both `mcpo` units still
  `active running` since 08-14 08:41 with `ExecStart` pointing at the
  now-nonexistent `/home/corporatetraveldc/mcp/dispatch-mcp/…wrapper.sh`
  (only `dispatch-mcp.archived-20260817/` exists); `127.0.0.1:8083/openapi.json`
  → 200; `https://mcp.example.com/openapi.json` → 200. Still
  unrestartable, still contradicting "MCP is fully retired".

## New live finding (adjacent to this commit, not caused by it)

- **A third unhandled FDPS source type, `DH`, is now visible in the live
  capture.** `fdps_debug_fixm30/` is the first 25 messages after each
  ingest-fdps start, so the 12:24 restart produced a fresh batch (written
  12:24–12:25). Composition: AH 4, HX 4, HZ 4, OH 3, TH 3, CL 2, HP 2, HF 1,
  RH 1, **DH 1** (`sample_20.xml`, 12:25). `DH` is not in
  `_KNOWN_SOURCES_FIXM30` (`fdps_parser.py:143-158`) and is dropped at
  `:503` with only a `log.debug` — the same silent-skip path `HF`/`RH` were
  on until this morning. The sample is a **departure message**
  (`centre="ZOB"`, `fdpsFlightStatus="ACTIVE"`, `departure/…/actual
  time=…`, `arrival/…/estimated`, gufi, `aircraftIdentification`,
  `icaoModelIdentifier`) — i.e. an actual-off event, which is precisely
  what the OOOI watchlist phases want. `DH` appears nowhere in `src/ingest/`,
  `docs/`, or `tests/ingest/` (no fixture). Not a doc-drift item — no doc
  claims `DH` is handled — but the commit's own claim of "real-sample test
  coverage" and any fix to `src/ingest/README.md:94` should account for it:
  add `DH` to the allowlist (generic path handles it, as with `HF`/`RH`),
  capture `sample_20.xml` as a tenth fixture, and extend the source list in
  the README in one go rather than reopening this next week. Frequency in
  this batch is 1/25; earlier batches (08-16 22:07, 11:36 today) had none,
  so it's uncommon but real.

## Pre-existing, unchanged

- `runner-demo`: still crash-looping, **same** `sqlite3.OperationalError:
  unable to open database file` at `_chat_db_init` (`/app/main.py:1620`,
  `CHAT_DB_PATH`), exit 3, ~8 s cycle. `NRestarts` reads **351** now vs
  36728 at 12:25 only because the unit was **manually restarted at
  12:24:50–12:25:12** (counter reset to 1 at 12:25:19); the underlying
  failure is untouched. `:8005` refused, public `/healthz` → 502.
  `CLAUDE.md:99-100` still presents it as public and live.
- `CLAUDE.md:17`/`README.md:14` "145 loaded units" → 115; `CLAUDE.md:165`/
  `README.md:475` "V31" → `SCHEMA_V33`. Both flagged since 08-12.

## Bottom line (addendum)

The three post-`be4cb62` commits introduced no new doc drift of their own —
they *closed* four operational items the 12:20 check flagged (manifest
committed, `HF`/`RH` live, 0 failed units, smoke test PASS). What's still
open is unchanged: the five one-line doc fixes and the MCP retire/un-retire
decision from the 12:20 check, plus the runner-demo loop. One new thing to
fold into the `HF`/`RH` follow-up: `DH` (departure) messages are being
silently dropped by the same allowlist.

---

# Addendum 2 — 14:49–14:52 EDT, after `b3a914b` (EP-advance code-generation leak fix)

`b3a914b` (14:48:21 EDT, **on branch `fable-timing-artifact-sweep-2026-08-17`,
not `main`** — `main` is still `3fcc1f5`; `main..HEAD` is exactly this one
commit) — "EP-advance: fix code-generation hallucination leak (persona rule
+ content-sanity guard)". 23 files: all **21** repo-root `corporatetraveldc.*`
Modelfiles get one new line in the shared "Rules for every brief" block
("Never write code, pseudocode, or programming instructions …");
`src/poller/skills/ep_advance_brief.py` gets `import re` and a guard in
`_call_ollama()` (`:1000-1002`) that discards any narrative containing a
code fence or a line starting `import `/`def `/`class ` and returns `None`
so the deterministic fallback runs instead; and the 13:10 addendum above.
Same question, same rules: does anything the docs now claim no longer match
the live box? Nothing staged, committed, or changed live. The operator was
deploying concurrently again — poller image rebuilt at **14:48:54 EDT**
(33 s after the commit) and manifest re-signed at 14:48:35.

## What this commit could invalidate — checked

- **`src/poller/skills/ep_advance_brief.py` guard is deployed.**
  `localhost/corporatetraveldc-poller:latest` (`5b44e57364e6`, created
  14:48:54) contains the `code-shaped output` guard and the new Modelfile
  text; `corporatetraveldc-ep-advance.container` runs that image via
  `scripts/verified-exec.sh` (`Exec=` line), so the **first run with the
  guard is 15:30 EDT**. The 14:30 run (finished 14:43:48, "brief generated
  via Ollama") predates it. `brief-fallback-monitor` at 14:50:02 → "healthy".
- **The Modelfile persona rule is NOT live in any Ollama model.**
  `ollama list`: all 21 `corporatetraveldc-pi5-*` models are 19–46 h old
  (`ep-advance:latest` 19 h, `ops-brief`/`chat`/`ep-advance-trend` 46 h);
  `ollama show <model> --modelfile | grep -c "Never write code, pseudocode"`
  → **0** for all four checked. `build-models.sh` has not been run since the
  commit (no build process running at 14:50). Until it is, only half the fix
  is in effect — the Python guard catches the leak after the fact and pushes
  a deterministic brief; the prompt-side prevention does nothing. Not doc
  drift (README/CLAUDE correctly say Modelfile → `build-models.sh` → model),
  but the commit message implies both halves and the box has one.
- **Guard coverage is narrower than the persona rule.** The Modelfile line
  is in all 21 models; the Python guard is only in `ep_advance_brief.py`
  `_call_ollama()`. The 12h trend path (`_generate_trend_narrative_ep`,
  `:1140`, returns `resp.json()["response"].strip()` unfiltered), `ops_brief.py`,
  and every other brief/watch skill have no equivalent (`grep -l 'code-shaped'
  src/poller/skills/` → one file). No doc claims otherwise, so not drift —
  recorded so the "defense in depth" reading isn't over-applied.
- **`scripts/brief-fallback-monitor.sh:65` alert text** still says a
  fallback streak "is the gemma3-SWA / Ollama-timeout failure class". After
  this commit a fallback can also be the guard discarding code-shaped output
  (the skill logs `brief generated (deterministic fallback)` either way, and
  the monitor keys on that string at `:47-48`). Message accuracy, not doc
  drift; the WARNING line at `ep_advance_brief.py:1001` in the same journal
  disambiguates.
- **No test covers the guard** — `grep -rl ep_advance tests/` → nothing.
  Not a doc claim.
- **Manifest race, third time today.** HEAD's committed `MANIFEST.sha256`
  (667 lines) does not match HEAD's tree — it lacks the new hashes for
  `ep_advance_brief.py` and all 21 Modelfiles (`git show HEAD:MANIFEST.sha256
  | grep ep_advance_brief` → old hash `e352a0bb…`; tree is `2357c0e7…`).
  The re-signed manifest (668 lines, matches, `verify-manifest.sh` → **OK,
  668 files**) is again an **uncommitted working-tree edit** (` M
  MANIFEST.sha256`, ` M MANIFEST.sha256.asc`, mtime 14:48:35). The 13:10
  addendum's "Manifest is now committed and matches HEAD" is therefore true
  of `3fcc1f5`/`main` but **false of this branch's HEAD**. Same consequence
  as at 12:20: a clean checkout of `b3a914b` fails every `verified-exec.sh`
  skill until the re-sign is committed on top.
- **`corporatetraveldc-integrity-sweep.service` is `failed`** (14:47:24 —
  ran in the window after the working-tree edits and before the 14:48:35
  re-sign: "22 computed checksums did NOT match" = 21 Modelfiles +
  `ep_advance_brief.py`). Will clear at its next trigger (15:02:24) since the
  working tree now verifies OK. Same manifest-race pattern as the 19 failures
  in the 12:20 check.

## Docs — nothing in this commit's surface area drifted

Grepped README.md, CLAUDE.md, docs/, `src/ingest/README.md`,
`src/shared/watchlist_README.md` for anything this commit could contradict:

- No doc reproduces the "Rules for every brief" persona text (`grep -rl
  'Never invent data that'` outside the Modelfiles → nothing), so no doc
  quotes a now-incomplete rule list.
- No doc describes ep-advance's Ollama-output handling beyond "falls back
  to deterministic templates when Ollama is unavailable" (`CLAUDE.md:157-160`,
  `README.md:519-524`, `docs/ALERT_REFERENCE.md:42`). That's still true; the
  guard adds a second, undocumented reason for a fallback (garbage output),
  which is a gap rather than a contradiction. `docs/ALERT_REFERENCE.md:97,207`
  (topic/priority for `ep`/`ep-advance`) unchanged and still correct.
- `llm.py`'s manifest gate (`_verify_before_inference`, verifies the calling
  skill file and the model's Modelfile against the signed manifest) —
  documented in `docs/COMPLIANCE_SECURITY.md` and README — behaves as
  documented: both changed files are in the (working-tree) manifest, so the
  15:30 run will pass the gate.

## Items from the 13:10 addendum — status now

- Manifest committed & matching HEAD → **regressed** for this branch (above).
- Failed units 0 → **2**: `integrity-sweep` (manifest race, above) and
  `second-brain-rss` (`Failed with result 'timeout'` at 14:16:32 after
  6 m 30 s wall — image `build-date=20260817T162012Z`; unrelated to this
  commit, not investigated further here).
- Smoke test PASS → **`SMOKE-TEST: FAIL (1 failing category)`** at 14:51 —
  category 1 (the two failed units); manifest OK, all 4 endpoints 200, ACARS
  still `KNOWN-FAIL`. Should return to PASS once integrity-sweep re-runs at
  15:02, leaving only second-brain-rss.
- 31 containers → **36** (`podman ps`; the extra are in-flight skill
  containers on the fresh image). 115 `corporatetraveldc-*` units, unchanged.
- runner-demo: still looping — `ActiveState=deactivating`, `NRestarts=1138`
  (was 351 at 13:10; ~8 s cycle), `:8005/healthz` → connection refused.
  Unchanged, still contradicting `CLAUDE.md:99-100`.
- MCP half-retirement, `HF`/`RH` README lines, `DH` allowlist gap, "145
  units"/"V31" — untouched by this commit, all still open exactly as listed
  above.
- **Pre-existing, already flagged 08-14/08-16, restated because this commit
  touched every Modelfile:** `CLAUDE.md:147-151` / `README.md:54,492,528`
  still say "16 dedicated models … 4 brief-class on `phi3:mini`, everything
  else `gemma3:4b` … builds all 16". Live and repo: **21** Modelfiles, **21**
  `corporatetraveldc-pi5-*` models, **all 21 `FROM phi3:mini`** (one shared
  base blob in `ollama show`; `gemma3:4b` not present). This commit's 21-file
  Modelfile touch is the clearest evidence yet that the "16 / gemma3" story is
  stale — the one-paragraph fix in those two files is still outstanding.

## Bottom line (addendum 2)

`b3a914b` introduced **no new documentation drift** — nothing in README,
CLAUDE.md, docs/, or the two sub-READMEs describes the ep-advance output
path or quotes the persona block, so there was nothing for it to invalidate.
Operationally the fix is **half-deployed**: the Python guard is in the
poller image and will run at 15:30, but the persona rule is in zero live
Ollama models until `build-models.sh` runs; the manifest re-sign is once
again uncommitted (HEAD's manifest ≠ HEAD's tree); the commit sits on a side
branch, not `main`; and integrity-sweep is transiently `failed` from the
sign race. Everything else open from the 12:20 check and 13:10 addendum is
unchanged.

---

# Addendum 3 — 21:05–21:15 EDT, after `675b0c2` (ops-brief prose fixes + Fable timing/artifact sweep)

`675b0c2` (21:03:03 EDT, branch `fable-timing-artifact-sweep-2026-08-17`;
`main..HEAD` is exactly this one commit — note `main` has moved to
`b3a914b` since addendum 2 said "main is still 3fcc1f5"; `main` is 5 ahead
of `origin/main`, un-pushed) — 25 files: `src/common/llm.py`
(`sanitize_llm_response()` + wiring in `_ollama()`), `ops_brief.py`
(NWS/Amtrak local-DB fallbacks, `max_tokens` 500→900, `OLLAMA_TIMEOUT`
1200→2000, AAM summary fed into the prompt via new
`aam_watch.get_aam_watch_summary()`, raw METAR/NAS appendix and the
post-hoc `=== ADVANCED AIR MOBILITY WATCH ===` block dropped from the
pushed/archived brief), `ep_advance_brief.py` (guard wired at both direct
call sites, `num_predict` 750→1000, timeout 2220→2800), `weekly_summary.py`
400→700/990→1530, `second_brain_weekly.py` 500→700/2340→2940, four
Modelfiles' `num_predict` in parity + a new venue-scope rule in
`corporatetraveldc.ep-advance`, a 22nd repo-root
`corporatetraveldc.ops-brief.PROPOSED-enrichment-2026-08-17` (proposal, not
a model), 4 Quadlets' `TimeoutStartSec` (ops-brief 2600→3600, ep-advance
3600→4500, disruption-weather-digest 1700→3000, second-brain-rss 300→900),
all 6 daily-watch timers (`OnCalendar` anchor + `OnUnitActiveSec=90min` →
two-line fixed 90-min calendar grid), `docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md`
(new), and addendum 2 above. Same question, same rules. Nothing staged,
committed, or changed live by me. The operator deployed concurrently again
— everything below is as observed 21:05–21:15.

## Live snapshot — what of this commit is actually in effect

- **Timers: live and staggered.** All 6 `~/.config/systemd/user/…-daily-watch.timer`
  are byte-identical to the repo (mtime 21:04:06), `daemon-reload` at
  21:04:06–07, all six timers restarted 21:04:30, `NeedDaemonReload=no`.
  `list-timers`: aviation 21:15, gig 21:30, concierge 21:45, EP 22:15, aam
  22:30 — the 15-min stagger is back. **One-time transition artifact:** the
  reload immediately *started* `aam-daily-watch` and
  `executive-protection-daily-watch` at 21:04:07 (systemd computes a
  calendar timer's next elapse from `LastTrigger`; both had a new grid slot
  between their last lockstep fire and 21:04, so it was "overdue"), and
  trains-yachts re-triggered (no-op, already running) at 21:04:30. Result
  right now: **all six watch services are `activating` at once** (aviation/
  concierge/gig from the 20:46:37 lockstep, aam/EP from 21:04:07,
  trains-yachts from **19:16:37**), plus the 21:00 ops-brief — load 6.2,
  ops-brief model at 100 % CPU. This is the old lockstep's last gasp, not a
  bug in the grid; trains-yachts hit its 7 000 s ceiling at **21:13:17**
  (it had already lost two slot waits at 20:06/20:20): "start operation
  timed out. Terminating" → stop-sigterm timed out → SIGABRT 21:14:03,
  `Result=timeout` — **the second trains-yachts kill today, today's vault
  note lost again** (the sweep doc §5(a) predicted "the lockstep recurs
  tonight"; it did, once, before the grid took over). Next grid slot 22:00.
- **Container ceilings: live.** The 4 changed `.container` files are
  identical to the repo; effective `TimeoutStartUSec` = ops-brief **1h**,
  ep-advance **1h 15min**, disruption-weather-digest **50min**,
  second-brain-rss **15min**; `NeedDaemonReload=no`. The ops-brief instance
  that started 21:00:01 predates the reload — it is on the **old** image
  `5b44e57364e6` (14:48) — so the **first ops-brief run with the new code
  (900-token cap, NWS/Amtrak DB fallback, AAM woven in, no raw appendix) is
  22:00**, and the first ep-advance run with `sanitize_llm_response()` is
  **21:30**. Archived briefs 1607/1609/1611 (18:23/19:17/20:17 EDT) still
  carry `=== ` sections + the AAM block, as expected pre-deploy.
- **Images: rebuilt and live.** poller `2eaf83fda734` / web `27ab4a345492`
  / pusher `1468b440cd94`, build-date `20260818T010324Z` (21:03:24–21:04:06
  EDT); web/poller/pusher restarted 21:04:29–30. `grep` inside
  `poller:latest`: `def sanitize_llm_response` present, `ops_brief.py`
  `OLLAMA_TIMEOUT = 2000` / `max_tokens=900`. The 21:04:08 aam/EP watch
  containers are already on it.
- **Ollama models: NOT rebuilt (again).** `ollama list`: all 21 still 25 h
  (`ep-advance`) to 2 d old; `ollama show --parameters` → `num_predict`
  **500 / 750 / 400 / 500** for ops-brief / ep-advance / weekly-summary /
  secondbrain-weekly (the pre-commit values); the 08-17 "never write code"
  persona rule and the new ep-advance venue-scope rule are in **zero** live
  models; no `build-models.sh` running. **Practical effect is smaller than
  it looks:** both `_ollama()` (`llm.py:1289`) and `ep_advance_brief.py`
  pass `num_predict` in the request `options`, which overrides the
  Modelfile `PARAMETER`, so the raised caps ARE in effect via the image;
  the Modelfile edits are parity only. What is genuinely not live is the
  prompt-side content: the persona rule (since 14:48) and the ep-advance
  venue-scope rule (this commit). The sweep doc's §0 GUARD-0 blocker was
  gone between the 21:03:20 re-sign and ~21:10 (manifest verified OK, 670
  files) — a rebuild was unblocked and simply not run; this addendum's own
  edit re-blocks it until the next re-sign (see "Failed units" below).
  `build-models.sh` resolves models from an explicit
  name→suffix map (`:52`), so the `.PROPOSED-enrichment-…` file cannot be
  picked up by accident.
- **Manifest race, fourth time today.** HEAD's committed `MANIFEST.sha256`
  is the 668-line one from `b3a914b` — it lacks entries for the two new
  files (`corporatetraveldc.ops-brief.PROPOSED-enrichment-2026-08-17`,
  `docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md`) and carries the OLD
  hashes for the 44 files this commit changed (`git show
  HEAD:MANIFEST.sha256 | grep llm.py` → `df3ee4c9…`; tree is `75a646fd…`).
  The re-signed 670-line manifest (mtime **21:03:20**, `verify-manifest.sh`
  → **OK, 670 files**) is once more an **uncommitted working-tree edit**
  (` M MANIFEST.sha256`, ` M MANIFEST.sha256.asc`). Same consequence as
  before: a clean checkout of `675b0c2` fails every `verified-exec.sh`
  skill until the re-sign is committed on top.
- **Failed units: 2.** `integrity-sweep` (20:57:20, "21 computed checksums
  did NOT match" — ran in the window after the sweep's edits and before
  the 21:03:20 re-sign) and `disruption-weather-digest` (the 15:57:28
  SIGKILL at the old 1 700 s ceiling that this commit's Quadlet comment
  describes; stays `failed` until its 04:35 run or a manual reset — the
  new 3 000 s ceiling is loaded for that run). `smoke-test-platform.sh` at
  21:06 → **FAIL (1 failing category)** = those two units; manifest OK, all
  4 endpoints 200, ACARS `KNOWN-FAIL`. 115 `corporatetraveldc-*` units;
  **38** containers (`podman ps`; 7 are in-flight skill containers).
  **Disclosure — this check itself is now the integrity-sweep blocker:**
  the working tree verified OK (670 files) at 21:05, but this addendum is
  an edit to a manifested file, so the 21:12:20 sweep re-run failed on
  exactly one file — `docs/LIVE_STATE_CHECK_2026-08-17.md: FAILED` — and
  will keep failing (with its ops-health alert) every 15 min until the
  operator re-signs. Same mechanism the sweep doc §0 describes for the
  14:48 parallel edit; unavoidable for a doc-drift check that writes into
  the repo, and it also re-blocks `build-models.sh` GUARD-0 until the
  re-sign. It does **not** block any skill: `verified-exec.sh:35-37` scopes
  its check to `src/`, the two verify scripts and the signing key, so
  `docs/` edits never stop a container from running.
- New DB helpers the ops-brief fallbacks depend on exist: `db.py:1079
  get_active_nws_alerts()`, `:1118 get_latest_amtrak_status()`.

## Drift found

1. **`docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md` — committed by this
   commit and already stale against the box it describes.** Its header
   says "all changes staged/uncommitted" (now `675b0c2`); §0 says a model
   rebuild "is STILL blocked … GUARD-0 … fails on `docs/LIVE_STATE_CHECK_2026-08-17.md`"
   (manifest OK, 670 files — unblocked, just not run); §5(a) says the timer
   fix is "staged (NOT applied live … Until applied, the lockstep recurs
   tonight)" (applied live 21:04); §6 says everything is "staged,
   uncommitted, NOT live — needs operator sign + image/model rebuild" (now
   committed, signed, image-live; models not); §7's deploy steps 1, 2 and 4
   are done, **step 3 (`build-models.sh`) is not**, step 5 (re-run and
   check `brief_archive`) is pending on the 21:30/22:00 runs. Also **two
   placeholders were committed unfilled**: `<!-- ABC_RESULTS -->` (§3 — the
   A/B/C demo the section promises has no results) and
   `<!-- SCORECARD_ADDENDUM -->` (§4 — seven scorecard rows say "see
   addendum"/"see §4a addendum" for aam-weekly, aviation 2nd run,
   dispatch-desk-memo, executive-protection 2nd run, route-impact,
   tfr-enrichment, secondbrain-weekly, weekly-summary; the addendum does not
   exist in the committed file). Fix: fill or delete the two placeholders,
   and add a one-paragraph "status as of `675b0c2`" note at the top rather
   than rewriting the tense throughout.
2. **`docs/INFRA_MAP.md:164`** — "daily category watches 07:30–08:45
   (+ 15:30/15:45 PM runs)". Stale since 08-16 (`OnUnitActiveSec=90min`
   made them ~16×/day) and now definitively wrong: each of the six is on a
   fixed 90-min 24 h grid (16 slots/day; anchors 07:30 aam / 07:45 aviation
   / 08:00 gig / 08:15 concierge / 08:30 trains-yachts / 08:45 EP; a slot is
   skipped if the previous run is still active). No timer on the box
   fires at 15:30/15:45. Not flagged in any earlier check. One-line fix.
3. **Addendum 2 above** now has one stale line of its own: "`main` is
   still `3fcc1f5`" — `main` is `b3a914b`. Annotated here, not edited.
4. **`docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:17-18`** ("`OLLAMA_TIMEOUT`
   240 s … 600 s container `TimeoutStartSec`") — already flagged 08-15/16;
   this commit moves the real numbers further still (ops-brief 2000+510 s
   under a 3600 s ceiling, ep-advance 2800+540 s under 4500 s). Restated
   because the gap is now 15×, not 4×.
5. **Adjacent, outside the named doc set:** the module docstring in
   `src/common/aam_watch.py:14-16` and `src/poller/skills/aam_weekly_watch.py:24-27`
   say the hourly briefs "append this text as a raw post-synthesis appendix
   — it does NOT pass back through ops_brief.py's or ep_advance_brief.py's
   own Ollama call". After this commit that is false for ops-brief (the
   condensed summary is fed *into* its prompt) and still true only for
   ep-advance (`ep_advance_brief.py:1296` still appends `get_aam_watch_section("ep")`).
   The split-framing rationale ("ops" vs "ep" caches) still holds for both.

## Still accurate (checked because this commit could have touched them)

- `CLAUDE.md:138` "`src/common/llm.py` is the single entry point
  (`generate()` → `ollama_post_with_retry()`)" — still true; the new
  response guard sits inside `_ollama()` so every `generate()` caller gets
  it, and the two ep-advance direct-`ollama_post_with_retry()` sites are
  wired explicitly (CLAUDE.md never claimed those go through `generate()`).
  Cloud-fallback and deterministic-template statements (`CLAUDE.md:157-160`,
  `README.md:519-524`) unchanged and still correct — the guard adds "garbage
  output" as another reason for the deterministic fallback, same gap noted
  in addendum 2, still not a contradiction.
- `docs/PHASE4_VALIDATION_2026-08-16.md` §1 "num_predict matching each call
  site's max_tokens exactly (all 21 pairs)" — still true of the **repo**
  (the four pairs moved together: 900/900, 1000/1000, 700/700, 700/700);
  §3 "every `TimeoutStartSec` ≥ its computed worst case" — still true (four
  ceilings raised, each with a re-derived budget comment). Its specific
  numbers (2600/3600/300) are a dated record, not a live claim. Its "All 21
  present at repo root" is now 22 `corporatetraveldc.*` files (one is the
  proposal, not a model) — cosmetic.
- `docs/ALERT_REFERENCE.md:94,97,196-207` (topics/priorities for
  `ops-brief`, `ep`, `ep-advance`) — unchanged, still correct; the content
  of the ops-brief push changes (no raw appendix, no AAM block), the topic/
  priority don't.
- `docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md:159` cites `llm.py:258
  sanitize_prompt_text()` — the function is at `:199` in both `HEAD~1` and
  `HEAD`; the new code was inserted *after* it, so this commit did not move
  it (already stale, not caused here).
- `src/ingest/README.md`, `src/shared/watchlist_README.md` — nothing in
  this commit touches ingest or watchlist; unchanged, and the open `HF`/`RH`
  + `DH` items stand as listed in the 12:20 check / 13:10 addendum.
- Sweep doc §5(f) template-drift claim verified true: `config/dispatch.env:77`
  `OLLAMA_OSINT_MODEL=corporatetraveldc-pi5-osint`, `:87 OLLAMA_TIMEOUT=240`;
  live `/etc/corporatetraveldc/dispatch.env:73` `…-osint-monitor`, `:118
  OLLAMA_TIMEOUT=3600`.

## Pre-existing, unchanged

- MCP half-retirement: both `mcpo` units `active running`,
  `127.0.0.1:8083/openapi.json` → 200. runner-demo: `NRestarts=3977`,
  `:8005/healthz` → connection refused. `CLAUDE.md:17`/`README.md:14` "145
  units" → 115; `CLAUDE.md:165`/`README.md:475` "V31" → `SCHEMA_V33`;
  `CLAUDE.md:147-151`/`README.md:54,492,528` "16 models / gemma3:4b" → 21,
  all `phi3:mini`; `weekly-doc-drift-check.sh:3-5` / `INFRA_MAP.md:175-178`
  "both repos"; `src/ingest/README.md:94` sources list; `SMOKE_TEST_HARNESS`
  "uncommitted" wording; `brief-fallback-monitor.sh:65` alert text. All
  exactly as in the earlier sections.

## Bottom line (addendum 3)

`675b0c2` is fully deployed on the code/unit side (images 21:03–21:04,
core restarted 21:04:29, all 4 Quadlets and all 6 timers live and
`daemon-reload`ed) and **not at all on the model side** — no
`build-models.sh` run, so the persona rule and the new ep-advance venue
rule are still absent from every live model (the token-cap raises are
nonetheless effective because the request options override the Modelfile).
The one doc it clearly invalidates is the one it ships:
`FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md` describes itself as staged/
un-live/blocked and has two unfilled placeholders. Beyond that, one
genuinely new one-liner (`INFRA_MAP.md:164`, watch cadence), one line in
addendum 2, and the manifest re-sign is uncommitted for the fourth time
today — and, as of 21:10, this file's own edit is the one thing failing
the collective integrity sweep (docs-only; no skill is blocked). The old
lockstep took one last casualty on its way out (trains-yachts killed at
21:13:17, vault note lost). First real evidence of whether the ops-brief
prose fixes work lands at 22:00 (`brief_archive` id > 1612: NWS + Amtrak
present, no mid-sentence tail, no `=== ` blocks) and ep-advance's guard at
21:30. Operator to-do implied by all of the above: commit the re-sign,
run `build-models.sh` for at least ops-brief/ep-advance, fill or drop the
two placeholders in the sweep doc, one-line `INFRA_MAP.md:164`.
