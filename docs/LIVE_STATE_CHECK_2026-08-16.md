# Live State Check — 2026-08-16

Written ~13:15 EDT, immediately after `f57744d` landed as HEAD (13:07 EDT):
"Fix dispatch-context-guardian install; rename thermal-guard alerts; attempt
EP-advance leak fix and ops-brief echo fix" — 9 files: the guardian skill
(`SKILL.md` added, `context_hook.py` `SKILL_DIR` derived from `__file__`,
`save_dispatch_state.py` `DISPATCH_BASE` → `http://100.x.x.x:8000` with
env override), `scripts/thermal-ingest-guard.py` (trip/restore alert titles
now `Thermal Guard` / `Load Guard` / `Thermal+Load Guard`, `guard_label`
persisted in the state file), `corporatetraveldc.ep-advance` (+8-line
anti-echo paragraph), `src/poller/skills/ops_brief.py` (+21-line trailing
"write the briefing now, don't echo" trigger), the 08-15 check doc, and the
manifest re-sign. Same rules as the 08-12/13/14/15 checks: does THIS commit
invalidate anything README.md, CLAUDE.md, docs/, `src/ingest/README.md`, or
`src/shared/watchlist_README.md` currently claim? Verified against the live
box, not prior docs. Nothing staged, committed, or changed live.

## Live snapshot verified

- `scripts/verify-manifest.sh` against HEAD: **OK, 650 files.**
- 117 `corporatetraveldc-*` user units loaded; 25 containers up.
- Ollama: 21 `corporatetraveldc-pi5-*` models + `phi3:mini`, nothing else
  (the `dispatcher-baseline-test` orphan flagged in
  `PHASE4_VALIDATION_2026-08-16.md` §6 is gone). **`pi5-ep-advance:latest`
  was rebuilt ~11:10 EDT** and its live SYSTEM prompt byte-matches the
  committed Modelfile including the new anti-echo paragraph — the
  Phase-4 doc's "SYSTEM + params byte-match the on-disk Modelfiles for
  ops-brief and ep-advance" claim still holds post-commit.
- `localhost/corporatetraveldc-poller:latest` (the image `ops-brief`,
  `ep-advance` and the poller itself run from) was built **2026-08-15
  23:36 EDT — before this commit.** `podman run … grep` confirms the image's
  `ops_brief.py` does not contain the new trailing trigger. Matches the
  commit message ("not yet deployed"); needs `build-images.sh poller` +
  restart before it does anything.
- `thermal-ingest-guard.service` `ExecStart` runs the script straight from
  the repo working tree (`/opt/corporatetraveldc/private/…/scripts/
  thermal-ingest-guard.py`), so that change was live the moment the file
  was saved — see drift item 2.
- Guardian hook is wired in `~/.claude/settings.json` `hooks.Stop` at the
  repo path; all seven Tier-0 endpoints in `save_dispatch_state.py`
  return 200 at `http://100.x.x.x:8000`, `/api/v1/runsheet` 403
  without a token, and `https://dispatch.example.com/healthz`
  302s to Cloudflare Access — the code comment's reason for the switch is
  correct.

## Drift found

### 1. New `SKILL.md` misstates what the public copy will show for `DISPATCH_BASE`

`.claude/skills/dispatch-context-guardian/SKILL.md` ("Configuration"):
"`DISPATCH_BASE` defaults to `https://ops.example.com` in the public copy of
this repo". `scripts/scrub-public-tree.py` has no such substitution — its
only rule for that address is `100.x.x.x → 100.x.x.x` (line 151), and
`.claude/` is not in `DROP_DIRS`/`DROP_FILES`. The public mirror will read
`http://100.x.x.x:8000`, not `https://ops.example.com`. Minor, but it's a
brand-new claim in a brand-new doc. Fix: either add a scrub rule or change
the sentence to describe the placeholder that will actually appear.

### 2. Thermal-guard alert rename IS live-verified (commit message says it isn't)

Not a repo doc, but the commit message — the only record of the change —
says "Not yet live-verified against a real trip." Because the timer runs
the working-tree script, the rename fired for real before the commit.
ntfy `ops-health` history (loopback `:2586`, `since=4h`):

| EDT | title | trigger |
|---|---|---|
| 11:10:45 | `Thermal Guard -- TIER 2 shed` | load 28.15 (old code) |
| 11:31:05 | `Thermal Guard -- TIER 1 shed` | load 13.02 (old code) |
| 12:27:36 | `Thermal/Load Guard -- restored` | fallback label (state file predated `guard_label`) — the fallback branch works |
| 12:29:29 | `Load Guard -- TIER 2 shed` | load 19.31 |
| 12:48:27 | `Load Guard -- restored` | |
| 12:51:32 | `Load Guard -- TIER 2 shed` | load 28.96 — **current state**, `guard_label: "Load Guard"` in `thermal_ingest_guard_state.json` |

All three code paths (trip label, fallback restore label, persisted restore
label) have now been exercised live. Nothing to change in the repo; noted
so the next person doesn't re-test it.

### 3. Pre-existing gap sharpened by this commit: the docs describe a temperature-only guard, but every trip today was load, and the alerts now say so

`docs/DATA_SOURCES.md:715–731` ("Thermal ingest guard"), `docs/
GUARDRAILS_JUSTIFICATION.md:162–167`, `docs/HARDWARE_GUIDANCE.md:35`, and
`docs/benchmarks/THERMAL_BASELINE_2026-08-10_nas-case-argument.md:37` all
describe tier 1/2 as 74 °C / 79 °C with resume at 65 °C — nothing else. The
script has had independent 1-min load triggers since 2026-08-11 (commit
`1781555`, 08-12): `THERMAL_GUARD_TIER1_LOAD=10.0`, `TIER2_LOAD=14.0`,
`RESUME_LOAD=6.0` (defaults; neither `config/dispatch.env` in the repo nor
the live `/etc/corporatetraveldc/dispatch.env` sets them, so DATA_SOURCES'
"see that file for the full rationale and defaults" doesn't cover them),
and RESUME requires *both* signals under threshold. Today's six trips were
all load (peak temp 63.9–66 °C, well under 74). Until this commit that was
invisible in the alert stream — every title said "Thermal Guard" — so the
docs and the alerts agreed even though both were wrong about the cause.
Now an operator seeing `Load Guard -- TIER 2 shed` and opening
`DATA_SOURCES.md` finds no mention of a load tier, a load threshold, or
that title. `docs/ALERT_REFERENCE.md:298–304` (topic `ops-health`, p5/p4/p3
on tier-2/tier-1/restore, hand-rolled `urllib`) is still accurate — it
never stated the titles. Suggested one-paragraph fix in DATA_SOURCES:
list the three `_LOAD` tunables, say either signal trips a tier and both
must clear to restore, and that the alert title names the tripping signal.

## Still accurate (checked because this commit could have touched them)

- **README.md / CLAUDE.md say nothing about the dispatch-context-guardian**
  (grep for guardian/context_hook/900k across README, CLAUDE, docs/: zero
  hits outside the new SKILL.md), so the `SKILL_DIR`/`DISPATCH_BASE` fixes
  invalidate nothing there. `README.md:50/158`, `docs/INFRA_MAP.md:220`
  ("`dispatch.example.com` … CF Access gated; served as Tier 0")
  agree with the live 302 the script comment now documents.
- **Doc line-number references into touched files** — `ops_brief.py:4`,
  `:679`, `:707` (LIVE_STATE_CHECK_2026-08-15, PHASE4_VALIDATION,
  PHASE4_FIXES_VALIDATION) all precede the insertion at line 775; no doc
  cites a line in `thermal-ingest-guard.py`, `context_hook.py`,
  `save_dispatch_state.py`, or the ep-advance Modelfile by number.
- **`README.md:319` / `ALERT_REFERENCE.md:207`** — `ops-brief` and
  `ep-advance` are hourly (`OnCalendar=*-*-* *:00:00` / `*:30:00`
  America/New_York, live timers match repo). Unchanged.
- **CLAUDE.md "brief skills pass `allow_anthropic=False`"** — the
  `ops_brief.py` edit is a prompt-content change only; call-site flags
  untouched.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — nothing in
  this commit touches their subject matter.
- **`PHASE4_VALIDATION_2026-08-16.md` §6** — see snapshot: model list,
  Modelfile byte-match, thermal timer active/waiting all still hold; the
  orphan-model fix-list item is done.

## Live observations (not doc drift)

1. **The ep-advance anti-echo paragraph is live and, as the commit message
   says, does not work.** The 12:30 EDT run used the rebuilt model
   (`brief generated via Ollama/corporatetraveldc-pi5-ep-advance:latest`,
   12:59 EDT, ~25 min for the main brief) and the archived brief still
   echoes section descriptions as content ("PRINCIPAL MOVEMENT … DC Metro
   + 50-mile ground transit advisory: No active closures, POTUS corridor
   impacts, vehicle staging approach."). Needs the prompt restructure the
   commit message calls for, not more trailing instructions.
2. **The ops-brief echo fix is not deployed and the failure it targets is
   still visible.** Latest archived `ops` brief (16:15Z / 12:15 EDT, old
   image) opens with `OPS BRIEF DATA PULL 2026-08-16 16:00 UTC` — the raw
   data heading regurgitated — and contains zero mentions of Amtrak or NWS
   (8167 chars). Consistent with the commit's diagnosis; nothing to judge
   until the poller image is rebuilt and the 14:00 EDT (or later) run lands.
3. **thermal-ingest-guard is at tier 2 (load)** since 12:51:32 EDT — only
   `ingest-core` and `ingest-notam` are running; tfms/stdds/fdps/tbfm/itws
   are stopped pending 300 s below 65 °C / load 6.0 (`below_resume_since`
   set 13:05:28, so restore was imminent at check time). Same load pattern
   the 08-15 check recorded; the four TIER-2 trips today were 19–29 load
   on a 57–66 °C box.
4. **`corporatetraveldc-runner-demo` is still crash-looping** — 08-15 check
   drift item 1 remains open: `NRestarts=25408`, `:8005` connection
   refused, `https://dispatch-runner.example.com/` → 502. The
   `README.md:49` "Live" claim is still false; nothing in this commit
   touched it.
5. **08-15 check items still open**: CLAUDE.md:147–158 / README.md:54,
   488–512 "16 dedicated models … 4 brief-class on phi3, rest gemma3:4b"
   (live: 21 models, all phi3:mini, gemma3:4b not present) and the
   ANTHROPIC_FALLBACK parenthetical — unchanged by this commit, not
   re-argued here.

---

# Second pass — ~13:20 EDT, after `67f8026` (13:11 EDT) landed as HEAD

Run by a separate session that found the first pass above already on disk
(untracked, written 13:13) when it went to create this file; appended
rather than overwritten. Scope: HEAD `67f8026` ("Allowlist two
illustrative example emails in scrub-public-tree.py") — 3 files:
`scripts/scrub-public-tree.py` (+6 lines: `firstname.lastname@army.mil`
and `drones@dhs.gov` added to `ALLOWED_EMAILS`) plus the manifest re-sign.
Independently re-verified the first pass's `f57744d` findings where cheap;
agreements noted inline, one addition below. Nothing staged, committed, or
changed live.

## HEAD `67f8026`: no doc drift

- The two emails exist only in `src/demo/scrub_rules.py:136–137` (a
  docstring) — grep of README.md, CLAUDE.md, docs/, `src/ingest/README.md`,
  `src/shared/watchlist_README.md` finds no other occurrence, so nothing
  doc-side names or depends on them.
- Every doc that describes `scrub-public-tree.py` does so generically —
  "allowlist `verify_scrubbed()` post-scan that fails the whole push"
  (`PENTEST_CLEARANCE_CHECK_2026-08-13.md:242–244`,
  `PHASE4_FIXES_VALIDATION_2026-08-16.md:46–67`, `COMPLIANCE_SECURITY.md:44`,
  `DEMO_DATA_ISOLATION_PLAN_2026-08-13.md:86–87`,
  `SECOND_BRAIN_STATUS.example.md:9`). None enumerates the allowlist
  contents, so extending it invalidates nothing. The fail-closed
  discipline those docs describe is exactly what caught these two strings
  in the first place — the commit is the docs working as written.
- Verified: the module imports and both entries are present in
  `ALLOWED_EMAILS` (`importlib` load of the script). `src/demo/` is not in
  `DROP_FILES`, so the docstring does ship to the public mirror and the
  allowlist is the right fix. **Not run end-to-end** — the script writes
  git objects (`mktree`), which this pass avoided touching.
- `scripts/verify-manifest.sh` against HEAD: **OK, 650 files** (re-run
  13:14 EDT).

## `f57744d` re-check — one addition to the first pass

**Drift 4 (missed above): `docs/SUDO_JUSTIFICATION_PROPOSAL.md:23–29`
says the approval-gate resolve endpoint has "no Cloudflare Access login
wall" on the public hostname — live it does.** The text (unchanged since
`1c9b1b4`, 2026-07-27, though the file was touched again in `0325a55`):
"the resolve endpoint answers cleanly over
`https://dispatch.example.com` from a fully external network
path … HTTP 200, correct JSON, no Cloudflare Access login wall despite the
cloudflared config's comment suggesting Access-gating. That comment looks
stale/aspirational, not enforced". Verified 13:14 EDT from this box (which
egresses to the public hostname over the internet, not the tailnet):

| path | result |
|---|---|
| `/healthz`, `/api/v1/feeds`, `/` | **302** → `fancy-unit-cd51.cloudflareaccess.com/cdn-cgi/access/login/…` |
| `/admin/approval-requests/test/resolve?action=allow` (the exact shape `sudo-approval-gate.sh:94–95` puts behind the ntfy Allow/Deny buttons, `RESOLVE_HOST=https://dispatch.example.com`) | **302** → Cloudflare Access login |
| `/robots.txt`, `/llm.txt` | 200 (Access bypass — matches `HONEYPOT_FAIL2BAN.md:204`) |

So the guardian commit's own comment ("Cloudflare Access now 302s
`/healthz` and the rest of `/api/v1/*` too") is right, and the SUDO doc's
"not enforced" claim is what's stale — the Access gate is enforced on
every app route, including the one the phone's Allow/Deny buttons hit.
Operational consequence: the buttons only work from a device that already
holds a Cloudflare Access session for that hostname (not verifiable from
this box; the operator's phone may well have one). `ALERT_REFERENCE.md:103`
("hit `/admin/approval-requests/{id}/resolve` directly from the
notification, no need to open the app") is true only under that same
condition. `README.md:50/158` and `INFRA_MAP.md:220` ("CF Access gated")
are consistent with live. Suggested fix: rewrite the SUDO doc bullet to
"Access-gated as of 2026-08-16 (verified 302); the phone needs an Access
session", and drop the "stale/aspirational" sentence.

Cross-checks of the first pass's other findings, all confirmed:
`~/.claude/settings.json` `hooks.Stop` → repo-path `context_hook.py`, and
`~/.config/Claude/dispatch_state_snapshot.json` (88 KB) rewritten at 13:02
EDT — the guardian install works; `pi5-ep-advance:latest` `/api/show`
`modified_at` **11:37:35 EDT** (first pass says ~11:10 — the 11:37 stamp
is what the server reports now; either way pre-commit and post-Modelfile-
edit) and its SYSTEM contains the anti-echo paragraph; poller image
`Created 2026-08-15 23:36:46 EDT`, running container started 23:39:32,
`grep -c "write the operational briefing now"` inside it = **0** — the
ops-brief change is not deployed; `.claude/` (4 tracked files) is in the
public tree and the only scrub rule touching the guardian's default is
`100.94.80.\d+ → 100.x.x.x`, so drift item 1's `ops.example.com` point
stands.

## Snapshot deltas since the 13:13 first pass

- **thermal-ingest-guard restored at 13:12:35 EDT** (`restored
  (tfms,stdds,fdps,tbfm,itws) at 57.85C load=2.55`); state file is now
  `{"tier": 0, "below_resume_since": null}` and all seven ingest
  containers are up (5 restarted 13:12–13:13). Containers up: **30**
  (first pass's 25 was mid-shed). 117 units loaded, unchanged.
- `runner-demo`: `NRestarts=25461` at 13:17, same
  `sqlite3.OperationalError: unable to open database file`, `:8005` still
  refused. `demo-api :8004/healthz` still `{"ok":false,"demo_db":
  "/var/lib/corporatetraveldc/demo.db"}`; `demo-source-refresh.timer`
  still `disabled`. 08-15 items 1–3 remain open; nothing in either of
  today's commits touched them.
- Live `dispatch.env`: `OLLAMA_TIMEOUT=3600`,
  `ANTHROPIC_FALLBACK_ENABLED=false`, `OLLAMA_BASE_URL=http://100.x.x.x:11434`
  — unchanged, so the CLAUDE.md:83/156–158 discrepancies from the 08-15
  check still stand.
- Poller journal since its 23:39 restart: zero integrity/manifest lines.
- Working tree: clean except this untracked file.

## Bottom line (both passes)

Today's two commits invalidate almost nothing in the docs. The only new
text-vs-reality drift attributable to them is (a) the brand-new SKILL.md's
`ops.example.com` sentence (item 1) and (b) the `SUDO_JUSTIFICATION_PROPOSAL.md`
"no Access wall" claim that the guardian commit's live finding exposes
(item 4 above). Item 3 (temperature-only guard narrative in
DATA_SOURCES/GUARDRAILS/HARDWARE_GUIDANCE) is older but is now visible in
the alert stream and worth a paragraph. HEAD `67f8026` has no doc surface
at all. The big pre-existing items — 21-phi3-model narrative in
CLAUDE.md/README.md, `ANTHROPIC_FALLBACK` parenthetical, `240 s`/`600 s`
timeout cells, and the crash-looping public demo that README calls "Live"
— are unchanged and still the ones most worth fixing.

---

# Third pass — ~13:50 EDT, after `8577452` (13:48 EDT) landed as HEAD

Scope: HEAD `8577452` ("Add board-write token self-rotation gated on
weekly GPG presence attestation") — `src/common/db.py` (+107: board-write
token TTL 7 d → 1 d, new `board_presence` table, `board_presence_set/
status`, `board_refresh_token`), `src/web/main.py` (+36: `GET
/api/v1/board/refresh`), new `scripts/board-presence-attest.sh` and
`scripts/board-presence-ingest.py`, manifest re-sign, and the first two
passes of this file (now tracked). Same question as above: does THIS commit
invalidate anything README.md, CLAUDE.md, docs/, `src/ingest/README.md`, or
`src/shared/watchlist_README.md` currently claim? Verified live. Nothing
staged, committed, or changed live; this append is the only working-tree
edit.

## Live snapshot verified

- `scripts/verify-manifest.sh` against HEAD: **OK, 653 files** (13:49 EDT).
- 117 units loaded; 26 containers up — thermal-ingest-guard is back at
  **tier 2 (Load Guard)** since 13:45:46 EDT (`shed_at` 1786902346, peak
  load 14.11 @ 63.9 °C, `below_resume_since` set 13:49:46 so restore is
  imminent); only `ingest-core`/`ingest-notam` running. Same load-not-heat
  pattern as every trip today (first pass, drift item 3).
- **The web image predates the commit.** `localhost/corporatetraveldc-web:
  latest` was built 2026-08-15 23:36:35 EDT and `systemd-corporatetraveldc-web`
  has been up since 2026-08-14 08:42 EDT. Inside the running container
  `grep -c board/refresh /app/src/web/main.py` = **0**, `db.py:348` still
  reads `_BOARD_TOKEN_TTL_S = 7 * 86400`. Live: `GET
  http://127.0.0.1:8000/api/v1/board/refresh` → **404** (with or without
  `X-Board-Key`); `/api/v1/board/enroll?nonce=x` → 401 (old code, still
  mints 7-day tokens). So the repo describes daily self-rotating tokens; the
  box still runs flat 7-day tokens with no refresh route until
  `build-images.sh web` + restart. The commit message doesn't claim
  deployment and no doc does either — recorded so nobody reads the new
  `db.py` comment ("Now: tokens are daily…") as a live fact.
- **DB (`corporatetraveldc.db`)**: `board_presence` table exists (created
  by the commit's own DB-layer test run against the live DB) and is
  **empty**; `/var/lib/corporatetraveldc/board-presence.asc` does not
  exist — `board-presence-attest.sh` has never been run. `board_tokens`
  holds one row, `cowork-opml`, minted 2026-08-08 00:06Z, **expired
  2026-08-15 00:06Z**; three `cowork-research` nonces minted 2026-08-16
  04:06–04:33Z were never consumed. Net: there is currently **no valid
  board-write token at all**, and once the new web image ships, `refresh`
  will 403 (presence stale) until the operator runs the attest script and
  hands out a fresh enrollment nonce — exactly the fail-closed behaviour
  the commit describes.
- **`audit_log`**: the four `board_refresh` rows stamped 17:46:03Z
  (`presence_stale`/`ok`/`invalid_token`/`presence_stale`, tier `board`,
  `127.0.0.1`) from the commit's test run are the **only four rows the
  table has ever held** (`count(*)=4`, all `egress_status=pending`;
  `COMPLIANCE_HOOK_ENABLED=false` live so they will not ship). The test
  cleaned up its `board_tokens`/`board_presence` rows but — correctly,
  the log is append-only — not these. Worth knowing when someone first
  opens `/admin/audit`.
- **Public reachability of the board routes** (from this box over the
  internet, 13:50 EDT): `https://dispatch.example.com/api/v1/board`
  → 200, `/api/v1/board/threads` → 200, `/api/v1/board/enroll?nonce=x` →
  **401** (reaches the app), `/api/v1/board/refresh` → 404 (reaches the
  app; not yet deployed) — while `/healthz` and `/api/v1/feeds` still 302
  to Cloudflare Access. `/api/v1/board*` is an Access bypass, so the
  public-hostname enrollment URL `board-presence-ingest.py` prints for
  Cowork will actually work, and `refresh` will too once deployed.

## Drift found

### 5. `board-presence-attest.sh` header points at `docs/COMPLIANCE_SECURITY.md`, which says nothing about the board

`scripts/board-presence-attest.sh:9` — "GET /api/v1/board/refresh -- see
docs/COMPLIANCE_SECURITY.md". `COMPLIANCE_SECURITY.md` has zero
occurrences of board / X-Board-Key / refresh / attest / clearsign; its
`audit_log` section (§3, lines 68–96) says the log records "every admin
action taken through the platform's API" and never mentions the new
`board_refresh` action (or `cui_status_read`, the only other caller of
`db.audit()`). No doc anywhere in scope describes the presence-attestation
gate, the daily TTL, or `GET /api/v1/board/refresh` — the closest are
`README.md:189` ("posts need `X-Board-Key`") and `INFRA_MAP.md:276–277`
("`X-Board-Key` + one-time enrollment nonces"), both still true but now
incomplete. Fix: either write the paragraph the script points at (in
COMPLIANCE_SECURITY §3 or a new "Board-write token lifecycle" subsection
near INFRA_MAP §7), or drop the reference from the script header.

### 6. The script says "whenever a reminder fires" — nothing fires

`board-presence-attest.sh:12–13`: "run it yourself on a ~7-day cadence,
whenever a reminder fires (or proactively)". Grep of `.config/`
(user units + Quadlets), `scripts/`, `src/`: no unit, timer, ntfy sender,
or monitor references board presence / attestation. The only board-adjacent
timers are `board-sweep` and `research-board-mirror`. So the human step
the whole design leans on has no nudge; the first sign of a lapsed
attestation will be Cowork's refresh 403ing. Either build the reminder
(the hourly `brief-fallback-monitor` pattern would fit — check
`board_presence_status()['valid_until']`, warn on `ops-health` at T-24 h
and on lapse) or change the sentence to "no reminder exists yet".

### 7. Correction to the second pass (item 4): Cloudflare Access is NOT "enforced on every app route"

Second pass wrote "the Access gate is enforced on every app route,
including the one the phone's Allow/Deny buttons hit." The
`/admin/approval-requests/…/resolve` and `/healthz` / `/api/v1/*`
findings stand (re-verified 302 today), but `/api/v1/board*` bypasses
Access (see snapshot). The SUDO_JUSTIFICATION_PROPOSAL fix suggested there
is still right; just don't generalise it to "every route". No repo doc
lists the bypass set (`HONEYPOT_FAIL2BAN.md:204` implies robots/llm.txt
only) — one line in `INFRA_MAP.md` §6a naming the bypassed paths would
close it.

## Still accurate (checked because this commit could have touched them)

- **`README.md:189`, `INFRA_MAP.md:276–277`, `PENTEST_CLEARANCE_CHECK
  _2026-08-13.md:56/62` (board reads Tier-0 by design, POST gated by
  `X-Board-Key`)** — unchanged by the commit and re-verified live (public
  GET 200, enroll 401 without a valid nonce). The auth model paragraph in
  CLAUDE.md ("Bearer-token only — network origin grants no tier") is about
  `resolve_tier()`; the board uses its own header and isn't in scope of it.
- **`PENTEST_CLEARANCE_CHECK_2026-08-13.md:277` / `DEMO_DATA_ISOLATION
  _PLAN_2026-08-13.md:51`** enumerate the sensitive tables the demo copy
  never touches (`board_tokens`, `board_enroll_nonces`, …). The new
  `board_presence` table (holds the clearsigned attestation text and key
  fingerprint — not secret, but not demo content) is likewise outside the
  extract-allowlist by construction, so the claim holds; the list is just
  one table short if anyone reads it as exhaustive.
- **`CLAUDE.md:163–167` schema rules** — the new table is `CREATE TABLE IF
  NOT EXISTS` inside `_ensure_board_auth()` (lazy, like the other board
  tables), no version bump needed, nothing dropped or renamed. Consistent.
- **`COMPLIANCE_SECURITY.md:75–82` `audit_log` DDL and the egress envelope
  (`record_id, event_time_utc, source_node, action, tier, token_prefix,
  remote_addr, detail`)** — `board_refresh_token()` writes through
  `db.audit()` with an 8-char prefix and a small JSON detail; nothing new in
  the row shape. Still accurate.
- **CLAUDE.md:59–61 / README.md:435 signed-manifest rule** — the new
  scripts are tracked, in `MANIFEST.sha256`, and verify OK; the attest
  script self-verifies against `security/trusted-signing-key.pub.asc`,
  which is tracked and present, and `security/signing.env` has a real
  fingerprint (not the placeholder). The script will run as written.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — nothing in
  this commit touches their subject matter.
- **`board-presence-ingest.py`'s "10 min, single-use" nonce claim** matches
  `db.board_mint_nonce(ttl_s=600)` and the enroll route's 410 wording.

## Bottom line (third pass)

HEAD `8577452` invalidates no existing doc sentence — the docs never stated
a board-token TTL or a refresh path, so there is nothing to un-say. The
drift is inside the commit's own new text: it cites a doc that doesn't
cover it (item 5) and a reminder that doesn't exist (item 6). Live, none of
it is deployed yet (web image 2026-08-15 23:36, refresh 404, TTL still 7 d,
no attestation recorded, no valid board token). Item 7 trims an
over-general sentence from earlier today. Everything flagged in the first
two passes (SKILL.md `ops.example.com`, load-vs-thermal narrative, SUDO
"no Access wall", 21-phi3-model narrative, `ANTHROPIC_FALLBACK`
parenthetical, crash-looping `runner-demo`) is untouched by this commit and
still open.

---

# Fourth pass — ~14:40 EDT, after `cc863d2` (14:35 EDT) landed as HEAD

Scope: HEAD `cc863d2` ("Add Tier-0 vault research read/list endpoints for
credential-less agent tools") — `src/web/main.py` (+117: `_VAULT_RESEARCH_ROOT`,
`_vault_research_path_allowed()`, `GET /api/v1/vault/research?path=`,
`GET /api/v1/vault/research/list?path=`), the third pass of this file, and
the manifest re-sign. Same question: does THIS commit invalidate anything
README.md, CLAUDE.md, docs/, `src/ingest/README.md`, or
`src/shared/watchlist_README.md` currently claim? Verified live. Nothing
staged, committed, or changed live; this append is the only working-tree
edit. Probes were read-only (loopback/tailnet GETs — one real file fetched
for status + length only, content not dumped; one raw WebDAV `PROPFIND
Depth: 1` from the host using the standard `webdav_client` env to see
folder names).

## Live snapshot verified

- `scripts/verify-manifest.sh` against HEAD: **OK, 653 files** (14:36 EDT).
- **This commit IS deployed** (unlike `8577452` at third-pass time):
  `localhost/corporatetraveldc-web:latest` built **14:23:11 EDT**,
  `systemd-corporatetraveldc-web` started 14:23:13, `grep -c vault/research
  /app/src/web/main.py` inside it = 3. As a side effect the third pass's
  "`/api/v1/board/refresh` → 404, TTL still 7 d" is now superseded: the
  same image carries `8577452`, and `GET /api/v1/board/refresh` → **401**
  (route present, no `X-Board-Key`). The board_presence table is still
  empty and no attestation has been run, so refresh will 403
  `presence_stale` for a valid token — third-pass drift items 5–6 unchanged.
- Loopback behaviour of the new routes matches the code: `research/list`
  (default path) → 200; `research?path=…/personal-notes/x` → 400 (out of
  scope); `…/Family Office - CTDI` → 400; `list?path=../x` → 400;
  `research?path=…/Uber Series/Article 1.md` (via `100.x.x.x:8000`, no
  headers) → **200, 7 911-char `content`** — i.e. anonymous read works
  from the tailnet, as designed. `list` on `Uber Series` and `Research -
  Uber Series` → 6 files each. `/api/v1/vault/file` still 403 unauth
  (PENTEST_CLEARANCE §1a control intact).
- Vault reality under `01-Sources/personal-notes/` (PROPFIND): three
  folders — `Uber Series/`, `Research - Uber Series/`, `Family Office -
  CTDI/` — no loose files. Two in scope, one out, exactly as the docstring
  says.
- Web journal since the 14:23 restart: zero manifest/integrity lines, zero
  500s from the operator's own tests. The three 500s at 14:20:53–14:21:41
  (`list` on both Uber topics) came from the **previous** container
  (14:20:42 start, pre-`BUSINESS_ROOT`-prefix build — Nextcloud 404 on
  `…/files/corporatetraveldc/01-Sources/…`), fixed by the committed
  prefix. All test traffic in the journal originated from `100.x.x.x`
  (this box's tailnet address) — see drift item 10 for why that matters.
- Public hostname (from this box over the internet, 14:37 EDT):
  `/api/v1/vault/research/list` (with and without `?path=…Uber%20Series`)
  → **302** to `fancy-unit-cd51.cloudflareaccess.com` (Access login);
  `/api/v1/vault/file?path=x` → 302; `/api/v1/board` → 200 (bypass, as
  third pass found); `/healthz` → 302.
- 117 units loaded; 26 containers up — thermal-ingest-guard back at
  **tier 2 (Load Guard)** since 14:34:07 EDT (`shed_at` 1786905247, peak
  load 14.95 @ 64.45 °C, `below_resume_since: null`), only
  `ingest-core`/`ingest-notam` running. `runner-demo` `NRestarts=26100`,
  `activating`, `:8005` refused — 08-15 item 1 still open. `/healthz`
  `status ok`, snapshot 10 s, `token_count_active 5`, **CPS RED / NO-GO**
  at check time (weather/NAS state, not a platform fault — noted only
  because the earlier passes all saw GREEN).

## Drift found

### 8. New `research/list` docstring says the default path "lists the current topic folders" — it can't, and doesn't

`src/web/main.py` `vault_research_list` docstring: "path defaults to the
research root itself, which lists the current topic folders." Live:
`GET /api/v1/vault/research/list` → `{"path":"01-Sources/personal-notes",
"files":[]}` — while the root holds three topic folders. Cause is in the
callee, not the route: `second_brain/webdav_client.py:149–183`
`list_files()` is documented "List files (**not folders**)" and explicitly
`continue`s on `<d:collection/>` (line 179–181), so directories are never
returned. Consequence beyond the docstring: the "discovery counterpart"
role the docstring assigns to this route doesn't exist — an agent with no
prior knowledge of topic names cannot find `Uber Series` or `Research -
Uber Series` through the API; it must already know a topic to list it.
Fix: either add a `list_dirs()`/`include_dirs=` variant to `webdav_client`
and use it for the root case, or reword the docstring to "the root
listing is always empty (folders only); pass a known topic path" so the
public contract matches the code. `SECOND_BRAIN_STATUS.md:256` ("WebDAV
PROPFIND only returns files") already records this limitation for the
index — the new route walked into the same one.

### 9. `PENTEST_CLEARANCE_CHECK_2026-08-13.md` §1a "two independent controls" / "even if the edge were removed, the committed tier gates catch them" no longer holds for the research subtree

§1a (lines 65–105) closes the 08-12 vault exposure with two layers —
app-tier gate on every vault/knowledge-graph route *and* CF Access at the
edge — and states the second is redundant with the first. `LIVE_STATE_CHECK
_2026-08-12.md:455` (fifth check) says flatly "The vault is no longer
publicly readable." This commit deliberately re-opens one scoped subtree
of the vault (`01-Sources/personal-notes/{Research - *, * Series}/**`) at
Tier 0 with **no tier dependency**, so for that subtree the app layer is
now only the CUI/SSN `scrub_gate` (blocks radio-CUI / SSN-shaped tokens;
everything else is served) and the sole access control is the Cloudflare
Access edge — the layer both docs recorded as *absent* on 08-12/13 and
that came back later. Nothing in §1a is wrong about `vault/file`,
`knowledge-graph/*`, or `osint/scopes` (re-verified 403 today), but its
"both layers" generalisation, the M2 row's "Tier-based auth rejects
unauth" framing, and the 08-12 "no longer publicly readable" sentence now
need a carve-out: research-topic notes are Tier-0 by design as of
`cc863d2`, gated publicly by Access alone. Related incompleteness:
`README.md:160` Tier-0 table (a "selection", so not strictly wrong) and
the `src/web/main.py:4–20` "Route structure" docstring list neither
`vault/file` (T1) nor `vault/research*` (T0) — a security-relevant new
anonymous route is worth a row in both, since a future pentest pass will
grep them.

### 10. The commit's stated consumer cannot reach the endpoint the way the commit describes

Commit message and both docstrings: built "for agent tools that cannot
send an Authorization header or embed credentials in a URL (e.g. Cowork's
fetch tool)". Live: on `https://dispatch.example.com` both new
routes **302 to Cloudflare Access login** (snapshot above) — a client that
can't send headers can't hold an Access session or present a service token
either (INFRA_MAP §6a's template is header-based), so over the public
hostname the endpoint is exactly as unreachable to Cowork's fetch as the
board-write route was. The endpoints are credential-less only on loopback
and the tailnet (`100.x.x.x:8000`), and every test request in the web
journal came from that tailnet address. "Tested end-to-end against real
vault content" is true of the app; it is not evidence that the intended
client path works. Two ways to make the commit's premise true, and they
have different doc consequences: (a) route Cowork via the tailnet — then
the docstrings should say so and drift item 9 stays edge-gated; or (b) add
`/api/v1/vault/research*` to the Cloudflare Access bypass set alongside
`/api/v1/board*` — then the research subtree becomes **internet-readable
with only the scrub gate**, and §1a / README:158 "CF Access gated" / the
08-12 remediation record all need a stated exception, plus the bypass list
`INFRA_MAP.md` §6a still doesn't enumerate (third-pass item 7). No repo doc
currently records which is intended. Flagging as drift because the code
comment asserts a capability ("requires no credential at all") that is
false on the only hostname the named tool can reach today.

## Still accurate (checked because this commit could have touched them)

- **`SECOND_BRAIN_STATUS.md:275`** — `POST /api/v1/remember` remains
  admin-tier; the web Quadlet's `Network=pasta:--map-gw` +
  `NEXTCLOUD_WEBDAV_BASE=http://host.containers.internal:80/…` (lines
  25/34–35 of the `.container`) is what let the new routes reach WebDAV
  with no Quadlet change. Consistent.
- **CLAUDE.md auth-tier paragraph** ("Bearer-token only — network origin
  grants no tier … T0 forced by `X-CTDI-Public: 1`") — still true of
  `resolve_tier()`; the new routes never call it, so `X-CTDI-Public` is
  irrelevant to them (verified: identical 200 with the header). Not drift,
  but the same caveat the third pass gave for the board applies.
- **`webdav_client.py:14`/`:69` `BUSINESS_ROOT="corporatetraveldc"`
  nesting** — the committed prefixing is right; the double
  `corporatetraveldc/corporatetraveldc/` in Nextcloud URLs is the account
  root + business root, as `SECOND_BRAIN_STATUS.md:46` and the
  `knowledge_graph/retrofit_links.py` comment describe.
- **`PENTEST_CLEARANCE_CHECK_2026-08-13.md:56` M2 evidence lines** —
  `/admin/tokens`, `/api/v1/runsheet`, `/api/v1/vault/file`,
  `/knowledge-graph/meta` all still 403 unauth (re-verified 14:36).
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — nothing in
  this commit touches their subject matter.
- Doc line-number references into `src/web/main.py`: none in scope cite a
  line number past 152, and the insertions are at 155–171 and 1439–1537;
  `PENTEST_CLEARANCE` cites routes by name only.

## Live observations (not doc drift)

1. **Unhandled 500 on a well-formed but nonexistent topic.**
   `GET /api/v1/vault/research/list?path=01-Sources/personal-notes/Research%20-%20DoesNotExist`
   → **500 Internal Server Error** with a full traceback in the web
   journal (`requests.exceptions.HTTPError: 404 Client Error … PROPFIND`)
   — `list_files()` calls `raise_for_status()` and the route doesn't catch
   it. The docstring's contract is "200 / 400 / 429" with no 404. The
   `read` route handles the same case (`webdav_client.get` returns `None`
   → 404). One `try/except requests.HTTPError → 404` around the
   `list_files` call closes it. (Verified 14:38 EDT on the current
   container; my probe is the only post-restart 500 in the journal.)
2. **The 30/min rate limit is one shared counter** (`_vault_research_hits`
   is appended by both routes), so a list + reads burst is 30 total, not
   30 each; the two "rate limit (30/min)" messages read as per-route.
   Cosmetic unless an agent walks a topic with `list` + 6 `read`s in a
   tight loop several times a minute.
3. **`list` results are not scrub-gated** (file names only; content is
   gated on `read`). Fine today — the six names are `Article N.md` — but
   a note titled with an SSN would list even though it wouldn't read.
4. Third-pass drift items 5–7 and every earlier open item (SKILL.md
   `ops.example.com`, load-vs-thermal narrative in DATA_SOURCES/
   GUARDRAILS/HARDWARE_GUIDANCE, SUDO "no Access wall", 21-phi3-model
   narrative, `ANTHROPIC_FALLBACK` parenthetical, `runner-demo`) are
   untouched by this commit and still open. Live `dispatch.env` unchanged.

## Bottom line (fourth pass)

`cc863d2` is deployed and works on the tailnet exactly as coded, and it
invalidates no sentence about the routes it doesn't touch. What it does
drift is (8) its own `list` docstring — the "topic folder discovery" it
promises is impossible through `webdav_client.list_files()`, so the root
listing is always `[]`; (9) the 08-13 pentest clearance's "two independent
controls" story and the 08-12 "vault no longer publicly readable" line,
which now need a Tier-0 research-subtree exception; and (10) the commit's
own premise — over the only hostname a header-less tool can reach, the
route sits behind the same Cloudflare Access wall it was written to avoid,
so either the tailnet is the intended path (say so) or an Access bypass is
coming (then §1a/README "CF Access gated" need the exception spelled out).
Plus one real bug (unhandled 500 on a nonexistent in-scope topic) worth a
three-line fix.

---

# Fifth pass — ~21:55 EDT, after `171f7e4` (21:39 EDT, amended in place during this pass) landed as HEAD

Scope: HEAD `171f7e4` ("Vault Series/ scope + podcast feed resolver +
guardrails.py + faster watch cadence") — 14 files: seven timers gain
`OnUnitActiveSec=` (six daily-watch → 90 min, `second-brain-daily` → 2 h,
all on top of the existing daily anchors), the two `-pm` duplicate timers
move to `.config/systemd/user/retired-20260816/`, new
`src/common/guardrails.py` (SR1 mutation gate / SR2 model-tier routing,
ported from the agentic-tools MCP), new `scripts/podcast-feed-resolve.py`,
`webdav_client.delete()`, one aviation RSS entry, and the vault-research
scope in `src/web/main.py` widened (root → `01-Sources/personal-notes/
Series/` recursive, plus `04-Syntheses/`, `02-Concepts/`,
`00-Inbox/cross-link-findings/`). **HEAD moved under this pass:** it started
as `7209d1a`, whose message ("Fix DISPATCH_BASE_URL default to the real
tailnet IP…") described a commit in a different repo (`~/mcp/dispatch-mcp`
`05268e2`, 21:41 EDT — none of `server.py`, `tools/admin.py`,
`docs/mcpo-openwebui.md` exist here); by ~21:50 it had been amended twice
to `171f7e4` with the correct message and the podcast script/catalog
edits folded in. Same tree contents for everything checked below except
those two files (re-checked at `171f7e4`). Same question as the four
passes above; verified live; nothing staged, committed, or changed live —
this append is the only edit this pass made. Probes read-only (loopback /
tailnet / public GETs; one raw WebDAV `PROPFIND Depth: 1` from the host
via the standard `webdav_client` env, names + sizes only). The working
tree also carries an unrelated uncommitted `corporatetraveldc.ep-advance`
edit (+86/−57) that predates this pass and was not touched.

## Live snapshot verified

- **`scripts/verify-manifest.sh` against HEAD: INTEGRITY FAILURE.**
  `MANIFEST.sha256` was last signed at `cc863d2` (14:35 EDT); at HEAD the
  seven edited timers, `src/web/main.py`, `src/second_brain/webdav_client.py`,
  `src/shared/rss_catalog.py` mismatch, the two moved `-pm` timers are
  "listed but could not be read", and `guardrails.py` /
  `podcast-feed-resolve.py` are not in the manifest at all. (`corporatetraveldc.
  ep-advance` and this file also fail, but those are working-tree edits, not
  HEAD.) `corporatetraveldc-integrity-sweep.service` has failed and pushed
  `INTEGRITY SWEEP FAILED` to `ops-health` every 15 min since **19:01 EDT**
  (11 pushes 19:01–21:31 and counting) — same failure mode the 08-15 report
  §"manifest" recorded. Nothing in the repo is signed for this commit.
- **The web half of the commit IS deployed** — and it shipped unsigned.
  `localhost/corporatetraveldc-web:latest` was built **20:05:04 EDT** (before
  the commit, from the same file contents: `sha256` of `/app/src/web/main.py`
  and `/app/src/second_brain/webdav_client.py` inside the running container
  equal the HEAD files), `systemd-corporatetraveldc-web` started 20:05:08,
  `_VAULT_RESEARCH_ROOT = "01-Sources/personal-notes/Series"` live. Inside
  that container `scripts/verify-manifest.sh src/web/main.py` → **FAILED**,
  yet the container started normally — see drift item 12.
- Poller image (`localhost/corporatetraveldc-poller:latest`, 2026-08-15
  23:36 EDT — what all six daily watches and `second-brain-daily` run from
  via `Exec=scripts/verified-exec.sh …`) still verifies clean internally
  (`OK, 195 files under src/`); it does not contain `guardrails.py` or the
  new catalog entry, so the RSS addition is not live in the poller until a
  rebuild — which `verified-exec.sh` will refuse until the manifest is
  re-signed.
- **Timers, live vs repo:** all seven edited timer files in
  `~/.config/systemd/user/` are byte-identical to the repo copies; the two
  `-pm` timers are gone from the live dir and from `list-unit-files`
  (units loaded: **115**, was 117 at the fourth pass). `daemon-reload` ran
  19:24–19:33 EDT; `NeedDaemonReload=no`. **But five of the six daily-watch
  timers are `inactive` (enabled, stopped) since 19:47:12 EDT** — journal
  shows all six stopped at 19:47:12 and only `aam-daily-watch.timer`
  started again at 19:47:21. Live cadence right now: `aam-daily-watch`
  every 90 min (`LastTrigger 20:54:43`, next 22:24 EDT — the 20:54 elapse
  was a no-op because the 19:24 run was still going, exactly the "skips
  that firing" behaviour the timer comment describes, so that claim is
  live-verified); `second-brain-daily` every 2 h (`LastTrigger 21:33:27` =
  19:33:27 + 2 h, running at check time, `TimeoutStartSec=2600` = the
  "43 min ceiling" the comment cites); **aviation / gig-economy /
  concierge-travel / trains-yachts / executive-protection: no next elapse
  at all** — neither the 90-min cadence nor tomorrow's 07:45–08:45 anchors
  will fire until someone `systemctl --user start`s those timers (or the
  box reboots; they are enabled). Whether the stop was deliberate (load —
  see below) or an oversight isn't recorded anywhere.
- All six watch services were started together at **19:24:42 EDT** and ran
  concurrently for 43 min–1 h 35 min (finishes 20:08–21:00). `ops-health`
  shows Load Guard TIER-2 sheds at 19:25:58 (load 24.6), 20:06:03 (15.1),
  20:50:07 (27.6), 21:40:32 (20.8) and TIER-1 at 20:44/21:26 — ingest
  fdps/stdds/tfms/tbfm/itws were shed for most of 19:25–21:09 and again
  from 21:40. The timers' 15-min stagger only holds if the timers are
  (re)started 15 min apart; started together, `OnUnitActiveSec` keeps them
  in lock-step.
- `second_brain_daily.py:272–273` does `webdav_client.put()` to
  `01-Sources/daily/<today>.md` with no exists/skip check — the timer
  comment's "overwrites the same day-file, no dedup" claim is accurate.
  Both timers' `TimeoutStartSec` claims (8600 s = 2 h 23 min; 2600 s =
  43 min) match the live Quadlets.
- **New research-route behaviour on loopback matches the code:** default
  `list` → 200 `[]`; `list?path=04-Syntheses` (bare and trailing-slash) →
  5 files (`aam-ems-hyde-county-2026-08-13.md`, `device-tracker-surveillance
  -2026-08-09.md`, `homelab-stress-research-2026-08-08.md`,
  `project-knowledge-synthesis-2026-07-23.md`, `vault-graph.html`);
  `02-Concepts` → 1; `00-Inbox/cross-link-findings` → 47; `Series/Uber
  Series` → 6; `Series/Family Office - CTDI` → 0; old
  `01-Sources/personal-notes/Uber Series` → 400 (out of scope now);
  `00-Inbox`, `Docs`, `06-AI-Memory`, `00-Inbox/cross-link-findingsX`,
  `…/../rss` → 400; `research?path=04-Syntheses/x.md` → 404;
  `/api/v1/vault/file` still 403 unauth.
- **Public hostname (from this box over the internet, 21:50 EDT):
  `/api/v1/vault/research/list?path=04-Syntheses` → 200; `/api/v1/vault/
  research?path=04-Syntheses/aam-ems-hyde-county-2026-08-13.md` → 200,
  7 463-char `content`; `/api/v1/vault/research?path=04-Syntheses/
  vault-graph.html` → 200, 200 082 chars.** At the fourth pass (14:37) the
  same routes 302'd to Cloudflare Access; `/healthz`, `/api/v1/vault/file`,
  `/api/v1/knowledge-graph/meta` still 302. So a Cloudflare Access bypass
  for `/api/v1/vault/research*` was added between 14:37 and 21:50 EDT —
  fourth-pass item 10 option (b) happened. It is a dashboard-side rule:
  `cloudflared/config.yml` (last commit `f15c21e`, 08-05), `nginx/`, and
  every doc in scope have zero occurrences of `vault/research`.
- Vault reality (PROPFIND, host, business-root prefix): `01-Sources/
  personal-notes/` holds **four** folders — `Series/`, `Uber Series/`,
  `Family Office - CTDI/`, `Research - Uber Series/`. `Series/Uber Series/`
  has the 6 articles (00:02Z 08-17) plus a `research/` subfolder;
  `Series/Family Office - CTDI/` is empty; **the old `Uber Series/` (6
  files, 11 Aug) and `Research - Uber Series/` (6 files, 11 Aug) are still
  there in full**, and the old empty `Family Office - CTDI/` too. See item
  15.
- 27 containers up; `runner-demo` `NRestarts=29486`, `:8005` refused (08-15
  item 1 still open). `podcast-feed-resolve.py` (HEAD) runs and returns a
  real iTunes match with a `feedUrl` for the show whose feed was added;
  `PODCASTINDEX_API_KEY/SECRET` are not set in either env file, matching
  the commit's "not yet configured".

## Drift found

### 11. Every "daily / 23:45 / twice-a-day" cadence sentence for the watches and the digest is now wrong

- `docs/INFRA_MAP.md:164` "daily category watches 07:30–08:45 (+ 15:30/
  15:45 PM runs)" — the PM timers are retired and each watch now re-arms
  every 90 min after its last activation (~16×/day per the timer comment).
- `docs/INFRA_MAP.md:166` and `:346` "second-brain daily 23:45" /
  "daily digest 23:45", `docs/SECOND_BRAIN_STATUS.md:133` "Timer: 23:45 ET
  daily" — now every 2 h anchored at 23:45 (`Persistent=true` kept), so the
  day-file is rewritten ~12×/day; the 23:45 run is the "definitive"
  capture only in the sense that it's last.
- `docs/INVESTOR_MATERIALS_REVERIFICATION_2026-08-09.md:58` "aviation &
  AAM watches twice daily" — dated snapshot, but it's the doc investors get
  pointed at; now ~16×/day (or, live tonight, 0×/day for five of them —
  see snapshot).
- Cosmetic but visible in `systemctl`: the seven `.container` Descriptions
  and unit names still say "daily …"; the timers' `[Unit]` Descriptions
  were updated, the services' were not.
- `docs/DRIFT_GAPS_REPORT_2026-08-15.md:187` "aam-daily-watch (15:46 EDT
  PM run…" and `PHASE4_VALIDATION_2026-08-16.md:140/145` "6 daily watches"
  are historical/naming — fine as they stand.

### 12. Manifest not re-signed — and the web container proves the "refuse to run" guard doesn't cover the core containers

Repo state is as in the snapshot (unsigned HEAD, sweep alerting since
19:01). What's new versus the 08-15 finding is what happened live: the web
image was rebuilt at 20:05 EDT from unsigned `main.py`/`webdav_client.py`
and **started fine**, because `Containerfile.web`'s `CMD` is plain
`uvicorn web.main:app` and `corporatetraveldc-web.container` has no `Exec=`
override; `verified-exec.sh` is only *copied* into the image (since
`644796e`, 08-09). The same is true of `Containerfile.poller/pusher/
ingest/demo/amtrak-tracker/runner` and their Quadlets — no `Exec=` — so:

- `README.md:433–435` "container entrypoints run `scripts/verified-exec.sh`
  … rebuilt images will [not] start" and `CLAUDE.md:58–61` "container
  entrypoints and `llm.py` run `verify-manifest.sh` before executing …
  rebuilt containers/skills will refuse to run" hold for the **31 skill /
  watchdog Quadlets** that carry `Exec=scripts/verified-exec.sh …`, and for
  LLM calls via `llm.py`, but **not** for web, poller, pusher, the 7 ingest
  containers, runner, demo, or amtrak-tracker — those verify nothing at
  start. Both docs should say so; the guard is real but narrower than
  written, and tonight's web deploy is the live counter-example.
- Until `scripts/sign-manifest.sh` is run: `integrity-sweep` alerts every
  15 min; any poller/skill image rebuild will refuse via `verified-exec`;
  and `guardrails.py` (not in the manifest) would fail `llm.py`'s
  `_verify_integrity()` if it were ever the caller of an inference —
  academic today, since nothing imports it.

### 13. `PENTEST_CLEARANCE_CHECK_2026-08-13.md` §1a / `README.md:158` / `INFRA_MAP.md:220` "CF Access gated" — vault research routes are now internet-readable, anonymous, scrub-gate only

Fourth-pass items 9–10 were conditional ("if an Access bypass is coming…").
It has come. Live from the public internet with no cookie, header, or
token: every path under `01-Sources/personal-notes/Series/**`,
`04-Syntheses/`, `02-Concepts/`, `00-Inbox/cross-link-findings/` is
listable and readable through `dispatch.example.com`, gated
only by `scrub_gate` on `read` (nothing on `list`) and a 30/min shared
rate limit. That is a materially different exposure statement from what
the docs say:

- `PENTEST_CLEARANCE_CHECK_2026-08-13.md` §1a (lines 65–105) "two
  independent controls … even if the edge were removed, the committed
  tier gates catch them" and `LIVE_STATE_CHECK_2026-08-12.md:455` "The
  vault is no longer publicly readable" — now need an explicit exception:
  four vault subtrees are Tier-0 *and* edge-bypassed by design as of
  2026-08-16.
- `README.md:158` and `INFRA_MAP.md:220` "`dispatch.example.com`
  … CF Access gated; served as Tier 0" — the bypass set is now at least
  `/api/v1/board*` (third pass) + `/api/v1/vault/research*` + `robots.txt`
  / `llm.txt` (`HONEYPOT_FAIL2BAN.md:204`); no doc lists it (third-pass
  item 7 asked for one line in INFRA_MAP §6a — it's now four paths, and
  the rule lives only in the Cloudflare dashboard, not in
  `cloudflared/config.yml` or anywhere tracked).
- The `main.py` comment/docstring premise ("agent tools that cannot send an
  Authorization header … Cowork's fetch tool") is now **true** on the
  public hostname — fourth-pass item 10 is resolved by (b), and its stated
  consequence stands: the research subtree is readable by anyone who
  guesses a path (the `list` route makes guessing unnecessary for the
  three extra prefixes, whose bare names are listable).
- What is actually exposed today (checked, file names only): 5 syntheses
  incl. `device-tracker-surveillance-2026-08-09.md`,
  `homelab-stress-research-2026-08-08.md`, and the 200 KB
  `vault-graph.html`; 1 concept note; 47 cross-link-findings notes; 6
  Series articles. Whether that is intended is the operator's call — the
  drift is that no repo doc says it is.

### 14. The amended commit's own privacy directive is undercut by tracked artifacts that the same commit made public

`171f7e4`'s message and `rss_catalog.py:48–58`: the new feed is added under
a generic name, "no show name in code/comments/commit history by design …
avoid a discoverable list of tracked shows even in private-repo history."
But (a) the show is named — via the title of the `00-Inbox/…` note it was
captured from — in tracked `src/second_brain/knowledge_graph/graph.json:
24–28` and `src/second_brain/knowledge_graph/vault-graph.html:139` (both
in the public-mirror tree unless `scrub-public-tree.py` drops them — it
doesn't list them), and (b) the vault copy of that same graph,
`04-Syntheses/vault-graph.html`, is now **anonymously readable over the
internet** through the route this commit widened (item 13; verified the
public 200 body contains that note title). The catalog comment's redaction
is fine; the discoverability it aims to prevent already exists two other
ways. Also minor: `git reflog` still holds the two superseded amend targets
(`7209d1a`, `1f3b45f`), the second of which names the show in its subject
— local-only and expiry-bound, but "history" in the sense the directive
uses; a `git reflog expire`/`gc` is the operator's call.

### 15. `main.py` widening comment describes a vault-side move that hasn't happened

`src/web/main.py:183–189`: "Vault-side move executed same night: old
`Uber Series/` -> `Series/Uber Series/` (verified byte-identical before
**the old copy was removed**), duplicate `Research - Uber Series/`
**retired** … `Family Office - CTDI/` created fresh under the new parent
(previously existed outside any scope entirely)." PROPFIND at 21:50 EDT:
`01-Sources/personal-notes/Uber Series/` (6 files, 3 741–11 602 B, 11 Aug
22:23Z) and `01-Sources/personal-notes/Research - Uber Series/` (same 6,
same sizes/times) both still exist alongside the new `Series/Uber Series/`
(same 6, 17 Aug 00:02Z, plus a `research/` subfolder), and the old empty
`Family Office - CTDI/` is still at the top level next to the new empty one
under `Series/`. So the copy happened; the removal/retirement the comment
asserts did not (which is presumably what `webdav_client.delete()` was
added for). Consequence: the fourth pass's "two in scope, one out" picture
is now "two copies of the same six articles out of scope, one copy in" —
harmless for the API (old paths 400), but the code comment is the only
record of the vault layout and it's wrong. Either finish the move or
reword to "copied; old folders retained".

### 16. `guardrails.py` — three claims in the new module don't match the repo it was ported into

- Docstring: "`GET /api/v1/admin/audit-log` already surfaces SR1/SR2
  events for free." No such route; the audit endpoint is `GET /admin/audit`
  (`main.py:2010`, admin tier). Same class of self-citation miss as
  third-pass item 5.
- Naming: the module defines **SR1 = mutation gate, SR2 = model-tier
  routing**, while `CLAUDE.md:169–176` and `README.md:465–468` define the
  repo-wide skill rules **SR-1 = `sr1_log.log_usage()`, SR-2 =
  `sr2_gate.hash_gate()`** ("every skill that calls an LLM must follow
  both"). Two unrelated meanings for the same tokens in one codebase;
  nothing imports `common.guardrails` yet (grep of `src/`, `scripts/`,
  `tests/`: zero callers), so renaming the functions/prefix now (or adding
  a one-line disambiguation to CLAUDE.md's SR section) is cheap.
- Docstring: "see docs on the dispatch-mcp/agentic-tools MCP disconnect
  that same night" and "that MCP server was demoted to public-facing-
  demo-only status" — no such doc exists in this repo (grep `demot`,
  `disconnect` × `agentic`/`mcp` across `docs/`: nothing).
  `INFRA_MAP.md:38` and `:323–325` still describe
  `agentic-management-tooling-mcp` as running alongside with
  `~/.claude.json` + Desktop as clients; live `~/.claude.json` has no
  `mcpServers` for any project, Desktop still lists `agentic-tools`, and
  the state dir was written at 20:51 tonight. Whatever the new status is,
  INFRA_MAP is the doc that should say it, and the guardrails docstring
  should point at it or at nothing.
- Also: it writes `audit_log` rows with tier `guardrail` and actions
  `SR1_INTERCEPT/SR1_ALLOWED/SR2_BLOCK/SR2_ROUTE` — `COMPLIANCE_SECURITY.md`
  §3's "every admin action taken through the platform's API" description
  of that table (already incomplete per third-pass item 5) gets further
  from the truth once anything calls this. Its "today every skill runs
  100% local Ollama (phi3:mini …)" line is accurate to the live box and is
  the third in-repo statement contradicting CLAUDE.md:147–158 / README:
  488–512's "16 models, gemma3:4b" (08-15 open item).

### 17. Smaller text-vs-code items

- `docs/SECOND_BRAIN_STATUS.md:24` "webdav_client has no delete for
  iterating on vault-side artifacts" and `:126` "shared WebDAV client
  (mkcol/put/get/list_files)" — `delete()` exists as of this commit (the
  §"knowledge graph" rationale built on its absence is now historical).
- `README.md:253` and `docs/dispatch-runner-design.md:90` "11 built-in
  categories, **27 feeds**" — HEAD `_RSS_CATALOG` is 11 / **32** (it was
  31 before this commit; the count has been stale since 08-11's
  `DOCS_REFRESH` recorded 27).
- Fourth-pass item 8 stands under the new root: `GET /api/v1/vault/
  research/list` (default) → `[]` because `list_files()` still skips
  collections, while the docstring at `main.py:1550–1551` still says the
  root "lists the current topic folders". Fourth-pass observation 1 (500
  on a well-formed nonexistent in-scope folder) also stands — re-verified
  `…/Series/DoesNotExist` → 500 with traceback (my three probes are the
  only post-20:05 500s in the web journal).

## Still accurate (checked because this commit could have touched them)

- **`CLAUDE.md` / `README.md` timer table entries that this commit didn't
  edit** — `ops-brief` :00 / `ep-advance` :30 hourly, aam-weekly Sun 09:00,
  RSS 2 h — unchanged live.
- **`docs/ALERT_REFERENCE.md`** — the watches push to no topic it
  enumerates by cadence; nothing to change there.
- **`.config/systemd/user/` → live mirror story (`CLAUDE.md` key-paths
  table)** — holds; the live dir is a flat copy and the three `retired-*/`
  subdirs are (correctly) not present there, so systemd never sees the
  archived `-pm` timers.
- **`src/ingest/README.md`, `src/shared/watchlist_README.md`** — untouched
  subject matter; ingest containers were being shed by the Load Guard
  during the check but that's the documented guard behaviour.
- **`webdav_client.py:14/69` `BUSINESS_ROOT` nesting** — the new prefixes
  resolve under it exactly as the fourth pass described (the loopback
  listings above prove it).
- **`db.audit()` signature** — `guardrails.py`'s five-positional calls
  match `db.py:733`.

## Bottom line (fifth pass)

`171f7e4` is the largest-surface commit of the day and it drifts the docs
in three real ways: (11) every cadence sentence about the category watches
and the second-brain daily digest is now wrong (and, live, five of the six
watch timers are stopped, so neither the old nor the new cadence is what's
running); (13) the vault-research routes are now Cloudflare-Access-bypassed
and anonymously readable from the internet — the 08-13 pentest clearance,
README/INFRA_MAP "CF Access gated", and the 08-12 "vault no longer publicly
readable" record all need a stated exception, and the bypass set exists
only in the CF dashboard; and (12) the manifest was again not re-signed —
the sweep has alerted every 15 min since 19:01, and the unsigned web image
that started anyway shows README/CLAUDE overstate which containers the
`verified-exec` guard covers. The rest is the commit's own new text being
wrong about the repo/vault it lives in: a vault move described as done but
only half done (15), a nonexistent audit route and a colliding SR1/SR2
name (16), and a show-name redaction defeated by a tracked graph file that
the same commit made public (14). Everything open from passes one–four
(SKILL.md `ops.example.com`, load-vs-thermal narrative, SUDO "no Access
wall", 21-phi3-model narrative, `ANTHROPIC_FALLBACK`, `runner-demo`,
board-token docs/reminder, research `list` root/500) is untouched and
still open.

---

## Fifth pass — independent cross-check (second session, 21:40–21:55 EDT)

A second, concurrent drift check of the same commit ran in parallel with
the pass above (started against `1f3b45f`, HEAD@{1}; finished after the
amend to `171f7e4`) and did not know the pass above existed until it went
to write. Rather than a duplicate section, this is the delta only.
Nothing staged/committed/changed live; probes were read-only.

**Independently reproduced, same evidence:** manifest INTEGRITY FAILURE at
HEAD (`MANIFEST.sha256` still the 14:35 `cc863d2` signing; two `-pm` paths
"could not be read"; `integrity-sweep.service` failing on its 15-min
cadence); five of six daily-watch timers `inactive dead` (enabled) since
19:47:12 with only `aam-daily-watch.timer` restarted 19:47:21; the six-way
simultaneous 19:24:42 run; the aam 20:54:43 no-op elapse and 22:24 rebase;
web image 20:05 EDT carrying the widened scope while its baked
`verify-manifest.sh src/` fails and the container runs anyway; poller image
08-15 23:36 without `guardrails.py`/`delete()`/the new feed; loopback
route behaviour (default `list` → `[]`, `04-Syntheses` 5 / `02-Concepts` 1
/ `cross-link-findings` 47 / `Series/Uber Series` 6, excluded and old
paths → 400, `Series/NoSuchTopic` → 500 — one of the post-20:05 500s in
the web journal is this session's 21:52 probe, three at 21:46 are the pass
above's); **public hostname 200 with real `content`** on `research` and
`research/list` for all four prefixes while `/healthz`, `/api/v1/vault/
file` still 302; vault PROPFIND showing the old `Uber Series/`, `Research -
Uber Series/` (6 files each, 11 Aug) and top-level `Family Office - CTDI/`
still present beside `Series/`; `_RSS_CATALOG` 32 (31 at HEAD~1, README
says 27); `db.audit()` signature match; `guardrails.py` has zero importers;
`/admin/audit` at `main.py:2010` (no `/api/v1/admin/audit-log`);
`setup.sh:141` copies top-level `*.timer` only. Items 11–17 and the bottom
line above stand as written.

**Deltas / one correction to add:**

- `src/second_brain/webdav_client.py:150–154` (`delete()` docstring): "No
  move/rename verb exists in this module **or in WebDAV itself** as a
  single atomic op across arbitrary paths — relocations are GET+PUT+DELETE
  at the call site." The second half is wrong: RFC 4918 defines `MOVE`
  (and `COPY`) with a `Destination:` header, and this Nextcloud advertises
  it — `OPTIONS …/01-Sources` → `Allow: OPTIONS, GET, HEAD, DELETE,
  PROPFIND, PUT, PROPPATCH, COPY, MOVE, REPORT`. Relevant to item 15: the
  unfinished vault move can be one `MOVE` per folder rather than
  re-uploading, and the docstring shouldn't steer the next reader away
  from it.
- On item 11's live half: the reason the 19:24 start was six-wide (and the
  reason `systemctl --user start` of the five dead timers together would
  do it again) is the pre-existing `Requires=<service>` in each timer's
  `[Unit]` — starting the timer pulls the service in immediately, and the
  new `OnUnitActiveSec` then re-bases each chain on that instant until its
  next `OnCalendar` anchor. Restarting them 15 min apart is what preserves
  the stagger the timer Descriptions promise; worth one line in the timer
  comments or INFRA_MAP when the cadence sentence is fixed.
- The `1f3b45f` → `171f7e4` amend closed one item this session had
  drafted (commit body describing a generic feed name and PodcastIndex
  fallback that HEAD didn't yet contain — it does now); listing it here
  only so nobody re-derives it from the reflog.

---

## Fifth pass — third-session cross-check (21:45–22:05 EDT), delta only

A third concurrent check of `171f7e4` (started after the amend, so it saw
only the final HEAD) found the two sections above already on disk when it
went to write. Everything it derived independently agrees with items 11–17
and the second-session deltas — re-derived, not copied: manifest failure
set (10 mismatches + 2 unreadable at clean HEAD, 4 new files unlisted);
`verified-exec.sh` scoping `src/` for 31 of 61 Quadlets while the core
Containerfiles `CMD` straight in; the six timers stopped 19:47:12 / aam
alone restarted 19:47:21, five with empty `NextElapseUSecRealtime`; aam
firing 19:25 → 20:54 → next 22:24:43 and second-brain-daily 19:33 → 21:33
(that run finished 21:45, so the "43 min ceiling" held); web image 20:05
EDT carrying `_VAULT_RESEARCH_EXTRA_PREFIXES` (5 hits) with an in-image
`verify-manifest.sh src/web/main.py` FAILED; poller image 08-15 23:36
without the new feed; loopback list/read results identical to the table
above (incl. `…/Series/DoesNotExist` → 500 — this session's 21:5x probe is
another of the post-20:05 500s); public 200s on `research` and
`research/list` for all four prefixes while `/api/v1/vault/file` and
`/api/v1/knowledge-graph/meta` still 302; PROPFIND showing old `Uber
Series/` and `Research - Uber Series/` (6 files each, old `Article 1.md`
GET → 7 961 B) and top-level `Family Office - CTDI/` still present;
`_RSS_CATALOG` 32 (27 was last true at `6ea0e9f`, 08-07; 31 from
`9436f67`, 08-14); zero `guardrails` importers; `/admin/audit` at
`main.py:2010`; `PODCASTINDEX_*` unset in both env files; 115 units.
Nothing staged/committed/changed live; probes read-only (counts/lengths
only, no vault content dumped).

**Three additions the sections above don't state:**

1. **Item 12 timeline correction — the sweep has been red continuously
   since 13:46 EDT, not since 19:01.** Journal for
   `corporatetraveldc-integrity-sweep.service` today: 44 `sweep OK` runs
   (last **13:31:00 EDT**) and 41 `INTEGRITY FAILURE` runs; every run from
   13:46 through 21:46 failed (33 consecutive), and the authenticated
   `ops-health` history shows **39 `INTEGRITY SWEEP FAILED` pushes in the
   last 12 h, first 11:45, last 21:46**. Before this commit the cause was
   working-tree edits (this file once tracked, the `corporatetraveldc.
   ep-advance` edit); since 21:39 a clean checkout fails too. So the p5
   alert has been continuous for ~8 h and the "possible tampering or
   corruption" text has been crying wolf all afternoon — anyone tuning
   that topic out tonight would miss a real one. Re-signing clears it
   only if the working tree is also clean or the two stray edits are
   committed/reverted first.

2. **Item 13/14 sharpened — the public `vault-graph.html` re-exposes the
   folders the commit's own comment excludes, and it is the artifact the
   08-13 clearance was about.** Counting inside the 200 082-char public
   `content` (no content recorded here): **956** occurrences of
   `03-Entities`, **218** of `06-AI-Memory`, **206** of `01-Sources/daily`,
   **36** of `Docs/`, 1 of `.internal-backups`, plus the graph's
   `nextcloud_web_base` / `file_open_base` / `vault_root` fields (internal
   URL bases) and 254 node ids / 844 edges with `label` and `source_file`
   paths. `main.py:195–205` lists `Docs/`, `06-AI-Memory/`, `03-Entities/`,
   `01-Sources/daily/`, `.internal-backups/` as "deliberately EXCLUDED …
   PII-adjacent / Pi-only, never meant to reach Cowork's side" — their
   note names and paths are now anonymously internet-readable through the
   one file in `04-Syntheses/` that indexes the whole vault. And
   `main.py:1383–1394` (`knowledge_graph_html`, T1) says the graph route
   was tier-gated on 08-13 precisely "to close public-internet exposure
   of the full second-brain vault"; `PENTEST_CLEARANCE_CHECK_2026-08-13.md`
   §1a calls that "the finding to lead with" and
   `LIVE_STATE_CHECK_2026-08-12.md:407` records `04-Syntheses/vault-graph.
   html` (WebDAV mtime 2026-08-13 00:23Z, 200 810 B) as the current
   vault-side copy of the same build output. Net: the T1 gate on
   `/api/v1/knowledge-graph/html` is intact and irrelevant — the same
   graph is served at Tier 0, Access-bypassed, via
   `/api/v1/vault/research?path=04-Syntheses/vault-graph.html`. Whatever
   the operator decides about the rest of item 13, this one file (or
   non-`.md` files generally, or `04-Syntheses/` as a prefix) should come
   out of `_vault_research_path_allowed()` before the next public sweep,
   and §1a / 08-12:455 need the regression noted rather than a carve-out.
   Related: the comment's rationale for `04-Syntheses/` ("every poller
   skill writing here imports `scrub_gate` — scrub-gated at write time")
   covers skill output only; three of the five top-level files there
   (`device-tracker-surveillance-…`, `homelab-stress-research-…`,
   `project-knowledge-synthesis-…`) and the graph are not skill output,
   so the read-time gate (radio-CUI/SSN tokens) is the only control on
   them.

3. **Item 11 addendum — README/CLAUDE also carry a now-wrong unit-count
   snapshot only in the labelled sense.** `CLAUDE.md:14–17` says "145
   loaded units at this snapshot" (08-11) and tells the reader not to
   hardcode it; every pass today measured 117 and this commit's `-pm`
   retirement makes it **115**. Not drift (it's labelled), recorded so
   the next rewrite has the current number.

No further deltas: `webdav_client.delete()` vs `SECOND_BRAIN_STATUS.md:24/
126`, the SR1/SR2 collision, the missing MCP-demotion doc, the RSS count,
the vault-move comment, and the WebDAV `MOVE` correction are all as stated
above. Bottom line for this commit stands as the fifth-pass section wrote
it, with item 12's clock moved back to 13:46 and item 13's graph exposure
upgraded from "operator's call" to a regression of the 08-13 fix.
