# Backups

`scripts/backup/ctdi-backup.sh`: encrypted, deduplicated, ledgered backups of the platform, synced to one parameterized off-box target.

**Current state (re-checked 2026-10-15 ~01:20Z against main 1491bf5a and the live box).** Nightly Borg archives are
written on the box (`~/.local/state/ctdc-backup/borg`, on the same NVMe btrfs volume as the data) and synced to
Proton Drive (`proton:ctdi-backup`; last good syncs 2026-10-13 05:30Z and 2026-10-14 06:41Z, the latter after the
stall described under "Sync caps"). `backup-freshness` reported OK on its 2026-10-14 13:15Z and 19:15Z runs. This is
an off-box **copy**, not a tested disaster-recovery path: the only restore test so far is a check of the dumps
(`pg_restore --list`, hashes), not a restore into a database or onto new hardware, and the only restore-test run is a
manual one on 2026-10-11 23:23Z (see the schedule table). The Pi 5 rebuild sequence is in `docs/PI5-BOOT-CONFIG.md`.

## What is backed up

| Item | How | Notes |
|---|---|---|
| Dispatch Postgres | `pg_dump -Fc` inside the container (its own env carries the credentials) | LADD table **data** excluded by default (see below) |
| Nextcloud database | `pg_dump -Fc` inside `nextcloud-db` | |
| Nextcloud files | the `nextcloud-html` volume, read through `podman unshare` | |
| Platform state | `/var/lib/corporatetraveldc` | `models/` excluded (pinned by digest, re-pullable) |
| Configuration | `/etc/corporatetraveldc` | already secret-bearing; the archive is encrypted |
| Traffic history | `/var/lib/vnstat` | vnStat database (unlimited daily retention since 2026-10-11) |
| Private ledger | a copy of `ledger.jsonl` inside every archive | |

Repositories are excluded (they live on GitHub, signed).

## Encryption: two layers

1. **Ours:** Borg `repokey` mode: AES-256-CTR for confidentiality, HMAC-SHA256 for authentication, and PBKDF2-SHA256 for the key passphrase. These are all NIST-approved algorithms. This host does not run a FIPS-validated (CMVP) crypto module. A deployment that must claim FIPS 140 validation has to run on a validated OS crypto stack; the algorithm choice already fits that. Restic was rejected because its primitives (Poly1305-AES, scrypt) are not on the NIST-approved list.
2. **The target's:** Proton Drive encrypts end to end on top of that. Other targets (SMB, Google Workspace, Microsoft 365, S3, SFTP) add their own at-rest encryption. The target only ever holds Borg-encrypted chunks.

Borg reads the passphrase at run time from `BACKUP_PASSPHRASE_FILE` (operator-only, mode 0600/0400) through `BORG_PASSCOMMAND`. No passphrase agent or cache is used, per the operator rule. Keep an offline copy of the passphrase and of `borg key export`. Without both, the backups can't be read.

## LADD (CUI): stays on the box, stamped `#` in the private ledger

The FAA LADD tables (`faa_ladd_aircraft`, `faa_ladd_removals`) are CUI. By default their **data never leaves the box**:

- The off-box dump carries the LADD schema but not its rows (`--exclude-table-data`).
- Each run writes a **local-only** LADD dump to `~/.local/state/ctdc-backup/cui-local/` (0600). The newest `BACKUP_CUI_LOCAL_KEEP` (default 7) dumps are kept and ~~older ones are shredded~~ (corrected 2026-10-15: older ones are deleted with `shred -fu` (`ctdi-backup.sh:107`), but on btrfs, which is copy-on-write, the overwrite does not reach the original blocks, so this is a plain delete in effect. The real controls are the 0600 mode, the dumps never leaving the box, and physical custody of the box. The stage directory and the restore test already use plain `rm` for this reason (`ctdi-backup.sh:81-83`, `backup-restore-test.sh:24`).)
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

> **Re-measured 2026-10-15:** each nightly archive now covers about 20.5 GB before deduplication and compression
> (ledger `original_size` of the 2026-10-14 archive; 19.6 GB on 10-12 and 10-13). The Borg repository is 27 GB on disk
> (`du -sh`), and the 2026-10-12 Proton seed was 19 GB. The root volume is 83% used
> (188 G of 238 G, 39 G free; `df -h /`), so the local repository is now a material share of the free space. The
> figures below are the 2026-10-09 estimate, kept for the record.

The first full archive is about 10–14 GB before deduplication and compression:

- dispatch DB: 13 GB live, about 3–5 GB as a compressed dump;
- Nextcloud: 5.3 GB of files and 135 MB of database;
- platform state: 5.2 GB once models are excluded.

~~Daily increments are expected to be small, mostly the dispatch dump.~~ (corrected 2026-10-15: they are not small. Borg's `deduplicated_size` was 4.5 GB, 5.0 GB and 4.2 GB for the 10-12, 10-13 and 10-14 archives. The compressed dispatch dump changes almost entirely every night, so it barely deduplicates, and each nightly sync uploads several GB, roughly 30 min or more at the 2.5 MB/s soft cap.) This still fits in the 2 TB Proton allotment.

## Schedule, restore test and freshness (2026-10-11)

| Unit | When | What |
|---|---|---|
| `corporatetraveldc-backup.timer` | daily ~~03:40 UTC (±10 min)~~ 03:40-03:50 UTC (corrected 2026-10-15: `RandomizedDelaySec=10m` only delays, it never starts early) | `ctdi-backup.sh`: dumps, LADD `#` stamp, Borg archive, prune and compact, sync to `BACKUP_TARGET` |
| `corporatetraveldc-backup-restore-test.timer` | Sundays 05:30 UTC (as of 2026-10-15 the timer has not fired yet: it was enabled 2026-10-11 23:23Z, after that Sunday's 05:30Z slot, so no scheduled slot has come up. The only `restore-test` ledger entry is the manual run of 2026-10-11 23:23Z. The first scheduled run is Sun 2026-10-18 05:30Z, ahead of the 8-day freshness limit at 2026-10-19 23:23Z.) | `backup-restore-test.sh`: extracts the newest archive's dumps, matches their SHA-256 against the ledger, runs `pg_restore --list` on each (read-only, inside the Postgres container), checks the local LADD dump against its `#` stamp, writes a `restore-test` ledger entry. Nothing is restored into a live database. |
| `corporatetraveldc-backup-freshness.timer` | every 6 h (01:15, 07:15, 13:15, 19:15 UTC) | `backup_freshness.py` fails and pages when the last backup is older than 36 h, the last successful off-box sync is older than 36 h (when a target is set), no target is set while `BACKUP_REQUIRE_OFFBOX=1` (the default since the 2026-10-13 security batch (127a2f76): on-box only is a standing failure, not a quiet state), any agent snapshot is older than 36 h, the restore test is older than 8 days or failed, or the ledger chain breaks. |

All three page through `corporatetraveldc-unit-failure-notify@` on failure.

## Agents: daily root snapshot + time cards (2026-10-11)

Default behaviour for **every** `ctdc-agent-*` account (`CTDC_SNAPSHOT_EXCLUDE` removes names):

| Unit | When | What |
|---|---|---|
| `corporatetraveldc-agent-home-snapshot.timer` (system, root) | daily 03:20 UTC | `agent-home-snapshot.sh` (installed root copy): tars each agent home into `/var/lib/corporatetraveldc/agent-snapshots/<account>/` (operator-owned, 0600, SHA-256 beside it, last 7 kept). ~~**Credentials are excluded** (SSH private keys, auth and token files, keyrings): a restored agent re-enrolls.~~ **Superseded 2026-10-12:** the name excludes missed a Claude Code session key and a browser profile (see below). Now: wider excludes plus a tripwire that deletes any archive still holding a credential-shaped name and fails the unit. A restored agent re-enrolls. |
| `corporatetraveldc-agent-timecard.timer` (user) | daily 03:50 UTC | `agent_timecard.py`: the agent analogue of a daily run sheet or call-center performance sheet. For the previous UTC day it records shift (first and last activity), work (tool calls, board posts, contributions, signed requests), exceptions (refused or failed calls) and custody (that day's snapshot hash, the covering backup archive). JSON goes to `/var/lib/corporatetraveldc/agent-timecards/`, the readable card to vault `04-Syntheses/agent-timecards/`, and the card's SHA-256 to the private ledger. |

The freshness check also pages when any agent snapshot is more than 36 h old. It checks every `ctdc-agent-*` account (minus `CTDC_SNAPSHOT_EXCLUDE`, or exactly `CTDC_SNAPSHOT_AGENTS` when set): six accounts on 2026-10-15, all with a 2026-10-14 03:20Z snapshot. Before that batch (2026-10-13, 127a2f76) it checked only the Codex account.

**2026-10-12 incident (docs-parity audit, verified on the box).** The 2026-10-11 and 10-12 snapshots captured a
Claude Code session key (`.claude/sessions/*.key`) in the Claude agent's home and a Brave browser profile
(`Local State` holds the cookie encryption key) in the Codex agent's home. Both rode, Borg-encrypted, in the
three archives made before the fix; none had left the box (no off-box target was configured yet). Fix:
`.claude/sessions`, `*.key`, `*.pem`, `*.p12`/`*.pfx`, browser profile directories (Brave, Chrome, Chromium,
Firefox), `Cookies`, `Login Data`, `Web Data` and `Local State` are excluded, and `agent-home-snapshot.sh` lists
every archive it writes and refuses (deletes it, exits 1) on any credential-shaped name, so a new kind of
credential file fails loudly instead of being backed up. The existing snapshots were deleted and regenerated,
the three archives were rewritten without them (`borg recreate`, then `borg compact`), and the ledger records it.
`CTDC_SNAPSHOT_EXCLUDE` is now settable through `/etc/corporatetraveldc/agent-snapshot.env` (the unit's
`EnvironmentFile`). Tests: `tests/scripts/test_agent_home_snapshot.py`.

No rotation was needed (operator, 2026-10-12): the Claude session key belonged to a remote-control session the
operator had already restarted when agents moved to the gateway, and the Codex account has never used Brave, so
its profile held no sign-in.



## Off-box target: Proton Drive (live 2026-10-12)

- `BACKUP_TARGET=proton:ctdi-backup`. Proton only ever receives Borg-encrypted chunks; LADD table data stays on
  the box (`BACKUP_INCLUDE_CUI=0`).
- **Login:** one 6-digit code at setup; rclone saved a Proton **session** in `~/.config/rclone/rclone.conf` (0600)
  and runs on the session alone: the account **password is not stored** and **no OTP seed is stored**. When Proton
  ends the session (password change, "sign out everywhere"), the nightly sync fails and the freshness check pages;
  one fresh code restores it.
- **rclone is pinned:** `/usr/local/bin/rclone` v1.75.2 (root-owned; the official arm64 build, its `SHA256SUMS`
  verified against the rclone release key `FBF7 37EC E9F8 AB18 604B D2AC 9393 5E02 FF3B 54FA` and the zip hash
  pinned). Fedora's package (1.74.3) is left installed; `/usr/local/bin` comes first in every PATH here. Why:
  rclone < 1.75.0 cannot read some Proton accounts' keys (`gopenpgp: user ID signature with wrong type`,
  rclone issue #9434). Retire the pinned copy when Fedora ships 1.75 or later.
- **Interrupted uploads leave Proton "drafts"** that block the next sync of that file ("a draft exist", 5 files
  on the 2026-10-12 seed after the ramp's phase changes). Every sync passes `--protondrive-replace-existing-draft`
  (a global flag, harmless for other targets). The seed finished with it at 2.5 MB/s.
- **Known rclone bug:** listing the Drive **root** fails (`iso8601: Cannot parse ""`, an item with an empty
  timestamp). Nothing in the backup lists the root; checks use `proton:ctdi-backup`.
- **Hidden upload placeholders** (2026-10-13): after an interrupted upload Proton can keep an invisible
  placeholder that blocks a file's name even with the draft flag. Workaround used once: move the folder aside
  server-side (`moveto data/0 data/0.stale-<date>`), recreate it, move the good files back server-side. The
  aside folder `data/0.stale-20261013` was gone by 2026-10-13 ~17:30Z; remote and local `data/0` matched (53/53).
- **Seed:** the first 19 GB went up on 2026-10-12 through the measured ramp (`scripts/backup/uplink_ramp.py`,
  ledger `~/.local/state/ctdc-backup/uplink-ramp.jsonl`): 1 MB/s ok (feeds worst 34 s), 2 MB/s ok (2.31 delivered,
  35 s), 4 MB/s link-bound (2.22 delivered, 31 s); the finish ran at about 3.2 MB/s with the feeds under 30 s.

## Sync caps, turn-taking, dry run (2026-10-12)

| Setting (`dispatch.env`) | Default | Meaning |
|---|---|---|
| `BACKUP_BWLIMIT_SOFT_MB` | 2.5 | rclone's own `--bwlimit` (it overshoots by about 10%) |
| `BACKUP_BWLIMIT_HARD_MB` | 3 | `rate_guard.py`: total upload on the interface, 30 s average. Over it, rclone's limit is lowered live (80%, then 50% of soft) over a private rc socket; still over after that, the sync is stopped and fails loudly. Restored to soft when calm. |
| `BACKUP_IFACE` | wld0 | interface the hard cap watches |

- Each sync's ledger entry carries the guard's summary (highest 30 s average, actions).
- **Stall stop and one retry (2026-10-14).** If rclone's own transferred-bytes counter has not grown for 20 minutes (`rate_guard.py --stall-min`), the guard asks rclone to quit and reports `stall: true` (exit 4); the backup retries the sync once from scratch, which also covers Proton's transient "Upload file empty" (2003). On 2026-10-14 a hung sync (four uploads at 6%, no bytes moving) held the heavy-job turn from 04:40Z until it was stopped by hand at ~06:40Z.
- **One heavy job at a time** (`scripts/lib/heavy_lock.sh`): the nightly backup, the weekly restore test, the
  full-load rollout and the stack refresh take one shared turn; the second waits (up to 2 h) and logs whom it waits
  on. Root cause: the 2026-10-12 03:40Z backup started six minutes into a relay's full load (load 20.5 on 4 cores).
- **The rollout's hold-before-restart gate** (`scripts/lib/load_gate.sh`, 2026-10-13): it holds while
  `load1 >= max(LOAD_GATE=15, LOAD_GATE_REL=1.25 x the load when the rollout started)`, for at most
  `LOAD_HOLD_MAX=900` s, then proceeds only below `LOAD_CEIL=30` (a real overload keeps holding; a stack-refresh
  deadline still wins). It was a fixed `load1 >= 15` with no limit: on 2026-10-13 the box's resting load (17-20)
  held an unchanged acarshub image 45 min. Tests: `tests/scripts/test_load_gate.py`.
- The nightly sync also waits for a running seed upload (`corporatetraveldc-proton-seed`), up to 90 min.
- **Flags and exit behaviour** (`ctdi-backup.sh [--no-sync] [--dry-run]`): `--no-sync` writes the archive and ledger entries but skips the off-box sync. Borg exit code 1 (a warning, such as an unreadable file) no longer aborts the run: the archive is kept, the warning is logged, and the ledger's `backup` entry carries `borg_warnings: true`. Exit 2 or higher fails the unit. Excluded from every archive: `/var/lib/corporatetraveldc/models` (pinned by digest, re-pullable), `__pycache__`, `*.tmp`, and the root-only `/etc/corporatetraveldc/cf-honeypot.token` (the operator account cannot read it; it is re-issuable). The heavy-job wait is up to 2 h, after which the backup is skipped and the unit fails.
- `--dry-run` now exits before any dump, ledger write or LADD rotation (it used to run both dumps and append a
  cui-stamp; the 2026-10-12 22:22Z cui-stamp in the ledger is that dry run, not a backup).
- The dumps read `</dev/null`: `podman exec -i` used to read the caller's stdin and swallowed the rest of a relay.
