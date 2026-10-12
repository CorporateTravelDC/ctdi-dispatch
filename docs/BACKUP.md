# Backups

`scripts/backup/ctdi-backup.sh`: encrypted, deduplicated, ledgered backups of the platform, synced to one parameterized off-box target.

## What is backed up

| Item | How | Notes |
|---|---|---|
| Dispatch Postgres | `pg_dump -Fc` inside the container (its own env carries the credentials) | LADD table **data** excluded by default (see below) |
| Nextcloud database | `pg_dump -Fc` inside `nextcloud-db` | |
| Nextcloud files | the `nextcloud-html` volume, read through `podman unshare` | |
| Platform state | `/var/lib/corporatetraveldc` | `models/` excluded (pinned by digest, re-pullable) |
| Configuration | `/etc/corporatetraveldc` | already secret-bearing; the archive is encrypted |
| Private ledger | a copy of `ledger.jsonl` inside every archive | |

Repositories are excluded (they live on GitHub, signed).

## Encryption: two layers

1. **Ours:** Borg `repokey` mode: AES-256-CTR for confidentiality, HMAC-SHA256 for authentication, and PBKDF2-SHA256 for the key passphrase. These are all NIST-approved algorithms. This host does not run a FIPS-validated (CMVP) crypto module. A deployment that must claim FIPS 140 validation has to run on a validated OS crypto stack; the algorithm choice already fits that. Restic was rejected because its primitives (Poly1305-AES, scrypt) are not on the NIST-approved list.
2. **The target's:** Proton Drive encrypts end to end on top of that. Other targets (SMB, Google Workspace, Microsoft 365, S3, SFTP) add their own at-rest encryption. The target only ever holds Borg-encrypted chunks.

Borg reads the passphrase at run time from `BACKUP_PASSPHRASE_FILE` (operator-only, mode 0600/0400) through `BORG_PASSCOMMAND`. No passphrase agent or cache is used, per the operator rule. Keep an offline copy of the passphrase and of `borg key export`. Without both, the backups can't be read.

## LADD (CUI): stays on the box, stamped `#` in the private ledger

The FAA LADD tables (`faa_ladd_aircraft`, `faa_ladd_removals`) are CUI. By default their **data never leaves the box**:

- The off-box dump carries the LADD schema but not its rows (`--exclude-table-data`).
- Each run writes a **local-only** LADD dump to `~/.local/state/ctdc-backup/cui-local/` (0600). The newest `BACKUP_CUI_LOCAL_KEEP` dumps are kept and older ones are shredded.
- Each run appends a **`#` stamp** (`kind: cui-stamp`, `"stamp": "#"`) to the private ledger. It records:
  - the **SHA-256** of that local dump;
  - its size and local path;
  - the row count per table (counts only, never identifiers);
  - `off_box: false`.
  - the head (`seq`, `entry_hash`) of the in-database **reference-import ledger** (`reference_import_ledger`, migration 0074). Every LADD filter/remove import and every weekly FAA registry download appends to that chain: source basename, size, SHA-256 and the resulting row counts. So each backup also pins exactly which LADD and registry source files were loaded.

The stamp is hash-chained with every other ledger entry, and a copy of the ledger rides in each encrypted archive. So the off-box copy proves which LADD state existed at each backup without carrying it, and an on-box dump can be checked against its stamp at any time with `backup_ledger.py file <dump>`. `BACKUP_INCLUDE_CUI=1` puts the data in the off-box set as well, but only where the target is authorised for CUI. The stamp is still written, with `off_box: true`.

## The private ledger

`scripts/backup/backup_ledger.py` keeps `~/.local/state/ctdc-backup/ledger.jsonl` (0600). Each line holds one event: `backup`, `cui-stamp`, `sync`, `restore-test` or `note`. Each entry's hash is `sha256(prev_hash | canonical entry)`, so any edited, reordered or removed line breaks `verify`. Backup entries record the Borg archive id and sizes plus the SHA-256 of every database dump. The ledger is hashed first and then encrypted along with the rest of the archive.

## Targets (parameterized)

`BACKUP_TARGET` in `dispatch.env` takes either an rclone remote path or a mounted directory:

| Target | `BACKUP_TARGET` | Remote set up once, interactively, with `rclone config` |
|---|---|---|
| Proton Drive | `proton:ctdi-backup` | `protondrive` backend (account login + 2FA) |
| SMB share / an organization's Z: drive | `smb-org:share/ctdi` or a mount path | `smb` backend, or a CIFS mount |
| Google Workspace (shared drive) | `gdrive:ctdi-backup` | `drive` backend, `team_drive` set |
| Microsoft 365 (OneDrive / SharePoint) | `m365:ctdi-backup` | `onedrive` backend, drive type `business` or `documentLibrary` |
| S3-compatible | `s3:bucket/ctdi` | `s3` backend |
| SFTP | `sftp-host:/srv/backup/ctdi` | `sftp` backend |
| Local only | empty | |

rclone keeps its own config (`BACKUP_RCLONE_CONFIG`, operator-only). Its credentials never go in a tracked file.

## Retention

`BACKUP_KEEP_DAILY` / `WEEKLY` / `MONTHLY` default to 30 / 12 / 12. Each run prunes and compacts the local repository first and then syncs, so the target mirrors the pruned state.

## Sizing (measured 2026-10-09)

The first full archive is about 10–14 GB before deduplication and compression:

- dispatch DB: 13 GB live, about 3–5 GB as a compressed dump;
- Nextcloud: 5.3 GB of files and 135 MB of database;
- platform state: 5.2 GB once models are excluded.

Daily increments are expected to be small, mostly the dispatch dump. This fits easily in the 2 TB Proton allotment.

## Schedule, restore test and freshness (2026-10-11)

| Unit | When | What |
|---|---|---|
| `corporatetraveldc-backup.timer` | daily 03:40 UTC (±10 min) | `ctdi-backup.sh`: dumps, LADD `#` stamp, Borg archive, prune and compact, sync to `BACKUP_TARGET` |
| `corporatetraveldc-backup-restore-test.timer` | Sundays 05:30 UTC | `backup-restore-test.sh`: extracts the newest archive's dumps, matches their SHA-256 against the ledger, runs `pg_restore --list` on each (read-only, inside the Postgres container), checks the local LADD dump against its `#` stamp, writes a `restore-test` ledger entry. Nothing is restored into a live database. |
| `corporatetraveldc-backup-freshness.timer` | every 6 h | `backup_freshness.py` fails and pages when the last backup is older than 36 h, the last successful off-box sync is older than 36 h (when a target is set), the restore test is older than 8 days or failed, or the ledger chain breaks. |

All three page through `corporatetraveldc-unit-failure-notify@` on failure.

## Agents: daily root snapshot + time cards (2026-10-11)

Default behaviour for **every** `ctdc-agent-*` account (`CTDC_SNAPSHOT_EXCLUDE` removes names):

| Unit | When | What |
|---|---|---|
| `corporatetraveldc-agent-home-snapshot.timer` (system, root) | daily 03:20 UTC | `agent-home-snapshot.sh` (installed root copy): tars each agent home into `/var/lib/corporatetraveldc/agent-snapshots/<account>/` (operator-owned, 0600, SHA-256 beside it, last 7 kept). **Credentials are excluded** (SSH private keys, auth and token files, keyrings): a restored agent re-enrolls. |
| `corporatetraveldc-agent-timecard.timer` (user) | daily 03:50 UTC | `agent_timecard.py`: the agent analogue of a daily run sheet or call-center performance sheet. For the previous UTC day it records shift (first and last activity), work (tool calls, board posts, contributions, signed requests), exceptions (refused or failed calls) and custody (that day's snapshot hash, the covering backup archive). JSON goes to `/var/lib/corporatetraveldc/agent-timecards/`, the readable card to vault `04-Syntheses/agent-timecards/`, and the card's SHA-256 to the private ledger. |

The freshness check also pages when any agent snapshot is more than 36 h old.

