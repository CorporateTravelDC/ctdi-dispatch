# Drift & Gaps Report — 2026-08-15 (~02:10–02:30 EDT)

Independent cold-start audit: live system (`systemctl --user`, `podman`,
`journalctl`, `curl`, live env files) vs. README.md, CLAUDE.md, docs/, and
source at HEAD `be958ac` (2026-08-15 02:03 EDT, working tree clean).
Read-only — nothing was changed, restarted, staged, or committed.

**Headline:** 10 drift items, 6 gaps. The two most urgent are new tonight:
(1) **HEAD fails its own signed-manifest check** — 4 files were committed at
01:43/02:03 without re-signing `MANIFEST.sha256`, so the 15-min integrity
sweep has failed every run since 01:59 and any container (re)build will refuse
to start; (2) the **public demo is still down** (crash loop since 08-14 08:46,
~6,000 restarts in the last 12 h) while README/INFRA_MAP/COMPLIANCE_SECURITY
still say "Live". Much of the rest was correctly pre-flagged by
`docs/LIVE_STATE_CHECK_2026-08-15.md` (written ~01:30–02:00 tonight) and
re-verified here independently; items new since that check are marked **NEW**.

Baseline note: README.md and CLAUDE.md are both self-dated "verified
2026-08-11". Three sizeable commits have landed since (9436f67 model
consolidation 08-14; 2ff1fbb demo isolation + resource re-baseline 08-15;
be958ac naming sweep 08-15) — most drift below traces to those.

---

## Drift (docs claim X, live reality is Y)

### D1 — **NEW** — HEAD fails the signed-manifest integrity check; sweep failing every 15 min
- **Docs/convention say:** CLAUDE.md ("After changing tracked code, re-sign
  with `scripts/sign-manifest.sh` … or rebuilt containers/skills will refuse
  to run. Never bypass this."); README.md §Installation ("code changes require
  re-signing the manifest before rebuilt images will start").
- **Live:** `bash scripts/verify-manifest.sh` against the clean tree at HEAD:
  **INTEGRITY FAILURE — 4 files do not match**: `build-images.sh`,
  `dispatch-secrets.env.template` (changed in `be79c47`, 01:43),
  `docs/INFRA_MAP.md`, `src/ctdc_token/cli.py` (changed in `be958ac`, 02:03).
  Neither commit touched `MANIFEST.sha256` (the last manifest re-sign was
  `b0a5c86`/`2ff1fbb` at 01:19–01:20, before those file edits).
  `corporatetraveldc-integrity-sweep.service` has failed on exactly these 4
  files at 01:59 and 02:14 (journal) and will keep failing every 15 min.
- **Consequence:** any image rebuild or fresh container start that runs
  `scripts/verified-exec.sh` will refuse to run until the manifest is
  re-signed with the operator GPG key. Currently-running containers predate
  the commits and are unaffected.
- **Checked:** `scripts/verify-manifest.sh` output; `git log --stat` of
  be958ac/be79c47/b0a5c86/2ff1fbb; `journalctl --user -u
  corporatetraveldc-integrity-sweep`.

### D2 — Public demo documented "Live"; it is down (crash loop, day 2)
- **Docs say:** README.md:49 ("Public demo … **Live** at
  `https://dispatch-runner.example.com`"), README.md:100,
  README.md §Demo Mode ("The demo-playback stack is **live and public**"),
  `docs/INFRA_MAP.md` demo row, `docs/COMPLIANCE_SECURITY.md` §2.
- **Live:** `corporatetraveldc-runner-demo.service` in
  `activating/auto-restart` continuously since 2026-08-14 08:46 EDT — every
  start dies in `_chat_db_init` (`src/runner/main.py:1608`) with
  `sqlite3.OperationalError: unable to open database file` (`CHAT_DB_PATH`
  under `STATE_DIR=/var/lib/corporatetraveldc`, which no longer exists in the
  container after `2ff1fbb` removed the live-dir mount; nothing creates it).
  ~6,000 restart cycles in the last 12 h. `curl http://127.0.0.1:8005/` →
  connection refused; `https://dispatch-runner.example.com/` →
  **502**.
- Also part of the same demo cutover mess:
  - `demo-api` (:8004) runs the **2026-08-10 image** (pre-rewrite
    `demo_api.py`): `GET :8004/healthz` →
    `{"ok":false,"demo_db":"/var/lib/corporatetraveldc/demo.db","loop_days":14}`
    — old code, old path, path no longer mounted. INFRA_MAP /
    COMPLIANCE_SECURITY describe the intended (demo-source.db) state, not the
    running one. Note `build-images.sh` has **no demo target**, so there is
    no documented rebuild path for this image.
  - `corporatetraveldc-demo-source-refresh.timer` — INFRA_MAP,
    COMPLIANCE_SECURITY, and PENTEST_CLEARANCE_CHECK_2026-08-13 say nightly
    04:45 ET; live it is `disabled` / `inactive`, never run.
  - README.md:49/101 still say the demo replays **`demo.db`**; the code
    cutover (`2ff1fbb`) moved demo-api's source to `demo-source.db`.
- **Checked:** `systemctl --user list-units`, `journalctl --user -u
  corporatetraveldc-runner-demo`, `podman ps`/`podman images`, curl (8004,
  8005, public URL), `systemctl --user is-enabled`.

### D3 — "16 dedicated Ollama models" narrative is one consolidation behind
- **Docs say:** README.md:54 & §Local LLM ("16 models exist", table of
  4 phi3 brief models + 12 gemma3 models, "`build-models.sh` … builds all
  16"); CLAUDE.md §Local LLM ("16 dedicated models … 4 brief-class …
  everything else is `gemma3:4b`"); `docs/DEDICATED_MODELS_PLAN.md`.
- **Live:** commit `9436f67` (2026-08-14) consolidated to **2 models**.
  `build-models.sh` `MODELS=( [corporatetraveldc-pi5-chat]="chat"
  [corporatetraveldc-pi5-brief]="brief" )`; **both** Modelfiles are
  `FROM phi3:mini` (so "the rest on gemma3:4b" is also false). `ollama list`
  shows 13 `corporatetraveldc-pi5-*` models: the 2 real ones plus 11
  orphaned gemma3-era per-task models (route-impact, secondbrain-weekly,
  osint-monitor, aam-watch, dispatch-desk, transport-digest, tfr-enrichment,
  secondbrain-daily, disruption-weather-digest, weekly-summary, osint) built
  2026-08-12, no longer referenced by `build-models.sh`.
- **Checked:** `build-models.sh:55–83`, `head corporatetraveldc.chat
  corporatetraveldc.brief`, `ollama list`, git log 9436f67.

### D4 — CLAUDE.md's ANTHROPIC_FALLBACK claim is inverted
- **Doc says:** CLAUDE.md §Local LLM: "`ANTHROPIC_FALLBACK_ENABLED`
  **defaults true** (it is NOT set to false in dispatch.env, despite an old
  llm.py docstring claim)". README §Local LLM repeats "defaults `true`".
- **Live:** `/etc/corporatetraveldc/dispatch.env:200` is
  `ANTHROPIC_FALLBACK_ENABLED=false` (with a comment block above it
  explaining the template default was overridden). The doc's parenthetical
  asserts the opposite of the live file.
- **Checked:** `grep -n ANTHROPIC_FALLBACK_ENABLED /etc/corporatetraveldc/dispatch.env`.

### D5 — OLLAMA_TIMEOUT documented as 240 s; live is 3600 s
- **Docs say:** CLAUDE.md §Container resource limits (`OLLAMA_TIMEOUT=240`);
  `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md` (240 s in two places); README §Local
  LLM narrative ("blowing the 240 s `OLLAMA_TIMEOUT`" — historical context,
  but presented as the current knob).
- **Live:** `/etc/corporatetraveldc/dispatch.env:118` → `OLLAMA_TIMEOUT=3600`
  (raised 240 → 1200 → 3600 across 08-13/08-14 per env comments and prior
  live-state checks). `src/common/llm.py`'s docstring was already fixed
  ("currently 3600s") in `2ff1fbb`.
- **Checked:** live dispatch.env; CLAUDE.md:83; SINGLE_EDGE_UNIT_ASSUMPTIONS.md.

### D6 — Prewarm timers documented; all 28 are retired/masked
- **Docs say:** README.md ("`corporatetraveldc-ollama-prewarm-*` timers
  pre-load each skill's model 2–3 minutes before its scheduled run");
  `docs/INFRA_MAP.md` ("with prewarm timers ~2–3 min ahead").
- **Live:** all 28 prewarm units masked (`/dev/null` symlinks); repo copies
  moved to `.config/systemd/user/retired-20260814/` in `2ff1fbb`. No prewarm
  timer appears in `systemctl --user list-timers`. `scripts/ollama-prewarm.sh`
  remains in-tree, orphaned. (Consistent with the 2-model world — per-skill
  prewarm is meaningless now.)
- **Checked:** `list-timers --all`, git log 2ff1fbb diffstat, scripts/ listing.

### D7 — `ctdc-token` presented as a command; no such binary exists
- **Docs say:** CLAUDE.md ("CLI name: ctdc-token"), README §Auth model
  ("Tokens are created with **`ctdc-token`**"), `src/ctdc_token/cli.py`
  docstring examples, INFRA_MAP open-item text.
- **Live:** `which ctdc-token` → not found (nor `csex-token`, which
  `be79c47` correctly purged). The real invocation — which both files do
  also show — is `PYTHONPATH=src python3 src/ctdc_token/cli.py …`. Cosmetic,
  same family as the just-landed csex-token cleanup; already noted in
  tonight's live-state check, still true post-be958ac.
- **Checked:** `which`, cli.py, README/CLAUDE.md text.

### D8 — NWS alerts marked "✅ Active (push-primary via NWWS-OI)" while the DB shows a flagged silent-failure pattern
- **Doc says:** README feed table: NWS alerts ✅ Active, NWWS-OI push ✅ Live.
- **Live:** `feed-db-integrity-check` has warned on every run for hours:
  "`push:nws: feed_state claims healthy (fetched 0.5min ago, no error), but
  nws_alerts is 1511min stale -- silent-failure pattern`" (~25 h stale). The
  fresh heartbeat also suppresses the REST fallback poll per the failover
  design, so if the XMPP session is dead-but-heartbeating, no path is
  refreshing alerts. Could in principle be a genuinely quiet 25 h, but the
  platform's own integrity check is calling it a mismatch — status is not
  cleanly "✅" right now.
- **Checked:** `journalctl --user` (14× WARNING in last 12 h), README:123.

### D9 — Unit-count snapshots stale (self-caveated, minor)
- **Docs say:** README.md:14 / CLAUDE.md:17 "145 loaded units at this
  snapshot" (2026-08-11).
- **Live:** 117 `corporatetraveldc-*` units loaded (28 prewarm units gone).
  Both files explicitly say not to trust the number and give the command, so
  this is minor — but the snapshot number no longer matches.
- **Checked:** `systemctl --user list-units 'corporatetraveldc-*' --all | wc -l`.

### D10 — CLAUDE.md/README describe SWIM ingest as continuously live; two feeds are load-shed right now (by design, but undocumented behavior in those files)
- **Docs say:** README status table "All 6 live"; nothing in README/CLAUDE.md
  mentions that the thermal/load guard deliberately stops feeds.
- **Live:** `ingest-stdds` and `ingest-tfms` are `inactive` —
  `thermal-ingest-guard` is at tier 1 (load1 ~10–13 since ~01:05 EDT;
  tier-1 sheds tfms+stdds) and will auto-resume on load drop. Not a fault,
  but a reader of README would not know 2 of 6 SWIM feeds can be
  intentionally down; only the guard's own script/journal documents it.
- **Checked:** `systemctl --user is-active`, thermal-ingest-guard journal.

Verified-no-drift spot checks (for completeness): web `:8000/healthz` ok
(GREEN/GO, snapshot age 3 s); runner `:8001/healthz` 200 (tailnet-only story
intact); public mcpo on :8083 serves exactly **26** OpenAPI paths as README
claims; schema top version V31 matches docs; `scripts/ingest-feed-ctl.sh`,
`sign-manifest.sh`, `verified-exec.sh`, `verify-manifest.sh` all exist as
documented; watchlist JSON layout as described; 26-tool public/full mcpo
split units both running; key paths table accurate.

---

## Gaps (real and live, but undocumented)

### G1 — Timer-driven LLM skills are systematically dying on `TimeoutStartSec`, post-raise — no doc, and the 2ff1fbb fix didn't fix it
`2ff1fbb` raised skill LLM timeouts to 900–1200 s specifically to stop
timeout deaths, but the oneshot units still carry `TimeoutStartSec=950s`
(verified live: `aam-daily-watch` shows `TimeoutStartUSec=15min 50s`,
`Result=timeout`). With 900 s × `max_retries=2` a slow Ollama run guarantees
a unit SIGKILL. The first post-commit runs confirm it: **failed with
`Result=timeout` in the last 24 h**: `aam-daily-watch` (15:46 EDT PM run —
after the raise), `gig-economy-daily-watch`, `second-brain-daily`,
`weekly-summary`, `transport-pattern-digest`, `pull-path-verify`. Several
wrote `status=fallback` output before dying. No doc describes the
TimeoutStartSec/LLM-budget interaction; the in-code comments cite
`RuntimeMaxSec` (infinity), which is not the limit that bites. (Pre-flagged
as an open question in tonight's live-state check; now answered — the raised
budgets did not help.)

### G2 — `scripts/ollama-wedged-detector.sh` committed but unwired and undocumented
Landed in `be958ac` ("D-state/CPU-tick hang detector, built for the
OLLAMA_TIMEOUT-raised observation window"). No systemd unit references it
(grep of `~/.config/systemd/user/` — nothing), no doc mentions it outside
the commit message. Either wire+document it or note it as a manual tool.

### G3 — `sdr-crashloop-guard` timer live and firing every 5 min; zero doc coverage
New in `2ff1fbb`, enabled and running (last fired 02:10). Mentioned only in
`docs/LIVE_STATE_CHECK_2026-08-15.md`; absent from INFRA_MAP's timer
highlights, README's auxiliary/SDR sections, and `docs/SDR_SERVICES.md`.

### G4 — Live-env tuning values exist only in the untracked env file
`OLLAMA_TIMEOUT=3600` (D5) and `OLLAMA_READY_TIMEOUT_S=10` (raised from the
code default 3.0 on 08-15) live only in `/etc/corporatetraveldc/dispatch.env`,
which is not repo-tracked and has no template counterpart — no repo artifact
records the current values, and docs actively contradict one of them.

### G5 — Orphaned artifacts from the model consolidation
11 stale `corporatetraveldc-pi5-*` gemma3 models (~2.2 GB each) still on the
Ollama server (D3), plus `scripts/ollama-prewarm.sh` orphaned in-tree (D6).
No doc or plan records whether these are pending deletion or kept
deliberately; `docs/DEDICATED_MODELS_PLAN.md` still describes the 16-model
design with no addendum for the 08-14 consolidation.

### G6 — Boot-order failure noise has no runbook status
`corporatetraveldc-boot-stagger.service` (failed 08-14 08:48, fdps job
canceled — raced the ingest-restart timer) and
`corporatetraveldc-second-brain-demo-archiver-daily` (failed 08-14 08:41,
Nextcloud not yet up post-reboot) remain in `failed` state from the 08-14
boot. Both look like startup-ordering noise, but nothing in docs/ describes
expected-benign post-reboot failures, so every audit re-derives this.

---

## Method / evidence trail

- Live: `systemctl --user list-units`/`list-timers --all` (117 units, 48
  timers, 10 failed units at check time), `podman ps -a` (28 containers up),
  `podman images` (demo image 2026-08-10; poller/runner rebuilt 08-15 01:23),
  `journalctl --user` (unit-scoped + 12 h priority-err sweep),
  `curl` 8000/8001/8004/8005/8083 + public demo URL,
  `/etc/corporatetraveldc/dispatch.env`, `ollama list`,
  `bash scripts/verify-manifest.sh`.
- Repo: README.md, CLAUDE.md, docs/ (incl. LIVE_STATE_CHECK_2026-08-12→15,
  DOCS_REFRESH_2026-08-11, INFRA_MAP.md, COMPLIANCE_SECURITY.md,
  SINGLE_EDGE_UNIT_ASSUMPTIONS.md), `build-models.sh`, `build-images.sh`,
  `src/common/db.py`, `src/runner/main.py` (crash site), scripts/.
- Git: `git log` through HEAD `be958ac`. Working tree was clean when this
  audit began; by ~02:15 EDT `docs/LIVE_STATE_CHECK_2026-08-15.md` had gained
  +132 uncommitted lines (a "Third pass — ~02:10 EDT" section) from a
  concurrent session — not written by this audit, and this audit did not
  touch that file. This report is this audit's only write, left untracked.
  Note the concurrent third pass covers `be958ac` itself; findings D1 (the
  unsigned manifest at HEAD) and G1 (post-raise timeout deaths, confirmed by
  the 15:46 aam-daily-watch failure) were derived independently here.
