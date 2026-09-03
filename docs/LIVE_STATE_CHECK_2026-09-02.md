# Live State Check — 2026-09-02 (post-commit d94817b)

Doc-drift check scoped to commit `d94817b` ("ops-brief context-budget fix,
email routing for weekly/second-brain-daily/weekly reports, IPv6-flakiness
CLAUDE.md drift reconciliation"), run ~09:11–09:15 EDT, minutes after the
commit landed. Checked README.md, CLAUDE.md, docs/ (ALERT_REFERENCE.md,
ALERT_ARCHITECTURE.md, DATA_SOURCES.md, DEDICATED_MODELS_PLAN.md,
CLAUDE_MD_DRIFT_REPORT.md), src/ingest/README.md,
src/shared/watchlist_README.md — against the diff and the live system
(systemctl --user, podman images, journalctl, verify-manifest).

Prior art consulted first (second-brain search): vault note
`corporatetraveldc/01-Sources/manual/20260902T070747Z.md` (this morning's
root-cause pass) already documents both halves of the headline fix — the
IPv6-tether root cause of the build failures and the ops-brief
exceed_context_size_error outage + the token-budget fix this commit ships,
including the ~1.9–2.1 chars/token trap for aviation-dense text. Nothing
below contradicts it; this check builds on it.

## Drift found (real)

### 1. docs/ALERT_REFERENCE.md — new FDPS meter-fix proximity alert is undocumented

The FDPS watchlist section (~line 204) documents exactly one `TH` (track)
alert path: `_maybe_alert_on_approach`, within 50 nm of the *destination
airport*. This commit added a second, parallel `TH` path,
`fdps_parser._maybe_alert_on_meter_fix_approach`: fires when a watched
flight is within 50 nm of any coordinate-resolved DC-area TBFM meter fix
(the 5 of 10 in `tbfm_parser.DC_METER_FIXES` with confirmed NASR/CIFP
lat/lons — SWANN, RAVNN, FLUKY, WOOLY, PALEO). It writes a
`watchlist_event_hit` (priority 3, trigger `fdps_th_meterfix_approach`)
and fires `_fire_fdps_nas_alert` → `fdps-alerts`/`fdps-<zone>`, with its
own 600 s per-aircraft dedup (`_FDPS_METERFIX_PROX_DEDUP`, distinct from
`_FDPS_PROX_DEDUP`). ALERT_REFERENCE.md is the canonical alert-path
reference and now under-describes a live alert path on the fdps topics.

Related, same section still accurate: the TBFM `check_tbfm_alerts`
description is unaffected — `DC_METER_FIXES` changed shape (frozenset →
dict with coords/None) but the TBFM alert logic itself didn't change.

### 2. docs/ALERT_REFERENCE.md — email delivery leg not reflected (topics table, line ~174)

The `dispatch-debriefs`/`dispatch-ops` row's 2026-08-30 narrative
("run-status ping only; report content stays vault-only") is now
incomplete on the *delivery channel* axis:

- `weekly_summary.py` now passes `email=True` to `send_dual` — the FULL
  weekly summary content (not just a ping) is now also delivered to the
  operator inbox via ntfy's `X-Email` relay (`config.operator_email()`).
- `second_brain_daily.py` / `second_brain_weekly.py` pass `email=True` to
  `send_run_status` — email carries only the "ran OK, report at <vault
  path>" status line; their report content genuinely stays vault-only, so
  that part of the doc's claim still holds for the digest fleet.

Before this commit no code path in the repo ever set `X-Email` (confirmed
in the diff's own audit note). The email leg is opt-in per call, off by
default, so no other topic's behavior changed.

### 3. docs/DATA_SOURCES.md — OpenSky registry section is stale (pre-dated-snapshot)

The "OpenSky Network Aircraft Database" section (~line 750, "Last
verified: 2026-07") describes only the rolling
`aircraftDatabase.csv` URL and says updates "were on hold as of the last
check" — treat as frozen. Since de0f53d (yesterday) and this commit, that
is no longer how the platform consumes OpenSky: real dated monthly
snapshots (`aircraft-database-complete-YYYY-MM.csv`) exist at
`s3.opensky-network.org/data-samples/metadata/`, and
`check_opensky_freshness()` was repointed this commit from the dead
rolling-file HEAD probe (which could never detect a new dated snapshot —
the thing it polled never changes) to a cheap prefix-filtered S3
ListObjectsV2 listing, run monthly by the poller
(`WatchlistSweep.OPENSKY_FRESHNESS_INTERVAL`), importing the ~103 MB file
only when a genuinely new month appears. The doc's access URL, freshness
characterization, and "supplementary/best-effort frozen" framing all
predate this and need updating.

## Minor / cosmetic (noted, not vault-worthy on their own)

- `geo_filter.distance_nm()` (added this commit) is dead on arrival: its
  docstring says it was added so `fdps_parser`'s meter-fix alert uses a
  public shared primitive, but `fdps_parser` still imports and calls
  `_haversine_nm` directly (fdps_parser.py:60, :1920). Zero callers.
  Harmless — the primitive *is* shared, just via the private name — but
  the wrapper's stated reason for existing is false.
- `docs/DEDICATED_MODELS_PLAN.md` mentions ops-brief Modelfiles — already
  historical (Ollama-era, superseded by the 2026-08-27 llama.cpp
  cutover), not new drift from this commit.

## Still accurate (checked, no drift)

- `src/ingest/README.md` — TBFM `<sta>` capture-trap note, feed table,
  SWIM handler descriptions: unaffected by the parser diffs (the fdps
  change is alert-side, not parse-side; DC_METER_FIXES isn't described
  here).
- `src/shared/watchlist_README.md` — no FDPS approach/proximity claims;
  unaffected.
- `README.md` — no claims invalidated by this commit.
- `docs/CLAUDE_MD_DRIFT_REPORT.md` — regenerated 2026-09-02 05:15 by the
  daily checker, "No drift found"; consistent with the failed-unit
  reality below (verify-manifest failures are in CLAUDE.md's Known-bad).
- CLAUDE.md's 2026-09-02 expected/self-resolving entry — confirmed
  accurate live, with a count update and one wrinkle (next section).

## Live-state verification

- **22 user units are in failed state** (06:00–09:11 EDT today), every
  one checked and every one the *expected* `verify-manifest: INTEGRITY
  FAILURE` pattern from CLAUDE.md's 09-02 entry (which names only 4 —
  same pattern, wider blast radius: all poller-image skills whose timers
  fired this morning). Running `poller:latest` is build-date
  `20260902T032046Z` — built from the night's edited-but-unsigned source,
  exactly as that entry says. ops-brief's 09:05 fire (4 min pre-commit)
  failed the same way, so **the context-budget fix has still never run
  live**; first real test is the first fire after a post-signing rebuild.
- **Wrinkle on "resolves once this pass signs"**: an in-flight
  `podman build -f Containerfile.ingest` has been running since 07:15 EDT
  (2 h+, consistent with the IPv6-tether slowness in this morning's vault
  note). Its build context was snapshotted *before* the 09:09 signing, so
  when it finally lands, the ingest image will fail verified-exec again —
  that build is wasted and needs a re-run against the signed tree.
- **Concurrent session activity observed mid-check** (~09:12–09:14 EDT):
  `scripts/scrub-public-tree.py` was edited (allowlisting
  `config.operator_email()`'s default address, comment says
  operator-confirmed safe to publish — a direct follow-on to this
  commit's hard-coded default, which the public-mirror email scrub would
  otherwise block) and the manifest was re-signed; changes staged,
  uncommitted at check time. My first `verify-manifest` run at ~09:13
  caught the mid-edit window and reported a failure that was a race, not
  a real break — as of 09:14 `verify-manifest: OK, all 873 files match`
  against the working tree. Any image rebuild should happen after that
  pass commits, or the freshly-baked manifest will exclude the scrub
  change.

## Not verified (pending, by design)

- ops-brief fix end-to-end in-image (blocked on rebuild, above). The fix
  itself was validated this morning against llama-chat's real `/tokenize`
  (3791 vs 4096 budget) per the vault note.
- Actual email delivery through ntfy's SMTP relay — no test email sent
  (would notify the operator); first scheduled fire of weekly-summary /
  second-brain-daily post-rebuild is the real test.
- Meter-fix alert firing — needs a watched flight within 50 nm of a
  resolved fix, post-rebuild.

## Second-pass verification (09:18 EDT, separate session)

A second drift-check session ran ~2 minutes after the above was written
(same prompt, re-invoked — the earlier "concurrent session" observations
above and this file's two passes are two runs of the same check). It found
the prior pass first via second-brain search (vault note
`corporatetraveldc/01-Sources/manual/20260902T131642Z.md`) and
independently re-verified rather than re-deriving:

- Drifts 1–3 confirmed still present in the docs verbatim
  (ALERT_REFERENCE.md `TH` section line ~204 and topics-table
  `dispatch-debriefs`/`dispatch-ops` row; DATA_SOURCES.md OpenSky section
  line ~750). `geo_filter.distance_nm()` still has zero callers.
- Live state unchanged: same 22 failed units (all the expected
  verify-manifest pattern), `poller:latest` still the pre-signing
  `20260902T032046Z` build, ops-brief context-budget fix still never run
  live. The wasted `Containerfile.ingest` build is still in flight
  (2 h 02 m at check time, build-date label `20260902T032046Z` — confirms
  its context predates the signing; still needs a re-run when it lands).
- New since the first pass: the scrub-allowlist change committed as
  `f001fb5` (09:15:49), and `verify-manifest: OK -- signature valid, all
  873 files match` now holds against the *committed* tree, not just the
  working tree. Images can rebuild against HEAD safely now.

No new drift found; nothing persisted to the vault by the second pass —
the 09:16 note already covers all three drifts, and duplicating it would
pollute future searches.

---

# Third pass — post-commit ebb5b7c (cowork-coord timers), ~10:00 EDT

Scoped to `ebb5b7c` ("cowork board coord: 24h/7d belt-and-suspenders
backup checkpoints"): two new scripts (`scripts/cowork-coord-24h-check.sh`,
`cowork-coord-7d-check.sh`) and four user units
(`.config/systemd/user/corporatetraveldc-cowork-coord-{24h,7d}.{service,timer}`).
Second-brain searched first (`cowork`, `board refresh token`, `presence
attestation`): no prior findings on this area exist — the only "cowork"
hits are unrelated (coworking-space RSS, personal LinkedIn notes, the
Cowork mobile client mention in the infra-map note). This check starts
cold; nothing below contradicts prior art because there is none.

## Documentation drift: none

Checked every doc surface that mentions the board/attestation chain —
none of their claims are invalidated by this commit:

- `docs/COMPLIANCE_SECURITY.md` (~line 268) — describes `board_refresh`
  audit events from `board_refresh_token()`'s three call sites; the new
  scripts call `db.board_insert()`/`board_presence_status()` only, never
  the token path. Still accurate.
- `scripts/board-presence-attest.sh` docstring — "run it yourself on a
  ~7-day cadence, whenever a reminder fires (or proactively)": the 7d
  timer now *satisfies* this previously-aspirational line rather than
  contradicting it (the 7d script's own header says "this IS that
  reminder"). Reverse-drift closed, not opened.
- `src/common/db.py`'s board-chain comment block (~line 397) — unchanged
  semantics, still accurate.
- `docs/tasks/scheduled/README.md` — skills doc, not a timer inventory;
  no exhaustiveness claim to break. `docs/REFERENCE_INFRA.md` line ~287
  (`/api/v1/board*` posts need `X-Board-Key`) — unaffected (scripts write
  via `db.board_insert` directly, not the HTTP surface).
- `README.md`, `src/ingest/README.md`, `src/shared/watchlist_README.md`,
  CLAUDE.md — no claims touching this area.

## Real findings (behavioral, confirmed live — persisted to vault)

### 1. Both timers use `Requires=` — the documented 2026-08-30 llama-restart bug pattern, re-introduced

CLAUDE.md's 2026-08-30 entry records fixing exactly this on
`scheduled-llama-restart`: `Requires=` in a timer's `[Unit]` pulls the
service into the same transaction the moment the *timer* activates, firing
it immediately rather than at scheduled time. Both new timers copy the
pattern, and it fired live: timers enabled 09:34:31 EDT → both services
ran at 09:34:31, posting board coord messages seq=37/38. Stakes are far
lower than the llama case (a duplicate board post + operator ping, not a
killed in-flight LLM run), but every future
`systemctl --user restart`/re-enable of either timer will fire a spurious
checkpoint. (`OnBootSec` being long-elapsed would also have fired them on
first enable — but `Requires=` makes it recur on every timer restart.)
Fix is the same one-word change as last time: `Wants=`.

### 2. That premature 09:34 fire ran a pre-fix script — both first-run operator pings were silently lost

Journal shows both 09:34:32 runs failed the ntfy leg:
`ntfy unreachable: url=http://host.containers.internal:2586` — the
committed scripts' `export NTFY_URL="http://127.0.0.1:2586"` override was
added at 09:34:50 (file mtime), 19 seconds *after* that run, and the
commit landed 09:46. So the exact host-vs-container `ntfy_url()` gotcha
the committed comment warns about is what ate the first checkpoint's
operator ping+email (board posts landed fine). The committed version is
verified correct mechanically (`config.ntfy_url()` resolves to
`127.0.0.1:2586` under the export — `config.get` prefers already-set
process env over dispatch.env — and ntfy answers `{"healthy":true}`
there), **but the ntfy/email leg has never run successfully live**; first
real test is the next 24h fire, Thu 2026-09-03 16:51 EDT.

### 3. The 7d reminder is scheduled to fire ~10h AFTER the attestation it guards expires

Current attestation: issued 2026-09-02 09:03:54 EDT, expires 2026-09-09
09:03:54 EDT. First scheduled 7d fire: **2026-09-09 19:23:45 EDT** —
10h20m after expiry, i.e. after `board_refresh_token` has already been
failing closed. The script handles the lapsed case (priority-4 "failing
closed" wording), but as a *pre-lapse* reminder it structurally can't
work: `OnUnitActiveSec=7d` + `RandomizedDelaySec=12h` anchors each fire
to the previous *activation* (period 7d + 0–12h, drifting later every
cycle) while the attestation window anchors to whenever the operator
actually runs attest — the two clocks decouple over weeks. A reminder
that keys off `board_presence_status()['valid_until']` (e.g. daily
OnCalendar + fire-only-when `<36h` remaining) would track the real
deadline. Until then, expect fail-closed gaps between attestation lapse
and reminder.

## Minor / cosmetic (noted, not vault-worthy)

- Failed operator ping doesn't fail the unit: both scripts ignore
  `ntfy_push.send()`'s boolean return, so the 09:34 half-failed runs show
  `status=0/SUCCESS` — a dead alert leg is invisible to failed-unit
  sweeps, journal-warning only. For a unit whose whole purpose is
  operator visibility, exiting nonzero on a failed send would be truer.
- `Persistent=true` is inert on both timers — it only applies to
  `OnCalendar=` timers, and these are monotonic-only. Harmless copy-paste
  (and unlike the ep-advance-venues case, can't even cause a catch-up
  fire).
- Timer descriptions say "6-12h grace window"; `RandomizedDelaySec=12h`
  actually gives 0–12h. Also the effective 24h-timer period is 24–36h
  (mean ~30h) against an 86400s token TTL — accepted grace-window design
  per the script header, just noting the real numbers.

## Live-state verification / continuity with the morning passes

- `verify-manifest: OK -- signature valid, all 879 files match` (working
  tree, post-ebb5b7c). `git status` clean at check time.
- Live installed copies of all four units are byte-identical to tracked;
  timers enabled and waiting (24h → Sep 3 16:51 EDT, 7d → Sep 9 19:23
  EDT). Board messages seq=37/38 confirmed posted by the first fire.
- Still 22 failed user units, unchanged set — all the expected
  verify-manifest pattern; `poller:latest` still build-date
  `20260902T032046Z` (pre-signing). **The post-signing poller rebuild is
  still pending**, so ops-brief's context-budget fix has still never run
  live.
- The morning passes' prediction about the in-flight ingest build came
  true: `corporatetraveldc-ingest:latest` finished 09:41 EDT but carries
  build-date label `20260902T032046Z` — context snapshotted pre-signing,
  so it will fail verified-exec; that 2.5h build was wasted as predicted
  and needs a re-run against the signed tree (ingest containers are still
  on the old image anyway, up 2+ days).
- Presence attestation currently valid (~167h remaining at check time).

---

# Fourth pass — post-commit 98ea231 (blog Accept: text/markdown), ~11:15 EDT

Scoped to `98ea231` ("blog: Accept: text/markdown content negotiation"):
nginx vhost negotiation maps + internal `/_md/` location, `render_markdown()`/
`render_index_markdown()`/`title_yaml_escape()` in
`src/executive_standard/render.py`, `body_md` sourcing (html2text for
Substack, real source for Pi-native, overrides patch both) + `_md/`
emission in `executive_standard_sync.py`, `html2text>=2024.2.26` in
requirements.txt.

Second-brain searched first (`substack`, `executivestandard`, `llms.txt`,
`html2text`, raw `markdown AND negotiation`): **no prior findings on this
area** — the blog/markdown-negotiation surface has never been investigated;
this check legitimately starts cold. (The search itself surfaced a separate
real finding — see finding 2.)

## Documentation drift from this commit: none

No doc in the checked set (README.md, CLAUDE.md, docs/, src/ingest/README.md,
src/shared/watchlist_README.md) mentions the Executive Standard blog, the
sync skill, or this vhost at all — there were no claims to invalidate.
`executive_standard_sync.py`'s own docstring ("not yet wired to a timer --
run manually") is still accurate; confirmed no unit/timer references it.
Pre-existing, NOT from this commit: `docs/INFRA_MAP.md`'s hostname table
(§7, ~line 556) has never listed `executivestandard.example.com`
(the vhost predates this commit) and still lists `ollama.` as live though
the 2026-08-30 pass purged that vhost — both fall under INFRA_MAP's
already-documented §6b "repo/live nginx drift, operator decision pending"
territory, noted here for the next INFRA_MAP refresh, not re-derived.

## Real findings

### 1. The feature is committed+signed but 100% inert live — all three deploy legs missing, and nothing records them as pending

Verified live at 11:12 EDT: `curl -H 'Accept: text/markdown'` against the
vhost (root and a post URL) returns `200 text/html` — the HTML page, no
`Link`/`Vary` headers. Because:

- **Live nginx conf is the pre-commit version.** Byte-diffed
  `/etc/nginx/conf.d/executivestandard.example.com.conf`
  against `HEAD:nginx/conf.d/…` — the entire negotiation block (3 maps,
  server-level headers, gzip, the rewrite, the internal `/_md/` location)
  exists only in the tracked copy. Root-owned target; needs operator
  `sudo cp` + `nginx -t` + reload (no deploy script exists for nginx
  confs — consistent with INFRA_MAP §6b).
- **`/var/www/executivestandard.example.com/_md/` does not
  exist.** The webroot's last build is 01:37 EDT — pre-commit code. The
  sync (manual-run by design) has not been re-run since the commit.
- Unlike the same-day precedents in CLAUDE.md ("needs rebuild+redeploy,
  not yet done" for runner/web on 08-31), **no note anywhere records the
  pending deploy steps** — a future session reading the present-tense
  commit message would assume this is live.

Host-side prerequisites all verified in place, so deploy is genuinely just
those two steps: `html2text` 2025.4.15 importable on the host, both new
render functions import and produce correct output (smoke-tested offline:
YAML escaping of colon/quote titles, H1 dedup for Pi-native bodies, the
body_html-strip fallback), and the tracked conf **passes `nginx -t`**
standalone (syntax ok; tested with a stubbed `corporatetraveldc_lr` zone).

**Deploy checklist:** (1) `python3 src/poller/skills/executive_standard_sync.py`
(writes `_md/`); (2) sudo cp the vhost conf + `nginx -t` + reload. Order
doesn't matter for safety: conf-first 404s markdown requests until sync
runs (the `internal` location can't leak); sync-first leaves `_md/*.md`
directly browsable as plain files until the conf lands (same public
content, no exposure).

### 2. `scripts/post-commit-doc-verify.sh`'s search guidance drifted — default second-brain search now silently under-returns multi-word queries (fixed in-tree, uncommitted)

The prompt this very check runs under (tracked,
`scripts/post-commit-doc-verify.sh:55`) says "plain-language query, no
quoting needed even for hyphenated terms." That guidance predates the
phrase-wrap change in `index_db.search_notes()` (commit `893b6b0`,
semantic-layer): a default-mode multi-word query is now wrapped as an FTS5
**exact phrase**, not implicit-AND. Confirmed live:
`second-brain-search.sh llama swap` → **0 results**;
`--raw llama AND swap` → the real swap-thrash notes. Every automated pass
following the script's guidance verbatim has been silently under-searching
its "prior findings" step (this pass's own first four queries came back
empty for exactly this reason). Same agent-facing-procedural-doc drift
class as the 08-31 flight-hifi-track SKILL.md case. Fixed the guidance
line in the script (uncommitted working-tree edit): single word or exact
phrase for default mode, `--raw X AND Y` for multi-word, `--semantic` for
concept queries. The `search_notes()` behavior itself is deliberate and
documented in its docstring — not touched.

## Minor / cosmetic (noted, not vault-worthy)

- `gzip_types text/html` in the new conf triggers
  `nginx: [warn] duplicate MIME type "text/html"` on every `nginx -t`/
  reload (text/html is always gzipped implicitly) — harmless, confirmed
  in the standalone syntax test; operator will see the warning at deploy.
- Extensionless-URL gap: `try_files $uri $uri/ $uri.html` serves posts at
  `/slug` too, but the negotiation and Link-header maps only match `/`
  and `*.html` — an agent hitting the extensionless variant gets HTML
  with no alternate advertised. All canonical links the site emits use
  `.html`, so cosmetic.
- `html2text` lands in all 7 container images on their next rebuilds
  (every Containerfile pip-installs requirements.txt) though only the
  host-run sync uses it — small image bloat, nothing functional.
- No tests cover the new render functions (this surface had zero test
  coverage before; not a regression).

## Live-state continuity

- `verify-manifest: OK -- signature valid, all 879 files match` at check
  start (this pass's own edits — this file + the script fix — re-open the
  usual unsigned window until next signing; expected, self-resolving).
- Failed-unit set and pre-signing `poller:latest`/`ingest:latest` builds:
  unchanged from the third pass; post-signing rebuilds still pending.
  Nothing in this commit changes container-side behavior until those
  rebuilds happen anyway (html2text is host-side for this feature).

---

# Fifth pass — post-commit 8a5fa26 (nwws retry + NOTAM retirement), ~14:10 EDT

Scoped to `8a5fa26`: bounded retry-with-backoff for `push:nws` heartbeat
writes in `src/ingest/nwws.py`, the FAA NOTAM Search API retirement research
documented into `src/poller/fetchers/notam.py`'s docstring, and committing
the fourth pass's search-guidance fix in `scripts/post-commit-doc-verify.sh`.

Prior art consulted first: the nwws heartbeat area is well-trodden — vault
notes `20260831T161751Z` (the original try/except fix this commit upgrades),
`20260831T120046Z` (doc-drift pass on that commit), and the 08-20/21/23
NWWS incident chain. This pass builds on those. The NOTAM Search API
retirement has NO prior vault note (searched `notam`, plus `--raw` variants)
— it is new research introduced by this commit, which is exactly why the
docs below now lag it. (Meta: this pass's prior-art step ran under the
freshly committed search guidance and it worked — single-word and `--raw`
queries all returned; the pass-4 under-searching failure mode is gone.)

## Drift found (real)

### 1. docs/DATA_SOURCES.md §"FAA NOTAM API" — access process is now factually wrong

Lines ~237–251 ("Last verified: 2025-12"): signup portal
`api.faa.gov/signup`, API docs link, "**Access process:** Self-serve API
key registration… Key is issued immediately after email verification",
"**No email required** — portal registration is fully self-serve."
All of that is invalidated by this commit's documented research: the legacy
NOTAM Search API this fetcher targets was retired 2026-04-18 with the rest
of legacy FNS, replaced by NMS, whose API access is **email-request-only**
(`notams@faa.gov`) — there is no self-serve path, and a key from the old
portal flow would not work against a retired product. DATA_SOURCES.md is
the canonical access-process reference; an operator following it today
would chase a dead registration. Lesser echoes of the same claim:
README.md:154's "⚠️ Needs `FAA_NOTAM_API_KEY` + `FAA_NOTAM_API_SECRET`"
row and the README.md:525 setup comment ("populate credentials (…, FAA
NOTAM key, …)") both frame the credentials as pending-but-obtainable;
the truth is now "unobtainable without an NMS-API rewrite of the fetcher."
Suggested fix: mirror `notam.py`'s new docstring into the DATA_SOURCES
section. **Not edited this pass** — a concurrent session is mid-sign (see
continuity below) and pass-1 precedent is to document doc-drift, not
rewrite under someone's feet. Left alone deliberately: the dated snapshot
docs (COGS_VENDOR_COMPARISON's "free via public NOTAM Search") are
historical records; DESIGN-PRINCIPLES.md:36's "credentials always
optional / a missing key degrades one feed" remains true.

### 2. The nwws retry fix is committed+signed but not deployed — pending-deploy-leg class again

Running ingest image is build-date `20260902T151152Z` (11:11 EDT), which
predates the 14:01 EDT commit; grep inside the running `ingest-core`
confirms the retry loop ("after 4 attempts") is absent. The image is
self-consistent (built from the signed post-`98ea231` tree), so
verified-exec passes and all 7 ingest containers run fine — they just run
the OLD one-attempt-per-tick heartbeat, so the false-kickover window the
commit closes stays open until the ingest image is rebuilt (post-signing)
and `ingest-core` restarted. Harmless right now: all 8 push heartbeats
were 1–28 s fresh at 14:08 EDT, `push:nws` at 1 s. The poller image
likewise predates the commit, but `notam.py`'s change is docstring-only —
no behavioral gap, rebuild is routine. Recording this here because (same
as pass-4 finding 1) nothing else records the pending leg.

## Real findings, not from this commit (both persisted to vault)

### 3. The commit's own sqlite-lock class claimed a third victim the same day: `index_db.index_note()`

`corporatetraveldc-second-brain-rss.service` failed at 12:13:20 EDT on
`sqlite3.OperationalError: database is locked` — a hard, uncaught crash in
`src/second_brain/index_db.py`'s `index_note()` (`vault_documents` INSERT)
against the vault-index DB. This is the exact error class `8a5fa26` fixes
for the nwws heartbeat and yesterday's opensky fix addressed for bulk
imports — now surfacing at a third call site, in a *different* database
(the vault index, not the dispatch DB). `index_db.py`'s writers have no
retry/backoff at all, so any rss/indexer run that collides with a
concurrent vault write (agent sessions write notes constantly) dies
outright. Self-heals on the timer's next fire, but the class is picking
off call sites one at a time; a connection-level `busy_timeout` on the
index DB (rather than a fourth per-call-site retry loop) looks like the
structural fix. Operator decision on approach; nothing changed this pass.

### 4. runner PWA is down — stopped 12:47 EDT, never restarted

`corporatetraveldc-runner.service`: journal shows a clean `Stopping` at
12:47:15 (then the known uvicorn-slow-shutdown SIGKILL-after-10s, exit
137, unit `failed`), and **no `Starting` line since** — a deliberate stop,
not a crash, most likely the concurrent session iterating (runner had been
up only ~11 min at that point; web was restarted again at ~14:04). Port
8001 confirmed connection-refused at 14:10. NOT restarted by this pass —
restarting under an active session's feet risks clashing with whatever
they stopped it for — but if it's still down when that session winds up,
it needs `systemctl --user start corporatetraveldc-runner.service`.
(`runner-demo` is unaffected, up 2 days.)

## Still accurate (checked, no drift)

- README.md §push-primary/heartbeat mechanism (~lines 232–235, 90 s
  `FALLBACK_MAX_AGE`) — matches the commit's own rationale; the retry is
  internal behavior, no doc claim touches attempt counts.
- `src/ingest/README.md` — heartbeat-stamping and feed-key table
  unaffected; `docs/DATA_SOURCES.md:179`'s "30 s mark_push_healthy
  heartbeat" still accurate.
- `notam` feed live state: still `awaiting_credentials` in `feed_state`,
  skip-gracefully behavior unchanged — the new docstring's "same as
  today" claim verified live.
- CLAUDE.md's 08-31 entry ("logs and retries next tick instead of dying")
  now describes superseded behavior, but it's a dated log entry in a
  write-only scratchpad, not living-doc drift.

## Live-state continuity

- Failed units 22 → 6 since the fourth pass: the post-signing rebuild
  finally happened (all four images rebuilt `20260902T151152Z`, containers
  restarted ~12:05–13:32 EDT), clearing the morning verify-manifest
  pattern. Remaining 6 = four *expected* stragglers whose only fire today
  hit the pre-signing image (daily-opsplan 07:00, ep-advance-venues 06:10,
  freshness-audit + pull-path-verify 06:00 — all clear on next scheduled
  fire) + findings 3 and 4 above. Note: ep-advance-venues' 06:10 failure
  means the venue half of the ep-advance split has *still* never run live;
  next real test 06:10 ET tomorrow.
- **ops-brief's context-budget fix has now run live** (the thing passes
  1–4 were tracking): the 12:05 EDT fire ran 47 min against the new image
  and deactivated cleanly; 13:05 and 14:05 fires followed (13:05 finished
  in 30 s — plausibly the trend-vs-hourly branch, not investigated;
  neither failed). Output-quality verification out of scope here.
- verify-manifest at check time: INTEGRITY FAILURE on exactly one file,
  `scripts/scrub-public-tree.py` — a *staged, uncommitted* 14:03 EDT edit
  allowlisting `notams@faa.gov`, i.e. the direct follow-up this commit's
  docstring requires so the public-mirror email scrub doesn't block it.
  Concurrent session mid-pass; expected, resolves when they sign+commit.
  (This file's own edit re-opens the usual unsigned window on top.)
