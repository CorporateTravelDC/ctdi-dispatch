-- 0001_office_roles.sql -- the OFFICE instance's roles and databases
-- (2026-10-20, office skeletons; template, disabled until after the feature
-- freeze). See pg_schema_office/__init__.py.
--
-- Run by scripts/office-skeleton-ctl.py init-db: psql -X -v ON_ERROR_STOP=1
-- -d office -f - (stdin), as office_owner inside corporatetraveldc-pgsql-office.
-- Idempotent. No password here: roles are created LOGIN with none
-- (set-passwords applies SCRAM verifiers afterwards, on stdin).

SET log_statement = 'none';

-- One role per service. Re-audited on every run: no superuser, no CREATEDB,
-- no CREATEROLE, no replication, no RLS bypass, a connection cap that sums
-- (with the reserved slots) under max_connections = 40.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'firefly') THEN
        CREATE ROLE firefly LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'paperless') THEN
        CREATE ROLE paperless LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vikunja') THEN
        CREATE ROLE vikunja LOGIN;
    END IF;
END
$$;

ALTER ROLE firefly   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10;
ALTER ROLE paperless LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 12;
ALTER ROLE vikunja   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8;

-- One database per role, owned by it (PostgreSQL 15+: the owner may create in
-- its own `public` schema; nobody else may). CREATE DATABASE cannot run inside
-- a DO block, so psql's \gexec runs it only when the database is absent.
SELECT 'CREATE DATABASE firefly OWNER firefly ENCODING ''UTF8'' TEMPLATE template0'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'firefly') \gexec
SELECT 'CREATE DATABASE paperless OWNER paperless ENCODING ''UTF8'' TEMPLATE template0'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'paperless') \gexec
SELECT 'CREATE DATABASE vikunja OWNER vikunja ENCODING ''UTF8'' TEMPLATE template0'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'vikunja') \gexec

ALTER DATABASE firefly   OWNER TO firefly;
ALTER DATABASE paperless OWNER TO paperless;
ALTER DATABASE vikunja   OWNER TO vikunja;

-- Nobody connects anywhere by default; each role gets its own database only.
REVOKE ALL ON DATABASE firefly   FROM PUBLIC;
REVOKE ALL ON DATABASE paperless FROM PUBLIC;
REVOKE ALL ON DATABASE vikunja   FROM PUBLIC;
REVOKE ALL ON DATABASE office    FROM PUBLIC;
REVOKE ALL ON DATABASE postgres  FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE firefly   TO firefly;
GRANT CONNECT, TEMPORARY ON DATABASE paperless TO paperless;
GRANT CONNECT, TEMPORARY ON DATABASE vikunja   TO vikunja;
