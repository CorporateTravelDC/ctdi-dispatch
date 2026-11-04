# LADD filter data — CUI (SP-PRVCY) handling

~~Verified against HEAD db64018 and live state on 2026-10-06 18:12Z / 14:12 ET.~~
(corrected 2026-10-15: re-verified against main 1491bf5a plus the batch staged
for the next relay, and against the live system at 2026-10-15 ~01:00Z /
2026-10-14 21:00 ET.)

The FAA's automated `LADD_Aircraft.zip` download
(`src/poller/fetchers/faa_registry.py`, `_FAA_LADD_URL`) has redirected to
an FAA office page since June 2026 and produces nothing (unverified 2026-10-25:
the redirect would need a call to the FAA, which this read-only pass does not make)
(since 2026-10-08
the poller no longer calls it at all; see "One source only" below). Current LADD
(Limiting Aircraft Data Displayed) data comes as files the operator
downloads manually each week (unverified 2026-10-25: the cadence is operator
practice; `reference_import_ledger` shows `ladd-import` loads on 2026-10-09,
2026-10-13 and 2026-10-20, SELECT 21:55Z): an "FAA Source" filter, a broader "Industry"
filter (a strict superset, confirmed 2026-08-31), and a separate "remove"
list. All are marked CUI SP-PRVCY.

## Handling rules

**Procedure since 2026-11-04 (operator): `scripts/ladd-import.sh`, crypto-erase intake.** The steps
below it are the earlier procedure, kept for the record.
- Copy the FAA files straight into `/run/user/<uid>/ladd-intake/` (tmpfs, RAM), never into the home
  folder. The script encrypts them at once with a fresh per-run AES-256 key that exists only in its
  process, and removes the plaintext.
- The decrypted files are streamed into the poller container's `/dev/shm` (tmpfs). ~~Copies in the
  container's /tmp sit on tmpfs~~ (corrected 2026-11-04: the poller quadlet mounts no tmpfs on /tmp; the
  container's /tmp is overlay storage on the NVMe, so earlier runs wrote their copies to disk).
- Exhaustive check, not a sample: the table must equal every filter identifier minus every recorded or
  supplied removal (set equality), every entry is queried through `db.faa_is_ladd()`, and every removal
  must report not LADD. Counts only.
- Crypto-erase (NIST SP 800-88 Rev.1 cryptographic erase): the key is destroyed and the ciphertext and
  container copies removed; ledger kind `ladd-source-deleted` records each file's name, size, sha256,
  the method and the import seq. Multi-pass overwriting is not used: on flash behind btrfs copy-on-write
  it cannot reach the old cells (NIST SP 800-88 Rev.1). For files that ever touched disk, `fstrim -av`
  asks the NVMe to discard the freed blocks (best effort; the USB stick does not support discard).
- A run done the old way is replaced, never removed: `--supersede SEQ,SEQ --reason TEXT` adds a
  `ladd-supersede` entry naming the earlier seqs and the run that replaces them; they stay in the chain.
- Still open (tracked): keyed-hash (HMAC) storage so the table holds no readable identifier, encrypted
  local LADD dumps (today they are compressed `pg_dump` files, recoverable, mode 0600, on-box only), an
  external anchor for the ledger head, and a human co-signature on delegated runs.

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
  3. Only then `shred -u` the source files, and record the deletion in the ledger (2026-11-04):
     a `ladd-source-deleted` entry, chained after the import, carries each deleted file's name, size
     and sha256 (the same values the `ladd-import` entry recorded), `absent_after_delete: true` and
     the import's seq. The ledger then proves the private data left the box, not only that an import
     ran. (Recorded by `ladd-import.relay`; the first entry back-filled the 2026-11-04 import.)
     Earlier text: Only then `shred -u` the source files. The database is the system of record, not
     the intake files. (corrected 2026-10-15: this host's root filesystem is
     btrfs, which is copy-on-write, so `shred` cannot overwrite the old blocks in
     place and in practice works as a delete. ~~`scripts/backup/ctdi-backup.sh:81-82`~~
     (corrected 2026-10-25: `scripts/backup/ctdi-backup.sh:93`)
     says the same. Copies in the container's `/tmp` sit on tmpfs, in RAM. The
     controls that matter are that the files stay in the operator's account,
     that their contents never reach a tracked file, and the import ledger
     below. The word "shred" in this doc means "delete with best-effort
     overwrite".)

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
  files list them, so US registrations carry the leading N (~~an illustrative example was
  given here~~ example removed 2026-10-15 under the masking rule; the form is
  `N` followed by the registry's number), while the FAA registry stores the
  number without the N. `db.faa_is_ladd()` now matches the
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

Live `GET /api/v1/aircraft-registry/status` 2026-10-08 01:2xZ, after the 2026-10-06 Industry + remove import: `ladd: 73479` (60 recorded removals). (2026-10-15 00:51Z: `ladd: 73543`, 84 recorded removals. `reference_import_ledger` holds two `ladd-import` entries, 2026-10-09 23:08Z and 2026-10-13 15:47Z.) (superseded 2026-10-25 21:23Z: `ladd: 73333`, 115 recorded removals; the ledger holds three `ladd-import` and ~~16~~ `faa-registry` entries (superseded 2026-10-26: 17 `faa-registry`, 3 `ladd-import`; `SELECT kind,count(*) FROM reference_import_ledger GROUP BY 1` 22:00Z).) <del>Live `GET /api/v1/aircraft-registry/status` 18:12Z: `ladd: 73369`.</del>

**Import ledger (2026-10-09).** Every import now appends a hash-chained entry to `reference_import_ledger` (migration 0074, `src/common/import_ledger.py`). The entry records each source file's basename, size and SHA-256 with its role (filter or remove), plus the resulting counts. Hashes and counts only, never an identifier. Take the hash before the source files are shredded; the importer does this itself. The 2026-10-06 load predates the ledger. The operator re-supplied both 10-06 files on 2026-10-09, and `import-ladd-filter.py --stamp-only` recorded their hashes after confirming they match the loaded table exactly (+0 / -0). The tables were not changed, and the files were shredded afterwards. `--stamp-only` is the general way to record the source of any earlier load. Each backup's `#` stamp records the ledger head (docs/BACKUP.md). (added 2026-10-15) `import_ledger.verify()` (`src/common/import_ledger.py:86`) checks the chain, but nothing calls it on a schedule: the ledger can be checked, and nothing checks it automatically.

## Where LADD status is and is not exposed

- `GET /api/v1/aircraft/{identifier}` — the real `ladd` flag is returned to
  Tier 1+ only; Tier 0 always gets `ladd: false` (fixed 2026-08-31).
- `GET /api/v1/aircraft-registry/status` — aggregate count only, Tier 0.
- **Demo and public-mirror surfaces** — any LADD-listed identifier found in
  recorded snapshot or brief text is dropped at promotion time by
  `scripts/scrub-demo-source.py` via `src/demo/scrub_rules.py`, fail-closed.
- ~~**Internal, authenticated use** is not restricted by LADD (as with ATC,
  FBOs and authorized dispatch in the real ecosystem). Any internal
  history feature built on this data must stay loopback + tailnet
  authenticated, never on a publicly reachable port (operator directive
  2026-08-31).~~ (superseded 2026-11-02 by the withheld categories below: internal
  use is still allowed by the FAA terms, but "internal" is now a verified
  pass holder, not a network address. The tailnet without a pass gets LADD
  aircraft withheld.)
- **Third-party lookups (added 2026-10-15).** The airplanes.live fallback
  (`src/common/airplanes_live.py`, enabled on this deployment since
  2026-10-08) never sends a LADD-listed identifier. For a hex it first maps
  the hex to its registration. If the LADD check fails, the identifier is
  treated as listed and nothing is sent. The query alone would tell a third
  party which privacy-listed aircraft is being followed. ~~Not covered: the
  airframes.io and ACARS Drama REST reads by registration
  (`src/acars_watcher/acars_watcher.py`, `src/common/acars.py`) have no LADD
  check (2026-10-15, open).~~ (superseded 2026-10-25: `src/common/acars.py:170-185`
  now has `_ladd_listed()`, which treats a lookup failure as listed and skips the
  external query; `src/acars_watcher/acars_watcher.py` builds its Jumpseat list
  from `GET /api/v1/watchlist/registrations`, which the dispatch API LADD-filters
  fail-closed (`acars_watcher.py:17-24,95,307-325`; ~~`web/routes/watchlist.py:138-167`~~ (corrected 2026-11-02: `web/routes/watchlist.py:239-297`)).)
- **Backups (added 2026-10-15).** The off-box backup carries the LADD table
  schema but not its rows (`docs/BACKUP.md`). Local CUI dumps stay on the box
  (`BACKUP_CUI_LOCAL_KEEP`, default 7), and older ones are removed with
  `shred -fu`, which on btrfs is a delete (see step 3 above).

## Withheld categories and passes (added 2026-11-02, operator directive)

LADD is one of three withheld categories: `aircraft` (the FAA LADD import), `rail` and
`vessel` (operator-kept lists in `withheld_identifiers`). Each is revealed only to a
bearer token naming its own grant (`ladd.view`, `rail.view`, `vessel.view`), human or
agent account alike, or to the master token. Admin tier, a cert service token or a
tailnet address alone is not enough. Everyone else has those items withheld, and a
direct lookup answers "No aircraft / train / vessel available", exactly like
something the platform has no data for. The public demo stays fully scrubbed.
Code: `src/common/withheld.py` (a new category is one more `CATEGORIES` entry);
routes: `src/web/routes/withheld.py`; panel: `src/runner/main.py` (`/api/withheld/pair`).

| Surface | Without the pass |
|---|---|
| `GET /api/v1/swim/positions` | listed aircraft removed |
| `GET /api/v1/swim/last-leg` | 404 "No aircraft available" |
| `GET /api/v1/watchlist` (incl. `?include=eta`) | a withheld flight, train or vessel watch keeps what the operator typed; identity, OOOI, status, notes and ETA nulled; `availability` = "No ... available" |
| `GET /api/v1/flightplan/{callsign}` | reads as "no plan on file" |
| `GET /api/v1/flights/{ident}/assignments` | everything `unknown` |
| `GET /api/v1/aircraft/{identifier}` | `ladd: false` |
| `GET /api/v1/amtrak` | withheld trains removed; the summary rebuilt from counts |
| runner `/api/adsb/*`, `/api/map/*` (local receiver), `/api/ais/vessels` | screened through `POST /api/v1/withheld/screen`; any failure withholds every local item |

Not covered yet: the full tar1090 receiver page (port 8080/8081 on the tailnet) and the
raw Beast output on port 30005 serve the receiver unfiltered; server-side briefs and
digests that read Amtrak or AIS upstream directly do not consult the rail and vessel
lists.

**PIA call signs.** There is no published Privacy ICAO Address (PIA) list, and a PIA
aircraft's temporary hex is not in the registry. Its third-party call sign is what
identifies it, so `db.faa_ladd_listed_batch` (the one LADD gate for the live filters,
the withheld categories, the third-party lookup guard, ACARS, gate records and the demo
scrub) treats every call sign from the four FAA-approved providers as listed:
FltPlan.com `DCM` ("DOTCOM"), ForeFlight `FFL`, FlightAware `FWR` and ARINCDirect `XAA`
("ROCKFISH"), each a designator plus a 1-4 character flight number
(`db.PIA_CALLSIGN_PREFIXES`). Sources: the FAA ADS-B Privacy page and the NBAA ADS-B privacy questions page.
A lesser-known or new provider is added to that tuple; a privately held list goes into
the operator-kept lists.

**Non-US aircraft: each country's own default** (`src/common/privacy_jurisdictions.py`).
No non-US authority distributes a LADD- or PIA-style list. So a non-US aircraft follows
its own country's default, taken from the registration mark or, failing that, the ICAO
24-bit address block.
- *Private* means it is not flying under an ICAO operator designator.
- Europe (EU/EEA, Switzerland, the UK and Crown dependencies, Monaco) and the UAE:
  private aircraft are withheld.
- Canada, Australia, New Zealand, Japan and Brazil: shown. LADD, PIA and opt-outs
  still apply.
- A non-US owner name from the OpenSky registry is returned only where that country
  publishes owner names itself. That excludes Germany, the UAE, New Zealand, Brazil
  and every country not in the table.
- An owner's opt-out request, from any country, goes into the operator-kept aircraft
  list (`withheld.sh list-add aircraft reg|callsign|hex`) and is withheld like LADD.

The basis for each country is recorded in the table itself. The European default
mirrors tracker practice (FlightAware); no EASA or EUROCONTROL rule behind it was found.

**Passes.** `scripts/withheld.sh issue <device> --categories aircraft,rail,vessel [--hours N]`
(N <= 12) creates a `withheld-pass` approval over the device, categories, lifetime and
the sha256 of a one-time pairing code, signs it with `approve.sh`, then prints the
code. On the device, `/api/withheld/pair` exchanges it (once, within 10 minutes) for a
token that lives only in an httponly, SameSite=Strict cookie. Agents get a grant only
through `scripts/mint-agent-api-token.sh ACCOUNT --actions ...,rail.view`.

**Master token.** `scripts/withheld.sh master <device> [--hours N]` reveals every
category, present and future. It is honoured only while all of these hold:
- it was minted by that command, so it has a `withheld_masters` row (a plain token
  mint naming `withheld.master` does nothing);
- its request carries a GPG signature by the operator primary key pinned in
  `security/signing.env` (the agent delegate key is refused), checked in an isolated
  keyring inside the web container, plus the operator's SSH approval signature;
- its holder is still an active human board signer with role admin.
Any kill or lockdown deletes master tokens outright, and a thaw never restores one.

**Kill switch.** `scripts/withheld.sh off [reason]` freezes every category, revokes
every pass (a mixed-scope agent token is revoked whole) and wipes the master token.
`off --category C` freezes one category. `lockdown.sh` and `agent-gateway.sh kill-all`
run the full kill. `withheld.sh revoke <device>` ends one device's tokens. Re-opening is
`withheld.sh on`, which needs the operator's signature (`withheld-thaw`).

**Lists.** `withheld.sh list-add rail train` / `list-add vessel mmsi|imo|name|callsign`
read the value at a hidden prompt; `status` shows counts only.

**Audit** (`withheld_audit`, no identifier stored). Every reveal is one row per
identifier per user per minute, with digest HMAC-SHA-512(key,
`minute|user|category:kind:value`). While frozen, every request that touched a
withheld item is one row per identifier with digest HMAC-SHA-512(key,
`category:kind:value`). The key is `WITHHELD_AUDIT_KEY` in the production secrets file
(at least 32 characters; without it nothing is revealed). The hash is keyed because
registrations, train numbers and MMSIs are few enough to reverse an unkeyed SHA-512
by enumeration. To test a suspected leak, run `withheld.sh audit-check [--since HOURS]`.

## Push-time tripwire and history scan (added 2026-10-15)

- **Tripwire (since 2026-10-09).** Before every public push,
  `scripts/push-public.sh` (step 4b) pipes the identifiers from
  `faa_ladd_aircraft` and `faa_ladd_removals` on stdin into
  `scripts/leak-guard/ladd_public_check.py --tree <extracted tree>`. Any
  whole-token match, or a match on one hyphen-separated part, stops the push
  (exit 7). It fails closed when fewer than 1,000 identifiers arrive. Output
  is file:line and counts only. The identifiers are never written to disk or
  printed. A confirmed coincidence (an ordinary word that is also an entry)
  is allowed by adding its SHA-256, never the value, to
  `scripts/leak-guard/ladd-public-allow.sha256`. `--review` shows matching
  tokens and is for the operator's own terminal only.
- **Why it exists.** Three identifiers quoted from the 2026-08-25 filter
  files sat in a doc and a docstring, public mirror included, from the first
  sanitized push until 2026-10-09.
- **History.** The tripwire guards the tree being published, not the history
  already published. `scripts/leak-guard/ladd_history_scan.py` (2026-10-14)
  scans every blob, commit message and tag reachable from any ref using the
  same matching rule. It can write the git-filter-repo replacement file
  (mode 0600; the file holds CUI, so shred it after use). Office files are
  read through their XML, as the tripwire reads them; identifiers inside an
  Office file stop the scan (exit 3), because text replacement cannot rewrite
  a zip. The public mirror's history was rewritten with it on 2026-10-15
  01:44Z / 21:44 ET 10-14 (6 identifiers; tip tree unchanged; a fresh-clone
  rescan finds 0). The archived public repository `corporatetravel-dispatch-mcp`
  still has 2 identifiers in its history (open operator item) (unverified 2026-10-25: the archived mirror's history was not rescanned).

## Transcript leak scan: LADD class (added 2026-10-19, Gate-1b)

- **What.** `scripts/leak-guard/transcript_leak_scan.py` (on-box agent
  transcripts and tool outputs) has a pattern class `ladd-identifier`. Its
  list is the production filter's own: `faa_ladd_aircraft UNION
  faa_ladd_removals`, the same union `push-public.sh` pipes into the
  tripwire, read in-process through `common.db.conn()` only when the scan runs
  with the dispatch env (`scripts/with-dispatch-env.sh`). The identifiers are
  never printed, logged or written; output is file, class and count.
- **Union only, no tier column (by design, operator 2026-10-19).** The scan
  does not distinguish listed from removed entries, or one filter file's
  tier from another: an identifier in either table is one class. Nothing is
  gained by telling them apart in a transcript, and a tier label next to a
  hit would itself describe the entry.
- **Matching.** Whole tokens of letters, digits and hyphens, case-sensitive,
  each hyphen part too (the tripwire's rule); every entry in its N-form and
  its registry form without the N; flight-ID strings as listed. JSON escapes
  are separators; base64 payloads of 200+ characters are skipped (random
  noise, not text anyone wrote); an all-digit registry form is never matched
  (it would hit every number). Coincidences use the tripwire's hash allow
  file.
- **Redaction.** `redact` replaces each hit with `[ladd-redacted]` through
  the scan's atomic temp-file + rename path (same mode, owner kept under
  root), and files written in the last 15 minutes (a live session) are left
  for a later run, as for every other class.
- **Fail closed.** With the dispatch env and the list unreadable (database
  down, or fewer than 1,000 identifiers), the scan prints `LADD class
  'ladd-identifier' NOT CHECKED` and exits 3 on both `scan` and `redact`.
  Without the dispatch env it prints the same as a warning and exits 3 only
  under `--require-ladd`. ~~(Open, 2026-10-19: `push-public.sh` and
  `relay-preflight.sh` still call the scan without the dispatch env.)~~
- **Required at every push point (2026-10-20, next relay).** `pre-push`,
  `push-public.sh` and `relay-preflight.sh` run the scan through
  `scripts/with-dispatch-env.sh` with `--require-ladd`: an unreadable list
  rejects the push. The hourly root unit (`agent-leak-scan.sh`) pipes the same
  union out of the `corporatetraveldc-pgsql` container into `--ladd-stdin`
  (never on disk; fewer than 1,000 identifiers = exit 3, the unit fails), as
  the public tripwire does. "With the dispatch env" now means
  `DISPATCH_PG_HOST` / `_DB` / `_USER` are set (the settings `common.db.conn()`
  connects with), ~~`DISPATCH_DB_BACKEND` or `DISPATCH_DB`~~.

## Demo timing anti-correlation (added 2026-10-20)

Operator decisions, 2026-10-20. Code: `src/demo/ladd_timing.py`, applied by
`scripts/scrub-demo-source.py` after the Q substitution. Tests:
`tests/demo/test_ladd_timing.py`, `tests/scripts/test_scrub_demo_source.py`.

- **Threat model.** The Q mark (a synthetic registration, Q followed by five
  characters, an HMAC of the listed identifier under a production-only key)
  hides the registration, not the timing. Someone physically at an FBO -- an
  employee, an onlooker, a stalker -- sees which aircraft arrives when. Watching
  the demo, they can line up "a jet arrived at a given minute" with "the Q mark
  that landed at that minute" and learn which real aircraft each Q mark is.
  Timing is the identifier the Q mark does not hide.
- **Where the rules apply.** Only to LADD-listed legs (production filter,
  `faa_ladd_listed_batch` / `faa_ladd_hex_batch`; a lookup failure is fatal,
  exit 5) and only on the demo path. **Public = demo, always; tailnet = live,
  no delays and no LADD restriction.** Nothing here changes production data,
  the live runner, the agents or any tailnet view.
- **Per-leg bucketing with a rotating hidden offset.** Every time on a LADD leg
  (ETA, ETD, OOOI, event and position times, ISO timestamps and clock times in
  prose) becomes `floor((t + offset) / 15 min) * 15 min`. The offset is a whole
  number of seconds in [0, 15 min), constant within a leg, drawn from an HMAC
  under the same production-only key as the Q marks with its own domain label.
  It is never printed, logged or stored, and it cannot be computed without the
  key. Offsets rotate **per leg**: every leg draws its own offset anywhere in
  [0, 15 min), and consecutive legs of the same aircraft -- two legs on one
  operational day, and the last leg of a day against the first of the next --
  always differ by at least 4 minutes. A reader can never learn "this aircraft
  is always N minutes behind". Constants: `BUCKET_S = 900`,
  `ROTATION_MARGIN_S = 240`, `CHAIN_LOOKBACK_DAYS = 7`, `PRE_DEPARTURE_S = 5400`.
- **Leg identity** is (Q mark, operational day, leg index). The operational day
  uses the platform's 05:00 America/New_York rollover. The **leg index comes
  from SWIM**, not from the text: the scrub reads the aircraft's legs that day
  from `flight_events` (FDPS departure and arrival times, by the real
  registration or callsign, in-process through the read-only production
  connection, never logged or stored) and orders them by departure. Each leg
  owns a time window, split at the midpoint between one leg's arrival and the
  next leg's departure (without an arrival time: 90 minutes, or half the gap,
  before the next departure). A reference goes to the leg whose window holds
  the time it mentions -- a record's departure time, else its earliest time; a
  sentence's first time; else the row's capture time. The destination is
  deliberately not used: brief prose often names a flight without one, and the
  window rule gives a leg's prose and its structured record the same index, so
  one leg never shows two offsets (two would let a reader intersect two
  15-minute windows).
- **Offset chain.** Leg k's offset is the first HMAC draw (domain label, Q mark,
  day, k, attempt) at least 4 minutes from the previous leg's. The chain starts
  after the aircraft's most recent operational day with no SWIM leg, at most
  7 days back, so every scrub of a given day computes the same offsets
  (idempotent, nothing stored). When the 7-day cap is hit, the day's first leg
  is checked against the previous day's last leg computed from its own chain;
  a miss drops that aircraft-day.
- **Fail closed on leg order.** If the order cannot be determined -- no SWIM leg
  for the aircraft that operational day, a failed lookup, or a record known
  only by its redacted hex -- every LADD record and sentence for that
  aircraft-day is **dropped**, never shown under a coarser rule (counted as
  `ladd aircraft-day dropped (no SWIM leg order)`).
- **Dark point.** A LADD leg ends at the dark point; nothing at or after it
  enters the demo database. Order: the approach's **inner marker** where the
  chart data has one; else crossing the **runway threshold**; else the
  confirmed SWIM **wheels-on** time. If none can be determined, the leg fails
  closed: no track point within 10 nm of the destination (or of any charted
  airport when the destination is unknown) survives. Whatever the time,
  arrival-side fields and sentences are always removed for a LADD leg: landing
  and ON/IN times, gate, terminal, baggage belt/carousel, FBO, taxi, surface
  movement, parking/ramp. The demo therefore never shows where a listed
  aircraft parks.
- **Chart data on this box (2026-10-20):** the loaded CIFP tables carry runway
  thresholds but **no marker beacons** (fix types: en-route and terminal
  waypoints, airports, runway thresholds, VOR/DME, NDB). The inner-marker step
  is coded and tested but cannot fire until a marker type is parsed; the
  threshold step applies today. The demo itself carries no live tracks (the
  demo runner's ADS-B and ACARS views are synthetic), so in practice the dark
  point acts on recorded snapshot and brief content.
- **Audit.** Counts only: the scrub report and the hash-chained `scrub_ledger`
  (`dropped_by_class`, keys prefixed `ladd timing:`) record how many legs were
  timed, how many arrival fields and sentences were dropped, and which
  dark-point path each leg took (`im`, `threshold`, `wheels_on`,
  `fail_closed`). Never an identifier, an offset or the key.
- **Not protected, and why.** An observer who already knows one real time of
  a leg can bound that leg's offset only; the next leg draws a new one.
  Aggregate counts (how many listed aircraft were active in a window) are not
  hidden. A LADD aircraft FDPS does not report (no filed registration or
  callsign match) is dropped from the demo rather than shown, so the demo
  under-shows listed traffic by design. FDPS revising a departure time after a
  scrub can move a reference to the neighbouring window on a later re-scrub;
  rows already promoted are not rewritten (`ON CONFLICT DO NOTHING`).

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
