# Live State Check — 2026-09-04 (post-commit 37676c5)

Doc-drift check scoped to commit `37676c5` ("Connection-pool DB fix,
CPUWeight starvation fixes, thermal-guard hysteresis, cross-process SWIM
alert throttle"), run ~13:45–14:05 EDT, ~25 minutes after the commit
landed and ~25 minutes after the post-signing container redeploy. Checked
README.md, CLAUDE.md, docs/ (INFRA_MAP.md, ALERT_ARCHITECTURE.md,
ALERT_REFERENCE.md, GUARDRAILS_JUSTIFICATION.md,
CODEBASE_REFERENCE_DRAFT_2026-09-03.md, SINGLE_EDGE_UNIT_ASSUMPTIONS.md,
dispatch-runner-design.md), src/ingest/README.md,
src/shared/watchlist_README.md — against the diff, current source, and
the live system (systemctl --user, podman ps/images, journalctl, state
files in /var/lib/corporatetraveldc).

Prior art consulted first (second-brain search: `CPUWeight`,
`sector_coalesce`, `thermal-ingest-guard`, `hysteresis`, `per-thread`,
`connection-per-call`, `knowledge-graph-compile`, `37676c5`): the
2026-09-01/02/03 drift passes (`20260902T015337Z.md`,
`20260902T021310Z.md`, `20260903T135237Z.md`) establish the
CPUWeight=9000 llama baseline and ingest 30→5000→(9500)→7500 history, and
`20260817T015317Z.md`/`20260817T015915Z.md` cover the sector_coalesce
dedup lineage this commit's throttle fix builds on. **Nothing in the
vault yet covered this commit's four fixes or the new
knowledge-graph-compile/docgen infrastructure** — zero hits for
`hysteresis`, `per-thread`, `connection-per-call`,
`knowledge-graph-compile`. This check's real findings are persisted to
the vault (note written via `remember_text()`, tagged
`doc-drift,live-state-check`).

## Live verification — commit claims all confirmed live

- **CPUWeight**: web/poller/pusher report 9000 live (tracked 9000); all
  seven ingest units report 7500 live (tracked 7500); runner still 100.
- **ADVERSARIAL_CODEBASE_REVIEW_2026-09-04.md NF-8 is already resolved
  live** (worth recording so nobody re-derives it): the stale
  `~/.config/systemd/user.control/…/50-CPUWeight.conf` set-property
  drop-ins it flagged as pinning ingest at 9500 are **gone** —
  `user.control/` is empty and the review's own verify command
  (`systemctl --user show … -p CPUWeight`) returns 7500 on all seven.
  That review is a point-in-time document committed in 37676c5; its
  headline runtime finding no longer reflects the box.
- **Deploy state**: all platform images rebuilt today after the source
  edits (poller/pusher 16:30–16:31Z, web/ingest 17:12–17:13Z); all
  platform containers restarted ~13:19 EDT and running. The per-thread
  DB connection code (`src/common/db.py:27-35`, `threading.local`) and
  the flock throttle are what's actually running.
- **Cross-process throttle operating**:
  `/var/lib/corporatetraveldc/sector_coalesce_throttle.json` + `.lock`
  exist and were written 13:45 EDT (minutes before this check) — the
  shared-file state is being actively used across the ingest containers.
- **Thermal guard**: `corporatetraveldc-thermal-ingest-guard.service`
  ExecStart runs `scripts/thermal-ingest-guard.py` **directly from the
  repo working tree** (no installed copy), so the committed
  resume-dwell-hysteresis fix is live by definition; timer active.
- **New units installed and firing**:
  `corporatetraveldc-knowledge-graph-compile` `.timer`
  (00/06/12/18:08 ET) + `.path` and
  `corporatetraveldc-semantic-compile-daily` `.timer`
  (00/06/12/18:02 ET) + `.path` are all enabled; both `.path` units
  active (waiting) since 2026-09-03 23:48 EDT; both timers fired their
  12:0x slots today and show next-fire 18:0x.
- **In-commit doc updates are in sync**: spot-checked
  ALERT_ARCHITECTURE.md:250-251 (flock file path, semantics) against
  `src/shared/sector_coalesce.py` — matches. ingest README and
  watchlist_README were updated in the same commit.

## REAL findings

### 1. `ep-advance-venues` has crashed on every genuine daily fire since 2026-09-01 (real bug, pre-existing, surfaced by this check)

`corporatetraveldc-ep-advance-venues.service` failed at its 06:10 ET fire
on Sep 1, 2, 3, and 4 — every scheduled run it has ever had — with
`TypeError: generate() got an unexpected keyword argument 'top_p'`.
Root cause: `src/poller/skills/ep_advance_venues.py:57-63` passes
`top_p=0.9` directly to `common.llm.generate()`, which has no `top_p`
parameter (`src/common/llm.py:831`; the `top_p` values in
`common/personas.py` are persona-registry data, not `generate()`
kwargs). The hourly sibling `ep_advance_brief.py` routes through
`generate()` correctly (no `top_p`) and is unaffected.

This **invalidates CLAUDE.md's 2026-08-31 claim** that the venues
timer's first failure was "harmless one-time noise, not a loop" from
`Persistent=true` catch-up semantics — that 13:54 catch-up was indeed
exit 2 (pre-rebuild missing file), but the skill has *never once
succeeded on schedule*: the venue-advisory half of the ep-advance split
is effectively not running, and the 08-31 entry's "next real fire is the
actual test" test has now run four times and failed four times. Also
invalidates the split's stated design (cached venue section refreshed
daily — the hourly skill is splicing in a venue cache that is never
regenerated).

**Not fixed this pass** — the fix is a src edit (drop the kwarg, or add
the param to `generate()`), which requires sign + poller image rebuild;
an unsigned working-tree edit to `src/` would trip `verified-exec`
across the whole timer fleet. Flagged in CLAUDE.md and persisted to the
vault instead. One-line fix for the next signing pass.

### 2. INFRA_MAP.md — semantic-compile schedule stale; new units absent

`docs/INFRA_MAP.md` (timer-highlights section, ~line 550) still says
"semantic-compile-daily 03:47" — this commit moved it to
00/06/12/18:02 ET *and* added a note-landed `.path` trigger. The map
also has no entry for the new `knowledge-graph-compile`
`.container`/`.timer`/`.path`, the `Containerfile.docgen` /
`corporatetraveldc-docgen` image (built 04:45Z today), or the expanded
`doc_generation.py` pipeline. Not corrected in-place this pass —
INFRA_MAP corrections have been batched into the drift-correction
commits (b623db9 pattern); recorded here and in the vault.

### 3. CODEBASE_REFERENCE_DRAFT_2026-09-03.md §5.1 — DB description now stale

Written yesterday, it describes `common/db.py` as "context-managed
`conn()` (commit/rollback/close)". After 37676c5, `conn()`
commits/rolls back but does **not** close — one persistent connection
per thread, reused across calls (`db.py:27-35`). The "6,545 lines"
count is also off by the commit's +64. Minor, but this draft is the
newest full-codebase reference and this is the exact hot-path behavior
it exists to describe. §5.5's sector-coalescing paragraph ("per-topic
throttle … JSON-persisted") happens to be *more* accurate post-commit
than pre-commit and needs no change.

### 4. SINGLE_EDGE_UNIT_ASSUMPTIONS.md baseline row (known-stale, further diverged)

Line 49's "Per-container `CPUWeight`/`CPUQuota` | 100/300%" baseline now
mismatches the majority of core containers (web/poller/pusher 9000,
ingest 7500; only runner and the periphery remain at 100). This doc was
already flagged stale in the vault 2026-08-30 (`20260830T030623Z.md`,
ntfy row) and is historical/assumption-scoped — noted, not rewritten.

## Failed units at check time (none caused by this commit)

- `ep-advance-venues` — finding #1 above.
- `second-brain-daily` — exit 1 at 12:04 EDT after `generate()` returned
  None repeatedly under load1≈34 (known LLM-contention class, flagged
  open in CLAUDE.md 2026-08-30). Re-fired 13:45 EDT and currently
  running, holding at the pre-flight load gate. Self-resolving.
- `gig-economy-daily-watch` — `requests.ReadTimeout` to
  `host.containers.internal:80` (15s) at 13:08 EDT under the same load;
  oneshot, retries next fire. Worth watching: it still reaches a local
  service via `host.containers.internal`, the same address class the
  2026-08-31 UltraFeeder/dispatch.env fix moved to the tailnet IP — if
  this recurs when the box is idle, check whether that endpoint needs
  the same treatment rather than blaming load.
- `personal-notes-import` — restarted 13:44 EDT on the new poller image;
  runs logging healthy "0 imported, 6 unchanged" results. Transient from
  the redeploy window.

## Still accurate (checked, no drift)

- `dispatch-runner-design.md` runner CPUWeight 100 — matches tracked and
  live; runner was deliberately excluded from the 9000 tier.
- README.md's resource-governance text (llama per-unit limits, :725) is
  about the llama units, untouched and still true; no README claim
  invalidated by this commit.
- CLAUDE.md's newest entry (website-integrity-sweep RESOLVED close-out,
  the commit's only CLAUDE.md change) — accurate.
- `docs/GUARDRAILS_JUSTIFICATION.md`, `docs/ALERT_ARCHITECTURE.md`,
  `docs/ALERT_REFERENCE.md`, `src/ingest/README.md`,
  `src/shared/watchlist_README.md` — updated in-commit, spot-checks
  consistent with source and live state.

---

# Second pass — post-commit 99d2c4d (same day, ~16:20–16:45 EDT)

Separate drift check scoped to commit `99d2c4d` ("STDDS safety bitmask
pairing + RVR/METAR history, ep-advance-venues generate() fix,
env-quoting hygiene"), run ~5–25 minutes after the commit landed. Same
doc set as the morning pass, verified against the diff, current source,
and the live system (systemctl --user, podman ps/images + `podman exec`
into the running containers, the live DB, state files). This section is
itself an uncommitted working-tree edit to an already-signed file — it
WILL show as `verify-manifest: INTEGRITY FAILURE` on integrity-sweep
fires until the next signing pass; that recurrence is expected, don't
re-diagnose.

Prior art consulted first (second-brain: `stdds_safety`,
`ep-advance-venues`, `sector_coalesce`, `metar_history`,
`stdds_rvr_history`): the same-day session-reconciliation note
`corporatetraveldc/01-Sources/manual/20260904T182619Z.md` already covers
this commit's entire work arc (bitmask pairing derivation, the two new
history tables, the silencing decision and its stdds_surface
over-silence correction, standing rules). This pass builds on it —
nothing below contradicts it.

## Reading the commit correctly (for future passes)

The commit message's first bullet is headed `shared/sector_coalesce.py:
silenced stdds_safety …` but **no sector_coalesce.py change is in this
diff** — the silencing was applied as persisted runtime config via
`sector_coalesce.set_feed_silence()`
(`/var/lib/corporatetraveldc/sector_coalesce_silence.json`), not a code
edit; the module's own code changes were commit 37676c5. Don't hunt for
a missing hunk.

## Live verification — commit claims confirmed

- **New history tables live and filling**: `stdds_rvr_history` (30 rows)
  and `metar_history` (224 rows, `wind_dir_deg` populated) both exist in
  the live DB with real rows accumulating. Both new `CREATE TABLE`s live
  in `db.py` (`init_db_v44`) — db_swim.py's diff is the history-write
  logic inside `upsert_stdds_rvr()`, not a new table there.
- **Fixed code confirmed inside the running images** (not just the
  tree): `podman exec` into the running poller shows
  `ep_advance_venues.py` with both bad kwargs gone (the in-file comment
  documents the drop); the running ingest-stdds image carries the
  bit-pairing code in `smes_parser.py`. poller image built ~16:13 EDT,
  ingest/web ~14:20 EDT (post-signing, pre-commit-timestamp — signing
  precedes the operator's commit here, so that ordering is normal).
- **Silence state as intended**: `sector_coalesce_silence.json` has
  `silenced_feeds: ["stdds_safety"]` only — `stdds_surface` un-silenced,
  no silenced sectors, matching the operator's corrected scope.
- **Zero `INTEGRITY FAILURE` journal hits since the commit** at check
  time (before this file was edited — see the caveat above).
- **`corporatetraveldc-ep-advance-venues.service` was in `failed`** from
  this morning's 06:10 ET fire — that fire ran the pre-fix image
  (expected; the fix landed 16:17). `reset-failed` run. Timer confirmed
  enabled, next fire Sat 2026-09-05 06:10 ET — **that fire is the first
  true end-to-end test of the fixed skill**; the fix has only ever been
  verified by signature introspection, never a live scheduled run.

## Drift found (real, all mild — queue doc fixes for a signing pass)

1. **`docs/ALERT_REFERENCE.md`'s smes_parser.py line references are
   stale** (STDDS section, ~:400–430, and the :852 summary table). Doc
   cites `:390`/`:584`/`:902`/`:1047` for the four
   `fire_family_alert()` sites, `:869` for the previous_bitmask
   change-gate, `:536`/`:540-542` for the dedup definitions. Actual
   post-99d2c4d: `:407`/`:607`/`:993`/`:1245`, `:944`, and `:561-563`.
   Partly pre-existing (pre-commit numbers were already
   `:407`/`:607`/`:892`/`:925`/`:1177` — the first two drifted before
   this commit), worsened by this commit's +68 net lines in
   `write_safety_status()`/`check_incursion_alert()`. The *behavioral*
   claims those references anchor (change-gate, no `_STDDS_SAFETY_DEDUP`,
   `escalating_only=False`) are all still accurate.
2. **`docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md` table census stale**:
   ":511's "64 `CREATE TABLE` statements in db.py + 12 in db_swim.py ≈
   76 tables" is now 66 + 12 ≈ 78, and its SWIM/aviation table lists
   don't include `stdds_rvr_history`/`metar_history`. It's a dated
   draft, so this is expected aging, but the census reads as current.
3. **No repo doc records that `stdds_safety` pushes are currently
   silenced.** `ALERT_REFERENCE.md:426` / `ALERT_ARCHITECTURE.md:352`
   still describe the incursion path as firing on first
   occurrence/change — true of the code, but live pushes for that feed
   are suppressed by the operator-applied standing silence (2026-09-04,
   raw/unconfirmed bitmask concern; vault note 20260904T182619Z.md §8).
   This is exactly the "deliberate silence looks identical to an
   outage" trap ALERT_REFERENCE itself warns about (:705). Suggest a
   one-line dated note in both docs' STDDS sections next signing pass.

## Still accurate (checked, no drift)

- `src/ingest/README.md` — its STDDS/RVR claims (`RVRDataUpdateMessage →
  stdds_rvr`, storage-not-alerting) are unchanged and still true; the
  new history table extends, not contradicts, them.
- `docs/ALERT_REFERENCE.md`/`ALERT_ARCHITECTURE.md` behavioral claims
  about the incursion path (change-gate at previous_bitmask, no
  PushDedup, escalating_only=False, priority 3) — the pairing work
  changed only the alert *detail text*, not fire/no-fire, and no doc
  describes the detail-text format, so nothing invalidated there.
- CLAUDE.md's ep-advance-venues entry (REAL bug → RESOLVED addendum, the
  commit's only CLAUDE.md change) — accurate; confirmed against journal
  and the running image.
- Env-quoting change (`AMTRAK_CORE_ROUTES` now quoted): no doc
  references that variable or instructs a bare `source` of
  `dispatch.env`; `scripts/second-brain-search.sh` extracts values via
  `sed`, unaffected. Nothing invalidated.
- README.md, `src/shared/watchlist_README.md`,
  `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md` — no claim in any of them
  touches this commit's surface. (SINGLE_EDGE_UNIT_ASSUMPTIONS.md still
  describes the retired Ollama governor as live — pre-existing drift
  from the 2026-08-30 purge, out of this commit's scope, noting only so
  it isn't re-found.)
