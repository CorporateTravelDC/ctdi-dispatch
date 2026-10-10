# LADD filter data — CUI (SP-PRVCY) handling

Verified against HEAD db64018 and live state on 2026-10-06 18:12Z / 14:12 ET.

The FAA's automated `LADD_Aircraft.zip` download
(`src/poller/fetchers/faa_registry.py`, `_FAA_LADD_URL`) has redirected to
an FAA office page since June 2026 and produces nothing. Current LADD
(Limiting Aircraft Data Displayed) data comes as files the operator
downloads manually each week: an "FAA Source" filter, a broader "Industry"
filter (a strict superset, confirmed 2026-08-31), and a separate "remove"
list. All are marked CUI SP-PRVCY.

## Handling rules

- **Never commit these files, or any value from them, to any repo.** They
  must never reach a public mirror. `scripts/scrub-public-tree.py` treats
  them like any other real identifier.
- **Import, verify, then shred the source files** (operator rule, 2026-10-08).
  1. `PYTHONPATH=src python3 scripts/import-ladd-filter.py <industry> [--remove <remove_file> ...]`
     (the FAA source filter may be given too; the Industry file is a strict superset).
     This is a **full replace** of `faa_ladd_aircraft` through `db.faa_upsert_ladd()`, which
     refuses to wipe the table on an empty or failed parse. The host has no database
     credentials in its shell, so the import runs inside a running container that has
     them: copy the files to its `/tmp`, run the script on stdin, delete the copies.
  2. **Random tail inquiry check:** sample random entries, US registrations included,
     plus recorded removals, and query them through the real consumer path
     (`db.faa_is_ladd()` / the registry lookups), not just a row count. Every sampled
     entry must report LADD; every removal must not. Report counts only, never identifiers.
  3. Only then `shred -u` the source files. The database is the system of record, not
     the intake files.

  <del>**Import, then delete the source files.** `PYTHONPATH=src python3 scripts/import-ladd-filter.py <faa_source> <industry> [--remove <remove_file> ...]` (a single filter file also works). This is a **full replace** of `faa_ladd_aircraft` through `db.faa_upsert_ladd()`, which refuses to wipe the table on an empty or failed parse. The database is the system of record, not the intake files.</del> SUPERSEDED 2026-10-08: the procedure did not verify the import through the lookup, and the source files were never deleted (12 had accumulated since 2026-08-25; shredded 2026-10-08).
- **Removals persist.** Every `--remove` entry is recorded in
  `faa_ladd_removals` (migration `0063_ladd_removals.sql`: identifier,
  `removed_at`, source file basename, note) and re-applied after every
  import, because the FAA's own filter files can still list removed
  entries (2 of 29 in the 2026-09-15 remove list reappeared in the
  2026-09-22 Industry file). An entry stays suppressed until explicitly
  un-removed.
- **Not only N-numbers.** The data mixes US N-numbers, foreign registration
  marks and flight-ID/callsign strings. `faa_ladd_aircraft.n_number` is a
  flat membership column for all three.
- **Lookup forms (fixed 2026-10-08).** Entries are stored exactly as the FAA
  files list them, so US registrations carry the leading N (`N01AB`, illustrative), while
  the FAA registry uses `01AB`. `db.faa_is_ladd()` now matches the
  identifier as given plus the other registration form
  (`db.ladd_lookup_keys()`); an N followed by a letter is a flight ID and is
  never stripped. Before the fix the lookup stripped the N from every input
  and **matched none of the ~24,000 US registrations** (0 of 200 sampled), so
  Tier 1+ registry lookups reported `ladd: false` for them. Tier 0 always
  receives `false` by design and the demo scrub matches raw tokens, so neither
  was affected. Evidence: `docs/security-reviews/2026-10-08-06-build-arch-and-ladd.md`.
- **One source only (2026-10-08).** The poller's registry fetcher no longer
  calls the FAA's discontinued public LADD download. It used to full-replace
  the table whenever that endpoint answered with anything parseable, which
  would have wiped the CUI-sourced list and skipped the recorded removals.
  The CUI import above is now the only writer.

Live `GET /api/v1/aircraft-registry/status` 2026-10-08 01:2xZ, after the 2026-10-06 Industry + remove import: `ladd: 73479` (60 recorded removals). <del>Live `GET /api/v1/aircraft-registry/status` 18:12Z: `ladd: 73369`.</del>

**Import ledger (2026-10-09).** Every import now appends a hash-chained entry to `reference_import_ledger` (migration 0074, `src/common/import_ledger.py`). The entry records each source file's basename, size and SHA-256 with its role (filter or remove), plus the resulting counts. Hashes and counts only, never an identifier. Take the hash before the source files are shredded; the importer does this itself. The 2026-10-06 load predates the ledger. The operator re-supplied both 10-06 files on 2026-10-09, and `import-ladd-filter.py --stamp-only` recorded their hashes after confirming they match the loaded table exactly (+0 / -0). The tables were not changed, and the files were shredded afterwards. `--stamp-only` is the general way to record the source of any earlier load. Each backup's `#` stamp records the ledger head (docs/BACKUP.md).

## Where LADD status is and is not exposed

- `GET /api/v1/aircraft/{identifier}` — the real `ladd` flag is returned to
  Tier 1+ only; Tier 0 always gets `ladd: false` (fixed 2026-08-31).
- `GET /api/v1/aircraft-registry/status` — aggregate count only, Tier 0.
- **Demo and public-mirror surfaces** — any LADD-listed identifier found in
  recorded snapshot or brief text is dropped at promotion time by
  `scripts/scrub-demo-source.py` via `src/demo/scrub_rules.py`, fail-closed.
- **Internal, authenticated use** is not restricted by LADD (as with ATC,
  FBOs and authorized dispatch in the real ecosystem). Any internal
  history feature built on this data must stay loopback + tailnet
  authenticated, never on a publicly reachable port (operator directive
  2026-08-31).

## Storage

Postgres: `faa_ladd_aircraft` (migration 0053) and `faa_ladd_removals`
(0063). Both hold identifiers only.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-data.md`.


### LADD filter data — CUI (SP-PRVCY) handling

~~The FAA's automated `LADD_Aircraft.zip` download (`src/poller/fetchers/faa_registry.py`) has redirected to an FAA office page since June 2026 and produces nothing. Current LADD (Limiting Aircraft Data Displayed — FAA privacy opt-out program) data instead comes as two files the operator downloads manually and supplies weekly: an "FAA Source Filter" and a broader "Industry Filter", both marked CUI (Controlled Unclassified Information) SP-PRVCY (Specified — Privacy).~~


### LADD filter data — CUI (SP-PRVCY) handling › Handling rules

- ~~**Never commit these files to the repo, in any form** — not the raw files, not embedded literal values in a script or fixture. They must never reach the public GitHub mirror. `scrub-public-tree.py` treats this the same as any other real, non-synthetic identifier.~~
- ~~**Import, then remove the source files.** Run `PYTHONPATH=src python3 scripts/import-ladd-filter.py <faa_source> <industry>` (see that script's docstring). This replaces `faa_ladd_aircraft` in the live DB — the only place this data should persist. Delete the raw txt files afterward; the DB table is the system of record, not the intake files.~~
- ~~**Weekly refresh**: the FAA publishes new files on this cadence (operator confirmed 2026-08-31: next drop ~12:00 ET the following day). Re-run the import script each week; it's a full replace, not an incremental merge (`db.faa_upsert_ladd()` — fail-safe: refuses to wipe the list on an empty/failed parse, see that function's docstring).~~
- ~~**Not exclusively N-numbers.** The current dataset mixes US N-numbers, foreign registration marks, and flight-ID/callsign strings (confirmed live 2026-08-25/31 — short operator-style callsign strings; examples withheld, the entries are CUI alongside N-number-shaped and foreign-prefixed entries). `faa_ladd_aircraft` is a flat membership table across all three; see `scripts/import-ladd-filter.py`'s docstring and `src/demo/scrub_rules.py`'s LADD check for the two different consumers (resolved-N-number lookup vs. raw broadcast-ident scan).~~

**~~Where LADD status is (and isn't) exposed~~** *(former heading)*


### LADD filter data — CUI (SP-PRVCY) handling › Where LADD status is (and isn't) exposed

- ~~`GET /api/v1/aircraft/{identifier}` — the real `ladd` flag is gated to Tier 1+ (fixed 2026-08-31; was Tier-0/public, a live disclosure of CUI-marked status for any queried tail). Tier-0 callers always get `ladd: false` regardless of the true value.~~
- ~~`GET /api/v1/aircraft-registry/status` — an aggregate LADD *count* only (not per-tail), left Tier-0; doesn't disclose which aircraft.~~
- ~~**Demo / public-mirror surfaces**: any LADD-listed identifier (tail, foreign reg, or callsign) found in recorded snapshot or brief text is dropped at promotion time by `scripts/scrub-demo-source.py` (via `src/demo/scrub_rules.py`) — fail-closed, same discipline as every other scrub rule there: a gap in demo history is acceptable, a leak is not. This applies **even to the public GitHub-mirror tree**, not only the live demo site.~~
- ~~**Internal, authenticated use** (the operator's own dispatch instance) is not restricted by LADD — this mirrors how LADD works in the real aviation ecosystem (ATC, FBOs, and authorized dispatch operations still see the aircraft; LADD blocks generic public trackers). Any future internal longitudinal/history feature built on this data must stay loopback + tailnet-authenticated only, never exposed on a publicly-reachable port (operator directive, 2026-08-31).~~
