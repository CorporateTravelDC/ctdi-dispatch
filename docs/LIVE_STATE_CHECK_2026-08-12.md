# Live-State Doc Check — 2026-08-12

Post-commit drift check for `d9a9d58` ("Add governor-watch: inference-aware
CPU governor drift detection" — 3 new files: `scripts/governor-watch.py`,
`.config/systemd/user/corporatetraveldc-governor-watch.{service,timer}`).
Scope: does this commit invalidate anything the current docs claim
(README.md, CLAUDE.md, docs/, src/ingest/README.md,
src/shared/watchlist_README.md)? Verified against the live system, not
prior docs. This is a findings file only — nothing staged or committed.

**Working-tree context:** the tree already carries ~56 uncommitted modified
files from the 2026-08-11 docs-refresh session (mtimes 08-11 ~15:32; see
untracked `docs/DOCS_REFRESH_2026-08-11.md`), awaiting operator commit.
All doc claims below were checked against those working-tree (refreshed)
versions — i.e., the newest content — not the committed versions. This
check added exactly one file: this one.

## Drifted (2 spots, same root cause: the new ops-health sender/timer isn't cataloged)

1. **`docs/ALERT_REFERENCE.md`** — two places that are explicitly meant to
   be exhaustive now miss `scripts/governor-watch.py`:
   - The topic-index `ops-health` row enumerates every firer
     (freshness_audit.py, container-mem-watch.sh, thermal-ingest-guard.py,
     …). governor-watch.py now also fires `ops-health` and isn't listed.
   - The "Standalone bash/script alerts" section catalogs exactly this
     class of sender (hand-rolled `urllib`/curl, no retry, own env
     parsing — thermal-ingest-guard.py is called out as the Python member
     of the class). governor-watch.py is a new member: its own
     `ntfy_alert()` via `urllib.request`, no retry, priority 3 on
     drift-corrected, priority 5 on fix-failed. Missing.

2. **`docs/INFRA_MAP.md`** — the "Timer highlights" watchdog enumeration
   (§4, which itself flags "New 2026-08-11" additions) doesn't include the
   new `corporatetraveldc-governor-watch.timer` (every 6 h,
   OnBootSec=300, plain user unit — not a Quadlet). One-line addition to
   the watchdog list.

Both are one-line-scale additions; left unfixed here since this file is a
check record, not a rewrite.

## Snapshot-value drift (self-protected, no action)

- **CLAUDE.md** says "(145 loaded units at this snapshot)" for
  `systemctl --user list-units 'corporatetraveldc-*' --all`. Live count
  today: **138** (loaded-unit count is volatile; inactive non-referenced
  units unload). CLAUDE.md already says not to hardcode this and to check
  live, so the operative claim stands — the parenthetical is just a dated
  snapshot doing its job.

## Verified still accurate (live checks, 2026-08-12 ~12:00 EDT)

- **Deployment is real, not just committed:** both unit files are present
  in `~/.config/systemd/user/` (copies dated Aug 12 11:39), the timer is
  `enabled`/`active (waiting)`, next trigger 17:51 EDT, and the first
  service run (11:51) exited 0 with `governor-watch: OK -- all 4 cores at
  'schedutil'`.
- **Governor state:** all 4 cores read `schedutil` from
  `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`.
- **Commit's "existing passwordless sudoers entry, no new grant" claim:**
  confirmed via `sudo -n -l` — `(root) NOPASSWD: /usr/bin/cpupower
  frequency-set -g schedutil` exists. Note the grant is argument-pinned to
  `schedutil`, so the script's `GOVERNOR_WATCH_TARGET` tunable can only
  ever succeed for `schedutil`; any other target would hit the
  FIX FAILED / priority-5 path. Consistent with
  `docs/SUDO_JUSTIFICATION_PROPOSAL.md`, which covers only the
  ollama/dnf approval-gated grants and makes no claim about cpupower —
  nothing there invalidated.
- **Script docstring's boot-config claim:** `cpupower.service` is
  `enabled` and `/etc/cpupower-service.conf` has `GOVERNOR=schedutil`.
- **Tunables:** no `GOVERNOR_WATCH_*` vars in
  `/etc/corporatetraveldc/dispatch.env` — script defaults (target
  schedutil, 900 s backoff, 30 s poll, 15% busy threshold) are what's
  live, matching the unit's `TimeoutStartSec=960` headroom.
- **Signed manifest:** `scripts/verify-manifest.sh` → OK, signature valid,
  all 618 files match, including the 3 new files. The commit was properly
  re-signed.
- **Unaffected docs:** README.md (its `ops-health` row says "Freshness
  audit, watchdogs, thermal guard" — generic enough that governor-watch
  fits under "watchdogs"), `docs/HARDWARE_GUIDANCE.md` /
  `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md` / thermal benchmark docs (their
  `schedutil` claims remain true — this commit *enforces* them),
  `src/ingest/README.md` and `src/shared/watchlist_README.md` (commit
  touches neither surface). No invalidated claims found in any of these.

## Minor nit (new code, not docs/)

- `scripts/governor-watch.py` docstring, "Inference-awareness" paragraph,
  names the backoff tunable `OLLAMA_GOVERNOR_WATCH_BACKOFF_S`; the actual
  variable (per the same docstring's tunables list and the code) is
  `GOVERNOR_WATCH_BACKOFF_S`. Cosmetic; fix next time the file is touched
  (requires manifest re-sign).

---

# Second check, same date — commit `30c52c0` (osint event scope + PWA visibility + Anthropic fallback closed)

Appended by a later session on 2026-08-12 (~evening EDT), after `30c52c0`
landed as HEAD. Same scope and rules as above: does THIS commit invalidate
anything the current (working-tree, post-08-11-refresh) docs claim?
Findings only — nothing fixed, staged, or committed here.

## Drifted — real invalidations by this commit

1. **CLAUDE.md (Local LLM section) and README.md ("Cloud fallback"
   paragraph, ~line 522)** — both state `ANTHROPIC_FALLBACK_ENABLED`
   **defaults true** and "is NOT set to false in dispatch.env", and that
   (only) *brief* skills pass `allow_anthropic=False`. All three claims are
   now inverted by this commit, verified live:
   - `/etc/corporatetraveldc/dispatch.env:111` now sets
     `ANTHROPIC_FALLBACK_ENABLED=false` explicitly.
   - `podman exec systemd-corporatetraveldc-poller env` shows
     `ANTHROPIC_FALLBACK_ENABLED=false` inside the running poller.
   - Every remaining `generate()`-calling skill (dispatch_desk_memo,
     osint_monitor, second_brain_daily/weekly, route_impact,
     tfr_enrichment, weekly_summary) now passes `allow_anthropic=False`
     per-call too — the "brief skills only" qualifier is obsolete. The
     cloud fallback is now closed belt-and-suspenders everywhere.
   This is the largest doc drift from this commit; the two paragraphs need
   rewording, ideally noting the fallback is dead code-path unless both
   the env gate and per-call flags are deliberately reopened.

2. **CLAUDE.md ("V31 exists as of this snapshot") and README.md (~line
   475, same claim)** — `SCHEMA_V32` now exists
   (`osint_scopes.event_name/audience/genre`; columns confirmed present in
   the live DB via `PRAGMA table_info(osint_scopes)`). Both files
   self-protect with "check the file for the current top", so the
   operative guidance stands, but the snapshot value is stale.

3. **`docs/ALERT_REFERENCE.md`** — two spots in the `osint-alerts`
   coverage are now incomplete:
   - Topic-index row: "Keyword/RSS/marketing intel hits" — there is now a
     fourth class, `scope_type="event"` (19 event scopes live in
     `osint_scopes`, plus new `market_intel`/`geo` rows).
   - osint_monitor.py entry: "Title is tagged `[EP]`/`[MKT]`/`[OSINT]`
     depending on scope type" — code now also emits **`[EVT]`**
     (`_push_item()` in osint_monitor.py) with `calendar,newspaper` ntfy
     emoji tags for event scopes.

4. **`docs/dispatch-runner-design.md`** — the PWA route table lists
   `/intel` ("RSS/Atom intelligence feeds") but not the new **`/events`**
   route (EventIntelView.jsx, wired in App.jsx, grouped by scope_type,
   backed by `GET /api/v1/osint/feed`). Also relevant context the doc
   doesn't capture: the ntfy SSE proxy's default topic list
   (`runner/main.py /api/ntfy/stream`) now includes `osint-alerts`, and
   `osint-alerts`' deep-link in `src/common/ntfy_push.py` `TOPIC_CLICK`
   moved from `/intel` to `/events`.

5. **`docs/SECOND_BRAIN_STATUS.md`** — describes second_brain_daily's
   digest as "(CPS distribution, TFR/NOTAM/NWS counts, Amtrak, METAR,
   watchlist activity, latest ops-brief excerpt)". The daily note now also
   folds in all OSINT scopes grouped by type (second_brain_daily.py,
   +86 lines this commit). Enumeration incomplete.

## Dated-record files — superseded, no action

- **`docs/DOCS_REFRESH_2026-08-11.md`** (untracked) states — correctly,
  as of 08-11 — that `ANTHROPIC_FALLBACK_ENABLED` is *not* set in
  dispatch.env and defaults true. True when written, inverted one day
  later by this commit. It's a dated record doing its job; the *current*
  docs it fed (CLAUDE.md/README, item 1 above) are what need the fix.
- **`docs/INVESTOR_MATERIALS_REVERIFICATION_2026-08-09.md`** claimed the
  fallback was "off by default" — wrong when written, accidentally
  accurate now. No edit needed, but don't cite it as having been right.

## Verified still accurate (live checks)

- **Deployment is real:** web/poller/runner containers rebuilt and
  running (uptimes 2 h / 34 min / 3 h at check time vs pusher's 40 h);
  `/healthz` OK; `GET /api/v1/osint/feed` live and each item carries
  `scope_type`, matching the commit's end-to-end claim.
- **DB state:** `osint_scopes` live counts — 19 `event`, 1 `ep_threat`,
  1 `market_intel`, 1 `geo`; V32 columns present.
- **Admin API:** `POST /api/v1/osint/scopes` allowed-types set in
  `src/web/main.py` now includes `"event"` and accepts
  event_name/audience/genre, as the commit says.
- **Signed manifest:** `scripts/verify-manifest.sh` → OK, signature
  valid, all 623 files match — the commit was properly re-signed.
- **`ntfy_send`/`send()` `title` now REQUIRED (no default)** — checked
  ALERT_REFERENCE.md and watchlist_README.md: neither documents a default
  title, so no doc claim is invalidated by the signature change.
- **Unaffected doc surfaces:** `src/ingest/README.md`,
  `src/shared/watchlist_README.md` (commit touches neither subsystem),
  `docs/DATA_SOURCES.md` (no OSINT/RSS-source enumeration exists there —
  per-scope feeds are DB-seeded, not config), CLAUDE.md's core ntfy topic
  list (never enumerated `osint-alerts`; defers to ALERT_REFERENCE.md),
  README's OSINT API rows (generic `GET/POST/PATCH/DELETE /api/v1/osint/*`
  — still true).

## Pre-existing inaccuracy noticed in passing (NOT caused by this commit)

- **README.md ntfy topic table, `osint-alerts` row** says priority
  "2–3", but osint_monitor.py's `PUSH_PRIORITY` has been
  `{CRITICAL: 5, HIGH: 4, MEDIUM: 3}` since well before this commit
  (unchanged by it — verified via `git log -S`). Actual range is 3–5.
  ALERT_REFERENCE.md gets this right ("scope-dependent, `PUSH_PRIORITY`
  by score_label"). Fix the README row whenever it's next edited.

---

# Third check, same date — commit `1781555` ("Sweep: OSINT dedup, second-brain graph, ingest/infra fixes")

Appended ~20:30 EDT, after `1781555` landed as HEAD (20:14:47 EDT). Same
rules: does THIS commit invalidate anything the current docs claim? Checked
against working-tree (newest) doc versions and the live system. Findings
only — nothing fixed, staged, or committed here.

**Concurrent-session caveat:** this check ran against a moving tree. An
active session staged changes to 5 files *while this check was running*
(`src/second_brain/knowledge_graph/{build_graph.py,graph.json,vault-graph.html,viz_template.html}`
+ `src/web/main.py`, mtimes 20:15:30–20:17:47, i.e. within 3 minutes of the
commit). No coordination note in `06-AI-Memory/notepad/` (newest note there
is 2026-08-03). The separate model-selection in-flight set (CLAUDE.md,
README.md, build-models.sh, 16 Modelfiles, DEDICATED_MODELS_PLAN.md)
remains uncommitted-unstaged, as the commit message says.

## Drifted — real findings

1. **The commit's own `MANIFEST.sha256` does not match the commit's own
   content for 5 files** (`scripts/verify-manifest.sh` → INTEGRITY FAILURE,
   6 mismatches). Verified three-way (manifest hash vs `git show HEAD:` vs
   worktree):
   - `src/runner/frontend/vite.config.js`,
     `src/second_brain/knowledge_graph/{build_graph.py,graph.json,vault-graph.html,viz_template.html}`
     — **manifest ≠ HEAD** (worktree == HEAD for vite.config.js; the four
     graph files have additionally moved on via the concurrent session's
     staged edits). The manifest was evidently signed before a last
     regeneration/edit of these files, then committed stale.
   - `src/web/main.py` — manifest == HEAD; only the concurrent session's
     staged +30 diverges. Expected during in-flight work, not a commit bug.
   `scheduled-integrity-sweep.sh` was already logging FAILED for the graph
   files at 20:10:30 (pre-commit), so the alerting works as designed.
   Per CLAUDE.md's signed-manifest rule, containers/skills rebuilt from
   this state will refuse to run until re-signed. Running containers are
   unaffected (they verify their baked-in copies; poller journal shows zero
   manifest/integrity errors since 19:30). **Needs a re-sign with the next
   commit** — plausibly the concurrent session's plan; flagging in case not.

2. **SDR docs say "restored"; live system says everything SDR is down.**
   `docs/SDR_SERVICES.md` (rewritten in this commit) says ultrafeeder
   "✅ Restored midday 2026-08-11 … Both dongles enumerate again; live
   decode confirmed", and README (working tree) echoes "restored
   2026-08-11" and "All other SDR containers (ACARS/VDL2 chain, feeders) up
   throughout". But the SAME commit's `stack-boot-ctl.sh` records the later
   reality: "both SDR dongles casualty-suspected (ADS-B off the USB bus
   entirely, VDLM present but unqueryable) … running all external until
   [hardware replaced]". Live check: `ultrafeeder`, `acarsrouter`,
   `acarshub`, `dumpvdl2`, `piaware`, `fr24feed`, `planefinder`,
   `airnavradar` are ALL `inactive`; only `acars-watcher` (external
   airframes.io side) is up. The 08-11 restore was overtaken by a same-day
   second casualty; SDR_SERVICES.md / SDR_SERVICES_README.md / README's
   SDR paragraphs need the update when the hardware thread resolves.

3. **`docs/INFRA_MAP.md` "board-sweep hourly"** (§ timer highlights) —
   invalidated by this same commit, which moved the timer to
   `OnCalendar=*:0/15`. Live: enabled, firing on the 15-min grid. One-word
   fix.

4. **`docs/ALERT_REFERENCE.md` + `docs/INFRA_MAP.md` still don't catalog
   the two newest ops-health senders.** governor-watch (flagged in check #1
   today) was NOT picked up by this commit's +31-line ALERT_REFERENCE
   addendum or the 742-line INFRA_MAP rewrite, and the commit adds a second
   missing member: `scripts/uber-traffic-watch.py` fires `ops-health`
   (own `ntfy_alert()` via urllib, no retry, priority 4 default, tags
   `taxi,mag`) on a 2-min timer — exactly the "standalone bash/script
   alerts" class ALERT_REFERENCE catalogs. Both docs: 0 mentions of either
   watcher (grep-verified). INFRA_MAP's "New 2026-08-11" paragraph does
   cover docs-drift-weekly, so that one is fine.

5. **`docs/dispatch-runner-design.md` route table** — despite a 438-line
   update in this commit, the PWA route table still lists neither `/events`
   (flagged in check #2) nor the NEW **`/graph`** tab this commit adds
   (GraphView.jsx, `<Route path="/graph">` in App.jsx, live: HTTP 200 on
   :8001/graph).

6. **CLAUDE.md/README schema snapshot now two versions behind** — both
   still say "V31"; `SCHEMA_V33` (osint_items `headline`/`outlet`/
   `story_key` — columns confirmed in the live DB) is now the top. Both
   files are in the excluded in-flight set, so this is expected debt for
   the model-selection session to pick up along with the
   `ANTHROPIC_FALLBACK_ENABLED` paragraph from check #2 (still present
   verbatim in the working-tree CLAUDE.md). Also unmentioned there: llm.py's
   new `OLLAMA_BACKPRESSURE_*` ingest-backpressure valve — currently inert
   (`OLLAMA_BACKPRESSURE_ENABLED` defaults false, not set in dispatch.env),
   so no live-behavior claim is wrong; just missing coverage.

7. **`docs/SECOND_BRAIN_STATUS.md`** — newly committed here, but carries
   forward check #2's finding unfixed: its second_brain_daily digest
   enumeration still omits the OSINT-scopes section the daily note now
   includes (0 grep hits for "osint" in the file).

## Verified still accurate (live checks, ~20:15–20:30 EDT)

- **All four unit changes deployed and live:**
  `uber-traffic-watch.timer` (2-min, firing — last run 20:14:40),
  `docs-drift-weekly.timer` (enabled, next Mon 2026-08-17 09:00),
  `board-sweep.timer` (*:0/15), `governor-watch.timer` (6 h) — all in
  `systemctl --user list-timers`.
- **ProtonBridge fix is live:** container binds
  `100.x.x.x:1025->25/tcp`, matching the fixed Quadlet; README's
  working-tree protonbridge line and INFRA_MAP §4 both already describe the
  new binding correctly.
- **OSINT V33 end-to-end:** `headline`/`outlet`/`story_key` columns present
  in the live DB; web `/healthz` OK.
- **Second-brain graph:** vault copy exists at
  `corporatetraveldc/04-Syntheses/vault-graph.html` (WebDAV-listed);
  runner `/graph` serves 200.
- **Watchlist vessel type:** watchlist_README (rewritten this commit)
  matches code — `EntryType` includes `"vessel"`, vessel branch routes to
  `vessel-alerts`, `permanent_vessels.json` → vessel mapping. CLAUDE.md's
  watchlist paragraph (already vessel-aware) unaffected.
- **Pusher `send_ntfy` title-now-required:** invalidates no doc claim (no
  doc documents a default title); CLAUDE.md's "polls DB every 30 s" pusher
  description untouched by the delegation cleanup.
- **ALERT_REFERENCE's new addendum content** (family-alert topics, vessel
  position-event fix, TFMS amendment content-hash dedup,
  brief-fallback-monitor) matches the code it describes.
- **`docs/tasks/scheduled/README.md`** — Cowork-skill catalog, not a timer
  catalog; no obligation to list the new timers. Not a gap.
- **CLAUDE.md "145 loaded units" snapshot:** live count today 146 —
  self-protected wording, no action (same as check #1's 138; it moves).

---

# Fourth check, same date — commit `cd5f7d3` (knowledge-graph "open file" click-through + `/api/v1/vault/file`)

Appended ~20:45 EDT, after `cd5f7d3` landed as HEAD (20:33 EDT). Same rules:
does THIS commit invalidate anything the current docs claim? Checked against
working-tree doc versions and the live system. Findings only — nothing
fixed, staged, or committed here; this check modified only this file. The
model-selection in-flight set (CLAUDE.md, README.md, build-models.sh, 16
Modelfiles, DEDICATED_MODELS_PLAN.md) remains uncommitted, as before.

## Finding 1 — SECURITY: vault note content is publicly readable, unauthenticated, right now

Not doc drift alone — a live exposure created by this commit landing behind
a doc claim that turns out to be false. Verified from outside (real HTTPS
through Cloudflare, no cookies/credentials), ~20:40 EDT:

- The new `GET /api/v1/vault/file` (this commit) has **no tier dependency**
  — anonymous by design, presumably reasoned as tailnet-only. But the
  public `dispatch.example.com` vhost proxies the `/api/v1`
  surface to web :8000, and the docs' **"Cloudflare Access gated" claim
  (README.md:50, README.md:158, INFRA_MAP.md:218) does not hold in
  practice**: `curl https://dispatch.example.com/api/v1/vault/file?path=corporatetraveldc/00-Inbox/…`
  → **200 + full note content**, no Access redirect. `/healthz`,
  `/api/v1/cps`, and `/api/v1/knowledge-graph/{html,meta}` also answer
  unchallenged, so the Access gate is absent for the whole hostname (when
  it lapsed is not determinable from this box — Cloudflare-side config;
  pre-existing relative to this commit, but load-bearing only now).
- Compounding: the **public** `/api/v1/knowledge-graph/html` (from
  `1781555`, also tierless) embeds `source_file` for every node — a full
  index of all 254 vault note paths. Index + by-path reader = the whole
  second-brain vault (EP/OSINT/business notes) enumerable and readable by
  anyone, no auth.
- Also minor: public `knowledge-graph/meta` now discloses the tailnet
  hostname via the new `file_open_base` URL.
- What ISN'T exposed: traversal guard works (`..`/leading-`/` → 400);
  missing path → 404; demo runner (`:8005`, untrusted `CF-Connecting-IP`)
  → 404 on the proxied path, so the password-gated demo does not leak;
  vault WRITE (`/api/v1/remember`) remains admin-tier; the `X-CTDI-Public`
  T0-pinning itself still works as documented (it just doesn't matter for
  a tierless endpoint).
- `SECOND_BRAIN_STATUS.md`'s privacy-review reasoning ("Nextcloud's WebDAV
  port (8090) is published on `127.0.0.1` only") is literally still true,
  but the new T0 read path bypasses the isolation that claim was standing
  in for.

**Alert pushed to `ops-health` (priority 4) at check time.** Remediation is
the operator's call — any of: tier-gate the endpoint (T1), block
`/api/v1/vault/*` + `/api/v1/knowledge-graph/*` in the public nginx vhost,
or restore the CF Access policy — and re-align the three "CF Access gated"
doc claims to whatever the fix is. The README/INFRA_MAP rows are wrong
today regardless of which fix lands.

## Other findings

1. **`src/web/main.py` "Route structure" docstring** — doesn't list the new
   `/api/v1/vault/file`; but it was already missing board/osint/
   knowledge-graph/watchlist/opsplan/runsheet/aircraft routes, so this is
   one more row of pre-existing docstring rot, not new-in-kind. Code
   docstring, not docs/ — fix opportunistically next main.py edit
   (manifest re-sign required).

## Resolved from prior checks

- **Check #3 finding 1 (commit's own manifest INTEGRITY FAILURE, 6
  mismatches) is resolved by this commit** — `scripts/verify-manifest.sh`
  → OK, signature valid, all 624 files match. The commit message's
  "Re-signed manifest to match" claim is true.

## Verified still accurate (live checks, ~20:35–20:45 EDT)

- **Deployment is real:** web container Up 2 min at check time (rebuilt +
  restarted post-commit); endpoint live on :8000 (real vault path → 200
  themed HTML, matching the commit's dark-mode claim).
- **The commit's routing story checks out end-to-end:** runner proxy
  `:8001/api/dispatch/api/v1/vault/file?path=…` → 200, i.e. the
  transparent-GET-proxy behavior README.md:244 and
  `docs/auth-token-proxy-pattern.md` document ("frontend calls
  `/api/dispatch/api/v1/your-new-endpoint` with no token") is exactly what
  viz rev 4's iframed same-origin path relies on. The `file_open_base`
  fallback URL (ts.net → runner :8001 → `/api/dispatch` → :8000) matches
  README:157 / INFRA_MAP:225 documented tailnet routing.
- **`nextcloud_dav_base` → `file_open_base` rename in graph.json meta:**
  zero doc references to the old key (grep across README/CLAUDE/docs/
  src `*.md`) — nothing invalidated by the rename.
- **Vault-side standalone copy is current:** `04-Syntheses/vault-graph.html`
  carries the rev-4 template (iframed/`file_open_base` markers present —
  checked through the new endpoint itself).
- **Commit-message claim about `cloudflared/config.yml`** (dispatch-runner.
  = public rolling-demo hostname) matches the actual config.
- **`docs/SECOND_BRAIN_STATUS.md` viz description** ("click-through to node
  type + vault source file + per-edge neighbor list") doesn't pin the old
  DAV-link mechanism, so the click-through rework invalidates nothing
  there — the mechanism commentary lived in `build_graph.py`'s comment,
  which this commit rewrote in place.
- **Untouched surfaces:** `src/ingest/README.md`,
  `src/shared/watchlist_README.md`, CLAUDE.md (no knowledge-graph/vault
  claims), ALERT_REFERENCE/INFRA_MAP timer sections — this commit touches
  none of those subsystems. Checks #1–#3's still-open items (governor-watch
  / uber-traffic-watch cataloging, `/events`+`/graph` route-table rows,
  SDR status, V33 snapshot) remain open and are unchanged by this commit.

---

# Fifth check, same date — commit `cbd69d7` (tier-gate second-brain vault, knowledge-graph, and OSINT scope endpoints)

Appended ~22:45 EDT, after `cbd69d7` landed as HEAD (22:34 EDT). Same rules:
does THIS commit invalidate anything the current docs claim? Checked against
working-tree doc versions and the live system. Findings only — nothing
fixed, staged, or committed here; this check modified only this file.

**Concurrent-session caveat:** a linked worktree is active at
`/tmp/live-pentest-redteam-2026-08-13-worktree` (branch
`live-pentest-redteam-2026-08-13`, at `cd5f7d3`) — the second pentest pass
the commit message credits is evidently still in flight, and it (or the
operator) re-signed `MANIFEST.sha256`/`.asc` as uncommitted working-tree
edits at 22:36:49, mid-check (see Drifted #0 — those edits are
load-bearing). The model-selection in-flight set (CLAUDE.md, README.md,
build-models.sh, 16 Modelfiles, DEDICATED_MODELS_PLAN.md) remains
uncommitted, as before.

## Resolved from prior checks — the headline security finding is closed at the app layer

Check #4's Finding 1 and `LIVE_VALIDATION_AND_PENTEST_2026-08-13.md`
Part 2 item 6 (public, unauthenticated vault/knowledge-graph read) are
**remediated by this commit**, verified live ~22:36 EDT:

- **:8000 unauthenticated:** `knowledge-graph/meta`, `knowledge-graph/html`,
  `vault/file`, `osint/scopes` (GET and POST) → all **403**.
  `osint/feed` → still 200 (T0 by design, unchanged — matches the commit's
  stated scope).
- **From the public internet** (real HTTPS, 1 request each):
  `dispatch.example.com/api/v1/knowledge-graph/meta` → **403**,
  `…/osint/scopes` → **403**. The vault is no longer publicly readable.
- **Trust-conditional injection works both ways as claimed:** runner proxy
  from loopback (trusted) → 200 on both kg/meta and osint/scopes; same
  request with `CF-Connecting-IP: 203.0.113.50` (spoofed public origin) →
  403. Real tailnet/PWA use is unaffected; every actual caller goes through
  the runner proxy (`GraphView.jsx`, the viz template's same-origin/ts.net
  `file_open_base` paths — repo-grep confirms no direct-:8000 callers, and
  the commit's "nothing calls `/api/v1/osint/scopes`" claim is accurate).
- **Deployment is real:** web Up 10 min / runner Up 9 min at check time.
  (They were rebuilt from the *staged* content a few minutes before the
  commit landed — consistent with the commit message's staged-not-committed
  story; deployed code == HEAD either way.)
- **Manifest (worktree):** `verify-manifest.sh` → OK, signature valid, all
  625 files match (run BEFORE this append; see the finding below on the
  *committed* manifest). This append re-introduces the benign
  collective-check mismatch on this one doc (see pentest doc Part 2 item 1);
  scoped `verify-manifest.sh src/` re-run after the append → still OK, all
  194 files — runtime guards unaffected. Fold this file into the next
  re-sign.

## Still open — carried forward, explicitly deferred by the commit

1. **"CF Access gated" is still false for `dispatch.example.com`**
   (README.md:50, README.md:158, INFRA_MAP.md:218). Public `/healthz` → 200
   with no Access challenge at check time. The commit message itself flags
   this as a Cloudflare-dashboard issue it does not fix; the new tier gates
   are the effective defense. The three doc rows remain wrong until either
   the Access policy is restored or the wording changes.
2. **Runner `_is_trusted()` CF-Connecting-IP spoof** (commit's second
   deferred finding) — confirmed live in the trust-*raising* direction:
   `CF-Connecting-IP: 100.64.0.5` from loopback → treated trusted → 200
   with token injection. Edge-mitigated today (Cloudflare overwrites the
   header; no direct inbound port), unfixed in code, exactly as the commit
   states.

## Drifted — doc claims invalidated by THIS commit

0. **The commit's own "Re-signed manifest to match" claim is false for the
   commit content** — `cbd69d7` touches only the two `main.py` files, not
   `MANIFEST.sha256`/`.asc`. Verified three-way: HEAD's committed manifest
   still carries the PRE-commit hashes for `src/web/main.py` and
   `src/runner/main.py` (`f40d9257…`/`3ae3a454…`) while HEAD's actual file
   content hashes `527b548a…`/`3d6891f6…`. The matching re-signed manifest
   exists only as **uncommitted working-tree edits** (mtime 22:36:49, two
   minutes after the commit — presumably the concurrent pentest session or
   operator; +4/−3 lines: the two main.py hashes, this doc, and the newly
   covered pentest doc). Same class as check #3 finding 1, inverted: code
   committed, manifest not. Consequence: a rebuild from *committed* main
   (fresh clone, or if the working-tree MANIFEST edits are discarded) fails
   verification on the two changed files and containers/skills refuse to
   run. The working tree itself verifies clean (modulo this doc). **The
   uncommitted MANIFEST.sha256/.asc edits must ride along with the next
   commit.**
1. **`docs/auth-token-proxy-pattern.md`** — documents a single,
   unconditional injection allowlist: §4 "Path allowlist" and §5's code
   snippet show only `_TIER1_PATHS`, and "Extending to other Tier-1
   endpoints" says adding a path there is the whole recipe. The commit adds
   a second allowlist, `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`
   (runner/main.py:1490), whose injection is conditional on
   `_is_trusted(request)` — a deliberately different pattern for
   operator-only endpoints (vault/kg/osint-scopes) vs. the
   public-Ops-widened set. The doc's extension recipe now under-specifies a
   real decision (unconditional vs. trusted-origin-only) and its injection
   pseudocode no longer matches the code.
2. **`docs/dispatch-runner-design.md`** ("Tier-1 token injection" item 3) —
   same story: describes only the `_TIER1_PATHS` allowlist, no mention of
   the new conditional list.
3. **README.md:188** — the API table row
   `GET/POST/PATCH/DELETE /api/v1/osint/*` sits in the **Tier 0** section.
   Now wrong three ways: `osint/scopes` GET is T1, scopes POST/PATCH/DELETE
   are admin; only the feed/items reads remain T0. Row needs splitting
   across the table's tier sections. (README is in the in-flight
   model-selection edit set — good moment to fold this in.)
4. **README.md / dispatch-runner-design.md API tables** never gained rows
   for `/api/v1/knowledge-graph/{html,meta}` or `/api/v1/vault/file`
   (pre-existing omission from `1781555`/`cd5f7d3`, noted in check #4) —
   flagging here because whoever adds them should now put them under
   **Tier 1**, not Tier 0.

## Dated records — superseded on remediation status, no edits needed

- **`docs/LIVE_VALIDATION_AND_PENTEST_2026-08-13.md`** item 6 says
  "CONFIRMED, unremediated as of this snapshot" and "the finding to lead
  with" — true against `cd5f7d3` when written, remediated at the app layer
  by `cbd69d7` ~2 h later. Its "Open security items" summary should be read
  with that update (CF Access absence and the pre-commit-hook gap, item 5,
  do remain open). Note the doc's header says it was written to local
  branch `live-validation-pentest-2026-08-13`; the branch exists, and the
  file is *also* present untracked in the main working tree — two copies of
  the same record.
- Check #4's `ops-health` alert and remediation options: the "tier-gate the
  endpoint (T1)" option is the one that landed.

## Verified still accurate

- **CLAUDE.md / INFRA_MAP §7 auth-tier model** ("Bearer-token only —
  network origin grants no tier") — not invalidated; the commit conforms
  (the runner injects a real bearer token; web never trusts origin).
  Minor code nit: the new web/main.py docstring says "T1 == Tailscale-origin
  per auth.py", which contradicts auth.py and every auth doc — the
  runner-side comment in the same commit states the correct token-only
  model. Fix wording next main.py edit (manifest re-sign required).
- **`docs/SECOND_BRAIN_STATUS.md` / `SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md`**
  — neither makes an access/auth claim about these endpoints (grep); the
  graph automation prompt has no unauthenticated curl steps. Nothing
  invalidated.
- **Untouched surfaces:** `src/ingest/README.md`,
  `src/shared/watchlist_README.md`, ALERT_REFERENCE — different subsystems,
  no claims touched. All still-open items from checks #1–#4 (watchdog
  cataloging, `/events`+`/graph` route rows, SDR status, V33 snapshot,
  `ANTHROPIC_FALLBACK_ENABLED` paragraphs, and the pentest doc's Finding A
  dedicated-models-absent regression) remain open and unchanged.

## Noticed in passing (pre-existing, NOT caused by this commit)

- **`docs/dispatch-runner-design.md` flatly contradicts the code on
  watchlist injection:** "Watchlist reads are deliberately NOT injected —
  dispatch-web gates them itself." Since 2026-07-21, `_TIER1_PATHS` has
  included `api/v1/watchlist` and `api/v1/watchlist/history`
  (runner/main.py:1471-1472), with a comment explicitly reversing that old
  policy per operator direction ("Ops sees everything, view-only").
  `auth-token-proxy-pattern.md`'s §4 three-path listing is stale the same
  way. Both docs carry 2026-08-11 "verified" dates but missed this; fix
  both when the new conditional-injection section (Drifted #1/#2) is
  written, since it's the same two paragraphs.
