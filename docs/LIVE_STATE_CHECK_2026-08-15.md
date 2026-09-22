# Live State Check — 2026-08-15

Written ~01:30 EDT, immediately after `2ff1fbb` landed as HEAD (01:19 EDT).
The commit message is empty (`...`), so scope was reconstructed from the diff
(66 files): prompt-cache root-cause + Ollama cgroup re-baseline
(`LLAMA_ARG_CACHE_RAM=0`, MemoryLow/High/Max 3100/5100/6100 → 4850/6050/7250 M);
`production.slice` gains a memory ceiling (High 6656M / Max 7680M) and
`app.slice.d/50-desktop-memory-ceiling.conf` is deleted; all 7 ingest Quadlets
`CPUWeight 100 → 30`; the 28 `ollama-prewarm-*` units moved to
`.config/systemd/user/retired-20260814/`; two new timer pairs
(`sdr-crashloop-guard` every 5 min, `demo-source-refresh` 04:45 ET); demo
isolation Phase 4/5 (demo-api mounts narrowed to `-demo-source:ro` +
`-demo-state`, runner-demo live-dir mount removed, `demo_access.db` moved,
recorder gains a cert-tier token + 4 new endpoints, `scrub-demo-source.py` +
`scrub_rules.py`); llm.py records failed cold loads into the storm guard;
11 skills' LLM timeouts raised 240–800 → 900–1200 s; INFRA_MAP.md and
COMPLIANCE_SECURITY.md updated for the demo cutover. Same rules as the
08-12/13/14 checks: does THIS commit invalidate anything the current docs
claim? Verified against the live system, not prior docs. Nothing here was
staged or committed.

## Live snapshot verified

- Box rebooted 2026-08-14 08:41 EDT (uptime ~16.7 h at check time).
- 117 `corporatetraveldc-*` user units loaded (140 on 08-14; the 28 prewarm
  units are now `masked` → `/dev/null` symlinks live). 28 containers up.
- `ollama.service`: live `MemoryLow=4850M / MemoryHigh=6050M / MemoryMax=7250M /
  MemorySwapMax=0 / CPUWeight=500`, `LLAMA_ARG_CACHE_RAM=0` in its environment;
  `/etc/systemd/system/ollama.service.d/20-resource-limits.conf` is
  byte-identical to the repo copy; restarted 01:06 EDT; the load-time journal
  line now reads `prompt cache is disabled - use --cache-ram N to enable it`
  (the "8192 MiB" line the drop-in says to watch for is gone).
- `production.slice`: live `MemoryHigh=6656M / MemoryMax=7680M`;
  `~/.config/systemd/user/app.slice.d/` no longer exists live.
- All 7 ingest units show `CPUWeight=30` live (checked core, fdps).
- `scripts/verify-manifest.sh` passes clean against HEAD.
- `demo-api` container mounts (podman inspect): `-demo-source` rw=false,
  `-demo-state` rw=true, nothing else. `runner-demo`: **no mounts at all**.
  Recorder (`corporatetraveldc-demo`) still mounts `/var/lib/corporatetraveldc`
  rw as intended.

## Drift found

### 1. The public demo is DOWN, and the docs (including two updated by this commit) say it is live

`corporatetraveldc-runner-demo.service` has been crash-looping since
**2026-08-14 08:46 EDT** — the first boot after the live-dir mount was
removed — restart counter **8245** at check time, every cycle failing at
startup with `sqlite3.OperationalError: unable to open database file` from
`_chat_db_init` (`src/runner/main.py:1608`, `CHAT_DB_PATH` under
`STATE_DIR=/var/lib/corporatetraveldc`). The Quadlet comment this commit
added ("Verified before removing: with no volume mounted, those paths
resolve inside this container's own ephemeral overlay filesystem … not a
functional break") is false: SQLite cannot create a database file inside a
directory that does not exist, and nothing in the image creates
`/var/lib/corporatetraveldc`. Verified from the box: `curl
http://127.0.0.1:8005/` → connection refused;
`https://dispatch-runner.example.com/` → **502**.

Docs invalidated by the live state: `README.md:49` ("**Live** at
dispatch-runner…"), `README.md:100`, `docs/INFRA_MAP.md:96` (row rewritten
by this commit, still says public/password-gated), the new
`docs/COMPLIANCE_SECURITY.md` §2 row. Fix is code (create the dir, or
`Environment=STATE_DIR=` to a writable path, or a tiny `-demo-state`-style
mount) — flagged, not applied, since it changes a public service.

### 2. `demo-api` is running the pre-cutover image; the "reads the sovereign file" claim isn't live yet

`localhost/corporatetraveldc-demo:latest` was built **2026-08-10**;
`src/demo/demo_api.py` was rewritten in this commit. Live
`GET :8004/healthz` → `{"ok":false,"demo_db":"/var/lib/corporatetraveldc/demo.db"}`
and `GET :8004/api/v1/tfr` → **500** — old code opening the old path, which
the new (correct, verified) narrow mounts no longer expose. So the
isolation boundary is real, but the service behind it is non-functional
until `bash build-images.sh demo` + restart. `docs/INFRA_MAP.md:98` and
`docs/COMPLIANCE_SECURITY.md:35–46` describe the intended state, not the
running one. Same stale image serves the recorder, so `recorder.py`'s new
token + `knowledge_graph_meta/osint_feed/board/board_threads` capture is
not live either (the token IS in the container env via the uncommitted
`corporatetraveldc-demo.container` EnvironmentFile edit already applied
live — see observation 4).

### 3. `corporatetraveldc-demo-source-refresh.timer` is disabled and has never run

`docs/INFRA_MAP.md:100`, `docs/COMPLIANCE_SECURITY.md:50` and
`docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md:298` all say it runs nightly
04:45 ET. Live: unit files installed, timer `disabled`/`inactive (dead)`,
service journal empty. `/var/lib/corporatetraveldc-demo-source/demo-source.db`
exists (1.88 GB, mtime 08-14 10:21) — a one-off manual promotion. By
contrast the sibling `sdr-crashloop-guard.timer` from the same commit is
enabled and firing every 5 min. Either enable it (`systemctl --user enable
--now`) or soften the docs to "installed, not yet enabled".

### 4. Prewarm timers are gone; two docs still describe them

`README.md:532` ("`corporatetraveldc-ollama-prewarm-*` timers pre-load each
skill's model 2–3 minutes before its scheduled run") and
`docs/INFRA_MAP.md:163` ("with prewarm timers ~2–3 min ahead") are stale —
all 28 units are masked live and moved to `retired-20260814/` in the repo
(consistent with the one-shared-model world; a per-skill prewarm no longer
means anything). `scripts/ollama-prewarm.sh` still exists in the tree,
now orphaned. INFRA_MAP's timer highlights also don't yet list the two new
timers (`sdr-crashloop-guard` every 5 min; `demo-source-refresh` 04:45 —
subject to item 3). `CLAUDE.md:17`'s "145 loaded units at this snapshot"
is now 117 (the file already says don't hardcode it).

### 5. README stack table still says the demo replays `demo.db`

`README.md:49` ("replays `demo.db` via the demo API") and `README.md:101`
("read-only playback API (port 8004) over `demo.db`") predate the cutover;
demo-api's source is now `demo-source.db`. `README.md:342` (recorder writes
`demo.db`) and `docs/SECOND_BRAIN_STATUS.md:278` (demo-archiver reads
`demo.db` directly) remain correct — the recorder side didn't move.

### 6. `production.slice`'s budget math went stale inside the same commit

The new comment sizes the slice "as a matched pair with ollama.service's
existing hard cap (MemoryMax=6100M …) 6100 + 7680 = 13780 MiB = 85.0% …
~2.4 GiB headroom" — but the same commit raised ollama's `MemoryMax` to
7250M. Live sum is 7250 + 7680 = 14930 MiB = **92%** of 16211 MiB, ~1.25 GiB
headroom for kernel/page cache/app.slice. Not a docs/ file, but it is the
recorded design rationale and it's wrong at birth; the ollama drop-in's
own "still leaves ~8.3GB … for the rest of the stack" line likewise
doesn't account for the new slice ceiling.

### 7. Still open from the 08-13/08-14 checks (this commit didn't touch CLAUDE.md or README.md)

- 16-dedicated-model narrative: `CLAUDE.md:147–155`, `README.md:54`,
  `README.md:~488–512` ("builds all 16"), `docs/DEDICATED_MODELS_PLAN.md`,
  `docs/lmstudio-dispatch-prompts.md`. Live: `pi5-brief` + `pi5-chat`, plus
  the same 11 orphaned gemma3 per-task models still on the server.
- `CLAUDE.md:156–158` ANTHROPIC_FALLBACK parenthetical inverted — live
  `dispatch.env:200` is `ANTHROPIC_FALLBACK_ENABLED=false`.
- `OLLAMA_TIMEOUT`: `CLAUDE.md:83` and `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:17,25`
  still say 240 s. Live value moved again — **3600** (was 1200 on 08-14).
  This commit did fix llm.py's own docstring ("currently 3600s").
- `README.md:55` "ACARS/VDL2 chain, feeders up throughout": now true again
  live (acarsrouter/dumpvdl2/acarshub/ultrafeeder up ~16 h, fr24feed/
  planefinder up ~17 h since the reboot), but "throughout" was false
  08-11→08-14. `docs/INFRA_MAP.md:101` "(9 running, 1 down)" — 9 running
  matches.

## Checked, no drift

- **Ollama re-baseline** — no doc quotes the old 3100/5100/6100 values;
  `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md` only carries the nextcloud row.
  CLAUDE.md's `_abandon_ollama_generation`/thermal-gate/slot-lock bullets
  remain accurate. Live == repo (see snapshot).
- **Ingest `CPUWeight=30`** — `CLAUDE.md:90–92` states `CPUWeight=100` for
  the *core* containers and says ingest is "sized individually … see each
  Quadlet", so nothing there is contradicted; `dispatch-runner-design.md:148`'s
  "CPUWeight 100" is the runner. `src/ingest/README.md` never mentions
  weights. No drift.
- **`app.slice.d` removal / production.slice ceiling** — no doc under
  docs/ or README/CLAUDE.md ever cited the desktop ceiling or "no ceiling
  on production.slice"; only the slice file's own comment (item 6).
- **`demo_access.db` → `-demo-state/`** — only `PENTEST_CLEARANCE_CHECK_2026-08-13.md`
  names the old path, historically. `profiles.py` creates the dir; live
  file present.
- **llm.py failed-cold-load recording, per-skill timeout raises** — no doc
  enumerates storm-guard internals or per-skill timeout values.
- **COMPLIANCE_SECURITY §2 / INFRA_MAP demo rows** — content matches the
  Quadlets and `scrub-demo-source.py` as designed; the only false claims are
  the live-state ones in items 1–3.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — nothing in
  this commit touches their subject matter.

## Live observations (not doc drift)

1. **`ingest-stdds` / `ingest-tfms` are stopped** — deliberate:
   `thermal-ingest-guard` tripped tier 1 at 01:05:31 EDT (load1 13.38 > 10.0;
   tier-1 feeds = tfms,stdds), still tier 1 at 01:19 (load 8.7). Auto-resume
   when load drops. This is the load pattern the CPUWeight=30 change targets.
2. **Skill LLM timeouts now exceed the units' `TimeoutStartSec`.** The daily
   watches went 240 → 900 s with `max_retries=2`; `second_brain_weekly` and
   `dispatch_desk_memo` → 1200 s. The oneshot units still have
   `TimeoutStartSec=950 s` live (`aam-daily-watch`, `second-brain-weekly`,
   `dispatch-desk-memo` all `15min 50s`). The in-code comments cite
   "`RuntimeMaxSec` (infinity)" but the kill that already bites is
   `TimeoutStartSec` (08-14 observation 3). Currently `failed/timeout`:
   aam-daily-watch, gig-economy-daily-watch, second-brain-daily,
   ep-advance; the first post-commit daily runs (~07:30 EDT) will show
   whether the raised budgets help or just get SIGKILLed later.
3. **`integrity-sweep` failed at 01:14 EDT** on 13 skill files — the
   pre-commit edit window (commit re-signed at 01:19). Manual
   `verify-manifest.sh` passes now; the 01:29 sweep should clear it.
4. **Working tree already carries uncommitted, unrelated edits** (not
   touched by this check): `corporatetraveldc-demo.container` (+secrets
   EnvironmentFile — already applied live in `~/.config`),
   `corporatetraveldc.brief`, `corporatetraveldc.chat`,
   `install/ollama/install-ollama.sh`, `scripts/scrub-public-tree.py`;
   untracked `scripts/ollama-wedged-detector.sh` and — notably —
   `docs/LIVE_STATE_CHECK_2026-08-14.md` itself, which was never committed.
5. `boot-stagger` (08-14 08:48, fdps job canceled) and
   `second-brain-demo-archiver-daily` (08-14 08:41, Nextcloud 502) failures
   are post-reboot startup-order noise, unrelated to this commit.

---

# Second pass — ~02:00 EDT, after `be79c47` (01:43) and the deploy

Off-cycle weekly drift check, run early because of tonight's changes.
Covers what landed after the 01:26 pass above: commit `be79c47`, the
image rebuild + poller restart, the live env edits, and the uncommitted
naming sweep. Everything verified against the live box. Nothing was
staged, committed, or fixed in place — findings only.

## Verified, matches docs / no drift

- **`be79c47` (`csex-token` → real invocation)** — `build-images.sh:66`
  and `dispatch-secrets.env.template:199–207` now say
  `PYTHONPATH=src python3 src/ctdc_token/cli.py …`. The live
  `/etc/corporatetraveldc/dispatch-secrets.env` carries **zero**
  `csex`/`CSEX` references — the same fix was applied live. This closes
  `DOCS_REFRESH_2026-08-11.md` open item 6 and INFRA_MAP open item 7
  (item 7's text is already updated in the working tree, uncommitted).
- **Deploy is real** — six images rebuilt ~01:23 (web, poller, pusher,
  ingest, amtrak-tracker, runner); `corporatetraveldc-poller` restarted
  01:36:50 EDT onto the new build, active, and its journal since restart
  has **zero** integrity/`IntegrityCheckFailed` lines.
- **Ollama re-baseline still live** — system `ollama.service` active,
  `MemoryHigh=6050M / MemoryMax=7250M` (6343884800 / 7602176000 bytes),
  unchanged from the 01:26 snapshot.
- **`OLLAMA_READY_TIMEOUT_S` 3 → 10** — live `dispatch.env:176` says 10.
  No doc, README, CLAUDE.md, or template anywhere quotes a value for this
  knob, so no doc drift; note the raise lives **only** in the untracked
  live env (`src/common/llm.py:319` default remains `3.0`, and there is
  no `dispatch.env.template` to carry it).
- **`csexec-contact.container` — PERMANENT, by operator decision
  (2026-08-15).** Explicitly confirmed tonight: the ProtonMail-Bridge
  relay / website contact-form API keeps the `csexec-contact` name
  permanently. Grepped README.md, CLAUDE.md, and all of docs/: **no doc
  currently frames it as pending-rename/TODO**, so nothing needed
  correcting — recording the decision here so future naming sweeps and
  drift checks do NOT flag it. It is intentional, not incomplete cleanup.

## Drift / open items found this pass

1. **The broader `csex`/`csexec` naming sweep is UNCOMMITTED.** It exists
   only as working-tree edits: `src/ctdc_token/cli.py:61`
   (`CSEX_DISPATCH_TOKEN` → `DISPATCH_TOKEN` in the printed Cowork hint)
   and `docs/INFRA_MAP.md:381–383` (open item 7 marked fixed). Anything
   describing the sweep as a landed commit is wrong until it lands — the
   only committed piece is `be79c47`'s two files. (The same dirty tree
   also carries unrelated edits: `install/ollama/install-ollama.sh`
   OLLAMA_HOST default fix, `scripts/scrub-public-tree.py` allowlist
   additions, `corporatetraveldc.brief`/`.chat` num_thread=3
   test-and-revert writeups, `corporatetraveldc-demo.container`
   +secrets EnvironmentFile, untracked `ollama-wedged-detector.sh` and
   the 08-14/08-15 check files themselves.)
2. **`ctdc-token` is not actually a command either.** INFRA_MAP open
   item 7 — both the committed text and tonight's working-tree edit —
   says the real CLI is "`ctdc-token`", and `src/ctdc_token/cli.py`'s own
   docstring (lines 8, 213) uses `ctdc-token …` examples. No such binary
   exists on PATH; the real invocation is
   `PYTHONPATH=src python3 src/ctdc_token/cli.py` (which `be79c47`
   correctly used). Cosmetic, same family as the csex-token cleanup.
3. **First-pass items 1–3 are all still true at 02:00** — the deploy did
   not touch the demo stack:
   - `runner-demo` still crash-looping: `NRestarts=8549` and climbing,
     same `sqlite3.OperationalError`, `:8005` connection-refused.
   - demo image still the 2026-08-10 build; `:8004/healthz` still
     `{"ok":false,"demo_db":"/var/lib/corporatetraveldc/demo.db"}`.
     Correction to the first pass's suggested fix: **`build-images.sh`
     has no demo target at all** (it builds web/poller/pusher/ingest/
     amtrak-tracker/runner only), so "`bash build-images.sh demo`" is
     not a real command — the demo image needs its own build path.
   - `demo-source-refresh.timer` still `disabled`/`inactive`.
4. **Only the poller runs the new images.** web, pusher, runner, ingest
   (all 7), and amtrak-tracker containers have been up ~17 h — started
   before the 01:23 rebuild — so they still run pre-`2ff1fbb`-era builds.
   Their Quadlets say `AutoUpdate=local`, but `podman-auto-update.timer`
   is **disabled**, so nothing will restart them automatically. Fine for
   tonight's change set (llm.py + skill timeouts ship in the poller
   image), but don't read "images rebuilt + deployed" as "whole stack on
   new images" — the rest cut over on their next restart.

Net: tonight's items 1–6 from the work session are all correctly
reflected in docs or have no doc surface; the only doc-text drift newly
found this pass is the cosmetic `ctdc-token` naming (item 2). The
substantive open issues remain the demo-stack breakage carried over from
the first pass, plus the uncommitted sweep awaiting the operator's own
commit.

---

# Third pass — ~23:40 EDT, after `0325a55` (23:36 EDT, "Phase 4" model rebuild)

Same rules: does THIS commit invalidate anything README.md, CLAUDE.md,
docs/, `src/ingest/README.md`, or `src/shared/watchlist_README.md`
currently claim? Verified against the live box, not prior docs. Nothing
staged, committed, or changed live. Commit message is `...`; scope from
the diff (~90 files): 21 per-skill Modelfiles replace `.brief` /
`.dispatch-persona` / the three `.template` files; `build-models.sh`
`MODELS` map back to 21 dedicated models (20 brief-class + `chat`), all
`FROM phi3:mini`; `llm.py` drops the central `DISPATCH_PERSONA` injection;
`poller/main.py` `_OLLAMA_SKILL_TIMEOUT` 950 → 2000; 17 skill Quadlets get
measured `TimeoutStartSec` values (1600–10400 s, was 120/600/950);
`entity_tracking.py` extraction repinned `pi5-chat` → `pi5-osint-monitor`
(90/30 s → 1740/150 s); `runner/main.py` drops `OLLAMA_OSINT_MODEL`, chat
stream read timeout 40 → 110 s; `ollama-wedged-detector.sh` rewritten as a
65/80/110/120 s escalation ladder (feed shed → approval-gated SIGKILL);
`sudo-approval-gate.sh` auto-promotes kill/governor/DR requests to ntfy
priority 5; `scrub-public-tree.py` `DROP_FILES` now drops all 21
Modelfiles; `SUDO_JUSTIFICATION_PROPOSAL.md` updated; three new
validation/drift docs added; manifest re-signed.

## Live snapshot verified (23:37–23:41 EDT)

- **Concurrent deploy observed while checking.** At 23:37 the 17 edited
  Quadlets were copied into `~/.config/containers/systemd/` (mtime 23:37),
  `daemon-reload` ran (generator 23:37:33), and all six images were rebuilt
  (03:36–03:37 UTC) with the poller restarted at 23:37:35. Live
  `TimeoutStartUSec` now matches the repo for every unit checked
  (ops-brief 43m20s=2600, transport-pattern-digest 26m40s=1600,
  aam-daily-watch 2h23m20s=8600, weekly-summary 46m40s=2800, ep-advance
  1h=3600); `NeedDaemonReload=no`. All 61 repo `.container` files are
  byte-identical to live. My first cmp pass a minute earlier had caught
  the pre-copy state (live still 950/600/120) — that window is closed.
- **Running poller carries the new code:** inside
  `systemd-corporatetraveldc-poller` — `DISPATCH_PERSONA` count in
  `llm.py` = 0, `_OLLAMA_SKILL_TIMEOUT = 2000`,
  `EXTRACTION_MODEL = "corporatetraveldc-pi5-osint-monitor:latest"`,
  `EXTRACTION_TIMEOUT = 1740`. Poller journal since restart: zero
  integrity/manifest lines. Skill Quadlets use `poller:latest`, so the
  next timer firings run the new build.
- **`scripts/verify-manifest.sh` against HEAD: OK, 649 files.** The
  integrity sweep's 23:30 failure was the pre-commit dirty tree; the next
  15-min run should pass.
- **Ollama (bound to `100.x.x.x:11434`, not loopback):** exactly 21
  `corporatetraveldc-pi5-*` models + the `phi3:mini` base — nothing else.
  Every one of the 21 is `FROM` the same phi3 3.8B blob (`/api/show`
  checked individually). `pi5-brief`, the 11 orphaned gemma3 per-task
  models flagged 08-13/14, and `gemma3:4b` itself are all gone; nothing
  loaded at check time. Repo root has exactly the 21 matching
  `corporatetraveldc.<suffix>` Modelfiles; `.brief`, `.dispatch-persona`,
  `.chat.template`, `.dispatch-persona.template`, `.osint.template` are
  deleted, so `llm.py`'s `_modelfile_relpath_for()` (`pi5-<x>` →
  `corporatetraveldc.<x>`) resolves for all 21.
- web / pusher / runner containers still up since 08-14 08:42–08:45 on
  the pre-rebuild images (same "only the poller cut over" caveat as the
  second pass, item 4). `runner/main.py`'s new 110 s chat read-timeout
  and the `OLLAMA_OSINT_MODEL` removal are therefore **not live** in the
  runner until it restarts.
- `runner-demo`: still `activating/auto-restart`, `NRestarts=18979`, same
  `sqlite3.OperationalError: unable to open database file` at 23:40,
  `:8005` refused. Unchanged since the first pass.
- 117 `corporatetraveldc-*` user units loaded, 28 containers up (same as
  01:30).
- Live `dispatch.env`: `OLLAMA_TIMEOUT=3600`,
  `ANTHROPIC_FALLBACK_ENABLED=false`, `OLLAMA_BASE_URL=http://100.x.x.x:11434`
  (unchanged since the first pass).
- `sudo -n -l` live grants (read-only check): NOPASSWD for `systemctl
  restart/start/stop ollama.service`, `dnf remove/autoremove`, the
  argonone/cpupower/nginx-reload/semanage set, **`systemctl kill
  --signal=SIGKILL ollama.service` (listed twice — duplicate entry)**, and
  **`systemctl stop/start/restart ollama-governor.service`**. Plus the
  operator's own `(ALL) ALL`.
- `ollama-wedged-detector.sh`: no systemd unit anywhere (user or
  system). The only running instance is an ad-hoc `--loop` (no
  `--kill-if-wedged`) started 08-14 16:33 from a shell, piped to `grep
  "WEDGED CONFIRMED"` — a string the rewritten script never emits. Its
  in-memory loop body is still the old 4×60 s logic; the new escalation
  ladder is log-only until something runs the new script with
  `--kill-if-wedged`.

## Drift found (docs claim X, live/repo is Y)

1. **The model story in CLAUDE.md and README.md is now two rebuilds
   stale, and wrong in the opposite direction from before.**
   `CLAUDE.md:147–150` and `README.md:54`, `README.md:488–497`,
   `README.md:529` ("builds all 16"): "16 dedicated models … 4 brief-class
   models FROM phi3:mini; everything else is gemma3:4b". Live/repo: **21**
   dedicated models, **all** `FROM phi3:mini`, one per LLM call site;
   `gemma3:4b` isn't even pulled on the server. The old README table's
   names are also wrong — there is no `osint`, `aam-watch`,
   `dispatch-desk` model; it's `osint-monitor`, seven separate
   `*-daily-watch`/`aam-weekly-watch` models, `dispatch-desk-memo`. The
   08-13 "consolidated to 2" state that the 08-13/08-14/08-15 checks and
   `DRIFT_GAPS_REPORT_2026-08-15.md` D3 flagged is itself now history
   (`PENTEST_CLEARANCE_CHECK_2026-08-13.md:63` records it as current).
   `README.md:498–512` and `CLAUDE.md:151–155` (SWA denylist + smoke-test
   promotion gate) remain accurate — but "brief-class" now means all 20
   non-chat models, not 4, so a full `build-models.sh` run is 20 smoke
   tests (its own header says so). Same for
   `docs/DEDICATED_MODELS_PLAN.md:14–34` status callout ("16 … 24 call
   sites … all other Modelfiles remain gemma3:4b") and
   `docs/lmstudio-dispatch-prompts.md:8–9`.
2. **`docs/SUDO_JUSTIFICATION_PROPOSAL.md:229–230` (text written by this
   commit) says the governor has "no passwordless grant … and none
   should".** Live `sudo -l` shows a NOPASSWD grant for `systemctl
   stop/start/restart ollama-governor.service`. Either the grant should
   go, or the doc's "(a) interactive sudo" branch is wrong — as written
   the doc contradicts the box on the one service it calls a hard gate.
   Related: the doc's sudoers listings (lines 88–108, 317–318) still show
   only the ollama.service + dnf entries; the SIGKILL grant the new
   force-kill path depends on is installed live (twice) but never
   documented as installed, and `docs/GUARDRAILS_JUSTIFICATION.md:167` /
   `docs/DATA_SOURCES.md:720` still describe the governor as
   never-touched (true for thermal-ingest-guard's own behavior, but the
   "no access in any form" posture that backed those lines is now
   softened per this commit).
3. **`docs/ALERT_REFERENCE.md:103` and `:293`** — `approval-gate` fires
   "priority 4" for "the two approval-gated sudo grants (`ollama.service`
   start/stop/restart, `dnf …`)". Now: priority 4 default, **auto-5** when
   the command contains `kill`, touches `ollama-governor`, or
   `APPROVAL_GATE_DR=1`; and the gate has a third caller class (the
   detector's `systemctl kill --signal=SIGKILL ollama.service`). Also a
   gap: `ollama-wedged-detector.sh` now pushes TIER1 (p4) / TIER2 (p5)
   shed alerts to `ops-health` and is absent from the `ops-health` firer
   list at `:95` and the standalone-script section.
4. **`docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:17–18`** — "`OLLAMA_TIMEOUT`
   240 s … bounded by the 600 s container `TimeoutStartSec`" / "Brief
   container `TimeoutStartSec` 600 s". The 240 s figure was already stale
   (live env 3600, ops-brief.py 1200/510, ep-advance 2220); this commit
   moves the container ceilings to 2600 (ops-brief) / 3600 (ep-advance)
   and 1600–10400 across the other skill units, so both cells are wrong.
   The 240 s number is also still quoted at `CLAUDE.md:83` (resource-limits
   callout) and `README.md:501`.
5. **`docs/dispatch-runner-design.md:134`** — env table lists
   `OLLAMA_CHAT_MODEL / OLLAMA_OSINT_MODEL` with default `-osint:latest`.
   `OLLAMA_OSINT_MODEL` was removed from `runner/main.py` in this commit
   and `corporatetraveldc-pi5-osint` no longer exists on the server. (`:34`
   `OLLAMA_CHAT_MODEL` default is still correct.)
6. **`CLAUDE.md:156–158`** ("`ANTHROPIC_FALLBACK_ENABLED` defaults true —
   NOT set to false in dispatch.env") — still inverted vs. live
   `dispatch.env:200` (`=false`); carried over from the 08-13 check,
   untouched by this commit.

## Checked, no drift

- **`_OLLAMA_SKILL_TIMEOUT` 950 → 2000, per-skill Python timeouts,
  `TimeoutStartSec` per unit** — no doc outside the ones named in item 4
  quotes any of the old values (no "950" anywhere in README/CLAUDE/docs).
- **`llm.py` persona removal** — no README/CLAUDE/docs text ever
  described the central `DISPATCH_PERSONA` injection or the
  `.dispatch-persona` file (it lived only in code comments, the
  08-13/14/15 check files, and `scrub-public-tree.py`), so nothing to
  correct there. `CLAUDE.md:141` "signed-manifest verification of the
  calling skill and its Modelfile" is still exactly what
  `_verify_before_inference()` does.
- **`.template` Modelfile deletions / `DROP_FILES` expansion** — no doc
  under docs/, README, INFRA_MAP, or COMPLIANCE_SECURITY promises the
  public mirror carries `corporatetraveldc.chat.template` /
  `.osint.template` / `.dispatch-persona.template`. Only
  `SECOND_BRAIN_STATUS.example.md` mentions `DROP_FILES`, generically.
- **`entity_tracking.py` repin** — no doc names the extraction model or
  its timeouts.
- **`runner/main.py` chat read timeout 110 s** — the code comment claims
  an nginx `proxy_read_timeout=120s` ceiling; no doc states either number.
- **Brief skills still `allow_anthropic=False`, `max_retries=0`
  (`ops_brief.py:679`)** — README/CLAUDE claim holds.
- **`build-models.sh` guards** — SWA denylist and candidate→smoke→promote
  flow unchanged; only the model list changed.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — nothing
  in this commit touches their subject matter.
- **`docs/DRIFT_GAPS_REPORT_2026-08-15.md` D1 (HEAD fails manifest)** —
  resolved by this commit (re-signed; verify OK). D2 (demo down) — still
  true, see snapshot.

## Bottom line

Real doc drift from this commit is concentrated in one place: the LLM
model narrative in **CLAUDE.md** and **README.md** (item 1) is now
plainly false — 21 phi3-only models, not 16 mixed — and should be the
next thing rewritten in both files, along with the `240 s`/`600 s`
timeout cells (item 4). Items 2–3 are smaller but item 2 is a doc this
commit itself wrote that contradicts live sudoers. Everything else the
commit changed either had no doc surface or is already correctly
described. Live deployment of the commit is complete for the poller and
all skill Quadlets; web/pusher/runner still await a restart, and the
wedged-detector ladder is not armed by any unit.
