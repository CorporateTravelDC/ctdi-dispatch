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
