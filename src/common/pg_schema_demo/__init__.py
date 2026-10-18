"""Numbered Postgres DDL migrations for the DEMO instance
(corporatetraveldc-pgsql-demo, database corporatetraveldc_demo) -- the
public mirror of the dispatch platform (2026-10-16 demo/production split).

Applied by `scripts/pg_migrate.py --instance demo` (as demo_owner, host-side
over 127.0.0.1:5433), tracked in that database's own schema_migrations.
Never applied to the production instance: the runner refuses unless
DEMO_PG_USER is demo_owner, and db_backend._demo_pg_conninfo() refuses the
production database/role by name.

What lives here, and only this: the six demo tables --
  snapshots       scrubbed, promoted endpoint payloads (the sovereign shape
                  scripts/scrub-demo-source.py has written since 2026-08-14,
                  incl. `synthetic` for replay-filled days)
  brief_archive   scrubbed briefs (ops / weekly / ep-advance)
  meta            window_end / promoted_at, written only by the scrub
  demo_profiles   the public demo's password gate (same shape as production's
                  pg_schema/0057 table; one-time copy with --profiles)
  chat_messages   runner-demo's chat history (same shape as pg_schema/0056,
                  so src/runner/main.py runs unchanged under DISPATCH_ROLE=demo)
  scrub_ledger    hash-chained record of every scrub run (counts only)
No flight, watchlist, audit, auth, token or LADD table ever appears here;
tests/scripts/test_pg_schema_demo.py holds that line.

Three table NAMES coincide with production's (demo_profiles, chat_messages,
brief_archive) on purpose: the code paths that read them (src/demo/profiles.py,
src/runner/main.py, src/demo/demo_api.py) are unchanged by the split and are
pointed at this instance by DISPATCH_ROLE=demo / DEMO_SOURCE=postgres alone.
"""
