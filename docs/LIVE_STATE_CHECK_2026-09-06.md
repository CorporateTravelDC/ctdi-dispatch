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
