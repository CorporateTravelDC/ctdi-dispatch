"""Init SQL for the OFFICE instance (corporatetraveldc-pgsql-office) -- the
office skeletons behind the CFO, Executive Assistant and Chief of Staff
profiles (operator 2026-10-20; template, disabled until after the feature
freeze). docs/deployment-profiles/office-skeletons/.

Unlike pg_schema/ (production) and pg_schema_demo/ (demo), nothing here
creates a table: the three applications (Firefly III, Paperless-ngx, Vikunja)
own and migrate their own schemas. This directory only creates, per service,
one LOGIN role and one database owned by it, and closes everything else:

  firefly    -> database firefly    (Firefly III, CFO ledger)
  paperless  -> database paperless  (Paperless-ngx, EA + CFO documents)
  vikunja    -> database vikunja    (Vikunja, optional CoS initiatives)

Applied by `scripts/office-skeleton-ctl.py init-db`: the file goes to psql on
STDIN inside the container, as office_owner (the instance's bootstrap
superuser). Idempotent (roles checked in pg_roles, databases created through
psql's \\gexec only when absent), so a relay rerun is harmless. No password
appears here: each role is created with none, so under
config/pg_hba-office.conf's scram rules it cannot log in until
`office-skeleton-ctl.py set-passwords` applies a SCRAM verifier computed from
that service's own secrets file. Never applied to the production or demo
instance (the tool targets the office container by name only, and both of
those hba files reject these roles by name).
"""
