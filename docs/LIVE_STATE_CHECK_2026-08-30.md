# Live-state / doc-drift check — 2026-08-30 (~06:50–07:05 EDT), post-commits 1f70ce9 / 221d9d2 / 842bcdd / 2977912

Scope: the four commits landed since the 2026-08-29 file's last addendum —
`1f70ce9` (ntfy WebSocket nginx fix), `221d9d2` (run-status ntfy pings for
the 11 daily/weekly digest skills), `842bcdd` (TBFM alert threshold scope
fix), `2977912` (manifest re-sign + drift-report refresh). Second-brain
search ran first: prior art found and built on — `20260823T035147Z` (the
18-hour zero-alerts incident; same "ingest healthy, alerts silent" class
the TBFM fix closes from a different direction), `20260830T035729Z` (last
night's note flagging the ntfy nginx edit as unsigned/uncommitted — its
item 3 is CLOSED by this pass, see below), and `20260826T230133Z`-era note
predicting the weekly-dump failure (confirmed below). Checked README.md,
CLAUDE.md, docs/ living pages, `src/ingest/README.md`,
`src/shared/watchlist_README.md`; verified against `systemctl --user`,
`podman ps/images/inspect/exec/logs`, live nginx conf diff,
`verify-manifest.sh`, and journald. Read-only except this file and two
second-brain notes. Nothing staged, committed, or signed by this pass.

(Search-tool note for future passes: `second-brain-search.sh` phrase-wraps
multi-word queries by default — `tbfm alert threshold metering` returns 0
hits while `tbfm` alone returns plenty. Use single terms or
`--raw 'a AND b'`; an empty result on a multi-word query proves nothing.)

---

## F1 — REAL, deploy gap: the run-status ping fleet (221d9d2) is committed, signed, and documented as live — but deployed nowhere

`docs/ALERT_REFERENCE.md:174` (updated by the commit itself) describes the
dispatch-ops run-status pings in the present tense ("**Added 2026-08-30:**
`dispatch-ops` also now carries…"). Live-verified false as of this pass:

- All 11 digest skills run as per-run containers from
  `localhost/corporatetraveldc-poller:latest` (verified `systemctl --user
  cat` on six of them — `Image=` + `Exec=…verified-exec.sh python3
  src/poller/skills/<skill>.py`, no source mount; only `/var/lib` data
  volume mounted).
- That image's build date is **2026-08-29 06:46 UTC — ~22 h before the
  commit** (2026-08-30 05:06 UTC). `podman exec` into a running skill
  container: **0 occurrences** of `send_run_status` in either
  `common/ntfy_push.py` or the skill file.
- The ingest image WAS rebuilt for the same session's TBFM fix (build
  2026-08-30 ~06:46 UTC, see F3) — a partial deploy: one image rebuilt,
  the other forgotten. Same written-but-not-live class as P2-1
  (acarsrouter port fix in the stale copy) and the research-board-mirror
  timer.
- Concrete cost already incurred: `transport-pattern-digest` failed AGAIN
  at 00:53 EDT today (the chronic P3-2 timeout — 28 min wall, SIGKILL,
  `Result=timeout`) **with no failure ping** — the exact scenario this
  feature was built to surface happened hours after the commit landed and
  went unpinged.

Fix when picked up: rebuild the poller image (`build-images.sh`); per-run
skill containers pick up `:latest` on next timer fire, no unit changes
needed. Not done by this pass (deploy action). Persisted to second brain.

Minor doc drift from the same commit: `README.md:427`'s topic-table row
(`dispatch-debriefs` / `dispatch-ops` — "Full debrief tables / weekly
aggregate | 2–3") wasn't updated alongside ALERT_REFERENCE — once the
pings deploy, dispatch-ops also carries priority-2 success / priority-4
failure run-status pings. The ALERT_REFERENCE row itself was verified
accurate against the source (topics, 2/4 priorities, all-11 skill list,
`journalctl` wording, ops_brief exclusion) — it's just ahead of reality
until the image rebuild.

## F2 — REAL, predicted-now-confirmed: `second-brain-weekly-dump` failed its first-ever fire on session-limit exhaustion

`corporatetraveldc-second-brain-weekly-dump.service` (Sun 02:00, Gate 2
consolidation dump) fired for the first time today and failed in 5 s:
log `/var/lib/corporatetraveldc/second-brain-weekly-dump/2026-08-30.log`
contains exactly "You've hit your session limit · resets 3:10am
(America/New_York)". This is precisely the failure mode the 2026-08-26
drift-check note predicted for this unit ("same headless-claude pattern
[as docs-drift-weekly's 08-24 failure], first fire 2026-08-30").
Consequences: CLAUDE.md/memory did NOT get dumped or wiped this week;
next automatic attempt is next Sunday. The script's own failure-ntfy and
`OnFailure=` both fired, so the operator was notified — but the
docs-drift-weekly precedent (0-for-2 on scheduled fires, only a manual
run ever succeeded) says this cron slot will keep losing races with usage
windows until the 08-26 note's suggested retry-after-reset lands.
`docs/CLAUDE_MD_DRIFT_REPORT.md` (refreshed by 2977912) correctly flags
the unit as failed and absent from CLAUDE.md's known-bad section — the
report and this finding agree. Persisted to second brain (extends the
08-26 prediction note with the confirmation).

## F3 — Verified live end-to-end: the TBFM alert-scope fix (842bcdd) works; first TBFM alerts ever delivered

- The rebuilt ingest image (build 2026-08-30 ~06:46 UTC per container
  labels; `ingest-tbfm` restarted onto it ~01:00 EDT) contains
  `get_active_tbfm_sequence_count` in both `common/db.py` and
  `tbfm_parser.py` (verified `podman exec` grep) — the commit's "deployed
  container is running the fix" claim re-confirmed independently.
- Working as intended: since restart, 1,723 `fire_family_alert` calls
  with real DB-derived queue counts (LIB 22, DC_MET 21, the ZDC fix 58 —
  numbers matching the commit's own live observations), of which **88
  escalations**, several with `aggregate_fired=True`/`zone_fired=True` —
  i.e. actual `tbfm-alerts`/`tbfm-<zone>` pushes reached ntfy, ending the
  permanent suppression. The escalation gate + per-topic throttle are
  absorbing the volume as designed.
- Only the tbfm container runs the new image; the other six ingest
  containers are Up 2 days on the prior image — harmless here (the new
  db.py function is called only from tbfm_parser), but note the fleet is
  now intentionally mixed-image until the next full restart.
- Doc drift from this commit: effectively none. `docs/ALERT_REFERENCE.md:253`'s
  TBFM section ("fires one alert per fix per distinct sequence count",
  dedup on `tbfm:{fix}:{seq_count}`) remains literally accurate — it
  never claimed the count came from the incoming message batch, and the
  dedup-key shape is unchanged. It is now *incomplete* (doesn't mention
  the 5-aircraft floor or that the count is a live 30-min-freshness DB
  query), worth a sentence on the next ALERT_REFERENCE touch, not a
  falsehood. `src/ingest/README.md:139`'s TBFM row ("Live —
  `tbfm_sequences`, metering alerts") is *more* true than it was
  yesterday. No other doc states TBFM threshold semantics (grepped).

## F4 — Closes prior finding: ntfy WebSocket fix (1f70ce9) committed + signed; the build-models.sh gate block is resolved

Vault note `20260830T035729Z` item 3 flagged the nginx ntfy conf edit as
deployed-but-unsigned, blocking `build-models.sh` at exit 5. Now:
tracked `nginx/conf.d/ntfy.example.com.conf` is committed,
byte-identical to `/etc/nginx/conf.d/`'s live copy, nginx active, and
`verify-manifest.sh` reports **OK — signature valid, all 800 files
match** (post-2977912). The gate block self-resolved exactly as that
note predicted. Doc drift from this commit: none found — no living doc
describes the vhost's Connection-header/WebSocket internals (checked
README, INFRA_MAP, REFERENCE_INFRA, ALERT_ARCHITECTURE,
GUARDRAILS_JUSTIFICATION, SINGLE_EDGE_UNIT_ASSUMPTIONS;
ALERT_REFERENCE:753's "ntfy SSE" line is about the ops-dashboard SSE
bridge, unaffected).

## F5 — CLAUDE.md known-bad reconciliation (post-re-sign)

The nine units CLAUDE.md lists as "expected/self-resolving
verify-manifest INTEGRITY FAILURE" have all cleared from
`systemctl --user --failed` — the re-sign resolved them exactly as
documented. Current failed set is exactly three: `docs-drift-weekly`
(known 08-24 item, unchanged), `transport-pattern-digest` (chronic P3-2
timeout, still NOT the self-resolving class — see F1 for today's
instance), and `second-brain-weekly-dump` (new, F2). CLAUDE.md's
ultrafeeder hardware note still matches live (container now crash-cycling
Up-7h on the restart treadmill; dongle still absent — no change).
`src/shared/watchlist_README.md`: zero claims touched by any of the four
commits (grepped); no new drift beyond what Pass 3 already logged.

---

Working-tree note: this pass staged/committed nothing. The staged
`ollama-keepwarm` → `retired-20260830/` renames and the unstaged
`src/common/llm.py` edit appeared mid-pass from a concurrent session
(they were not present at this pass's start) — another session's
in-flight work, deliberately untouched and not assessed here.

**Persisted to second brain:** F1 and F2 (the real, non-trivial items)
via `remember_text()` (author_kind=agent), including the closure of
`20260830T035729Z` item 3 so future searches see the gate unblocked.
F3/F4 verifications and the minor doc notes are repo-state, tracked here
only, per the established convention.

---
---

# Pass 2 — post-87c4a57 (Ollama dead-code gut), ~07:35–07:55 EDT

Scope: commit `87c4a57` ("Gut all remaining Ollama-shaped code from
common/llm.py") and its companion working-tree edits — exactly the
in-flight work Pass 1's closing note deliberately left unassessed.
Second-brain search ran first; this pass builds directly on
`20260830T112502Z` (the mid-session checkpoint written at 07:25 EDT,
four minutes before this commit, describing the gut in full detail) and
`20260830T030328Z` (yesterday's post-cutover check, which already logged
the README/ingest-README Ollama-era drift wholesale — not re-derived
here). Verified against: `git show/status/diff`, `verify-manifest.sh`,
real `import` of the gutted modules, `systemctl --user` (unit files,
failed set, timers), system `systemctl`, `podman image inspect`/
`podman inspect`, live `/etc/nginx/conf.d/`, `journalctl`, and a direct
`curl` to the dead Ollama port. Read-only except this file and one
second-brain note. Nothing staged, committed, or signed by this pass
(hard rule).

## G1 — REAL: 87c4a57 is a partial commit — its message describes 14 code files that are NOT in it

The commit message (and CLAUDE.md, and the `20260830T112502Z`
checkpoint) describe deleting the readiness-wait subsystem from
`common/llm.py`, gutting `common/ollama_lock.py`, and fixing two
would-hard-crash callers across 14 touched files. **The commit contains
only the 3 `ollama-keepwarm` → `retired-20260830/` renames** (`git show
--stat`: 3 files, 0 insertions, 0 deletions). All the described code
edits exist solely as uncommitted working-tree modifications: 14 `src/`
files + CLAUDE.md + the re-signed `MANIFEST.sha256`/`.asc` (17 modified
files in `git status`). The checkpoint's own closing item — "then commit
(git add of the 3 renames + 14 modified files) and push" — was executed
halfway: the renames were added, the modifications never were.

What this does and doesn't affect:
- **Live operations: unaffected.** `verify-manifest.sh` reports OK —
  signature valid, all 800 files match — so the re-sign DID cover the
  working tree and the skill fleet's integrity gates pass. Confirmed the
  gut itself is genuinely clean in the working tree: `import common.llm`
  / `import common.ollama_lock` both succeed, and every remaining
  reference to the removed symbols in `src/` is a comment or docstring
  (grepped — zero live call sites).
- **Git history: now misleading.** Anyone reading `git log` sees a
  commit claiming ~430 lines of deletions it doesn't contain, and
  `git show 87c4a57` contradicts its own message. Worse, the only copy
  of the gut work is unprotected — a `git checkout .`/`reset --hard`/
  `stash drop` would silently destroy it, and HEAD's committed
  MANIFEST no longer matches HEAD's committed file contents (the signed
  pair in git covers the *working-tree* versions).
- Fix when picked up: a follow-up commit of the 17 modified files
  (message noting it carries the content 87c4a57 claimed), or amend if
  history allows. Not done by this pass — committing/staging is
  explicitly out of bounds. **Persisted to second brain.**

## G2 — Deployment status of the gut: not live yet, same image rebuild F1 already needs

`localhost/corporatetraveldc-poller:latest` build date is still
2026-08-29 06:46 UTC — ~19 h before the gut. Per-run skill containers
use the baked image with no source mount (established in Pass 1 F1), so
neither the gutted `llm.py` nor the two hard-crash fixes are live.
**Harmless as-is**: the old image is internally consistent (old
`llm.py` + old callers), so the "would have hard-crashed on next run"
window only opens if the sources ever mix — which the image model
prevents. The single `build-images.sh` rebuild that Pass 1 F1 (run-status
pings) already calls for will ship this too; nothing extra needed.

## G3 — Doc drift from this commit specifically: small, mostly extending known drift

- `README.md:704` describes `_abandon_ollama_generation()` in the
  present tense ("`src/common/llm.py` now sends…"). The function no
  longer exists. This is one more falsehood inside the README Local-LLM
  section already flagged wholesale by `20260830T030328Z` — same known
  drift, now one notch deeper; fold into the eventual README rewrite
  rather than patching piecemeal.
- `docs/TAILNET_MIGRATION_INVENTORY.md:155` cites
  `scripts/ollama-keepwarm.sh:33` as a live tailnet-IP binding — path
  now `scripts/retired-20260830/ollama-keepwarm.sh` and the binding is
  moot (script retired, port dead). Stale inventory row, minor.
- Everything else that names keepwarm or the removed symbols
  (`GUARDRAILS_JUSTIFICATION.md`, `HARDWARE_GUIDANCE.md`,
  `THERMAL_BASELINE_2026-07-26.md`, dated `LIVE_STATE_CHECK_*`/
  `PHASE4_*`/`DEDICATED_MODELS_PLAN.md`) is historical narrative in
  dated snapshot docs — accurate as history, left alone per convention.
- `docs/INFRA_MAP.md`'s thermal-samples item is unaffected: that CSV is
  produced by `thermal-sample.sh` (a different script/unit that merely
  writes under the `ollama-keepwarm/` state dir), not by the retired
  keepwarm.
- `src/ingest/README.md` / `src/shared/watchlist_README.md`: zero
  references to anything this commit touched (grepped); the ingest
  README's LOCKDOWN-row Ollama drift is prior-known (`20260830T030328Z`),
  unchanged.

## G4 — Inventory: Ollama-shaped infrastructure that SURVIVED the gut (so no future pass assumes the purge is complete)

CLAUDE.md words the directive's execution as scoped to `common/llm.py`,
so nothing there is false — but the directive's stated intent ("so
something can't get confused and eat up the clock") is not fully
achieved. Still tracked and/or live:

- `corporatetraveldc-ollama-swap-alert.service` — tracked at
  `.config/systemd/user/` AND live-installed AND **still enabled**
  (stopped 2026-08-27 18:35 EDT, cutover day, but never disabled).
  `Restart=always`, monitors the now-nonexistent `ollama.service`
  cgroup; on the next user-manager restart/reboot it comes back and
  polls a dead unit every 30 s forever. Exactly the confusion class the
  directive targeted.
- `nginx/conf.d/ollama.example.com.conf` — tracked AND live
  in `/etc/nginx/conf.d/`, proxying to `100.x.x.x:11434`, where
  nothing listens (direct `curl`: connection failure). A public vhost
  serving guaranteed 502s.
- `openwebui.service` — **active and running** with
  `OLLAMA_BASE_URL=http://100.x.x.x:11434` (live container env,
  matching the tracked Quadlet): its entire backend is dead. If the
  operator still wants a browser chat UI it needs re-pointing at
  llama-chat (8094, OpenAI-compatible endpoint) or retiring.
- Tracked but disabled/inert (lower priority): `ollama-governor.service`
  + `ollama_governor.sh` + `config/ollama-governor.env`,
  `ollama-wedged-detector.sh`, `ollama-prewarm.sh`,
  `restore-network.sh`'s OLLAMA_HOST rewrite,
  `systemd/ollama.service.d/20-resource-limits.conf` (already flagged
  by `20260830T030328Z` as the orphaned home of the memory-limit
  lessons), `nginx` vhost's tracked copy, and several timer
  `Description=` strings still reasoning about "Ollama jobs don't
  stack" (cosmetic; the anti-stacking schedule itself is still valid
  for llama.cpp).

## G5 — Failed-unit reconciliation vs Pass 1

Current failed set is Pass 1's three (docs-drift-weekly,
transport-pattern-digest, second-brain-weekly-dump — all unchanged)
plus `corporatetraveldc-integrity-sweep.service`, which failed at
07:24:41 EDT on `verify-manifest: INTEGRITY FAILURE` — i.e. it fired
inside the few-minute window between this session's source edits and
the re-sign completing. Manifest now verifies OK and the sweep runs
every 15 min, so this is the documented expected/self-resolving class
(same as CLAUDE.md's 08-28 and 08-29 instances); it should already be
clear by the time this file is read.

---

**Persisted to second brain (this pass):** G1, with the G4 survivor
inventory folded in as its secondary payload, via `remember_text()`
(author_kind=agent). G2/G3/G5 are repo-state or prior-known, tracked
here only.

---
---

# Pass 3 — post-3dafed8 / 75752fa (gut code actually committed + daily llama-hot/chat preventive restart), ~08:10–08:20 EDT

Scope: `3dafed8` (the 14 code files + CLAUDE.md that Pass 2 G1 found
missing from `87c4a57` — now committed) and `75752fa` (new
`scripts/scheduled-llama-restart.sh` +
`corporatetraveldc-llama-restart.service/.timer`). Second-brain search
ran first (`--raw 'llama AND restart'`, `'llama AND swap'`, `keepwarm`;
`--semantic` has no concept for this yet): builds on `20260830T112502Z`
(the checkpoint that defined the open 0.21 tok/s investigation and its
three hypotheses), `20260830T113803Z` (Pass 2), and `20260830T030328Z`
(post-cutover check). No prior note covers the restart timer itself — it
is ~10 minutes old. Verified against: `git show/status/diff`,
`verify-manifest.sh`, `systemctl --user` (status/cat/show/list-timers,
journald with `-o short-precise`), `systemd-analyze calendar`, live
`/health`, `/slots` and a real timed `/v1/completions` call on both
tiers, `/proc/<pid>/status`, `/proc/meminfo`, `vmstat`, `zramctl`,
`podman ps/stats/image inspect`. Read-only except this file and one
second-brain note. Nothing staged, committed, or signed (hard rule).
Note: a second headless drift-check session (PID 1671159, spawned by the
post-commit hook for `3dafed8` three minutes before this one) was alive
concurrently — if this file ends up with two overlapping Pass 3 sections,
that is why.

## H0 — What the commit claims that IS true (verified)

- Tracked `.config/systemd/user/corporatetraveldc-llama-restart.{service,timer}`
  are byte-identical to the live copies in `~/.config/systemd/user/`;
  timer `enabled` + `active (waiting)`, next elapse Mon 2026-08-31
  03:00:00 EDT; `Persistent=true`, `OnCalendar=*-*-* 03:00:00 America/New_York`.
- The script does what its header says: hot then chat, blocking
  `systemctl --user restart`, `is-active` check, ntfy `ops-health` p2/p4.
  Real run logged in `/var/lib/corporatetraveldc/scheduled-llama-restart/restart.log`:
  hot 08:07:11→08:07:25 (14 s), chat 08:07:25→08:08:56 (91 s — chat's
  `ExecStartPre` hot-health gate verified present at
  `llama-chat.service:21`, hot's `dd` pre-read at `llama-hot.service:32`).
- Pass 2 G1 is CLOSED: `3dafed8` carries exactly the 14 `src/` files +
  CLAUDE.md the `87c4a57` message described (`git show --stat`: 15 files,
  +125/−416). `verify-manifest.sh`: **OK — 803 files match**.
- Deploy status unchanged from Pass 2 G2: poller image still
  `2026-08-29 06:46 UTC`; today's skill runs still log the pre-gut
  `Ollama call failed` strings. Same single `build-images.sh` rebuild
  still owed (Pass 1 F1 + Pass 2 G2 + this).

## H1 — REAL: the 03:00 ET "quiet slot" is not quiet — it collides to the second with `aam-daily-watch`

The timer `Description=` (and the commit message) call 03:00 ET a
"quiet slot, clear of the 04:00 / 04:15 / 04:35 / 05:15 / 06:00 chain".
It checked the wrong neighbours. `corporatetraveldc-aam-daily-watch.timer`
is `OnCalendar=*-*-* 0/3:00:00 America/New_York` (+ `1/3:30`) — i.e. it
fires at **03:00:00** every day; `systemd-analyze calendar
--base-time='2026-08-31 02:59'` confirms next elapse **Mon 2026-08-31
03:00:00 EDT**, the same second as the restart. AAM is an LLM skill on the
chat/report port (8094, `llm.py:757` routes everything non-hot there).
More generally the six `*_daily_watch` timers are staggered 15 min apart
on a 90-min cadence, so **some LLM skill fires at every :00/:15/:30/:45
around the clock** and each runs for minutes — there is no 2-minute
"quiet slot" on this box at any hour.

What a mid-skill restart does was demonstrated live by the commit's own
08:07 run (see H2): `gig-economy-daily-watch` (container up since ~08:01)
logged `Ollama call failed: Server disconnected without sending a
response` at **08:08:55**, one second before chat came back at 08:08:56,
then `Anthropic fallback disabled for this caller` → deterministic
output. That is exactly what `aam-daily-watch` will get at 03:00:00
tomorrow and every day after. Fix when picked up: the clock slot cannot
solve this — make the script wait for `GET /slots` on the target port to
report `is_processing:false` (bounded) before each `systemctl restart`,
or teach `llm.py` to retry once on `RemoteProtocolError`. Persisted.

## H2 — REAL: `Requires=` in the timer fired an unscheduled restart of both tiers the instant the timer was enabled

`journalctl -o short-precise`: `Started …llama-restart.timer` at
**08:07:11.805**, `Starting …llama-restart.service` at **08:07:11.811**,
`Stopping …llama-hot.service` at 08:07:11.919. The timer's
`[Unit] Requires=corporatetraveldc-llama-restart.service` pulls the
service in whenever the *timer* is activated — `enable --now` on the
timer was itself the trigger. The `--dry-run` "verification" in the commit
message ran at 08:07:22, *while* the real restart it was meant to
rehearse was already in progress (`restart.log` interleaves them). So
both tiers took an unplanned Sunday-morning cold reload with skills
in flight (H1's gig-economy casualty; `aviation-daily-watch`, running
since 07:45, died at 08:09:16 on `sqlite3.OperationalError: database is
locked` inside the same I/O-thrash window — consistent with, not proven
caused by, the restart).

This is the repo-wide convention (52 of 56 tracked timers carry
`Requires=<service>`), not this commit's invention, and for idempotent
poll skills it is harmless. For the two *restart-class* timers
(`ingest-restart`, `llama-restart`) it also means every boot /
user-manager start runs the restart service on top of the targets' own
boot activation — for llama-hot that is a second cold read of the GGUF
right after the first, the thundering-herd shape the script's comment
says it exists to avoid. Fix: `Wants=` (or nothing) instead of
`Requires=`. Not done by this pass. Persisted with H1.

## H3 — REAL, advances CLAUDE.md's OPEN investigation: the thrash reproduces within 4 minutes of a cold restart — hypothesis (c) is out, (b) is confirmed with numbers

Measured 08:11–08:15, i.e. 3–7 min after the fresh restart:

- `free`: 10 Gi used / 80 MB `MemFree` / 6.5 Gi of 8 Gi zram swap used;
  `zramctl`: 3.9 G data compressed into **2.8 G of RAM** (the swap device
  itself costs ~2.8 GB of the 16.6 GB). `AnonPages` 6.6 GB + swapped ~6.9 GB
  ≈ **13.5 GB of anonymous demand on a 16.6 GB box**. `vmstat`: si/so
  ~230 MB/s *each*, bi ~315 MB/s (GGUF page-cache pages being evicted and
  re-read), 14–20 runnable, load1 **31.2**. `MemAvailable` (6 GB) is
  misleading here — it is the mapped model file being counted as
  reclaimable while it is the working set.
- llama-hot 3 min after load: `VmRSS 42 MB / VmSwap 3.6 GB` — the whole
  process paged out. A real 8-token `/v1/completions` call on 8093 got
  **no response in 90 s**; the journal shows its task 0 ran 08:12:06 →
  08:13:48 for 12 tokens (~0.12 tok/s incl. prompt). llama-chat: task 0
  (1013-token prompt, ~20 decoded) 08:09:45 → 08:14:47. `/health` said
  `ok` on both throughout (already known: it does not reflect serving
  capacity).
- Where the anon memory is: llama-chat RSS 1.75 GB (cgroup peak 3.4 GB),
  llama-hot cgroup peak 2.47 GB — phi3-mini has **no GQA** (32 KV heads ×
  32 layers × 3072), so an f16 KV cache at `-c 4096` is ~1.6 GB *per
  instance*, ~3.2 GB for the two always-resident tiers before compute
  buffers (derived from the architecture; the journal's KV-size line was
  not captured). Then `claude --remote-control` **1.1 GB RSS** (up since
  08-19, 828 CPU-min), **openwebui 705 MB** (its Ollama backend is dead —
  Pass 2 G4), nextcloud-app 436 MB, poller 188 MB, six ingest containers
  ~600 MB, and two concurrent headless `claude -p` drift sessions ~300 MB
  each (this one included).

Conclusion for the open item: multi-day fragmentation (c) cannot be the
cause when a 4-minute-old process is already fully swapped; this is
plain over-commit (b). The daily restart `75752fa` adds does not relieve
it — it re-incurs a cold load *under* thrash daily (and, per H1, mid-
skill). Levers the operator can pull, none taken here: `-ctk q8_0 -ctv
q8_0` on both tiers (halves KV), a smaller `-c` on hot (its TFR/route
prompts are short), retire `openwebui` (dead backend), end the 11-day
`claude --remote-control` session, and stop the post-commit hook from
running two drift sessions at once. Persisted.

## H4 — Doc drift from these two commits specifically

- `docs/ALERT_REFERENCE.md:176` (`ops-health` topic row) and `:609`
  ("Standalone bash/script alerts") enumerate every ops-health emitter
  incl. `scheduled-ingest-restart.sh` — `scheduled-llama-restart.sh`
  (p2 success / p4 failure, `Tags: recycle`) is now a live emitter and is
  absent from both. Same row still lists the dead `ollama-swap-alert.sh` /
  `ollama-wedged-detector.sh` (prior-known).
- `docs/INFRA_MAP.md:503` timer/watchdog inventory lists `ingest-restart`
  but not the new `llama-restart` daily.
- The new timer's own `Description=` ("quiet slot") is false — H1.
- `MANIFEST.sha256`/`.asc` were modified-uncommitted when this pass
  started (HEAD `75752fa`'s committed manifest still carried the pre-gut
  hashes and lacked the three new files — a fresh clone would have failed
  `verify-manifest`). **Closed mid-pass** by the concurrent remote-control
  session's `d3e9074` ("Re-sign manifest to cover the llama-restart timer
  files"): re-verified after it landed — `git status` shows only the two
  untracked `LIVE_STATE_CHECK_2026-08-29/30.md` files, `verify-manifest`
  OK (803 files) against HEAD. The vault note for this pass (written
  before `d3e9074`) still describes the manifest as uncommitted; this
  file is the corrected record.
- CLAUDE.md: says nothing about the new timer (fine, scratchpad); its
  "OPEN… NOT YET DETERMINED (a)/(b)/(c)" entry is now partly answered by
  H3 — worth updating at the next consolidation rather than here.
- Prior-known, unchanged, not re-derived: README Local-LLM section
  (`:704` `_abandon_ollama_generation` present tense), `README.md:12/75/191`
  Ollama-era stack description, `docs/DESIGN-PRINCIPLES.md:15` ("via
  **Ollama**" — still local, wrong name), `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:34-58`
  (whole tuning table is Ollama-governor-era). `src/ingest/README.md` and
  `src/shared/watchlist_README.md`: zero claims touched by either commit
  (grepped).

## H5 — Failed-unit reconciliation vs Pass 2

Now four: `docs-drift-weekly`, `transport-pattern-digest`,
`second-brain-weekly-dump` (all unchanged) + **`aviation-daily-watch`**
(new, 08:09:21, H2). `integrity-sweep` cleared as Pass 2 predicted.
The `llama-restart.service` itself exited 0.

---

**Persisted to second brain (this pass):** one note carrying H1 + H2
(the restart timer's two real defects) and H3 (the over-commit
measurements that close hypothesis (c)), via `remember_text()`
(author_kind=agent). H0/H4/H5 are repo-state, tracked here only.

---
---

# Pass 4 — post-a6cacfb (production.slice ceiling fix + Ollama infra purge), ~09:08–09:15 EDT

Scope: `a6cacfb` — production.slice re-sized 6656M/7680M → Low 6144M /
High 12000M / Max 13780M, per-tier MemoryLow/High/Max/SwapMax=0 on
llama-hot/chat, `Requires=`→`Wants=` on the restart timer, idle-wait in
`scheduled-llama-restart.sh`, tiers `enable`d, and 10 Ollama-era files
deleted (swap-alert unit, governor unit/env/script, wedged-detector,
prewarm, resource-limits drop-in, ollama nginx vhost) plus the Ollama
step cut from `lockdown.sh`/`restore-network.sh`. Second-brain search ran
first (`production.slice`, `swap-alert`, `llama-restart`, `openwebui`,
`--raw 'llama AND slice'`): builds on Pass 3 (`20260830T122137Z` /
`20260830T121921Z` — H1/H2/H3 are the findings this commit set out to
fix), Pass 2 (`20260830T113803Z` G4 survivor inventory — now mostly
closed by this commit) and `20260830T030328Z`. Verified against:
`git show/status/diff`, `verify-manifest.sh` (whole-tree and per-target),
`systemctl --user show/cat/list-timers/--failed`, system `systemctl`,
`journalctl` (user + `-b -1`), `coredumpctl`, `sudo -n -l`, `ss -ltnp`,
`podman ps`, `free`/`zramctl`/`vmstat`/`top`, live `/health` + `/slots`
on both tiers, live `/etc/fail2ban/action.d/`, live `/etc/nginx/conf.d/`,
live `/etc/corporatetraveldc/dispatch.env`. Read-only except this file
and one second-brain note. Nothing staged, committed, or signed (hard
rule). Context that colours everything below: **the box was cleanly
rebooted at 08:23 EDT** (new boot ID; `systemd-shutdown` at 08:22:48 in
the prior boot) — before the commit, after Pass 3.

(Tool note: the llama tiers bind `100.x.x.x`, not loopback — `curl
127.0.0.1:809x` returns empty and proves nothing. Pass 1's search-tool
note still applies.)

## I0 — What the commit claims that IS true (verified live)

- Tracked ↔ live byte-identical (`diff -q`) for `production.slice`,
  `llama-hot.service`, `llama-chat.service`, `llama-restart.timer`,
  `llama-restart.service`; `scheduled-llama-restart.sh` executes from the
  repo path directly (no installed copy to drift).
- `systemctl show`: production.slice MemoryLow/High/Max =
  6144M/12000M/13780M; llama-hot 3072/3840/4608M, llama-chat
  4096/5120/6144M, both `MemorySwapMax=0`, both `MemorySwapCurrent=0`,
  both `UnitFileState=enabled` with `default.target.wants` symlinks
  (08:55). System swap **479M used** (Pass 3 measured 6.5G); zram 354M
  data. The swap-thrash component is genuinely gone.
- Timer: `Requires=` empty, `Wants=corporatetraveldc-llama-restart.service`,
  next elapse Mon 2026-08-31 03:00 EDT. Script's idle-wait polls
  `/slots` on the correct host (`LLAMA_HOST=100.x.x.x`).
- `ollama-swap-alert.service` disabled/inactive; `openwebui.service`
  inactive with no `WantedBy` (stays down across boots).
- **The root-owned handoff is already done** — `/etc/systemd/system/
  ollama.service`, `ollama-governor.service`, `ollama.service.d/` and
  `/etc/nginx/conf.d/ollama.example.com.conf` are all gone,
  `systemctl show ollama.service` → `LoadState=not-found`, nginx active.
  The commit message's "could not reach (no sudo)" paragraph is history.
- Pass 2 G4's three live survivors (swap-alert enabled, ollama vhost
  serving 502s, openwebui with a dead backend) are CLOSED by this commit.
- Boot behaviour, observed: at the 08:23 boot the (then still
  `Requires=`) timer's `Persistent=true` catch-up pulled
  `llama-restart.service` at 08:23:18, which is what started both tiers
  (08:23:19 / 08:23:27) — exactly Pass 3 H2's shape, one last time. The
  `enable` symlinks post-date that boot, so "tiers start via
  default.target" is not yet boot-tested; it will be on the next reboot.

## I1 — REAL: `a6cacfb` landed unsigned — `MANIFEST.sha256` still covers `d3e9074`'s tree; fail2ban's stack-lockdown path is dead until re-sign

`verify-manifest.sh` (whole tree): **10 listed files missing + 8
mismatched** — exactly the commit's 10 deletions and 8 modifications
(`dispatch.env`, both tier units, the timer, `production.slice`,
`lockdown.sh`, `restore-network.sh`, `scheduled-llama-restart.sh`).
`MANIFEST.sha256` was last touched in `d3e9074` (08:14); the commit is
09:07; `git status` at 09:14 shows no re-sign in progress.

Blast radius is narrower than the 08-28 instance because
`verified-exec.sh` verifies **per target**: the 09:13 gated timers
(`research-board-mirror`, `personal-notes-import`) passed — their
targets are unchanged. What does fail:

- `corporatetraveldc-integrity-sweep.service` — failed 09:10:01, will
  fail every 15 min until re-sign (`journalctl` shows the sweep's own
  "48 files match" subset OK immediately followed by the whole-tree
  INTEGRITY FAILURE).
- **`/etc/fail2ban/action.d/corporatetraveldc-lockdown.conf`** —
  `actionban = verify-manifest.sh scripts/lockdown.sh && lockdown.sh …`
  and the matching `actionunban` for `restore-network.sh`. Tested
  directly: `scripts/verify-manifest.sh scripts/lockdown.sh` → **rc=1,
  INTEGRITY FAILURE**. A fail2ban ban right now applies the firewalld
  ban but does NOT run the stack lockdown, and the unban will not run
  `restore-network.sh`. This is precisely the two files the commit
  edited to remove the Ollama step.

Same self-resolving-on-re-sign class as CLAUDE.md's 08-28/08-29 entries
(`ollama-keepwarm` retirement → `d3e9074` followed within the hour); the
re-sign is the whole fix and is out of bounds for this pass. **Persisted
to second brain.**

## I2 — REAL: the "4.58 tok/s post-fix" verification is the HOT tier's 3-token sample; the CHAT tier — the tier the bug was about — sustains 1.15 tok/s with zero swap

- `journalctl -u llama-hot`: pid 118851 task 0 at 09:04 — `eval time
  654 ms / 3 tokens (218 ms per token, 4.58 tokens per second)`, prompt
  17 tokens @ 15.45 t/s. That is the literal figure in the commit
  message.
- `journalctl -u llama-chat`: pid 128982 task 0 started 09:00:41, still
  `is_processing:true` at 09:14, `n_decoded = 382`, **`tg = 1.15 t/s`**
  steady across 20 consecutive `print_timing` lines (`tg_3s` 0.88–1.24).
  Meanwhile `MemorySwapCurrent=0`, RSS 3.7G, **181% CPU** — i.e. it is
  getting its full `-t 2` and is neither swapped nor starved; this is a
  compute/bandwidth ceiling on a ~1000-token report-tier context with 2
  threads on a 4-core box at load1 7.7.
- Net: the memory root cause is fixed (0.1 → 1.15 t/s, ~10×, thrash
  gone), but a 300-token report-tier answer is still ~4–5 min and skills
  whose timeouts assume much more than 1 t/s will keep going
  deterministic. CLAUDE.md's "expected ~10–18 tok/s baseline" has no
  supporting measurement anywhere in today's journal — 4.58 on a
  17-token context is the best observed on this box. CLAUDE.md's OPEN
  (a)/(b)/(c) item resolves as: (b) over-commit, confirmed and fixed for
  the swap component; the residual is CPU-bound, not memory. **Persisted.**

## I3 — REAL, mechanism found: a mid-task restart of a llama tier = 45 s stop timeout → SIGABRT → 2.4–2.7 GB coredump → "database is locked" storm across ingest

The commit's own "one transient casualty … same I/O-thrash class as
08-27" has a specific, repeatable cause:

- 08:55 manual `scheduled-llama-restart.sh` run: hot was idle →
  restarted in 6 s. chat: idle-wait expired at 120 s (`still processing
  (or unreachable) after 120s idle-wait -- restarting anyway`),
  `systemctl restart` at 08:57:50 → SIGTERM ignored (journal stack trace:
  `llama_server` blocked in `std::thread::join` on the in-flight task) →
  `TimeoutStopSec=45s` with `TimeoutStopFailureMode=abort` → **SIGABRT at
  08:58:35** → `systemd-coredump` wrote a **2.4 GB** core
  (`LimitCORE=infinity`) → at **08:58:45** five ingest containers
  (tfms/fdps/stdds/tbfm/itws) logged `database is locked`, `aam-daily-
  watch` got `Ollama unavailable … returning None` at 08:59:09, chat
  back at 08:59:19 (89 s outage; unit `Failed with result 'timeout'`,
  `code=dumped`).
- `coredumpctl list`: an identical 2.7 GB dump at **08:09:05** — Pass 3
  H2's `aviation-daily-watch` "database is locked" death at 08:09:16 was
  this, not generic thrash — and a third at 07:32:52 (core missing).
- Consequence for the 03:00 timer: at I2's throughput any report-tier
  generation outlasts the 120 s idle-wait, so on the chat tier the wait
  will expire essentially every night (Pass 3 H1's `aam-daily-watch`
  collision at 03:00:00) and reproduce exactly this sequence, with the
  coredump write as the I/O weapon. Levers, none pulled: `LimitCORE=0`
  on both tier units (removes the multi-GB write entirely), an
  `IDLE_WAIT_MAX_SEC` comfortably above the longest generation or
  skip-when-busy, `KillSignal=SIGKILL` for a process that demonstrably
  ignores SIGTERM mid-task. **Persisted with I2.**

## I4 — Doc drift from this commit specifically (claims true until 09:07, false now)

- `README.md:738` and `docs/INFRA_MAP.md:464` — "`scripts/ollama-prewarm.sh`
  still exists but is orphaned" → deleted by this commit.
- `docs/GUARDRAILS_JUSTIFICATION.md:204-207` — `ollama_governor.py` "runs
  as a managed, enabled systemd unit … unit file is repo-tracked at
  `systemd/ollama-governor.service`" → unit deleted from repo AND `/etc`;
  only `/usr/local/bin/ollama_governor.py` (root, untracked) survives.
  `:146-184` — the `20-resource-limits.conf` drop-in → gone both sides.
- `docs/INFRA_MAP.md:37, :111, :128-144` — drop-in "installed: **Yes** …
  In effect" table → file gone both sides. `:562/:569` — `ollama.` vhost
  "Live" → nginx server block deleted (tracked + live); `openwebui.` is
  live but returns **502** (backend container stopped by this commit —
  verified `curl -H Host:` → 502).
- `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:9-14, :32-35` — drop-in/governor
  rows (prior-known as Ollama-era; now the cited files do not exist).
  `:40` — `OLLAMA_LOAD_TIMEOUT` "client-side load-phase probe in
  `llm.py`" → reader removed by `3dafed8`, var still set live (I5).
- `docs/COMPLIANCE_SECURITY.md:540-542` (drop-in) and `:575-580`
  (`lockdown.sh`'s `sed -i` on `ollama.service.d/10-binding.conf`) →
  both mechanisms removed.
- `docs/ALERT_REFERENCE.md:568-569` — "`lockdown.sh:100` … host-reach
  opt-ins for Ollama/pusher/acarshub" → Ollama step removed, line moved,
  ntfy body now says "pusher, acarshub". `:176/:736/:739` — list
  `ollama-swap-alert.sh` / `ollama-wedged-detector.sh` as ops-health
  emitters → files deleted; `scheduled-llama-restart.sh` still absent
  (Pass 3 H4, unchanged). `:184` approval-gate row → `ollama.service`
  grants are still in sudoers (I5) but the unit is `not-found`.
- `docs/HONEYPOT_FAIL2BAN.md:68` — `ollama` among CF-gated vhosts → gone.
- `docs/TAILNET_MIGRATION_INVENTORY.md:70-71, :76, :150-151, :156,
  :159-160` — every cited file/line deleted by this commit (adds to
  Pass 2's `:155`).
- `README.md:140` / `docs/INFRA_MAP.md:396` — `openwebui (:3000)` as a
  running stack member → stopped.
- `CLAUDE.md` "OPEN … NOT YET FIXED" paragraph → superseded (I2). Left
  untouched: editing a tracked file now would widen the I1 window.
- Nothing to invalidate on the slice numbers themselves: no living doc
  ever stated 6656M/7680M/13780M (grepped); `docs/dispatch-runner-design.md:196`
  only names slice membership, still true.
- `src/ingest/README.md`, `src/shared/watchlist_README.md`: zero claims
  touched (grepped); the ingest README's LOCKDOWN-row "host
  `ollama.service`" is prior-known (`20260830T030328Z`), and
  `thermal-ingest-guard.py:330` already says it no longer touches it.

## I5 — Live residue the commit did not claim to clean (cleanup list, not doc drift)

- `~/.config/systemd/user/corporatetraveldc-ollama-swap-alert.service`
  still installed (disabled) although deleted from tracking; its
  `ExecStart` points at a script that no longer exists.
- `/usr/local/bin/ollama_governor.py` (root) survives; `sudo -n -l` still
  grants NOPASSWD for `ollama.service` (restart/start/stop), `kill
  --signal=SIGKILL ollama.service` (listed twice) and
  `ollama-governor.service` (stop/start/restart) — all targeting
  `not-found` units. `scripts/sudo-approval-gate.sh:5,15` still describes
  them.
- Live `/etc/corporatetraveldc/dispatch.env` still sets
  `OLLAMA_LOAD_TIMEOUT`, `OLLAMA_READY_WAIT_CAP_S`, `OLLAMA_READY_TIMEOUT_S`
  (readers removed by `3dafed8`; only a comment at `llm.py:659` remains).
  Live `OLLAMA_BASE_URL` value differs from tracked (`100.x.x.x` vs
  `host.containers.internal`) — both inert per the commit's own loud
  comment, which was applied to both copies.
- `openwebui.container` Quadlet (tracked + live) and
  `nginx/conf.d/openwebui.example.com.conf` remain with the
  dead `OLLAMA_BASE_URL` backend; the public hostname 502s.

## I6 — Failed-unit reconciliation vs Pass 3

Now five: `integrity-sweep` (I1, new); `second-brain-demo-archiver-daily`,
`second-brain-rss`, `second-brain-weekly` (all three `502 Bad Gateway`
from Nextcloud WebDAV at 08:24:01–08:24:36 — `Persistent=true` catch-up
firing at the 08:23 boot before `nextcloud-app` was up at 08:24:38; boot
race, self-heals on next fire); `transport-pattern-digest` (chronic P3-2,
SIGKILL 08:51). Cleared since Pass 3: `aviation-daily-watch` (ran clean,
08:50), `docs-drift-weekly` and `second-brain-weekly-dump`
(`reset-failed` — `Result=success` with no `InactiveEnterTimestamp`, not
re-run). `docs/CLAUDE_MD_DRIFT_REPORT.md` is modified-uncommitted by the
08:23 `claude-md-drift-daily` run ("No drift found") — not this pass's
edit, left alone.

---

**Persisted to second brain (this pass):** one note carrying I1 (unsigned
commit / fail2ban lockdown path), I2 (chat tier 1.15 t/s, the 4.58 figure
is hot-tier) and I3 (SIGABRT→coredump→DB-lock mechanism), with the I5
residue as secondary payload, via `remember_text()` (author_kind=agent).
I0/I4/I6 are repo-state, tracked here only.

---

# Pass 5 — post-9489c7c/410fb01 (manifest re-sign + docs checkpoint), ~09:33–09:45 EDT

Scope: the two commits after `a6cacfb` — `9489c7c` (CLAUDE.md: OPEN
swap-thrash paragraph → FIXED, plus a new demo-archiver Known-bad entry;
manifest re-signed, 793 files) and `410fb01` (this file and the 08-29 one
committed; drift report timestamp refresh). Neither touched code, units, or
live config, so this pass is mostly *closing* Pass 4 items and checking
what the re-sign gate let through. Second-brain search first: `--raw
'llama AND slice'` surfaces Pass 4's note (`20260830T131615Z`), Pass 3
(`20260830T122137Z`), and `20260830T030328Z` — this pass builds on those,
nothing here re-derives them. Verified against: `git show/status`,
`verify-manifest.sh`, `check-claude-md-drift.sh` (read-only by its own
header), `systemctl --user show/status/--failed/list-timers`, `journalctl
--user`, `coredumpctl`, `sudo -n -l`, `podman ps`, `free`, live
`/slots` on both tiers, `curl -H Host:` against nginx, live
`/etc/corporatetraveldc/dispatch.env` (key names only). Read-only except
this file and one second-brain note. Nothing staged, committed, or signed.

(Tool note, adds to Pass 4's: `second-brain-search.sh` default mode is
*phrase* search — a multi-word query like `production.slice llama swap`
returns **nothing** even though every word is in Pass 4's note. Use
`--raw 'a AND b'` or a single term. `--semantic` falls back to the same
literal phrase and also returns 0. Three plain queries this pass came
back empty before `--raw` found four notes.)

## J0 — Closed since Pass 4 (verified live)

- **I1 CLOSED.** `verify-manifest.sh` whole-tree: `OK -- signature valid,
  all 793 files match`; `MANIFEST.sha256` last touched in `9489c7c`.
  `scripts/lockdown.sh` / `restore-network.sh` are covered again, so the
  fail2ban `actionban`/`actionunban` stack-lockdown path is live again.
  `integrity-sweep` last ran **09:25:01** (pre-re-sign, still the 10
  missing + 8 mismatched from I1) and is `failed` at time of writing; next
  elapse 09:40:01 — result recorded in J5 below.
- **I4's "CLAUDE.md OPEN paragraph" item CLOSED** by `9489c7c`. The
  replacement text's numbers check out live: production.slice
  Low/High/Max = 6144M/12000M/13780M, llama-hot 3072/3840/4608M,
  llama-chat 4096/5120/6144M, both `MemorySwapMax=0`, both
  `MemorySwapCurrent=0`, system swap **478M** used (Pass 4: 479M), both
  tiers `enabled`, timer `Wants=` only, next 2026-08-31 03:00 EDT. The
  one caveat it carries forward is I2's: "4.58 tok/s" is the hot tier's
  3-token sample, not the chat tier (see J4 for today's chat numbers).
- `9489c7c`'s new demo-archiver entry is accurate as far as it goes
  (`502` at 08:24:37, `nextcloud-app` up 08:24:38) — but it is only one
  of three identical boot-race failures, and the other two are the
  subject of J1.
- Manifest coverage note, not drift: the manifest is over `9489c7c`'s
  tree; `410fb01`'s three `docs/` files are unlisted (`grep` → 0 hits).
  `verify-manifest.sh` only checks listed files, so this pass's append to
  this file does **not** re-open the sweep window (Pass 4's caution about
  editing tracked files applied to manifest-listed ones).

## J1 — REAL: `check-claude-md-drift.sh` §5 is a bare substring match, so stale or longer-named Known-bad entries mask new failures — the 09:28 `--pre-sign` gate passed with 3 of 5 failed units unlogged

`scripts/check-claude-md-drift.sh:136-142`: takes each failed unit,
strips `.service`, then `grep -qF "${u}" CLAUDE.md`. Anywhere the bare
name appears — in any entry, for any cause, any date — counts as
"logged". Run read-only at 09:36: exit 0, `[OK] CLAUDE.md matches live
state`, with these five units in `--state=failed`:

| unit | actual failure (this boot) | what §5 matched in CLAUDE.md |
|---|---|---|
| `second-brain-demo-archiver-daily` | 502 boot race, 08:24:37 | line 31 — the real entry `9489c7c` added ✅ |
| `integrity-sweep` | I1 unsigned tree, 09:25 | line 17 — 08-29 acarsrouter entry (different cause, already "resolves once signing completes") |
| `second-brain-rss` | **502 boot race, 08:24:26** (same as demo-archiver) | line 11 — the **08-28** INTEGRITY-FAILURE list, an entry that explicitly says it already self-resolved |
| `transport-pattern-digest` | **SIGKILL on `TimeoutStop`, 08:51:30** (chronic, Pass 2/08-29 P3-2) | line 11 — same 08-28 list |
| `second-brain-weekly` | **502 boot race, 08:24:19** | line 25 — the `second-brain-weekly-dump` entry: a *different unit* whose name merely starts with this one |

The third row is the sharpest: `corporatetraveldc-second-brain-weekly` is
a prefix of `corporatetraveldc-second-brain-weekly-dump`, so as long as
weekly-dump has any Known-bad entry, weekly can never trip the gate. The
first two rows are the softer, more common form: the 08-28 consolidation
entry names nine units at once and will keep "covering" any of them for
as long as it stays in the file (the section is dated 08-28 and check 6
only complains at age, so it will stay for days).

Consequence, concretely: `9489c7c`'s message says the pre-sign check
"blocked ... demo-archiver ... wasn't logged" and that entry was added —
correct, but two more units failed the *same way at the same minute* and
one (`transport-pattern-digest`) is a real, chronic, still-unexplained
SIGKILL that has now been failed and unlogged across Passes 2–5. The gate
that exists to force CLAUDE.md to be current at every sign-off signed off
on a Known-bad section that under-reports by 3.

Fix shape (not applied — read-only pass; two-line change): match the
unit as a whole token, e.g. `grep -qE "(^|[^A-Za-z0-9-])${u}\.service"`
(the doc always writes names with `.service`), or `grep -qF
"\`${u}.service\`"` given the doc's backtick convention; and, separately,
have the entry's *date* matter — a unit whose last failure is newer than
the newest date on the line that mentions it is not logged. The second
part is what would have caught rows 2–4. **Persisted to second brain.**

## J2 — Doc drift delta since Pass 4

- **Nothing new.** `9489c7c`/`410fb01` changed CLAUDE.md (verified, J0)
  and added dated check files; no other doc moved. Every I4 item was
  re-grepped and is still present verbatim: `README.md:140/:191/:198/
  :217/:636-645/:728-740`, `docs/INFRA_MAP.md:37/:111/:128-144/:396/
  :456-464/:562/:569`, `docs/GUARDRAILS_JUSTIFICATION.md:146-184/:204-207`,
  `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`, `docs/COMPLIANCE_SECURITY.md`,
  `docs/ALERT_REFERENCE.md`, `docs/HONEYPOT_FAIL2BAN.md:68`,
  `docs/TAILNET_MIGRATION_INVENTORY.md`, `docs/DATA_SOURCES.md:100/
  :1064-1092`, `docs/ALERT_ARCHITECTURE.md:60`, `docs/HARDWARE_GUIDANCE.md:74`,
  `src/ingest/README.md:58` (LOCKDOWN row "host `ollama.service`"). I4
  stands in full as the open edit list; none of it is this pass's to fix.
- The two newly committed `LIVE_STATE_CHECK_2026-08-29/30.md` files now
  contain claims that are *already* stale (I1 "landed unsigned", Pass 3
  H2's `Requires=`) — they are dated records of what was true at each
  timestamp, not living claims, and this section is the correction
  trail. Not drift.
- `docs/CLAUDE_MD_DRIFT_REPORT.md` says "No drift found" at 08:23:18 —
  true at 08:23:18 (the boot-race failures began 08:24:19). Its next
  daily run will also say "No drift found" for the J1 reason, which is
  the report now being *wrong* rather than merely stale.
- `README.md`, `src/shared/watchlist_README.md`: no claim touched by
  either commit.

## J3 — Live residue: I5 unchanged, I3 levers unchanged

- `~/.config/systemd/user/corporatetraveldc-ollama-swap-alert.service`
  still installed (disabled, `ExecStart` → deleted script);
  `/usr/local/bin/ollama_governor.py` (root) still present; `sudo -n -l`
  still grants `ollama.service` start/stop/restart, `kill -SIGKILL
  ollama.service` (×2) and `ollama-governor.service` — all `not-found`
  units. Live `dispatch.env` still sets `OLLAMA_LOAD_TIMEOUT`,
  `OLLAMA_READY_TIMEOUT_S`, `OLLAMA_READY_WAIT_CAP_S` (no readers).
  `openwebui.example.com.conf` live + tracked, vhost → **502**.
  `openwebui.container` tracked = live (the drift checker's §11 even
  reports it `[OK]`), container stopped.
- Both tiers still `LimitCORE=infinity`, `KillSignal=SIGTERM`,
  `TimeoutStopSec=45s`, `TimeoutStopFailureMode=abort` — none of I3's
  levers pulled. `coredumpctl`: no new dumps since 08:59:19 (the 2.4 G
  one); the 07:32 / 08:09 / 08:59 trio is the full set for this boot.

## J4 — Measurements update (extends I2, not a new finding)

Chat tier (pid 128982, up since 08:59:19) `print_timing` 09:28–09:33:
prompt eval **14.3–14.6 t/s** (385–557-token prompts), generation
**2.39–2.42 t/s** (49–55-token answers) — roughly 2× I2's 1.15 t/s, which
was measured mid-generation on a longer (382-token) output at load1 7.7.
At 09:33 a **2,048+-token** prompt (`progress = 0.74` at 2048, ≈2.7 k
total) was 151 s into prompt processing at 13.5 t/s, slot
`is_processing:true` at 09:35; hot tier idle. Zero `deterministic` /
`Ollama unavailable` journal lines since 09:15. Still CPU-bound, still
nowhere near CLAUDE.md's "10–18 tok/s baseline", which remains an
unsupported number.

## J5 — Failed-unit reconciliation vs Pass 4

Same five as I6 at 09:36: `integrity-sweep` (I1 — awaiting its first
post-re-sign fire), `second-brain-demo-archiver-daily`, `second-brain-rss`,
`second-brain-weekly` (all 502 boot race 08:24), `transport-pattern-digest`
(SIGKILL 08:51, chronic). CLAUDE.md now logs exactly one of the five for
its current cause (demo-archiver); J1 explains why the gate didn't
notice the other four. Cleared: none. New: none.
