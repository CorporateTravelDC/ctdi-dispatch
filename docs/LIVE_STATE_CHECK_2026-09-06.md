# Live-state / doc-drift check — 2026-09-06 (post-5029bdb)

Scope: post-commit drift check after `5029bdb` ("Boot-storm fix, uplink
failover, CIFP arrival ETAs, Postgres Phase 0"). Checked whether existing
doc claims (README.md, CLAUDE.md, docs/, src/ingest/README.md,
src/shared/watchlist_README.md) are invalidated by this specific commit,
verified against the live box (systemctl --user, podman, journal, the
live SQLite DB read-only, nmcli/ip route, swapon). Run ~09:00 EDT.

Prior second-brain context consulted first (not re-derived here):
`20260905T225248Z` (boot-storm root cause), `20260906T060542Z` (session
archive covering the whole 2026-09-04/05 arc incl. failover + Postgres
Phase 0), `20260905T105918Z` + `20260905T142701Z` (runner-health-watchdog
reconciliation — these are what put the *hot-alerts* wording into
ALERT_REFERENCE.md that this commit then invalidated), and
`20260830T121921Z` (the one-timer `Requires=`→`Wants=` mis-fix). Nothing
below contradicts those notes; the drift here post-dates them.

## REAL drift caused by 5029bdb (five items, none edited — no-edit rule)

1. **`docs/ALERT_REFERENCE.md` hot-alerts row (line 201) and script
   table (line 809) — wrong topic for `runner-health-watchdog.sh`.**
   Both say it posts to `hot-alerts`, the script table calling it "the
   one exception to this table's all-post-to-ops-health preamble". This
   commit moved it (operator directive, §3 of the commit message) to
   `NTFY_OPS_TOPIC`; verified in `scripts/runner-health-watchdog.sh:77-84`
   (`NTFY_OPS="${NTFY_OPS:-ops-health}"`). So the "exception" no longer
   exists, the hot-alerts publisher list is over-inclusive, and the
   ops-health publisher list (line 200) is missing it. The reconciliation
   notes of 2026-09-05 were correct when written; the commit flipped the
   topic without touching the doc.
2. **`net-failover-watchdog.sh` is an undocumented publisher everywhere
   except its own design doc.** It posts to `ops-health` (script line
   157) with email relay on every flip/restore/apply-failure and runs
   every 60 s, but appears in none of `docs/ALERT_REFERENCE.md`,
   `docs/ALERT_ARCHITECTURE.md`, `docs/INFRA_MAP.md` (timer inventory,
   lines ~556-568) or `README.md`. Only `docs/NETWORK_FAILOVER.md`
   describes it. Same omission class the 2026-09-05 check flagged for
   the CIFP pull timer.
3. **`docs/PI5-BOOT-CONFIG.md` line 27 — "Swap: zram0 (8 GB) — no disk
   swap partition."** No longer true. Live `swapon --show`:

   | Device | Type | Size | Prio |
   |---|---|---|---|
   | /dev/zram0 | partition | 8G | 100 |
   | /var/swap/nvme-overflow.swap | file | 8G | 10 |

   The fstab entry is dated 2026-09-06 and credits
   `scripts/setup-disk-swap.sh`. The doc's failure-domain paragraph is
   still right; only the swap row is stale.
4. **`docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md` line 48 — per-container
   "1536m / 1536m (zero extra swap) … without host swap-thrash".** The
   new `corporatetraveldc-pgsql` quadlet is the first container with
   `--memory-swap` above `Memory=` (verified `podman inspect`: Memory
   1536 MiB, MemorySwap 3072 MiB), and the box now has disk swap by
   design (§3.3 of POSTGRES_MIGRATION.md explains why). The row should
   name pgsql as the deliberate exception.
5. **Container and timer inventories lag the commit.** `README.md` line
   148 and `docs/INFRA_MAP.md` line 446 enumerate infra containers and
   list only `nextcloud-db` as Postgres; `corporatetraveldc-pgsql`
   (32nd running container, `production.slice`, healthy) is absent from
   both. `docs/INFRA_MAP.md`'s timer paragraph is missing
   `net-failover-watchdog` (60 s) and `faa-cifp-parse` (Thu 08:20 ET);
   `faa-cifp-pull` was already flagged missing on 2026-09-05 and still
   is. `docs/DATA_SOURCES.md` still has no CIFP entry at all (also
   carried over from 09-05).

Plus one doc that is **incomplete rather than wrong**:
`docs/CIFP_DATA.md`. Its Pipeline §3 lists `cifp_lookup.py`'s API as
four functions; the live module also exports `estimate_runway_eta`,
`runway_eta_epoch`, `select_arrival_epoch` and `load_fix_coords`, and
`runway_eta_epoch()` is now the single arrival-time implementation for
both `fdps_parser.write_flight_event()` and the enrichment skill. The
commit message says `estimate_runway_eta` is "documented in
docs/CIFP_DATA.md" — it is not. The "Known-resolved / known-unresolved
fixes" section still presents the `DC_METER_FIXES` cross-check as a
confirmation without the commit's own §5(a) finding: none of
PALEO/RAVNN/SWANN/WOOLY/FLUKY occur in live `tbfm_sequences.meter_fix`,
production now resolves via `cifp_fixes` with `DC_METER_FIXES` as
fallback only (`fdps_parser.py:1641-1673`). And "Not yet done" does not
say `arrival_time` is now populated. A reader of that doc alone would
trust the wrong lookup path.

## CLAUDE.md — stale scratchpad entries (noted, not acted on; it is write-only by its own rule)

- "Operator to-do: `sudo scripts/setup-disk-swap.sh`; commit" — both
  done: swapfile active + in fstab (above), commit is `5029bdb`.
- "Known bad: integrity-sweep failed 05:29 UTC … next run should pass" —
  cleared. Twelve consecutive `sweep OK … all 933 files match` from
  06:02 through 08:48 EDT; unit Result=success.

## Verified still accurate

- **`docs/BOOT_STORM_TIMER_REQUIRES.md`**: `scripts/check-timer-requires.sh`
  exits 0 against the deployed units; wired at `scripts/pre-commit:155`
  and `scripts/scheduled-integrity-sweep.sh:88`; zero failed user units;
  67 timers listed by `list-timers --all`, all scheduled ones armed.
  (70 `.timer` files exist under `~/.config/systemd/user`; the 67 figure
  is `list-timers` output, which now also includes the transient pgsql
  healthcheck timer. Not drift.)
- **`docs/NETWORK_FAILOVER.md`**: `/proc/net/bonding/` absent;
  `Jorransgateway`/wld0 default via 10.x.x.x metric 100;
  `Verizon Hotspot`/enu1 metric 600, autoconnect yes; script defaults
  (`PRIMARY_CON_DEFAULT`, `BACKUP_CON_DEFAULT`, probe IPs
  1.1.1.1/8.8.8.8/9.9.9.9) match the doc; timer firing every ~60 s, last
  run "primary=wld0:ok backup=enu1:ok default_via=wld0 failed_over=0
  ok_streak=822".
- **`docs/POSTGRES_MIGRATION.md` §3**: `corporatetraveldc-pgsql` active
  and healthy (restarted 08:19 EDT, 20 MB RSS), `production.slice`,
  `CPUQuotaPerSecUSec=2s` (=200 %), `MemoryLow=512M`; `pg_hba.conf` and
  `postgresql.conf` bind-mounted read-only from the repo; sock volume at
  `/var/run/postgresql`; only TCP listener is pasta on 127.0.0.1:5432;
  `PORTS.md` line 37 registers it; live `dispatch.env` has
  `DISPATCH_DB_BACKEND=sqlite` + `DISPATCH_PG_HOST=/var/run/postgresql`.
  `psycopg` is absent from the running poller and ingest images, exactly
  as the doc says ("next rebuild"). Observation: no running app container
  has `DISPATCH_DB_BACKEND` in its environment yet (all started before
  the env edit), so they are on SQLite by code default, not by flag —
  harmless now, but the flag only takes effect after each unit restarts.
- **`docs/CIFP_DATA.md` data claims**: `cifp_meta` cycle 260903, imported
  2026-09-05T17:27Z; live counts 4,760 fixes / 5,060 legs / 135 holds
  match; pull (Thu 08:00) and parse (Thu 08:20) timers armed.
- **v46 / arrival_time (commit §5-6)**: `tbfm_sequences.eta_kind` present;
  last hour rwy 442 / mfx 1,221 / dfx 3, NULL only on pre-existing rows.
  `flight_events.arrival_time` in the newest 300 rows: 11 `real`, 289
  NULL, zero `text`.
- **TBFM scope-sizing histogram (commit §6d)**: live. 18 log lines in
  5 h from the running ingest-tbfm container; latest 08:45 EDT: 239,056
  dropped non-DC records in 15 min across 1,148 airports, DEN highest.
  (First grep via `journalctl` found nothing — container stdout is not
  in the unit journal, and the message says "dropped", not "discard".
  False alarm, recorded so the next pass does not repeat it.)
- **`src/ingest/README.md` line 10** ("writes … into the shared SQLite
  database") is still true and stays true until Phase 3 cutover; queue
  its rewrite with the cutover, not before.
- **`src/shared/watchlist_README.md`**: no claim touched by this commit.
- **Public mirror scripts-only bug** (2026-09-05 check, still listed as
  open in this commit's message): not re-examined; nothing here changes
  that finding.

## Working-tree state

Only this file added by this check. Nothing staged or committed, per
the hard rule. Separately, `git status` also shows
`src/poller/skills/second_brain_personal_notes_import.py` modified
(mtime 09:01:06 EDT, mid-check, +13/−1: research-category prefix
matching). That edit was made by another session, not this one, and was
left untouched; it is unsigned, so expect the next integrity sweep to
flag it until it is signed.

Second-brain note for this pass:
`corporatetraveldc/01-Sources/manual/20260906T130102Z.md`.
The five drift items above and the CIFP_DATA.md gap need a signing pass;
an unsigned edit would re-trip the integrity sweep that is currently
green.

---

# PASS 2 — same date (~09:20–09:40 EDT), post-6726eb0 (Cowork research surface + push-public CWD fix)

Scope: only what `6726eb0` touched (notes-import routing, research board
mirror extra sources, push-public/scrub CWD fix + three allowlisted IPs,
CLAUDE.md, manifest, poller image). Verified against systemd, the journal,
podman, the live env file, the vault over WebDAV, the live board table
(read-only) and the public mirror via the read-only `verify-public`
remote. Prior second-brain context: `20260905T172300Z` (the CWD bug this
commit fixes), `20260830T031346Z` (research-board-mirror
written-but-never-activated class), `20260906T130102Z` (pass 1 above),
`20260905T225248Z` + `20260906T060542Z` (boot-storm remediation — the
cause of the real finding below). Nothing here contradicts them; pass 1's
"all scheduled ones armed" line is corrected below.

## REAL live finding, NOT caused by 6726eb0 but exposed while verifying it

**Ten enabled timers have been inactive since 18:24–18:26 EDT on
2026-09-05, ~15 h at check time.** `list-timers --all` shows `-` for
NEXT on all ten; `systemctl show` gives `NextElapseUSecMonotonic=infinity`,
`ActiveState=inactive`, still `enabled`:

| Timer | Cadence | Last service run |
|---|---|---|
| personal-notes-import | 2 min | 09-05 18:23 (timer); 09-06 09:14 was a manual start |
| ops-brief | hourly :05 | 09-05 17:48 |
| ep-advance | hourly :35 | 09-05 17:49 |
| aam / aviation / concierge-travel / executive-protection / gig-economy / trains-yachts daily-watch | 90 min each | 09-05 17:48 |
| knowledge-graph-compile | 6 h | 09-05 18:08 |

Mechanism (verified from journal timestamps, not inferred): the boot-storm
recovery shed the 14 stuck LLM catch-up jobs at 18:24:47–18:26:06. At that
moment every timer still carried the self-referential `Requires=` on its
own service (the fixed units were installed at 18:35:39–40, `Reloading`
at 18:35:40), and `Requires=` propagates *stop*: stopping the service
stopped its timer. The journal shows `Stopped …timer` for each of the ten
in that window with no matching `Started`. The 18:35 daemon-reload
removed the coupling but a reload never starts a stopped timer. The
recovery note's "verified all 51 still armed" counted the survivors; 61
tracked − 51 = these ten. Both pass-1 lines "67 timers listed … all
scheduled ones armed" (above) and `20260906T130102Z` were wrong on this
point: `list-timers --all` lists inactive timers too, with NEXT `-`.

Consequences: no ops brief and no EP-advance for 15 h; the six daily
watches and the knowledge-graph compile silent since the reboot; the
personal-notes import that this commit fixes only ran because someone
started it by hand at 09:14. Nothing caught it: `brief-fallback-monitor`
(:50) reported "healthy — no alert" at 06:50/07:50/08:50 — it classifies
the last six runs as LLM vs FALLBACK and has no staleness check, so a
brief that never runs looks healthy. No script or skill under `scripts/`
or `src/poller/skills/` inspects enabled-but-inactive timers
(`grep list-timers|list-unit-files` → none); `check-timer-requires.sh`
checks unit text, not armed state; the integrity sweep passed at 09:18.

**Not remediated by this pass.** Starting all ten at once would re-fire
eight LLM jobs simultaneously (`Persistent=true` catch-up on most of them)
— the same shape as the 17:48 storm. Operator decision: start them
staggered (the daily-watch six at least 5 min apart, ops-brief/ep-advance
after), or `systemctl --user start` each and accept one bunched cycle.
Also worth queuing: a staleness clause in `brief-fallback-monitor.sh`, and
an "enabled-but-inactive timer" probe in the integrity sweep — this
failure class (stop propagating through the old `Requires=`) is now
impossible on the fixed units, but a hand `systemctl stop` of a timer
would still go unnoticed.

## Verified accurate for 6726eb0 (live)

- **Notes-import routing**: live `dispatch.env` has
  `PERSONAL_NOTES_RESEARCH_CATEGORY=Research,Series`,
  `PERSONAL_RESEARCH_DEST=00-Inbox/personal-research`; source defaults
  match (`second_brain_personal_notes_import.py:78-82`, prefix match at
  line 154). The 09:14 run on the rebuilt image logged "6 imported, 0
  unchanged, 0 blocked (from 6 source notes)", all to
  `00-Inbox/personal-research/Research - Uber Series/`; PROPFIND confirms
  six files there. Commit message says "the two affected notes re-PUT" —
  it was six (all of the one category); harmless, the state-file
  mechanism worked as described.
- **Research board mirror**: 09:15:49 run (manual; timer's own fires were
  09:03 and 09:30) logged "8 mirrored, 0 unchanged, 0 blocked (of 8
  items)": six articles + `entity-tracking/2026-09-05T101207Z.md`
  (3 findings inlined) + `2026-09-06T041207Z.md` (41 inlined, 0
  withheld). Live `board_messages` thread `research`: those eight rows
  from `dispatch`, the digest post is 62,324 bytes (commit said ~62 KB,
  cap 80 KB). Defaults in source match the commit
  (`EXTRA_SRCS=04-Syntheses/entity-tracking`, `EXTRA_NEWEST=2`,
  `MAX_INLINE_NOTE=1200`, `MAX_BODY_EXPANDED=80000`). The mirror timer is
  armed (next 09:30:49); INFRA_MAP.md lines 228-232 still call it
  repo-only — known since 2026-08-30, unchanged, not new.
- **push-public / scrub**: `scripts/push-public.sh:23` `cd "${REPO_ROOT}"`,
  root-shape check at lines 54-58; `scrub-public-tree.py:711` and `:769`
  both `--full-tree`; the three addresses are in the allowlist at lines
  526-527. The scrubbed tree the commit cites, `05f7215c`, exists locally:
  44 root entries including `README.md` and `src`.
- **Public mirror is still scripts-only** — expected, the restore push has
  not been run yet. `verify-public` tip `b96ca39` (09-05 12:19 EDT), 80
  root entries, all `scripts/` basenames, no README/src. The `public`
  remote is not reachable from this session (publickey), so the local
  `public/main` ref is stale at 08-31 and must not be read as mirror
  state. `bash scripts/push-public.sh main` from the repo root is still
  the pending operator action.
- **Poller image**: `localhost/corporatetraveldc-poller:latest` built
  09:13 EDT; both transient skill containers pulled it (journal). The
  long-running `systemd-corporatetraveldc-poller` container is on the
  previous image (up 5 h) — same "until each unit restarts" class as
  pass 1's `DISPATCH_DB_BACKEND` observation; neither changed skill runs
  in-process there, so not drift.
- **Integrity sweep**: 09:18:35 `sweep OK`, Result=success — the
  "may fail once more" warning in CLAUDE.md did not materialise after the
  sign. 32 containers running (unchanged from pass 1).
- **Docs that mention these areas**: `CODEBASE_REFERENCE_DRAFT_2026-09-03.md`
  lines 470-471 (one-line skill table) and 992-996 (scrub description)
  are still true; `INFRA_MAP.md:565` "personal-notes-import 2 min" is
  true of the unit file and false of the live box until the timer is
  started; `CAUSAL_REASONING_ROADMAP.md:78` still points at the
  `01-Sources/personal-notes/Research - Uber Series/` copies, which are
  still there (this valve never deletes). `src/ingest/README.md` and
  `src/shared/watchlist_README.md`: nothing touched.

## Premise worth recording (design, not a bug)

The commit justifies the extra-sources mirror with "`04-Syntheses/
entity-tracking/`, which [Cowork] cannot reach". The vault research
endpoint's scope (`src/web/main.py:288-294`) already includes
`04-Syntheses/` and `00-Inbox/cross-link-findings/`, and Cowork's own
board post of 2026-08-31 13:31Z ("direct read restored, disregard
mirror") reports that direct read working with a fresh board-write
token. So the 62 KB digest post duplicates content Cowork can read
directly; the mirror is still the *only* path for the Uber Series
articles, because `Research - Uber Series/` sits outside the endpoint's
`01-Sources/personal-notes/Series/` root and `00-Inbox/personal-research/`
is explicitly out of scope (main.py:244). Not drift, but the next person
sizing this mirror should know both routes exist.

## Working-tree state (pass 2)

Only this file modified. Nothing staged or committed. Second-brain note
for this pass: `corporatetraveldc/01-Sources/manual/20260906T132725Z.md`
(tags: doc-drift, live-state-check, 6726eb0, stopped-timers,
requires-stop-propagation).

---

# PASS 3 — same date (~09:27–09:35 EDT), post-4792b9a (placeholder swap for the mirror push)

Scope: `4792b9a` touched five files — `docs/regulated-operator-setup.md`
(Twilio placeholders `ACxxx…`/`VAxxx…` → `YOUR_*`),
`tests/runner/test_proxy_dispatch.py` (22-char fake bearer →
`ctdc_svc_example`), CLAUDE.md, and the two manifest files. Checked
whether any current doc claim is invalidated, against the hook source,
the live ntfy container, the signed manifest, pytest, and the mirror via
the read-only `verify` / `verify-public` remotes. Prior second-brain
context: `20260905T172300Z` (the scripts-only mirror finding this push
was meant to close), `20260906T132725Z` (pass 2, which saw the mirror
still at `b96ca39`), `20260826T230133Z` (why the hook's placeholder
exemption is window-scoped). Nothing below contradicts them; this pass
records the *resolution* of the 09-05 mirror finding.

## No doc drift from 4792b9a

- **`docs/regulated-operator-setup.md`** is internally consistent after
  the swap: step 4 ("Collect: Account SID, Auth Token, phone number,
  Verify service SID") matches the new `YOUR_ACCOUNT_SID` /
  `YOUR_AUTH_TOKEN` / `YOUR_VERIFY_SERVICE_SID` names; no other doc
  describes the SID format. "ntfy reads all `NTFY_TWILIO_*` env vars
  automatically" still holds — the live `ntfy` container (v2.25.0)
  carries all four variable names (names only inspected, not values),
  exactly as the doc's own blockquote warns.
- **Hook claims are true.** `.git/hooks/pre-push` and `pre-commit` are
  byte-identical to `scripts/` (pre-push mtime Aug 26, untouched).
  `YOUR_` and `example` are in both exemption lists
  (`scripts/pre-push:164` pattern window, `:206` env-assignment window);
  `Bearer ctdc_svc_example` is 16 chars, below the pattern's 20-char
  floor, so it is not even a candidate. `scripts/pre-commit-README.md`
  lines 68-73 list the same skip strings.
- **Tests:** `pytest tests/runner/test_proxy_dispatch.py` → 10 passed
  (2 deprecation warnings, FastAPI on_event, pre-existing).
- **Manifest:** signature good (09:22:31 EDT), `sha256sum -c` exit 0 on
  all 933 entries; CLAUDE.md, the doc and the test file are covered
  (lines 12/400/914). `docs/LIVE_STATE_CHECK_*.md` is excluded by
  `sign-manifest.sh:157`, so this file's uncommitted edits cannot trip
  the sweep. Sweep 09:18:35 OK; the 09:03 failure was the two unsigned
  skill files from before 6726eb0's sign, as CLAUDE.md predicted.
- `README.md`, `src/ingest/README.md`, `src/shared/watchlist_README.md`,
  `docs/INFRA_MAP.md:117`, `docs/SECOND_BRAIN_STATUS.md:709-712`,
  `docs/auth-token-proxy-pattern.md:139/286`,
  `CODEBASE_REFERENCE_DRAFT_2026-09-03.md:989-996`: every push-public /
  pre-push claim still true; none mention the placeholder values.

## Live-state change since pass 2: BOTH pushes have landed, mirror restored

- **Internal:** `origin/main` reflog "update by push 2026-09-06 09:26:47"
  → `4792b9a`; read-only `verify` remote confirms `4792b9a` at GitHub.
- **Public mirror:** read-only `verify-public` now reports
  `9b7b9f6d` ("chore(public): sanitize for public mirror", 09:27:40 EDT,
  tree `fc32414d`), replacing the scripts-only `b96ca39` that pass 2 and
  the 09-05 note described. Root has **44 entries** including
  `README.md`, `src/`, `docs/`, `tests/`, `scripts/` (vs 80 scripts
  basenames before; 41 at the last-good 08-31 tip `88af21b`). The scrub
  dropped 61 paths (CLAUDE.md, both manifest files, the
  `corporatetraveldc.*` Modelfiles, …) and modified 242. The mirror copy
  of `regulated-operator-setup.md` carries the `YOUR_*` lines and the
  test file has three `ctdc_svc_example` hits. The committed
  `verify_scrubbed()` run from the repo root against `fc32414d` returns
  clean — closing the 09-05 note's "the tree currently published is one
  the committed verifier rejects", and its Dependabot side-casualty
  (src/ is back on the mirror).
- **Hook re-simulated on the real push range.** The remote tip at push
  time was `b96ca39`, not `88af21b`: `88af21b` is the local `public/main`
  tracking ref, stale since 08-31 because the `public` remote is
  unreachable from agent sessions (publickey). Running
  `scripts/pre-push` with `CTDI_PUSH_PUBLIC_INTERNAL=1` on
  `b96ca39..9b7b9f6d` exits 0, and on `88af21b..9b7b9f6d` also 0. So the
  commit message's "simulated clean on the real 88af21b→scrubbed-HEAD
  range" named the wrong base — the *real* base was the scripts-only tip,
  which is the larger (re-add-everything) diff — but the push passed the
  hook on the real range anyway. Minor narrative inaccuracy in a commit
  message and CLAUDE.md, not a bug. The lesson for the next pass:
  `public/main` must never be read as mirror state; use `verify-public`.

## CLAUDE.md — stale scratchpad entries (noted, not acted on; write-only)

- "origin/main also still at 3fdb32a locally — neither push has landed
  yet" — both landed within a minute of the commit (above).
- "Mirror is still missing README/src since Sep 4 … a root-CWD
  `bash scripts/push-public.sh main` after this commit restores it" —
  done, 09:27:40 EDT.
- "Known bad: integrity-sweep may fail once more … cleared by the sign"
  — cleared; 09:18 OK after 6726eb0's sign, 4792b9a re-signed 09:22.

## Pass-2 finding follow-up

The ten enabled-but-inactive timers (aam / aviation / concierge-travel /
executive-protection / gig-economy / trains-yachts daily-watch, ep-advance,
ops-brief, knowledge-graph-compile, personal-notes-import) are **still
inactive** at 09:31 EDT. Unchanged; still the operator's staggered-start
call. Not caused by 4792b9a.

## Working-tree state (pass 3)

Only this file modified (passes 2 and 3 both uncommitted). Nothing
staged or committed. `git fetch verify-public main` updated only the
`refs/remotes/verify-public/main` tracking ref (as pass 2 did); no
branch, checkout or index touched. Second-brain note for this pass:
`corporatetraveldc/01-Sources/manual/20260906T133321Z.md` (tags: doc-drift,
live-state-check, 4792b9a, public-mirror, mirror-restored, pre-push).
The 09:33:38 integrity sweep, the first after 4792b9a's sign, logged
"sweep OK — signature valid, all 933 files match", Result=success.

---

# PASS 4 — same date (~13:19–13:35 EDT), post-70897c8 (board `research` thread accepts X-Board-Key)

Scope: `70897c8` touched `src/web/main.py` (`board_get` takes `Request`;
Tier-0 caller on a gated thread passes iff `_require_board_key()` accepts
the key), `tests/web/test_board_thread_gating.py`, CLAUDE.md, the two
manifest files, and pass 2 of this file. Checked every current doc claim
about board reads / tiers / the tunnel against the running web container,
the live nginx vhost, `auth.py`, the ten timers the commit says it
re-armed, and the "known bad" units it names. Prior second-brain context
consulted first: `20260826T213148Z` (the C-5 finding this commit narrows:
every thread served anonymously), `20260905T142118Z` (09-05 security-model
clarification — Cowork's gate-ask is a courtesy check-in, the real gate is
the weekly clearsigned presence attestation; unchanged by this commit),
`20260903T153246Z` (08-24 X-Board-Key gate on `/api/v1/vault/research`,
the precedent this commit copies), `20260906T132725Z` (pass 2, the
stopped-timers finding). Nothing below contradicts them. The
X-Board-Key-on-`research` fix itself had no prior note; this is the first.

## Fix is live and behaves as the commit says

- Running `systemd-corporatetraveldc-web` was created 10:32 EDT on image
  `0e6e2e967d78` (built ~10:20); `/app/src/web/main.py:437` carries
  `_require_board_key(request)` and `:441` the new detail string. So the
  commit's "web image rebuilt" is true and the container is on it.
- Loopback `:8000` with `X-CTDI-Public: 1` (the tunnel's Tier-0 pin):

  | Request | Result |
  |---|---|
  | `?thread=research`, no key | 403 "Tier 1+ or a valid X-Board-Key required" |
  | `?thread=research`, garbage `brd_` key | 403 (same body, not 401) |
  | `?thread=some-new-thread`, garbage key | 403 (unknown threads still default-gated) |
  | `?thread=research`, master key | 200, 17 messages, newest 16:16Z |
  | `?thread=coord`, no key | 200 (unchanged) |
  | `/board/threads`, no key | 200 (unchanged; L-1 of the 08-26 review, accepted) |
  | `/board/health` | 200 |

- Tests: `git archive HEAD` into `/tmp` → `pytest tests/web/test_board_thread_gating.py`
  6 passed (the commit's "6/6"). The working tree passes 8 — the two extra
  are the uncommitted read-scope token work, not this commit.
- Web is on `127.0.0.1:8000` + tailnet `:8000`; there is no `:80` listener
  on the host for the app (nginx fronts it only on the public vhost). The
  first curl of this pass hit `:80` and got `000`; false alarm, recorded so
  the next pass does not repeat it. (`PORTS.md` is not in the repo root —
  the CLAUDE.md line "live `dispatch.env` and `PORTS.md` updated in place"
  refers to the `/etc/corporatetraveldc` copy.)
- **No evidence Cowork has read the thread since the fix.** Every
  `thread=research` hit in the web journal since 10:30 EDT (3×200, 4×403)
  arrives as `10.x.x.x` — the host address pasta rewrites all
  loopback/LAN callers to — and matches this pass's curls plus the
  operator's verify step. `/var/log/nginx/` is root-only from this session,
  so tunnel traffic cannot be distinguished here. The keyed-read path is
  proven; whether the consumer has used it is not.

## Drift caused or codified by 70897c8

1. **`README.md:308` — "`GET /api/v1/board*` | Coordination board (read;
   posts need `X-Board-Key`)" in the Tier-0 table.** Flagged on 2026-08-25
   (pass in `LIVE_STATE_CHECK_2026-08-25.md:352-360`) as needing a "coord
   thread only" qualifier after C-5. Still unedited, and now wrong in a
   second way: `X-Board-Key` is no longer a write-only credential — a
   valid key is what lets a Tier-0 caller *read* any thread other than
   `coord`. Correct row: read `coord` anonymously; every other thread
   needs Tier 1+ **or** a valid `X-Board-Key`; posts always need the key.
2. **"Tier-0 surface for Cowork" wording is stale in the mirror's own
   unit and docstring.**
   `.config/containers/systemd/corporatetraveldc-research-board-mirror.container:2`
   (Description, so it is what the journal prints on every run) and
   `src/poller/skills/second_brain_research_board_mirror.py:4,12,165`
   ("Cowork (Tier-0, no vault/tailnet access)", "the board is a Tier-0
   surface"). The `research` thread has not been Tier-0-readable since
   08-25, and this commit is what finally made its real read model
   explicit: key-gated, not anonymous. The scrub gate the docstring
   justifies with that phrase is still right (the key holder is
   off-tailnet; C-5 stays closed), so behaviour is correct — only the
   stated reason is out of date. Pre-existing since 08-25, codified now.
3. **Commit message / `main.py:421` premise "the tunnel strips
   Authorization" is unverified and unnecessary.** Nothing in the live
   `dispatch.example.com.conf` strips or rewrites
   `Authorization`, and `docs/INFRA_MAP.md` (which the code comment
   cites) never claims it does. What actually blocks a tier token is
   `X-CTDI-Public: 1`, stamped in both location blocks (verified live,
   lines 33/46) and honoured at `src/auth/auth.py:69` *before* any token
   lookup — INFRA_MAP.md:700 "pins Tier 0 unconditionally" and
   `auth.py:9-18` say exactly this. The conclusion (no tier token can
   elevate through the tunnel, so X-Board-Key is the only credential that
   works there) holds either way; the "strips Authorization" clause is the
   part not to repeat, so nobody goes looking for a header strip in
   cloudflared. Narrative, not a bug.

Worth stating for the next security pass, not drift: this commit puts
`_require_board_key()` on a **GET** that is inside the CF-Access bypass
(`INFRA_MAP.md:629`, `dispatch-board-public-bypass`), i.e. internet-
reachable with only nginx's `limit_req burst=20`. That is the same
exposure `/api/v1/vault/research[/list]` has had since 08-24
(`ADVERSARIAL_CODEBASE_REVIEW_2026-09-04.md:475-490` — constant-time
compare, fails closed, non-ASCII header → 500 not bypass), so it is not a
new class, but the board read is now a second internet-facing key-check
surface. A wrong key costs one `board_token_valid()` hash lookup, no
writes — cheaper than the `/board/refresh` case that review rated.

## Verified still accurate

- **`docs/INFRA_MAP.md:629`** (CF Access bypass for `/api/v1/board`;
  nginx stamps `X-CTDI-Public: 1`): live conf matches, both blocks.
  **`:696-712`** (auth tiers; "the coordination board uses `X-Board-Key`
  + one-time enrollment nonces"): true, and now covers reads too.
- **`docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md:803-807`** (board bullet):
  describes the subsystem, makes no read-gating claim; not invalidated.
  `:783-791` Tier-0 list does not include `/api/v1/board`; fine.
- **`OPUS_BLIND_REVIEW_2026-08-26.md:705`** "`research` … correctly 403 to
  anonymous": still true — anonymous stays 403.
- **`tests/web/test_board_thread_gating.py` docstring** carries the
  09-06 addendum; the file is self-consistent.
- **The ten timers from pass 2 are all `active`/`enabled`** (`systemctl
  show`): ops-brief next 14:05, aam 13:30, aviation 13:45, concierge
  14:15, trains-yachts 14:30, knowledge-graph-compile 18:08,
  personal-notes-import every 2 min (last 13:19). ep-advance,
  executive-protection and gig-economy show NEXT `-` only because their
  services are mid-run; the transient `ctdi-restart-*` re-arm timers have
  fired and are gone. The daily-watch calendars are two `OnCalendar`
  lines each (every 3 h, offset 90 min → 90-min cadence, six timers 15 min
  apart, `Persistent=no`).
- **Commit's "known bad" list reconciled:** `research-board-mirror` 13:02
  `ReadTimeout` was a one-off — 13:15:53 run "0 mirrored, 8 unchanged, 0
  blocked", Finished. `dispatch-desk-memo` re-run Finished 11:44 (commit
  text "re-run scheduled" is superseded; CLAUDE.md already says cleared).
  `integrity-sweep` fails on exactly the unsigned working-tree files
  (thermal-ingest-guard.py, common/db.py, poller/main.py, web/main.py,
  the two tests, + 1) — expected until the next sign, as CLAUDE.md says.
  `transport-pattern-digest` still `failed` (12:25 fire, 28-min timeout);
  the re-run condition in CLAUDE.md is load < 15 and load is 31–33, so it
  stays open; next timer fire Mon 00:25.
- `src/ingest/README.md`, `src/shared/watchlist_README.md`: nothing this
  commit touches.

## Live observation (not drift, not caused by 70897c8)

At check time five LLM jobs were running at once — gig-economy (54 min),
ep-advance (47), concierge-travel, trains-yachts, executive-protection
(8) — with load1 31–33 and the thermal guard logging "watch band [15-40),
no action" every 2 min, temp 68–70 °C. The 15-min stagger between the six
daily-watch calendars is shorter than a run takes under this load, so
they stack toward the 40 LOCKDOWN trip described in CLAUDE.md. Nothing
in the docs claims they serialise, so no doc is wrong; the restart
hardening in the working tree does not address calendar stacking.

## Working-tree state (pass 4)

Only this file modified by this pass. The pre-existing unsigned edits
(`scripts/thermal-ingest-guard.py`, `src/common/db.py`,
`src/poller/main.py`, `src/web/main.py`, two tests, untracked
`scripts/board-token.py` + `tests/common/test_board_token_scope.py`) are
another session's restart-hardening / read-scope-token work and were not
touched. Nothing staged or committed; no branch, checkout or ref changed
(`git archive` to `/tmp/head70897c8` is read-only). Second-brain note for
this pass: `corporatetraveldc/01-Sources/manual/20260906T172802Z.md`
(tags: doc-drift, live-state-check, 70897c8, board-research-thread,
x-board-key, c-5, cowork, tier-0).
