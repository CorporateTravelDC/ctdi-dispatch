# Live-state / doc-drift check — 2026-09-05

Four checks ran this date. The morning check (post-3f2069c) is first;
the afternoon check (post-778da08) follows it; then the post-a53bc10
check (NWWS-OI env-quoting/heartbeat fix); the post-3fdb32a check
(public-mirror scrub false-positive fix) is appended at the end — it
contains a REAL production finding about the public mirror itself.

# Check 1 — post-3f2069c (morning)

Scope: post-commit drift check after `3f2069c` (runner health watchdog +
CLAUDE.md reconciliation + stdds_safety silenced notes). Checked whether
existing doc claims (README.md, CLAUDE.md, docs/, src/ingest/README.md,
src/shared/watchlist_README.md) are invalidated by this specific change,
verified against the live system. Prior second-brain context consulted
first: `20260904T202506Z` (the previous post-commit drift check, covers
the stdds-safety/alert-reference ground this commit's doc notes came
from — nothing here contradicts it) and `20260823T193811Z` /
`20260824T110837Z` (the root-scope `corporatetraveldc-watchdog`
restart-overreach root-cause — directly relevant to the real finding
below). The new runner-health-watchdog itself had no prior second-brain
notes; it is new in this commit.

## REAL FINDING (bug, not doc drift): runner-health-watchdog re-introduces the watchdog-vs-LOCKDOWN collision class

`scripts/runner-health-watchdog.sh` unconditionally
`systemctl --user start`s `corporatetraveldc-runner.service` whenever it
is not active. But a not-active runner is a **designed state** during a
thermal-guard LOCKDOWN: `scripts/thermal-ingest-guard.py:180` lists
runner in `LOCKDOWN_USER_UNITS` and `_lockdown_stop_stack()` (called
once at the tier-2 transition, `:568`) deliberately stops it at
`temp >= 79C` or `load1 >= 40`. Within at most 5 minutes of any
LOCKDOWN, the new watchdog will:

1. restart runner **during the thermal emergency**, defeating the shed
   for that unit for the remainder of the LOCKDOWN (the guard does not
   re-enforce stops per cycle, so this is a one-shot defeat per
   LOCKDOWN, not a 5-minute flap loop — verified against the guard's
   call sites), re-adding load exactly when the box is shedding it; and
2. page a misleading p4 "Runner PWA was DOWN -- auto-restarted"
   (ntfy `hot-alerts` + X-Email) that looks like a resolved outage when
   it is actually a defeated safety action. The success path has **no
   cooldown** (`ALERT_COOLDOWN_SECS` only gates the restart-FAILED
   path), so repeated LOCKDOWNs each page again.

This is precisely the incident class already root-caused in August (the
"watchdog-vs-LOCKDOWN collision", see `docs/CAUSAL_REASONING_ROADMAP.md`
~line 156 and CLAUDE.md's 2026-08-21/23 watchdog history), and the
platform already has the established mitigation pattern the new script
skipped: root-scope `scripts/watchdog.sh` reads
`/var/lib/corporatetraveldc/thermal_ingest_guard_state.json`'s `tier`
field via `_guard_tier()` (fails OPEN to 0 on missing/unparseable state,
so it can never permanently mask a real outage) and suppresses restarts
of guard-managed units at tier 2 (`watchdog.sh:55-63,213-228`).

Suggested fix (NOT applied this pass — an unsigned tracked-file edit
would trip the integrity sweeps fleet-wide; queue for next signing
pass): port the same fail-open `_guard_tier()` check into
`runner-health-watchdog.sh` — if tier==2, log and skip the
restart+page (or send a low-priority informational note at most). The
explicit-operator-stop case the watchdog was built for (the ~18h silent
outage in the commit message) remains fully covered: that state file
distinguishes the two cases exactly.

Persisted to second-brain (agent-authored, same pass) — see the vault
note referencing this file.

## Doc drift caused by 3f2069c (real, mild — inventory omissions)

The new watchdog is a new alert publisher, but the alert-inventory docs
were only updated for the stdds_safety silenced notes, not for the new
publisher itself:

- `docs/ALERT_REFERENCE.md` topic table (~line 201): the `hot-alerts`
  publishers row enumerates its publishers exhaustively (fdps_parser /
  tfr_enrichment / route_impact / aim_parser / lockdown.sh /
  threat-initiate.sh / watchdog.sh) — now missing
  `runner-health-watchdog.sh` (p4 on auto-restart, p5 + 30-min cooldown
  on failed restart, both with `X-Email` relay).
- `docs/ALERT_REFERENCE.md` "Standalone bash/script alerts" table
  (~lines 793-808): missing `runner-health-watchdog.sh` entirely; the
  preamble "All post to `ops-health` unless noted" makes the omission
  actively misleading since this one posts to `hot-alerts`.
- `docs/INFRA_MAP.md` timer-highlights watchdog enumeration (~line 556,
  the "watchdogs every 2-15 min (container-mem, thermal-sample, ...)"
  parenthetical): missing runner-health-watchdog (every 5 min). The
  list is labeled highlights, but it enumerates every other watchdog
  and INFRA_MAP's own convention is dated "New YYYY-MM-DD" entries for
  new units.
- `docs/ALERT_ARCHITECTURE.md` scope caveat (~line 52): the list of
  publishers sitting outside the three-layer model ("the two alert-only
  guards...") now under-counts — the new watchdog is another
  outside-the-model publisher, and unlike those two it is *not*
  alert-only (it restarts a service). The existing sentence is
  date-scoped ("2026-08-21/22 additions") so not strictly false, but
  the doc's purpose (what acts/publishes outside the model) now misses
  an actor with restart authority.
- Minor: neither alert doc mentions the ntfy `X-Email` relay channel at
  all. The new watchdog is the first *scheduled* script publisher to use
  it (hand-rolled curl header, same mechanism as
  `common/ntfy_push.py`'s opt-in `email=True` added 2026-09-02).
  `ntfy_push.py`'s "NO skill ever set X-Email" docstring claim is
  historically scoped ("before this") and remains accurate.

None of these were edited this pass (hard rule: no tracked-file edits /
no staging from a drift check; also avoids unsigned-edit integrity-sweep
noise). Queue alongside the LOCKDOWN-gate fix for the next signing pass.

## Verified accurate — no drift

- **stdds_safety silenced notes** (the commit's ALERT_REFERENCE /
  ALERT_ARCHITECTURE additions) match live state exactly:
  `/var/lib/corporatetraveldc/sector_coalesce_silence.json` shows
  `"silenced_feeds": ["stdds_safety"]`, nothing else silenced.
- **CLAUDE.md reconciliation claims** re-verified live:
  `corporatetraveldc-docs-drift-weekly.service` `Result=success` /
  `ExecMainStatus=0`; `corporatetraveldc-ultrafeeder.service` active;
  `lsusb` shows 2 RTL dongles; `corporatetraveldc-runner.service`
  active.
- **The watchdog deployment itself** matches its commit message:
  `.timer` installed + enabled (`active (waiting)`, next trigger on the
  5-min grid), `.service` fired 06:50 EDT with exit 0/SUCCESS and a
  clean "active -- ok" log line. Single-instance lock, 30-min failure
  cooldown, `--status` mode all present in the script as described.
- **README.md / src/ingest/README.md / src/shared/watchlist_README.md**:
  nothing in this commit's surface (a new host-side watchdog + doc
  notes) touches their claims; README's runner sections describe
  endpoints/demo instance, not supervision, so nothing there is
  invalidated.
- `docs/CLAUDE_MD_DRIFT_REPORT.md` and
  `docs/LIVE_STATE_CHECK_2026-09-04.md` were updated/added by the
  commit itself and are internally consistent with what was verified
  here.

# Check 2 — post-778da08 (afternoon, ~12:00 ET)

Scope: post-commit drift check after `778da08` ("Add automated FAA CIFP
pull; document confirmed-dead LADD source + manual path" — but the
commit's real surface is wider than its message: it also carries the
board-sweep token-gate/presence-reminder expansion (+314 lines), the
runner-health-watchdog LOCKDOWN gate, the `build_graph.py` per-file
retry fix, and the alert-doc inventory updates queued by Check 1 above).
Prior second-brain context consulted first: `20260905T105918Z` (Check
1's own vault note), `20260905T142118Z` / `20260905T142701Z` (same-day
board-sweep/token-gate + runner-watchdog reconciliation notes — the
"board-sweep was documented as hourly, actually 15-min" correction there
matches what this commit shipped in INFRA_MAP; nothing below contradicts
either), and the CIFP/LADD history notes (`20260831T230659Z` — CIFP
named there as the "honest next step", now delivered; `20260826T213148Z`
LADD alternate-source research).

## Verified delivered — everything Check 1 queued landed in this commit

- `scripts/runner-health-watchdog.sh` now carries the fail-open
  `_guard_tier()` LOCKDOWN gate (skips restart+page at tier 2), ported
  from root-scope `watchdog.sh` exactly as Check 1 suggested. The
  `.service` ExecStart runs the repo script path directly (no installed
  copy to drift), and the script's sha256 matches the signed manifest —
  the fix is live as of this commit, no deploy step pending.
- All four alert-doc inventory omissions from Check 1 are fixed:
  ALERT_REFERENCE hot-alerts row + standalone-scripts table (the "one
  exception posts to hot-alerts" claim in the new row was re-verified
  against every other row of that table — accurate), INFRA_MAP watchdog
  enumeration, ALERT_ARCHITECTURE outside-the-model caveat.

## Verified accurate — new claims in this commit vs. live state

- **CIFP timer**: `corporatetraveldc-faa-cifp-pull.timer` installed and
  enabled live (`active (waiting)`, next trigger Thu 2026-09-10 08:00
  EDT, exactly the documented Thursday/AIRAC slot). Data lands under
  `/var/lib/corporatetraveldc/faa-cifp/`, state in `skill-state/`.
- **Poller image rebuilt** (CLAUDE.md's "needs a rebuild to ship the new
  skill" is now satisfied): `localhost/corporatetraveldc-poller:latest`
  build-date `20260905T152146Z` (11:22 EDT), verified to contain
  `faa_cifp_pull.py`, the new token-gate `board_sweep.py`, and
  `build_graph.py` with `_FETCH_MAX_RETRIES`. That CLAUDE.md line is
  stale-but-resolved scratchpad state, nothing to do.
- **Integrity sweep green**: 11:53 EDT fire `Result=success` — the
  commit's own "expected/self-resolving integrity-sweep failure window"
  entry resolved exactly as predicted by the post-commit re-sign.
- **board-sweep cadence**: live timer fires on the :00/:15/:30/:45 grid
  (last 11:45, next 12:00) — INFRA_MAP's "every 15 min (corrected
  2026-09-05)" is right; 11:45 fire exit 0 on the new image.
- **LADD docs**: `docs/LADD_CUI_HANDLING.md`'s core claims (automated
  fetch dead, manual weekly `import-ladd-filter.py` is the path, DB is
  the system of record) all remain accurate. Cosmetic only: it still
  says the endpoint "has redirected to an FAA office page" — per this
  commit's live re-check it's now a flat 503, and the ADX-portal
  finding lives only in `faa_registry.py`'s comment. Not worth an edit
  on its own; fold in whenever that doc is next touched.
- **ep-advance-venues** (yesterday's fix, first real test today):
  06:10 ET fire succeeded (`Result=success`, exit 0, 06:16 EDT) — the
  first clean scheduled run this skill has ever had, closing the loop on
  CLAUDE.md's RESOLVED 2026-09-04 entry.
- **transport-pattern-digest** midnight failure recurred (00:53 EDT,
  `Result=timeout`) — already known-open in CLAUDE.md with its own
  investigation queued; not caused by this commit, nothing new.

## REAL drift caused by 778da08 (alert/inventory docs lag the board-sweep + CIFP expansion)

The commit fixed Check 1's inventory omissions for the *watchdog* but
introduced the same class of omission for its *own* two new publishers.
`board_sweep.py` now publishes on THREE topics (was one), and
`faa_cifp_pull.py` is a wholly new publisher; none of that is in the
inventories:

1. **Dangling cross-reference, `docs/INFRA_MAP.md` ~line 561**: the new
   text says board-sweep "gained token-gate/presence-reminder duties the
   same day, see ALERT_ARCHITECTURE.md" — ALERT_ARCHITECTURE.md contains
   zero mention of board-sweep, token-gate, or the presence reminder
   (verified by grep). The real documentation lives in
   `board_sweep.py`'s own module/function docstrings; the pointer should
   go there (or the content should be added where it points).
2. **`docs/ALERT_REFERENCE.md` hot-alerts row (~line 201)**: missing
   `board_sweep.py`'s new `_fire_presence_alert()` — priority 5,
   `tags=key`, `email=True`, on `hot-alerts` (the weekly GPG
   presence-attestation reminder). The row was updated *in this same
   commit* to add runner-health-watchdog and still enumerates
   exhaustively, so the omission of the commit's other new hot-alerts
   publisher is actively misleading.
3. **`docs/ALERT_REFERENCE.md` approval-gate row (line 208)**: names
   `scripts/sudo-approval-gate.sh` as the publisher — `board_sweep.py`'s
   `_ntfy_gate_push()` (line ~234 call site) now also posts there
   (Allow/Deny action-button push for the Cowork token-gate relay, same
   JSON-actions shape, resolve URLs on
   `dispatch.example.com`).
4. **`docs/ALERT_REFERENCE.md` ops-health row (~line 200) and
   poller-skills table (line 770)**: `faa_cifp_pull.py` (p2 "new cycle
   pulled" / p3 "pull FAILED", both ops-health) is missing from the
   exhaustive ops-health publisher enumeration; and the poller-skills
   entry `board_sweep.py:94` is a stale line reference (the ops-health
   push now sits at ~:374) that also predates the two new publish paths.
5. **`docs/INFRA_MAP.md` timer inventory**: the commit's own new weekly
   `faa-cifp-pull` timer (Thu 08:00 ET) is absent — the very paragraph
   edited by this commit to add runner-health-watchdog. INFRA_MAP's
   convention is a dated "New YYYY-MM-DD" entry per new unit.
6. **`docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md` line 474**: describes
   `board_sweep` as "Read-only sweep … hourly" — both halves now false
   (15-min cadence; and the token-gate thread relay is a deliberate,
   narrow exception to read-only, per the skill's own 2026-09-05
   docstring). Draft doc, but two days old and already stale.

None of these were edited this pass (hard rule: no tracked-file edits /
no staging from a drift check; an unsigned edit would also re-trip the
integrity sweeps that just went green). Queue for the next signing pass.
Persisted to second-brain (agent-authored, same pass).

## Working-tree state observation (not drift, but must not be lost)

`MANIFEST.sha256`/`.asc` carry uncommitted post-commit re-sign changes:
they add the CIFP `.container`/`.timer` entries and the final
CLAUDE.md/`faa_registry.py` hashes. Verified: the working-tree manifest
matches the live files (sha256sum spot-checks pass) — it is the
*committed* manifest in `778da08` that is stale for those entries. The
live system is consistent (integrity sweep passes); the uncommitted
manifest just needs to ride along with the next commit. Left untouched
per this check's no-commit rule.

## Knowledge-graph compile (`build_graph.py` fix) — live verification

The fix itself is in the deployed image and the code matches CLAUDE.md's
description (bounded per-file retries, skip-and-continue). Today's
journal shows the pre-fix reproduction failures CLAUDE.md describes
(including a 07:00 EDT failure) plus one 10:27 EDT failure of a
DIFFERENT class: `NameResolutionError` on the *initial* WebDAV PROPFIND
(container DNS, died in 2.4s — host-side DNS resolves fine). That is an
environmental error upstream of the per-file fetch loop the fix covers,
during a window of heavy image-build churn — not evidence against the
fix. The service sat in `failed` state awaiting its next scheduled fire
(12:08 EDT, 6-hourly cadence):
**12:08 fire result: PENDING at write time — see the addendum line
below.**

> **Addendum (Check 3, ~12:58 EDT): the 12:08 fire SUCCEEDED** —
> `Result=success`, exit 0, `ExecMainExitTimestamp` 12:19:06 EDT (an
> ~11-minute full compile). First scheduled fire on the fixed
> `build_graph.py` after the 10:27 DNS-class failure; the per-file-retry
> fix's first real-schedule run is clean. Entry closed.

# Check 3 — post-a53bc10 (afternoon, ~12:57 EDT)

Scope: post-commit drift check after `a53bc10` ("Fix NWWS-OI ~1hr silent
outage: env-quoting corruption + heartbeat masking bug"). Surface: env
files + the new never-quote rule and its `check-env-quoting.sh` guard
(wired into `pre-commit` and `scheduled-integrity-sweep.sh`), and the
`ingest/nwws.py` heartbeat-gating fix. Prior second-brain context
consulted first: `20260820T135212Z` / `20260820T135450Z` (the FIRST
NWWS-OI quoted-secret incident, 2026-08-20 — this commit is round two of
the identical class, now with an automated guard) and `20260904T202506Z`
(yesterday's drift pass, which endorsed the quote-the-values direction
this commit reverses — that note's "env-quoting change invalidates
nothing" conclusion is superseded; this check is the correction pass it
said wasn't needed).

## REAL drift — two docs teach the WRONG mechanism for the no-quoting rule

This commit's central finding is that **systemd's `EnvironmentFile=` DOES
strip quotes; podman's `--env-file` (the actual consumer for every
container) does NOT** — confirmed live via `podman exec … env`. Two
shipped docs state the opposite mechanism. Their bottom-line advice
("never quote") is correct, but the reasoning is exactly the kind of
plausible-wrong claim that has driven this repo's 4+ quoting flip-flops
(2026-07-19 → 2026-08-19 → 2026-09-04 → today) — a future reader who
knows systemd's real spec would "correct" these docs and re-break it:

1. `docs/REGIONALIZATION.md:43` — "read by systemd `EnvironmentFile=`,
   which does **not** strip shell quoting." False attribution; the
   sentence should name podman `--env-file` as the non-stripping parser
   (and note systemd/bash-source behave differently, per the
   `dispatch-secrets.env.template` header banner).
2. `docs/DATA_SOURCES.md:464-466` (NWWS-OI entry) — "systemd's
   `EnvironmentFile=` passes quote characters through as literal bytes."
   Same false attribution, attached to the 2026-08-20 incident story
   whose sequel this commit just fixed.

## Mild drift (pointers/inventories lagging the commit)

3. `docs/SDR_SERVICES.md:134` — "no quotes — see CLAUDE.md's
   `EnvironmentFile` quoting gotcha": CLAUDE.md is a periodically-cleared
   scratchpad and no longer has a section by that name. The durable home
   for the rule is now the file-header banner in
   `dispatch-secrets.env.template` / `config/dispatch.env` plus
   `scripts/check-env-quoting.sh`; re-aim the pointer there.
4. `docs/ALERT_REFERENCE.md:796` — the standalone-scripts row for
   `scheduled-integrity-sweep.sh` lists only "INTEGRITY SWEEP FAILED";
   as of this commit the sweep emits a second, distinct alert title,
   **"ENV QUOTING REGRESSION"** (priority 5, same ops-health path), when
   `check-env-quoting.sh` fails. The table's convention enumerates
   observed titles per script, so the row now under-counts.
5. `scripts/pre-commit-README.md` (the hook's own doc, outside this
   check's strict scope) — opening line still describes the hook as
   credential-pattern scanning only; it now also runs
   `check-env-quoting.sh` against the env/secrets files on every commit.

None of these were edited this pass (hard rule: no tracked-file edits /
no staging from a drift check; an unsigned edit would re-trip the sweep
that just went green at 12:50). Queue all five for the next signing
pass. Persisted to second-brain (agent-authored, same pass).

## Verified accurate — commit claims vs. live state (all pass)

- **Deploy claims**: all 6 SWIM ingest containers + `amtrak-tracker` +
  poller running the rebuilt images (`podman ps`: up ~7 min at check
  time, matching the commit-time restarts).
- **NWWS-OI genuinely authenticated**: `ingest.nwws INFO … joined MUC
  nwws@conference.nwws-oi.weather.gov` at 12:44 and 12:49 EDT, zero
  `not-authorized` since; `push:nws` heartbeat 22 s fresh / 0 failures,
  `push:amtrak` 25 s. The REST `nws` feed_state is ~37 min old — the
  poller's REST fallback correctly stood down once push became
  genuinely healthy, which is the exact behavior the heartbeat-masking
  bug was defeating.
- **Env fix live**: `check-env-quoting.sh` exits clean;
  `podman exec … env` shows zero quoted values in ingest-core and
  amtrak-tracker; `AMTRAK_CORE_ROUTES=Acela,Northeast Regional` (bare).
- **Guard wiring**: `.git/hooks/pre-commit` is byte-identical to the
  updated `scripts/pre-commit` (the diff's own copied-not-symlinked
  gotcha is handled via `git rev-parse --show-toplevel`); integrity
  sweep's last fire 12:50:13 EDT `Result=success`.
- **`src/ingest/README.md` heartbeat/fallback claims** ("a fresh
  heartbeat means push owns that feed", "on disconnect the heartbeat
  ages out and REST polling resumes") are NOT invalidated — the commit
  aligned the code to what this doc already claimed; those sentences
  were wrong about the pre-fix code and are true now. No edit needed.
- `dispatch-secrets.env.template` / `config/dispatch.env` banners,
  `check-env-quoting.sh`, and CLAUDE.md's new FIXED entry are mutually
  consistent (same mechanism story, same enforcement points).
- `docs/LIVE_STATE_CHECK_2026-09-04.md:264` and its "systemd and bash
  both preserve quoted values verbatim" era are superseded by this
  commit — left untouched (dated point-in-time record; the correction
  lives in CLAUDE.md's FIXED entry, this file, and the vault note).

## Working-tree observation (not this check's edit, left untouched)

`scripts/runner-health-watchdog.sh` picked up an uncommitted edit at
13:00:35 EDT, mid-check, from concurrent work in another session: it
replaces the `${OPERATOR_EMAIL:-…}` bash default with an explicit
if-check, because `scrub-public-tree.py`'s `EMAIL_RE` consumes the `:-`
operator's hyphen into the email match and fails `push-public.sh`'s
scrub verification (per the edit's own comment, confirmed live). Not
touched by this pass. Being an unsigned tracked-file edit, it will show
the usual expected/self-resolving `verify-manifest: INTEGRITY FAILURE`
on 15-min sweep fires until the next signing pass covers it.

# Check 4 — post-3fdb32a (afternoon, ~13:10–13:25 EDT)

Scope: post-commit drift check after `3fdb32a` ("Fix public-mirror push
failure: stray hyphen made a scrub false-positive" — the signing pass
that landed Check 3's mid-check working-tree observation above). Prior
second-brain context consulted first: `20260905T170007Z` (Check 3's own
vault note — its five queued doc fixes are re-verified below),
`20260831T135808Z` (push-public.sh fetch-and-retry / scrub prior art),
`20260823T033038Z` (standing never-raw-push rule). Built on those, not
re-derived.

## REAL FINDING (production bug + wrong live mirror state, not doc drift): the public mirror has been a scripts-only repo since 2026-09-04, and scrub verification never saw it

Found by verifying 3fdb32a's own claim ("blocking the public push") against
the real public mirror, root-caused and fully reproduced live:

**The public mirror (`github.com/CorporateTravelDC/ctdi-dispatch`) tip is
wrong-shaped and stale.** Its last four pushes — `28345944` (Sep 4
16:19 EDT), `1c6bdf82` (Sep 5 09:04), `c8aa3374` (11:57), `b96ca39`
(12:19, current tip) — each publish **only the scrubbed `scripts/`
subtree as the repo root** (80 flat files; no README.md, no `src/`, no
`docs/`). Last correct full-tree push: `9b4375dd`, Sep 4 13:42 EDT. All
four bad commits exist in this repo's local object store, GPG-signed
with the push-public commit message — they came from real
`push-public.sh` runs here, not from GitHub-side tampering.

**Root cause (reproduced 100%, every link):** `push-public.sh` computes
`REPO_ROOT` but never `cd`s to it, and `scrub-public-tree.py`'s
`git_out()` calls plain `git ls-tree` with no `--full-tree`. Git applies
the current directory as an implicit pathspec prefix *even for an
explicit root tree SHA*. So a run started from inside `scripts/`
(`cd scripts && bash push-public.sh …`, or any caller with that CWD):

1. `scrub_tree(<root tree>)` — `git ls-tree <root-sha>` from `scripts/`
   returns the scripts subtree entries with bare names (verified live:
   85 entries vs. the real root's 43) → the "scrubbed root" it builds
   IS the scripts subtree.
2. `verify_scrubbed(<that subtree>)` — `git ls-tree -r <subtree-sha>`
   from `scripts/` returns **0 entries** (the prefix pathspec matches
   nothing inside the subtree) → the last-line-of-defense scan checks
   ZERO blobs and **passes vacuously**. Verified live: 0 entries from
   `scripts/` CWD, 80 from repo root.
3. `git commit-tree` + force-push publishes the subtree as the mirror
   root, parented on the prior mirror tip as normal.

This also cleanly explains 3fdb32a's own incident: the ~12:55–13:09 push
attempt that FAILED on the hyphen-prefixed operator-address match (per
3fdb32a's message; the literal string is deliberately not reproduced
here — it would itself trip the verifier if this doc ships) was run from
the repo root (correct CWD, full-tree scan → hit the watchdog false
positive); the four "successful" pushes were CWD-broken runs whose
verification scanned nothing. Same code, two CWDs, opposite outcomes.
Confirmed decisively: `verify_scrubbed(f7376979…)` (the live mirror
tip's tree) run from repo root **fails** on exactly that string — the
tree currently published is one the committed verifier rejects.

**Leak assessment (important, checked before anything else):** no actual
secret/PII shipped. `scrub_blob()` operates on blob SHAs
(CWD-independent), so per-blob SUBSTITUTIONS/REGEX_SWEEPS and
DROP_FILES were correctly applied to what shipped (80 = 85 minus the 5
dropped scripts). A root-CWD re-run of `verify_scrubbed` against the
pushed tree flags ONLY the known-benign false positive (the
operator-approved public forwarder address with the `:-` operator's
hyphen consumed into the match — the exact 3fdb32a bug). The failure is structural: the *guarantee* was void —
anything in `scripts/` would have shipped unscanned — and the mirror
has been missing its entire actual content (README/src/docs) for a day.
Side casualty: GitHub Dependabot on the mirror (which caught the
2026-08-31 react-router CVEs) currently has no `src/` to scan.

**Still broken right now:** 3fdb32a fixed the false positive, but no
successful full-tree push has happened since (mirror tip 12:19 EDT
predates the 13:09 fix). Reproduced post-fix from repo root: the scrub
now completes clean on HEAD's full tree (exit 0, proper 43-entry root)
— so a plain `bash scripts/push-public.sh main` **from the repo root**
will restore the mirror. Not run this pass (outward-facing push;
operator/authorized-session action).

**Queued fixes (NOT applied — hard rule, no tracked-file edits from a
drift check; both files are manifest-signed):**
1. `push-public.sh`: `cd "${REPO_ROOT}"` right after computing it
   (push-and-sync.sh already does exactly this; push-public.sh is the
   only entry point that doesn't).
2. `scrub-public-tree.py`: use `git ls-tree --full-tree` in both
   `scrub_tree()` and `verify_scrubbed()` so the pipeline is
   CWD-independent regardless of caller.
3. Cheap invariant for defense in depth: refuse to push a scrubbed tree
   whose root lacks `README.md`/`src` (a scripts-only root would have
   been caught on the first bad push instead of the fourth).

## Carry-forward — Check 2/3's queued doc fixes MISSED the 3fdb32a signing pass

3fdb32a was "the next signing pass" Checks 2 and 3 queued their doc
fixes for, but it shipped only the watchdog fix. Re-verified at the
cited lines: all still unfixed — `docs/REGIONALIZATION.md:43` and
`docs/DATA_SOURCES.md:464-466` (wrong systemd-vs-podman quote-stripping
attribution), `docs/SDR_SERVICES.md:134` (dangling CLAUDE.md pointer),
`docs/ALERT_REFERENCE.md:796` (missing "ENV QUOTING REGRESSION" title),
`scripts/pre-commit-README.md` (no mention of check-env-quoting.sh),
plus Check 2's board-sweep/faa-cifp inventory items (INFRA_MAP dangling
ALERT_ARCHITECTURE cross-reference and missing faa-cifp-pull timer;
`CODEBASE_REFERENCE_DRAFT_2026-09-03.md:474` still says "hourly").
Still queued; now one missed pass old. The push-public CWD fix above
joins the same queue.

## Verified accurate / no new drift from 3fdb32a itself

- No doc describes the `${OPERATOR_EMAIL:-…}` fallback mechanism the
  commit changed; the watchdog's documented behavior (alerts, guard-tier
  gate — GUARDRAILS_JUSTIFICATION.md ~302, ALERT_REFERENCE.md 201/809,
  ALERT_ARCHITECTURE.md, INFRA_MAP.md) is untouched by the if-check
  rewrite. Watchdog fired clean on the fixed script at 13:10:00 EDT
  (exit 0, "active -- ok").
- README.md / src/ingest/README.md / src/shared/watchlist_README.md:
  nothing in this commit's surface touches their claims.
- Core services all active at check time (poller, pusher, web, runner);
  `corporatetraveldc-pusher.service` restart from the ~13:05 autonomous
  tick (see CLAUDE.md entry) confirmed holding.

## Working-tree / sweep state at end of check

CLAUDE.md carries an uncommitted ~13:05 autonomous-tick edit;
`verify-manifest` fails on exactly that one file, and the 13:20:13 EDT
integrity-sweep fire failed accordingly (the 13:05:11 fire raced ahead
of the edit and passed). Usual expected/self-resolving pattern — note
the tick entry's own "no sign needed" is wrong in effect, since
CLAUDE.md is manifest-tracked; resolves at the next signing pass. This
file's Check 4 addendum adds itself to that same unsigned-edit set.

Also observed at end of check (~13:25 EDT), not this pass's work:
concurrent-session working-tree changes appeared mid-check — a
CIFP-parse feature in progress (new `faa_cifp_parse.py` skill +
`.container`/`.timer`, `docs/CIFP_DATA.md`, `common/cifp_lookup.py`,
tests, plus edits to `common/db.py`, `tbfm_parser.py`, `poller/main.py`,
`faa_cifp_pull.py`, `web/main.py`). Left untouched; noted here so the
next signing pass attributes them to that session, not this check.
