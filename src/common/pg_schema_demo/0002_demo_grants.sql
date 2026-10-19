-- 0002_demo_grants.sql -- grants re-audit for the `demo` application role
-- (2026-10-19, demo/production database split, relay 2: the flip). Applied by
-- scripts/pg_migrate.py --instance demo as demo_owner, BEFORE the two demo
-- containers switch to DISPATCH_ROLE=demo.
--
-- What `demo` may do after this file, and nothing else (the live sweep
-- scripts/demo-isolation-check.sh section 4 compares the instance's
-- information_schema.role_table_grants against exactly this set, and
-- tests/scripts/test_pg_schema_demo.py holds the text):
--
--   snapshots, brief_archive, meta, scrub_ledger   SELECT only (the playback
--       tables; the scrub, as demo_owner, is their only writer)
--   demo_profiles   SELECT, INSERT, UPDATE  (demo-api's /admin/demo/profiles
--       create + revoke, src/demo/profiles.py; revoke is an UPDATE active =
--       FALSE, so no DELETE)
--   chat_messages   SELECT, INSERT, DELETE  (runner-demo's chat history,
--       src/runner/main.py _chat_*; "clear" is a DELETE, nothing UPDATEs)
--
-- The two write grants are the only writes the public demo's own code makes;
-- SELECT-only there would break the demo login admin and the chat drawer.
-- They are on the demo's OWN state, never on anything the scrub promotes.
--
-- Removed relative to 0001: SELECT on schema_migrations (nothing demo-side
-- reads it) and USAGE/SELECT on every sequence (identity columns call
-- nextval internally without a sequence privilege check, so INSERT on the
-- two writable tables needs none). Tightened: no CREATE/TEMP on the
-- database or CREATE on schema public for PUBLIC; CONNECT only for demo
-- (demo_owner owns the database); role attributes restated; a connection
-- cap so a public-demo bug cannot take the 30 slots the scrub also needs.
--
-- Idempotent: REVOKE/GRANT/ALTER ROLE restate state; re-running is a no-op.
-- Names no password (the demo role's password is set by hand:
-- scripts/demo-env.py set-password, a SCRAM verifier on psql's stdin).

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM demo;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM demo;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO demo;
REVOKE ALL ON DATABASE corporatetraveldc_demo FROM PUBLIC;
GRANT CONNECT ON DATABASE corporatetraveldc_demo TO demo;

GRANT SELECT ON snapshots, brief_archive, meta, scrub_ledger TO demo;
GRANT SELECT, INSERT, UPDATE ON demo_profiles TO demo;
GRANT SELECT, INSERT, DELETE ON chat_messages TO demo;

ALTER ROLE demo NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 12;
