# Live-state / doc-drift check — 2026-08-29 (~22:55–23:10 EDT), post-commit e9ac181

Scope: commit `e9ac181` ("Cut over local LLM inference from Ollama to raw
llama.cpp") — the largest LLM-surface change since the 2026-08-13/14
consolidation. Second-brain search ran first, per convention: the
2026-08-28 session-synthesis notes (`01-Sources/manual/20260828T042817Z.md`
/ `20260828T042841Z.md`) cover the cutover *design* (llama_pool tiers,
personas registry, backpressure removal) but contain nothing on the two
live findings below — both are new, not re-derivations. Read-only except
this file and one second-brain note (via `remember_text()`). Nothing
staged, committed, or signed by this pass.

Checked: README.md, CLAUDE.md, docs/ living pages, `src/ingest/README.md`,
`src/shared/watchlist_README.md`; verified against `systemctl --user`
unit/timer state, live llama-server `/health` + journald, `/proc` memory
counters, `free`, the load-fallback event log, and the tracked-vs-installed
unit files.

---

## Finding 1 — REAL, LIVE NOW: memory governance was not carried over to the llama units; the documented 2026-08-06/07 swap-thrash failure class has already recurred

The retired `ollama.service` drop-in
(`systemd/ollama.service.d/20-resource-limits.conf`) encoded three
hard-won, root-caused protections: `MemoryLow` (2026-08-06, protects the
model working set from *external* reclaim pressure), `MemorySwapMax=0`
(2026-08-07, converts silent multi-minute swap-thrash into fast reclaim or
a loud OOM), and per-baseline `MemoryHigh`/`MemoryMax`. The new
`corporatetraveldc-llama-hot/chat.service` units carried over the
CPUWeight lesson (`CPUWeight=9000`) but **none of the memory lessons**:
both units have `MemoryHigh=infinity`, `MemoryMax=infinity`, no
`MemoryLow`, no `MemorySwapMax`. They sit in `production.slice`, whose
slice-level settings are `MemoryLow=0` and `MemorySwapMax=infinity` — so
nothing anywhere protects the resident models from being paged out.

Measured live tonight (~23:00 EDT, while the box sat at load1 ≈ 27 with
6.5 GiB of 8 GiB swap used and ~90 MiB free RAM):

- **Hot tier (VIP/POTUS TFR alert path, "permanent, always resident,
  never thermally paused"): effectively cold.** `MemoryCurrent` ≈ 6.8 MB
  resident; `VmSwap` ≈ 3.72 GiB — the *entire model* is paged out. The
  next hot-priority call must fault ~3.7 GiB back through zram before its
  first token; that is worse than a cold start, and hot priority has no
  retry path by design ("fails straight to fallback"). The unit
  description's core guarantee is currently void.
- **Chat tier (which since 2026-08-27 also serves ALL report-tier
  traffic — see Finding 4): thrashing.** `VmSwap` ≈ 1.13 GiB, ~900 major
  page faults/second sustained, generation at **0.5–0.6 tok/s** (vs the
  ~5–15 tok/s this hardware normally does on phi3-mini q4_0) while
  pinning a full core on zram fault-in.
- **User-visible impact tonight:** 13 `generation_timeout` entries in
  `llm_load_fallback_events.jsonl` between 22:00 and 23:00 EDT (the only
  entries in the log), plus the demo runner logging repeated 15 s
  read-timeouts against the web API during the same window.

Contributing load, not root cause: the known `ultrafeeder` crash-loop
(CLAUDE.md known-bad — the ADS-B dongle is physically absent from the USB
bus; still failing tonight, exit 125 every few seconds) keeps churning
podman create/rm cycles, and the interactive desktop session adds external
memory pressure — exactly the pressure `MemoryLow` existed to defend
against.

Not remediated by this pass (adding resource stanzas to live units is an
operator design decision — the 8/27 near-OOM incidents show naive limits
on a 16 GB box running two resident servers can fail the *other*
direction). Flagged for operator: port `MemoryLow`/`MemorySwapMax=0`
thinking (re-baselined for llama-server's actual footprints) onto
`corporatetraveldc-llama-hot/chat.service`, or accept that the hot tier is
only warm when nothing else wants RAM.

## Finding 2 — REAL, latent: none of the four llama units is enabled; a reboot silently kills all local LLM inference

All four `corporatetraveldc-llama-*.service` unit files (tracked *and*
installed copies) have correct `[Install] WantedBy=default.target`
sections, but `systemctl --user list-unit-files` shows all four
**disabled** — `systemctl --user enable` was never run. hot/chat are
running only because they were started manually on 2026-08-27
(19:07/19:11 EDT). Linger is on, so enabling would work; as things stand,
the next reboot or user-manager restart brings the box up with **zero
llama-server processes**: every skill quietly degrades to deterministic
fallback (`ANTHROPIC_FALLBACK_ENABLED=false`, so there is no cloud
safety net) and the Dispatch Drawer chat breaks. Same failure class as
the research-board-mirror timer-never-installed bug fixed earlier this
session — written but never activated. (report-1/2 staying disabled may
be deliberate per Finding 4; hot/chat almost certainly not.)

## Finding 3 — Doc drift from this commit (the expected kind)

- **README.md — the big one.** The entire "Local LLM — Ollama" section
  and every reference radiating from it is now describing a retired
  stack: `OLLAMA_BASE_URL=…:11434` on the host (nothing listens on 11434;
  `ollama` binary is gone from PATH), "21 dedicated `corporatetraveldc-pi5-*`
  Modelfile models" (now one persona registry, `src/common/personas.py`,
  two resident llama-servers on ports 8093/8094), LOCKDOWN stopping "host
  `ollama.service`" (removed from the guard 2026-08-27 — see
  `scripts/thermal-ingest-guard.py` header), the `install-ollama.sh`
  bootstrap step (file deleted by this commit), `ollama.service.d`
  resource-governance description (see Finding 1 — the governance itself
  is gone from the live stack), and the `/api/ask` chat being "Ollama
  chat". Not rewritten by this pass per its own ground rules; logged
  here.
- **`src/ingest/README.md:58`** — LOCKDOWN row still lists "host
  `ollama.service`" in the stop set. The code (`_lockdown_stop_stack()`)
  deliberately stopped touching it on 2026-08-27; the README row was not
  updated.
- **`src/shared/watchlist_README.md`** — unaffected (zero LLM claims;
  nothing in this commit touches watchlist logic). Verified, no drift.
- **CLAUDE.md** — its known-bad section already reflects the cutover and
  the ultrafeeder hardware fault; still accurate. (Write-only file per
  its own header; nothing needed.)
- **Wider docs/** (out of this pass's named scope but noted for a future
  sweep): `INFRA_MAP.md` (37 Ollama mentions), `DEDICATED_MODELS_PLAN.md`
  (30), `COMPLIANCE_SECURITY.md` (16), `SECOND_BRAIN_STATUS.md` (10),
  `HARDWARE_GUIDANCE.md` (8), `COST_STRUCTURE.md` (7) all still describe
  the Ollama architecture. The dated reports/benchmarks are historical
  snapshots and correctly left alone.

## Finding 4 — Clarifications that are NOT bugs (so the next pass doesn't re-flag them)

- **`llama-report-1/2.service` inactive is deliberate.** `llm.py`
  (`ollama_post_with_retry()`, ~line 890) routes report-tier calls to the
  chat port after two near-OOM incidents on 2026-08-27; the operator
  explicitly accepted report/chat queueing on one slot. Consequence:
  `llama_pool.claim_port()` and `PoolBusyError` are currently dead code,
  and `llama_pool.py`'s docstring calling report ports "permanent,
  always-resident" describes the shelved design, not the running one.
  Revisit was planned "in a calmer session" — Finding 1 is a prerequisite.
- **The three deleted `.timer` files are benign.** `daily-opsplan` /
  `freshness-audit` / `weekly-summary` timers deleted from
  `.config/containers/systemd/` were byte-identical misplaced duplicates
  (systemd never loads `.timer` from the quadlet dir); the canonical
  copies in `.config/systemd/user/` remain tracked, enabled, and fired
  normally today.
- **`OLLAMA_BASE_URL` is now a pure feature flag.** No code derives a URL
  from it anymore (llama_pool has its own host/ports), but
  `generate()` still gates the *entire* local-inference path on it being
  non-empty. Unsetting it in `dispatch.env` — a natural-looking cleanup —
  would silently disable all local LLM inference. Needs either a rename
  or a loud comment in `dispatch.env` before anyone tidies it.

## Finding 5 — `build-models.sh` was broken by the rename, and the commit message overstates one thing

- The commit's `build-models.sh` edit replaced `ollama
  create/cp/rm/list` with `llama create/cp/rm/list` — **no `llama` CLI
  exists** (only `/usr/bin/llama-server`; llama.cpp has no model
  registry). The script cannot run in any environment now. Either it
  should have been retired outright alongside `install-ollama.sh`
  (personas.py supersedes Modelfile *builds*), or the rename was a blind
  sed. Note the 21 root-level `corporatetraveldc.<skill>` Modelfiles must
  NOT be deleted with it yet: `llm.py:_verify_before_inference()` still
  verifies them against the signed manifest on every inference call.
- Minor: the commit message says "ollama.service.d resource limits
  retired accordingly", but the tracked file was actually *updated*
  (CPUWeight 500→5000 with the 2026-08-27 rationale), remains tracked,
  and its live copy is still installed under
  `/etc/systemd/system/ollama.service.d/` for the now-disabled unit.
  Harmless today; misleading for archaeology (and Finding 1 makes that
  file's content the reference for what the new units are missing).

---

**Persisted to second brain:** Findings 1, 2, and 5 (the real,
non-trivial items) via `remember_text()`, tagged for future passes; this
file records the full detail. Doc-drift items (Finding 3) are repo-state,
appropriately tracked here only.

---
---

# PASS 2 — same date, post-commit 4664821 (priority/watchdog-coordination commit)

Separate check, run after commit `4664821` ("Raise CPU/reliability
priority for alert-delivery and ingress infra; coordinate ingest-restart
with thermal guard") — five files: `ntfy.container`,
`cloudflared.service`, `scheduled-ingest-restart.sh`,
`thermal-ingest-guard.py`, `systemd/corporatetraveldc-acarsrouter.container`.
Second-brain prior art consulted first: `20260823T200728Z`
(watchdog-vs-LOCKDOWN restart conflict — the flapping problem this
commit's restart-script backoff now closes from the other side),
`20260823T134303Z` (cross-doc consistency pass), `20260828T042817Z`
(cutover synthesis), plus Pass 1 above, discovered mid-check — Pass 1's
Finding 3 already logs the README/ingest-README Ollama drift, so this
pass does not re-derive it; only genuinely new items below. Verified
against live: `systemctl --user show/cat`, `podman exec … env`,
tracked-vs-installed unit diffs, journald. Nothing staged or committed
by this pass (the pre-existing staged `ALERT_REFERENCE.md` /
`uber-traffic-watch.py` changes in the index are another session's work,
untouched).

## P2-1 — REAL BUG: the acarsrouter port fix landed in a stale duplicate; NOT live

The commit's "unrelated 1-line fix" (airframes.io VDLM2 feed port
5555→5553) was applied to `systemd/corporatetraveldc-acarsrouter.container`
— a **stale legacy reference copy**, not the authoritative quadlet. The
repo ALSO tracks `.config/containers/systemd/corporatetraveldc-acarsrouter.container`,
which is byte-identical to the installed unit in
`~/.config/containers/systemd/` — and **both still say
`feed.airframes.io:5555`**, as does the running container's environment
(verified `podman exec corporatetraveldc-acarsrouter env`). Net effect:
the port fix is not in effect anywhere live, and the repo now carries two
contradictory tracked copies of the same unit.

How stale the `systemd/` copy is: it still has
`EnvironmentFile=…/dispatch-secrets.env` (removed live by the 2026-08-26
Opus C-4 secrets-scoping fix), and lacks the `Memory=1536m`/
`--memory-swap` caps, the whole production.slice/MemoryLow/CPUWeight/
CPUQuota governance block, the 2026-07-28 boot-stagger `WantedBy`
removal, and the `Network=acars-net.network` suffix. All three
`.container` files under `systemd/` that have `.config/containers/systemd/`
counterparts diverge this way (`acarsrouter`, `acars-watcher`,
`dumpvdl2`) — the `systemd/` tree is a stale SDR-era snapshot, the same
trap class as the known `amtrak-tracker.container` divergence in
CLAUDE.md's 2026-08-29/30 notes.

Not remediated here (hard no-commit rule, and porting the fix into the
authoritative copy + restarting a live feeder is a deploy action). When
picked up: apply 5553 to the `.config/containers/systemd/` copy, install,
restart the container — and decide whether `systemd/`'s three stale
container files get deleted or reconciled so this can't recur.

## P2-2 — Doc drift from this commit: the fallback-count LOCKDOWN trigger is still documented (beyond Pass 1's ollama.service rows)

Pass 1 Finding 3 flagged the `ollama.service`-in-LOCKDOWN rows; this
commit's *other* guard change is also still documented as current:

- `README.md` §"SWIM feed liveness and thermal load-shedding": the
  LOCKDOWN table row still lists "**or** `>= 2` load-attributed brief
  fallbacks in 300 s" as a trigger (demoted 2026-08-27 to
  informational-only — it can no longer shed anything), and the Restore
  row still requires "fallback count `< 2`" (removed from the resume
  gate; resume is now temp + load only). The lines-202-206 description of
  the fallback signal's attribution rule is still accurate as what gets
  *logged*, but its stated purpose ("so a deliberately-stopped Ollama can
  never cause ingest to shed itself") is moot now that no fallback count
  can shed anything.
- `src/ingest/README.md:58`: same row, same stale "≥2 load-attributed
  brief fallbacks" trigger (on top of the `ollama.service` mention Pass 1
  already flagged).

Everything else in those sections re-verified accurate against the new
script: tier-1 74 °C → `tfms,stdds`; LOCKDOWN at 79 °C / load1 ≥ 40;
resume temp < 65 ∧ load < 15 held 300 s; state-file path; journalctl
commands; "inactive(dead)+Result=success is expected" guidance. Both
watchdog timers `ExecStart` the repo working-tree scripts directly
(verified `systemctl --user cat`), so the committed guard/backoff logic
is what actually runs — no deploy step was needed for those two.

## P2-3 — Doc drift from this commit: CPUWeight-tier claims (partial)

- `docs/GUARDRAILS_JUSTIFICATION.md` §3: "the app-class containers are at
  `CPUWeight=100` (nextcloud-app excepted, below)" — ntfy is now a second
  exception at 9000. (The `CPUQuota=300%` row covering ntfy is still
  correct; only weight changed.) Same section's "`CPUWeight=500`" claim
  for the Ollama drop-in is Pass-1-class cutover drift (live and tracked
  are now 5000, unit disabled) — noted here because Pass 1's wider-docs
  list didn't name this file.
- `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`: the "Per-container
  `CPUWeight`/`CPUQuota` | 100 / 300%" baseline row now has the ntfy
  exception, and the `CPUWeight=10000` tier (documented as pihole-FTL/
  unbound/tailscaled only) gained a member: `cloudflared.service` (user
  scope, now `Slice=production.slice`, weight 10000).

Live-verified as deployed and in sync: `ntfy.service` active,
`CPUWeight=9000`; `cloudflared.service` active, `CPUWeight=10000`; both
installed units byte-identical to their tracked `.config/` copies. No
live-vs-tracked drift on these two.

## P2-4 — Still accurate / no action

- `docs/DATA_SOURCES.md` ~line 76 on `scheduled-ingest-restart.sh`
  (per-container thresholds/cooldowns): still true; the new
  guard-active/post-restore-grace skip is an addition, not an
  invalidation of anything written.
- `src/shared/watchlist_README.md`: no claims touched by this commit.
- CLAUDE.md known-bad section: consistent with this pass; its
  acarsrouter entry even names the `systemd/` path — P2-1 is the
  consequence of that path being the wrong copy to fix.

## P2-5 — Side observation (not doc drift)

`corporatetraveldc-transport-pattern-digest.service` failed today
12:53 EDT with `Result=timeout` (SIGKILL after ~28 min wall, 815 MiB
peak) — a **different failure mode** than the "expected, self-resolving
verify-manifest INTEGRITY FAILURE" classification CLAUDE.md's 08-28
notes gave that unit. Not investigated this pass; noting only that the
expected-failure label no longer explains the current failure.
`corporatetraveldc-docs-drift-weekly.service` remains the known,
unrelated 08-24 bare-exit-1 failure, unchanged.

---

**Pass 2 persisted to second brain:** P2-1 (the real bug) with the
`systemd/`-tree staleness context and the P2-5 observation, via
`remember_text()` (`--author-kind agent`). Doc-drift items P2-2/P2-3 are
repo-state, tracked here only, matching Pass 1's convention.

---
---

# PASS 3 — same date (~23:00+ EDT), post-commit 4479b05 (third-party-lookup purge / watchlist identity + OOOI authority)

Separate check after commit `4479b05` ("Remove all third-party
position-lookup calls; harden watchlist identity and OOOI-authority
handling"). Second-brain prior art consulted first:
`20260828T042817Z` (session synthesis — airplanes.live purge decision,
backpressure removal) and `20260828T042841Z` (watchlist OOOI/hex/tail
standing rules) cover the *decisions* this commit implements; the doc
drift below is simply the docs never having been told. Pass 1 Finding 3
and P2-2 already own the Ollama/LOCKDOWN drift — not re-derived here.
Verified against live: `systemctl --user`, `podman ps`/`inspect`,
`journalctl --user`, live sqlite schema, env-file key presence
(names only, values never read), `curl localhost:8000`. Nothing staged
or committed; working tree left as found (pre-existing staged
research-board-mirror fix + unstaged CLAUDE.md/MANIFEST edits untouched).

## P3-0 — Deployment context for "live-verified" claims below

Long-running containers (poller/pusher/web/ingest-*) are up ~47 h — they
predate this commit's finalization. But the purge itself is already live
in web: `curl localhost:8000/api/v1/adsb` returns `{"source":"local",…}`
(host ultrafeeder proxy, no airplanes.live). And the live DB **already
has the SCHEMA_V39 columns** (`hex_source`/`hex_updated_at`/
`hex_corroborated_at` present in `watchlist_entries`) — per-run skill
containers use the newer image (build-date 2026-08-29T06:46Z) and have
run the migrations. Mixed state: the long-running services execute
pre-commit code until their next rebuild+restart. `ultrafeeder` remains
down (known hardware fault, CLAUDE.md) — so post-purge the primary
live-position source contributes nothing until the dongle is physically
restored; `/api/v1/adsb` live-returns `count:0` + connection-refused to
host:8080. FDPS SWIM carries watchlist position/identity work meanwhile.

## P3-1 — Doc drift from this commit (repo-state, tracked here only)

1. **README.md:282** — `/api/v1/adsb` described as "airplanes.live proxy
   (250 NM of KDCA, 30 s cache)". Live-verified wrong: it now proxies
   the local ultrafeeder `aircraft.json` (`"source":"local"`).
2. **README.md:413-415** — "Flight monitoring source chain: FlightAware
   AeroAPI (if key set) → airplanes.live (free) → local UltraFeeder
   ADS-B → FDPS push cache → schedule inference". airplanes.live is
   purged; the chain is now AeroAPI (branch survives in code, inert —
   see P3-3) → local ADS-B → already-ingested FDPS SWIM → schedule
   inference. The "phases never revert" OOOI claim is still true, now
   reinforced by forward-only
   `update_watchlist_oooi_phase_authoritative()`.
3. **docs/DATA_SOURCES.md ~136-157 (backpressure valve section)** —
   documents `_engage_ollama_backpressure()` / `OLLAMA_BACKPRESSURE_*`
   and asserts it is "live on this box, and `bandwidth_priority=ollama`
   is the common everyday cause". Removed entirely 2026-08-27/28:
   functions gone from `llm.py`, `set_bandwidth_priority()` no longer
   accepts `ollama` at all (`swim_client.py:304-313`), so the example
   `suspended: bandwidth_priority=ollama` string can no longer occur.
   The **weather** auto-trigger and manual mode remain accurate.
4. **docs/DATA_SOURCES.md ~678-688 (airplanes.live entry)** — "Used as
   primary FlightAware fallback for watchlist tracking" → no longer used
   for any lookup. Still true and worth keeping distinct: ultrafeeder
   *feeds* airplanes.live outbound, and `globe.airplanes.live/?icao=`
   click-through links in pushes are deliberately kept.
5. **docs/REFERENCE_INFRA.md:174** — "Flight tracking defaults to the
   free `airplanes.live` API, with an optional FlightAware AeroAPI
   fallback tier". Wrong twice: airplanes.live is gone, and AeroAPI was
   always the *first* tier when keyed, not the fallback.
6. **docs/dispatch-runner-design.md:116** — `/api/adsb/live` "Proxy →
   airplanes.live v2 (250 nm of KDCA)". `runner/main.py::adsb_live()`
   now serves the local receiver-range snapshot (synthetic in
   DEMO_MODE), never airplanes.live. The `/map` row (":157 LIVE
   airplanes.live") inherits the stale label.
7. **src/shared/watchlist_README.md:196-219 (identity resolution)** —
   two invalidated claims: the resolve chain "local FAA/OpenSky
   registries before airplanes.live's flaky `/v2/reg/`" (no third-party
   query remains), and "hex-authoritative-once-known" listed as a
   false-positive *protection* — which is exactly the behavior
   SCHEMA_V39 identified as the un-catchable-wrong-hex bug and gated
   behind the new corroboration pass (provenance columns + one real
   callsign-based local match required). Section needs a rewrite against
   the 08-27/29 code. The identity_resolved one-shot push + globe
   click-through description is still accurate.
8. **src/shared/watchlist_README.md / src/ingest/README.md — missing the
   new OOOI/TBFM wiring.** Neither mentions
   `update_watchlist_oooi_phase_authoritative()` (TFMS airline-reported
   OUT/OFF/ON/IN now advances live `oooi_phase`; previously
   analytics-table-only) nor TBFM's first per-flight watchlist
   connection (`update_watchlist_tbfm_status()` + `watchlist_event_hit`,
   30-min dedup). `src/ingest/README.md:139`'s TBFM row ("Live —
   `tbfm_sequences`, metering alerts") is now incomplete.
9. **docs/ALERT_REFERENCE.md** — the `flight-alerts` publishers row
   (last corrected 2026-08-19) is missing the new `tbfm_parser`
   watchlist-hit publisher, and the `tbfm-alerts` row's
   aggregate-metering-only framing predates the per-flight leg. The
   :314-319 identity-resolved description (globe link kept, body-only)
   re-verified still accurate.
10. **README.md:626** — "**V36** is the top as of 2026-08-23" → now
    `SCHEMA_V40`. Minor: the sentence already tells readers to re-run
    the grep instead of trusting the number.
11. **Stale docstrings inside files this commit itself touched** (code
    not docs/, but they contradict the commit): `src/poller/main.py:434-435`
    sweep docstring still lists "2. airplanes.live (free, no key needed —
    primary live source)" and the `:468` comment calls airplanes.live
    "the OOOI-phase source"; `src/web/main.py:16` module docstring still
    calls `/api/v1/adsb` an "airplanes.live proxy".
    (`_check_flight_airplanes_live()`'s own docstring *was* updated and
    explains the kept-for-diff-size name.)

## P3-2 — REAL, now confirmed chronic (upgrades P2-5): transport-pattern-digest has zero successful runs in ≥14 days

P2-5 noted today's `Result=timeout` as unexplained; investigated this
pass. Journal (14-day window) shows **every** run failing: `timeout` on
08-26 00:53, 08-27 12:53, 08-29 12:53 (and the ~00:53 instance), and
`exit-code` on 08-28 00:25, 08-28 12:25, 08-29 00:25 — no
"Deactivated successfully" anywhere in the window. The exit-code ones
plausibly *were* the signed-manifest gate as CLAUDE.md's 08-28 whitelist
says; the timeouts are a distinct chronic failure CLAUDE.md's
"expected/self-resolving" classification does not cover:
`TimeoutStartUSec=26min 40s` while the run takes >28 min wall
(2 min 41 s CPU, 815 MiB peak — i.e. ~90 % waiting, consistent with
Pass 1 Finding 1's thrashing chat-tier LLM), so systemd SIGKILLs it
mid-run every time (today's kill escalated SIGTERM→SIGABRT→SIGKILL).
Needs an operator decision: raise `TimeoutStartSec`, make the digest
cheaper, or fix the underlying LLM starvation (Pass 1 Finding 1) first —
it is NOT self-resolving. Persisted to second brain.

## P3-3 — REAL nuance worth recording: "all third-party position-lookup calls removed" holds only by env-var absence

The commit title is absolute, but `_check_flight_aeroapi()`
(`poller/main.py:628`, queries `aeroapi.flightaware.com` for position)
survives and is still the sweep's first branch when
`FLIGHTAWARE_AEROAPI_KEY` is set. It is inert today only because that
var is set in **neither** dispatch.env nor dispatch-secrets.env. A
*different* var, `FLIGHTAWARE_API_KEY`, **is** set (non-empty) in
dispatch-secrets.env and feeds the untouched FIDS arrivals path
(`web/routes/fids.py` → `common/flight_resolver.py`) — airport-board
data, not position lookup, so not a violation of the standing rule, but
"everything is local" currently rests on one unset env var plus that
scope distinction. Future passes should not read the surviving AeroAPI
code as drift (it's a deliberate keyed tier), nor assume the purge
removed it. Persisted to second brain alongside P3-2.

## P3-4 — Still accurate / no action

- `docs/DATA_SOURCES.md` weather backpressure trigger
  (`nws.py::_maybe_set_weather_priority`), `_LOW_PRIORITY_FEEDS`, and
  swim_client pause mechanics (minus the removed ollama mode).
- `docs/GPS_COORDINATE_CONFIGURATION.md` (globe iframe is tar1090-served
  local data, not a lookup) and DATA_SOURCES' LADD note (airplanes.live
  as an *independent receiver network*) — still correct as written.
- watchlist_README's permanent/transient mechanics, REST route table,
  sweep table, ntfy routing, dedup, and delay-extension (`SCHEMA_V37`)
  sections — untouched by this commit.
- The three units today's regenerated `CLAUDE_MD_DRIFT_REPORT.md`
  (05:15 EDT, updated by this commit) lists as failed/crash-looping
  (`daily-opsplan`, `freshness-audit`, `pull-path-verify`) are all
  `inactive` (not failed) now — recovered since generation. Only
  `docs-drift-weekly` (known 08-24 bare exit 1) and
  `transport-pattern-digest` (P3-2) are currently failed.

**Pass 3 persisted to second brain:** P3-2 and P3-3 via
`remember_text()` (author_kind=agent). Doc-drift items P3-1 are
repo-state, tracked here only, matching the prior passes' convention.

---
---

# PASS 4 — same date (~23:10+ EDT), post-commit 5eb4e28 (Uber endpoint-anomaly watch consolidation)

Scope: commit `5eb4e28` ("Consolidate Uber endpoint-anomaly watch:
blocked-hit frequency + discovery-pattern tracking") — two files:
`scripts/uber-traffic-watch.py` (+216 lines) and one row of
`docs/ALERT_REFERENCE.md`. Second-brain searched first (literal +
`--semantic`) for uber / endpoint-anomaly / pihole / denylist prior art:
**zero notes** — this specific area has never been investigated before;
checked cold, nothing to build on or contradict. (Pass 2 above noted
these exact changes sitting staged as "another session's work,
untouched" — this pass is the first look at their content. Pass 3, a
concurrent session's check of 4479b05, landed in this file mid-write;
no overlap with this commit's surface.) Read-only except this file.
Nothing staged, committed, or signed by this pass.

## P4-1 — Verified live end-to-end: the consolidated watch is deployed and working

- `corporatetraveldc-uber-traffic-watch.timer` fires every 2 min;
  `ExecStart` runs the working-tree script directly, so the committed
  code is live with no deploy step (same pattern P2 confirmed for the
  watchdog timers). Tracked `.service`/`.timer` byte-identical to the
  installed copies. Runs exit 0, ~1 s, 18 MB peak; no errors in journald.
- **All four `TRACKED_ANOMALY_ENDPOINTS` domains are actually denylisted**
  in gravity.db (`type=1, enabled=1` — verified read-only via
  `sg pihole`), so the new priority-5 denylist-gap alert is silent. It
  fired exactly once, 22:32:25 EDT, for the two new domains
  (`lens.usercontent.google.com`, `rr1---sn-p5qs7nzr.gvt1.com`) and was
  quiet by the 22:34 pass — the operator denylisted them within one
  timer interval, i.e. the alert-loudly-with-the-exact-command design
  did its job on its first live firing. (Note the check has no cooldown:
  an unresolved gap would alert at priority 5 every 2 minutes. Reads as
  deliberate per the docstring; recorded so a future pass doesn't flag
  the repetition as a bug.)
- **`STATUS_DENYLIST = 5` is correct, validated against real data:** the
  two 2026-08-12 domains carry 33 + 36 status-5 rows in pihole-FTL.db
  post-denylisting (latest 2026-08-27) — the phone genuinely does keep
  querying them, which is exactly the phenomenon the new tracker
  measures.
- `blocked_hit_counts` empty in the state file is **consistent, not a
  bug**: every hit on the two new domains in the last 48 h predates
  their 22:32–22:34 denylisting (statuses 2/3/17 —
  forwarded/cache/stale), and nothing has queried any tracked domain
  since the new code deployed. Counters start at deploy; the 69
  historical status-5 hits are pre-history the tracker will never
  include.
- State-file schema migrated cleanly via `setdefault` — all four new
  keys present alongside the 4,674-domain `seen_domains` baseline.
- The public-twin claim checks out: `gig_mobility/endpoint_anomaly.py`
  exists in `/opt/corporatetraveldc/public/agentic-management-tooling-mcp`
  with all four capabilities, and the private script imports only stdlib
  (json/os/subprocess/time/urllib) — separate implementations exactly as
  both docstrings claim.

## P4-2 — Doc nits from this commit (minor; file-only, no second-brain note)

- **"mean/median" overstates the private script.** The commit message,
  the updated `docs/ALERT_REFERENCE.md` row, and the script's own
  docstring (line 33) all say the discovery-gap stats are "mean/median
  days"; `track_discovery_pattern()` in `uber-traffic-watch.py` computes
  **mean only** (`mean_gap_days`, `days_since_last`). The public twin
  *does* compute `median_gap_days` (`statistics.median`) — the language
  was copied from the twin, the median implementation wasn't. One-word
  doc fix or a three-line code addition, whichever is intended.
- **The "seeded with the real historical dates" seed lives only in
  unversioned live state.** `anomaly_discovery_log` in
  `/var/lib/corporatetraveldc/uber_traffic_watch_state.json` was
  hand-seeded (four entries; the 08-12 pair share one hand-picked round
  timestamp) — the code never reads `TRACKED_ANOMALY_ENDPOINTS`'
  `discovered` dates (its own comment says "purely informational, not
  read by any logic here") and after a state-file loss it re-seeds only
  `seen_domains`. A wiped state file silently restarts gap stats from
  zero and makes the alerts' "discovery #N" numbering wrong, with
  nothing in the repo to rebuild from except this note and the
  constants' informational dates.
- **Latent, minor:** a *future* new cname-drift anomaly re-alerts on
  every reconnect burst until it's added to the
  KNOWN_BLOCKED/TRACKED_ANOMALY_ENDPOINTS constants (pre-existing
  behavior), and the new code appends a **duplicate
  `anomaly_discovery_log` entry on each of those re-alerts** (the domain
  never enters the constant at runtime), inflating gap stats. Doesn't
  affect the four current entries; will matter the day discovery #5
  arrives on a chatty domain.

## P4-3 — Everything else checked: no drift

- `docs/ALERT_REFERENCE.md`: the commit's own row edit is otherwise
  accurate (priority 4 default / 5 on ANOMALY or gap ✓, novel-in-burst
  4 ✓, hand-rolled `ntfy_alert` to `/ops-health` ✓, gravity.db
  read-only + no-privilege design ✓); the `ops-health` publishers row
  (~line 176) already listed `uber-traffic-watch.py` and needed no
  change.
- `README.md`, `src/ingest/README.md`, `src/shared/watchlist_README.md`:
  zero Uber/Pi-hole/denylist/gravity claims in any of them — nothing
  this commit could have invalidated. Verified by grep, not assumed.
- CLAUDE.md known-bad section: nothing touching this area; no
  contradiction. (`gig-economy-daily-watch` there is the unrelated LLM
  daily-watch skill, not this script.)
- Dated docs/ snapshots mentioning the old script shape
  (LIVE_STATE_CHECK_2026-08-12 etc.) are historical records, correctly
  left alone per every prior pass's convention.

**Pass 4 second-brain persistence: intentionally none.** The bar (per
the task and Passes 1–3) is a REAL, non-trivial drift or bug; P4-2's
items are a one-word doc overstatement, a state-seed fragility note,
and a latent stats-inflation edge — all recorded here, none
vault-worthy. The feature itself verified working end-to-end.

---

# PASS 4 — same date (~23:10–23:25 EDT), post-commits 5eb4e28 (uber-watch consolidation) + 4f88977 (research-board-mirror fix)

Covers the final two commits of tonight's session, neither checked by
Passes 1–3. Second-brain search ran first: hits were the 2026-08-24 notes
(`20260824T110837Z` / `20260824T011337Z`, both asserting the mirror timer
is repo-only/never-installed — now superseded, see P4-1) and tonight's
own `20260830T030328Z` (Pass 1–3 findings; mentions the mirror bug only
as a class analogy, not its fix). No prior vault note covers the uber-watch
consolidation. Read-only except this file and one superseding
second-brain note. Nothing staged, committed, or signed by this pass.

## P4-1 — Doc drift from 4f88977: `docs/INFRA_MAP.md` still says the mirror timer is repo-only / never installed (two places)

The fix commit + tonight's live `systemctl --user enable --now` invalidate
two INFRA_MAP claims that were accurate when written:

- The 62-vs-63-gap paragraph (§4, ~`docs/INFRA_MAP.md:189-193`):
  "`corporatetraveldc-research-board-mirror.timer` exists **only** in the
  repo … Either install it or drop the repo copy." The
  install-it-or-drop-it decision has now been made (installed).
- The "Repo-only (staged, **not installed**)" unit list
  (~`docs/INFRA_MAP.md:841`): still lists the timer as its first entry.

Verified live this pass: timer loaded from
`~/.config/systemd/user/`, **enabled**, active/waiting, firing every
15 min (last trigger 22:58 EDT, next 23:13); both the `.timer` and the
`.container` are byte-identical repo↔live (`diff` clean); the 22:58 run
completed successfully ("0 mirrored, 0 unchanged, 0 blocked (of 0
items)"). No other living doc makes the stale claim — remaining mentions
are dated snapshots (older LIVE_STATE_CHECK files,
INVESTOR_MATERIALS_REVERIFICATION) which are historical records, not
drift, and CLAUDE.md's FIXED paragraph is accurate. Doc not edited by
this pass (per the Pass 1–3 convention of recording repo-state drift here
rather than editing); next INFRA_MAP touch should update both spots.

Persisted to second brain (exception to the repo-state-only convention,
deliberately): the vault currently holds two 2026-08-24 notes asserting
"timer repo-only/never installed" and nothing searchable saying it is
fixed — a future pass searching before checking live would conclude the
opposite of reality. A short superseding note went in via
`remember_text()` (author_kind=agent).

## P4-2 — 5eb4e28 (uber-watch): no drift; commit claims verified live

- `docs/ALERT_REFERENCE.md`'s `uber-traffic-watch.py` row was updated in
  the same commit; verified against the script — `TRACKED_ANOMALY_ENDPOINTS`
  (both 2026-08-29 domains present), `STATUS_DENYLIST = 5`, read-only
  denylist-gap check at priority 5, and the public-repo-twin caveat all
  match `scripts/uber-traffic-watch.py` as committed.
- The unit's `ExecStart` runs the repo script **in place**
  (`/usr/bin/python3 /opt/…/ctdi-dispatch-internal/scripts/uber-traffic-watch.py`),
  so the committed changes are live by definition — no tracked-vs-installed
  copy to diverge. Running cleanly every 2 min since the commit (23:00
  through 23:10 runs all finished, ~1 s each, no error output).
- The commit message's "Denylisted both" verified directly against
  `/etc/pihole/gravity.db` (read-only): `lens.usercontent.google.com` and
  `rr1---sn-p5qs7nzr.gvt1.com` both present as exact-deny (type 1),
  enabled. Consistent with the script's denylist-gap check staying quiet.
- No other doc surface (README.md, CLAUDE.md, `src/ingest/README.md`,
  `src/shared/watchlist_README.md`, ALERT_ARCHITECTURE.md, DATA_SOURCES.md)
  mentions the uber watch in a way these changes could invalidate.

## P4-3 — Still accurate / no action

`src/ingest/README.md` and `src/shared/watchlist_README.md` are untouched
by either commit's subject area — nothing in them references the mirror
skill or the uber watch. README.md likewise. The only drift from these
two commits is the single INFRA_MAP item above.

---

# Addendum — post-commit d5e5753 pass (~23:52–23:56 EDT)

Scope: commit `d5e5753` ("Rework build-models.sh as a Modelfile/personas.py
sync-verify tool, not a broken Ollama build") — build-models.sh rewritten
from a (broken) build/smoke/promote script into a manifest-gated
Modelfile↔personas.py SYSTEM-block diff tool, plus a comment-header cleanup
in all 21 `corporatetraveldc.<skill>` Modelfiles. Second-brain search ran
first: this commit is the direct resolution of finding 3 of THIS FILE's own
earlier pass (persisted as `01-Sources/manual/20260830T030328Z.md`,
"build-models.sh was broken by the cutover commit… retire it or fix it").
That finding is now CLOSED — resolution: repurposed, not retired. The
README/ingest-README Ollama-era staleness that note also recorded is
already-known drift and is not re-derived here; only what d5e5753 itself
changed is assessed below. Read-only pass except this file and one
second-brain note. Nothing staged, committed, or signed.

## D5-1 — REAL doc drift (new in kind): README.md's entire build/smoke/promote narrative for build-models.sh now describes a script that no longer exists

The earlier pass logged the Local-LLM section as generically Ollama-stale;
d5e5753 changes *what kind* of stale it is. The section can no longer be
fixed by a mechanical Ollama→llama rename, because the build pipeline it
documents was removed outright, not renamed. Specific passages now false:

- **Install sequence (README.md:531–532):** "Build the dedicated Ollama
  models (SWA-guarded, smoke-gated) / `bash build-models.sh`" — there is
  no build step. On a fresh box this command would verify text sync and
  build nothing; the actual restore items are the shared GGUF + the
  `corporatetraveldc-llama-*` units.
- **"Why phi3:mini" section (README.md:685–707):** "`build-models.sh` now
  has: a hard `SWA_DENYLIST_REGEX` guard … a smoke-test promotion gate:
  brief models build as `:candidate` … within `SMOKE_BUDGET_S` … promoted
  to `:latest`" and the pinned reference "`build-models.sh:125` →
  `SMOKE_BUDGET_S=…`". None of these exist in the reworked script — no
  SWA denylist, no candidate/promote, no smoke test, no `SMOKE_BUDGET_S`
  (grep: 0 hits); line 125 now lands inside the Python compare heredoc.
  Same for "The same fix is in `build-models.sh`'s smoke test"
  (orphaned-generation note) and "Don't tighten it back down without
  reading that rationale in `build-models.sh` first" — the rationale text
  is gone from the script.
- **"Rebuilding models" (README.md:724):** "verifies signed manifest,
  applies guards, builds all 21" — it verifies the signed manifest (still
  true, gate kept) and *diffs* all 21; builds nothing.
- **Model-table callout (README.md ~677):** "20 of the 21 models are
  brief-class (guarded candidate/smoke/promote build)" — no such build
  distinction exists anymore; chat's only remaining distinction is
  persona/sampling-level, in personas.py.

Not fixed by this pass (drift-log only, per this file's convention); the
section needs a rewrite as part of the already-open README llama.cpp
refresh, not a spot-patch.

Lesser, same commit:

- **docs/INFRA_MAP.md:515** — "called again at the end of build-models.sh
  on every successful model deploy": the mechanism is still true
  (`scripts/post-commit-doc-verify.sh deploy` still fires at script end,
  build-models.sh:184) but it now fires after a successful *verify pass*;
  "model deploy" is no longer a thing. Wording-level only.
- **docs/PI5-BOOT-CONFIG.md:166** — rebuild checklist item "Ollama +
  `build-models.sh`": already-known Ollama-cutover class, but note the
  checklist is now doubly wrong — build-models.sh would restore nothing on
  a fresh box.

## D5-2 — Commit's own claims verified live; drift-checker cross-check intact

- "Verified live: all 21 match": independently re-verified by replicating
  the SYSTEM-block/`build_system_prompt()` comparison read-only in Python
  (same whitespace-normalization) — **21/21 MATCH**, and
  `common.personas.PERSONAS` has exactly 21 entries, bijective with the
  Modelfile set.
- "MODELS map … otherwise unchanged; check-claude-md-drift.sh parses it":
  confirmed — the checker's literal `sed`/`grep` parse of the
  `declare -A MODELS` block still counts 21, and a full
  `scripts/check-claude-md-drift.sh` run ends `[OK] CLAUDE.md matches
  live state` with no drift lines.
- README.md:75's "21 dedicated models, all `FROM phi3:mini`" survives the
  Modelfile edits: only comment headers changed;
  `grep -h '^FROM' corporatetraveldc.* | sort | uniq -c` → `21 FROM
  phi3:mini`, unchanged.
- The new Modelfile header comments are accurate as written (spot-checked
  `corporatetraveldc.chat`: correctly states Ollama retired 2026-08-27,
  file kept as canonical source text, sync-verified by build-models.sh).

## D5-3 — Incidental live finding: the reworked verify tool is currently blocked by an unsigned, uncommitted (but deployed-live) ntfy nginx WebSocket fix

`bash build-models.sh` currently exits 5 at its whole-tree integrity gate:
`verify-manifest.sh` fails on `nginx/conf.d/ntfy.example.com.conf`,
which has an uncommitted working-tree edit made 23:52:27 EDT — 36 s after
d5e5753 landed. The edit is a WebSocket-upgrade fix (conditional
`map $http_upgrade` Connection/Upgrade headers replacing the unconditional
`proxy_set_header Connection ""` that broke newer ntfy mobile builds'
native WebSocket subscription with `WebSocketNotSupportedException`).
Verified: the live `/etc/nginx/conf.d/` copy is byte-identical to the
working-tree version and nginx is active, so the fix is deployed; it is
just not yet signed, committed, or logged (0 second-brain hits for it —
in-flight work from this same session window). This is the documented
expected/self-resolving integrity-failure class: the gate is doing its
job against unsigned content, and build-models.sh becomes runnable again
at the next sign-manifest pass. The read-only replication in D5-2 confirms
no actual Modelfile drift is hiding behind the blocked gate. Flagged only
so the next pass doesn't treat the exit-5 as a bug in the reworked script.

## D5-4 — Still accurate / no action

CLAUDE.md, `src/ingest/README.md`, and `src/shared/watchlist_README.md`
contain nothing about build-models.sh or the Modelfiles that this commit
could invalidate (ingest README's Ollama LOCKDOWN row remains the
previously-logged cutover drift, untouched here). Dated snapshot docs
(`docs/LIVE_STATE_CHECK_2026-08-1*.md`, `DEDICATED_MODELS_PLAN.md`,
`DOCS_REFRESH_2026-08-11.md`) reference the old build flow but are
historical records, not living claims — not drift.
