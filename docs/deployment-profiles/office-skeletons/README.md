# Office skeletons: CFO, Executive Assistant, Chief of Staff

> **Template, disabled until after the feature freeze.** Staged 2026-10-20 (UTC) on the operator's decisions of
> 2026-10-20. Nothing here is started at boot, by a timer, by the rollout or by `stack-boot-ctl.sh`. The relay
> fetches the images, creates the secrets files and the databases, and installs the Nextcloud apps **disabled**.
> Turning any of it on is a separate operator decision after the freeze.

## What the three profiles are

The operator split the old combined `executive-assistant-chief-of-staff` profile on 2026-10-20: an Executive
Assistant and a Chief of Staff are separate executive-suite positions. There are now three profiles, each its own
JSON in the pack format (`../profiles/`):

| Profile | Page | Modules beyond `base` | Sources | Services (all disabled) |
|---|---|---|---|---|
| `cfo` | [CFO.md](CFO.md) | budgeting, cash forecast, close checklist, board reporting, variance analysis, controls register | accounting ledger, bank statement import, ERP | Firefly III (ledger), Paperless-ngx (shared, receipts) |
| `executive-assistant` | [EXECUTIVE-ASSISTANT.md](EXECUTIVE-ASSISTANT.md) | calendar coordination, meeting preparation, travel coordination, follow-up, document intake | calendar, contacts, email, document repository | Nextcloud Calendar, Contacts, Tasks; Paperless-ngx (shared) |
| `chief-of-staff` | [CHIEF-OF-STAFF.md](CHIEF-OF-STAFF.md) | briefings, decision register, initiative tracking, OKR tracking, operating cadence, follow-up | document repository, task board, calendar | Nextcloud Deck, Collectives, Tables; Vikunja (optional) |

How they work together in the demo is in
[`../compositions/EXEC-OFFICE-CROSSOVER.md`](../compositions/EXEC-OFFICE-CROSSOVER.md). In short, the EA books
the travel, dispatch watches the flight, the CFO sees the cost line and the CoS sees the initiative.

Each profile gets its own glossary slice: `scripts/render-glossary.py --profile cfo` (or `executive-assistant`,
`chief-of-staff`). The 2026-10-20 glossary added the `finance`, `scheduling`, `governance` and `office` scopes and
mapped every module and source above to them (`docs/glossary.yaml` `profile_scopes`).

## Services

The research behind this choice (candidates, licences, PostgreSQL support, arm64 images, RAM) was done on
2026-10-19. The rule was the fewest new containers, PostgreSQL only and no SQLite anywhere. Four containers
plus one optional; everything else is a Nextcloud app on the existing instance.

| Unit (quadlet under `.config/containers/systemd/office-skeletons/`) | Image (pinned, arm64 checked 2026-10-20) | Memory cap | Network | Publish |
|---|---|---|---|---|
| `corporatetraveldc-pgsql-office` | `docker.io/library/postgres:16-alpine` (the production line) | 384m | office-db (internal) | none |
| `corporatetraveldc-valkey-office` | `docker.io/valkey/valkey:8.1.10-alpine` | 64m | office-db (internal) | none |
| `corporatetraveldc-firefly` | `docker.io/fireflyiii/core:version-6.7.9` | 384m | office-db + office-web | tailnet :8101 |
| `corporatetraveldc-paperless` | `docker.io/paperlessngx/paperless-ngx:3.3.3` | 1024m | office-db + office-web | tailnet :8102 |
| `corporatetraveldc-vikunja` (optional) | `docker.io/vikunja/vikunja:2.7.0` | 192m | office-db + office-web | tailnet :8103 |

Nextcloud apps (on the existing `nextcloud-app`, its own database): `calendar`, `contacts`, `tasks`, `deck`,
`collectives`, `tables`. They are installed with `occ app:install --keep-disabled` and stay off until the operator
runs `occ app:enable` after the freeze.

### Rules every skeleton unit follows (held by `tests/scripts/test_office_skeletons.py`)

- **No `[Install]` section at all.** The units are not in `scripts/stack-boot-ctl.sh`, `scripts/serialized-rollout.sh`,
  `scripts/stack-refresh.sh`, `scripts/restart-stack.sh`, `scripts/lockdown.sh` or `scripts/watchdog.sh`, and no
  timer names them. They are installed only in the live `office-skeletons/` subdirectory. Quadlet reads
  subdirectories, but the drift check and `stack-refresh.sh` read only the top level, so a weekly image refresh
  never touches these pins.
- **Pinned tags.** No `:latest` and no `AutoUpdate=`. A dormant template never changes underneath.
- **Own secrets file each**, `/etc/corporatetraveldc/<svc>-secrets.env` (examples in `config/*-secrets.env.example`),
  never `dispatch.env` or `dispatch-secrets.env` (also `tests/scripts/test_third_party_env_scope.py`).
- **Non-secret settings are `Environment=` lines in the tracked quadlet.** Podman applies `--env` after
  `--env-file`, so a secrets file cannot override them, and the check tool refuses an engine or connection name in
  a secrets file.
- **No SQLite.** Paperless-ngx and Vikunja default to SQLite and Firefly III to MySQL, so the quadlets pin the
  engine: `PAPERLESS_DBENGINE=postgresql`, `VIKUNJA_DATABASE_TYPE=postgres` and `DB_CONNECTION=pgsql`. Valkey
  persists nothing.
- `Slice=production.slice`, `NoNewPrivileges=true`, a memory cap and a CPU quota on every unit; Paperless runs at
  `Nice=10` (OCR is the one real CPU risk).
- **Networks.** `office-db.network` is `Internal=true`: the database and the broker have no egress and no
  publish. The apps also join `office-web.network` for their tailnet-only port. No dispatch unit joins either
  network.

### Database: a third instance

`corporatetraveldc-pgsql-office` is modelled on the demo instance: its own data volume, socket volume, config
(`config/postgresql-office.conf`) and hba (`config/pg_hba-office.conf`). Each app role may reach only its own
database, with scram. The platform roles are rejected by name, and so is everything else. Production
`config/pg_hba.conf` rejects the office roles and databases by name. The init SQL is
`src/common/pg_schema_office/0001_office_roles.sql`, one role and one database per service: `firefly`,
`paperless` and `vikunja`. The apps migrate their own schemas. Why a separate instance and not more databases on
production: [docs/POSTGRES_MIGRATION.md](../../POSTGRES_MIGRATION.md) §3.7.

Backup: the nightly backup does **not** dump this instance yet. While the units stay disabled it holds empty
databases only. Activation must add it to the Borg dump list, with a restore test, because the CFO profile
sets `security.backup.restore_test_required` ([docs/BACKUP.md](../../BACKUP.md)).

## Tooling

`scripts/office-skeleton-ctl.py`. No subcommand prints a value or puts one on a command line.

- `check` reports names, modes and states only.
- `fill` generates every placeholder in place and derives `PAPERLESS_REDIS` from the broker password.
- `init-db` sends the init SQL to psql on stdin.
- `set-passwords` applies a SCRAM verifier per role from that role's own secrets file and checks the login.
- `status` shows the container, roles and databases, by name.

## Relay steps (operator)

The relay installs the files and pulls the images. It runs `sudo install` for the five secrets files, `fill`,
starts `corporatetraveldc-pgsql-office` once for `init-db` and `set-passwords`, then stops it. It runs
`occ app:install --keep-disabled` for the six Nextcloud apps. It never enables a unit and never starts an app.
The batch's relay notes hold the exact block.

## Activation, after the freeze (not now)

1. Operator decision to lift the freeze for these units.
2. Add `corporatetraveldc-pgsql-office` to the Borg dump list and run a restore test.
3. Add the units to `stack-boot-ctl.sh` ORDER, after `pgsql-office`; that is the only place that starts long-running units.
4. Create the first human logins: Firefly registration, `manage.py createsuperuser` for Paperless, and a Vikunja user created from the command line, since self-registration is off. Single sign-on through Nextcloud as OIDC provider is not deployed.
5. `occ app:enable` for the Nextcloud apps the demo needs.
6. Seed synthetic data only (the crossover story).
