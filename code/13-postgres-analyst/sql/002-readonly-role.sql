-- The roles the server logs in as. This file, not security.py, is the real
-- security control.
--
-- The Python validator in src/pg_analyst/security.py is a parser. It is useful,
-- it catches a lot, and it is defeated by the first thing it did not anticipate.
-- What holds when it is wrong is this: a login role that was never granted the
-- ability to do the thing. A grant is not a heuristic.
--
-- Run automatically by docker-compose after 001-seed.sql. By hand, as a
-- superuser:
--   psql -U postgres -d analytics -f sql/002-readonly-role.sql
--
-- Change both passwords before this is anywhere but a laptop.

-- ---------------------------------------------------------------------------
-- mcp_readonly: the role behind DATABASE_URL. Post 13.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_readonly') THEN
        CREATE ROLE mcp_readonly WITH LOGIN PASSWORD 'readonly_password';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE analytics TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;

-- Tables created later are readable too, without anyone remembering to come
-- back and re-run the grant above.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_readonly;

-- No CREATE on the schema. Without this revoke, PUBLIC holds CREATE on `public`
-- on PostgreSQL 14 and earlier, and "read-only" turns out to include "can make
-- new tables".
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM mcp_readonly;

-- Every transaction this role opens is read-only, whatever it thinks it is
-- doing. This is what turns an INSERT that slipped past the parser into
-- "ERROR: cannot execute INSERT in a read-only transaction".
ALTER ROLE mcp_readonly SET default_transaction_read_only = ON;

-- A statement that runs for more than 30 seconds is killed. This is the answer
-- to pg_sleep, to a cartesian join, and to every other query whose cost the
-- parser cannot see. Note that it is enforced by the server, so it survives a
-- client that has gone away.
ALTER ROLE mcp_readonly SET statement_timeout = '30s';

-- A connection idle inside an open transaction for a minute is terminated,
-- so a crashed server cannot sit on locks indefinitely.
ALTER ROLE mcp_readonly SET idle_in_transaction_session_timeout = '60s';

-- ---------------------------------------------------------------------------
-- mcp_writer: the role behind DATABASE_WRITE_URL. Post 14.
--
-- A second login, deliberately. The read path holds a connection that cannot
-- write; the write path holds a connection that cannot alter the schema. A bug
-- in one cannot borrow the privileges of the other, because they are different
-- sessions authenticated as different roles.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_writer') THEN
        CREATE ROLE mcp_writer WITH LOGIN PASSWORD 'writer_password';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE analytics TO mcp_writer;
GRANT USAGE ON SCHEMA public TO mcp_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mcp_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mcp_writer;

-- The server creates mcp_audit_log at startup, so this role needs CREATE on the
-- schema. That is the one privilege here worth being uncomfortable about: it is
-- also what a compromised write path would use to make itself a table. If you
-- would rather not grant it, create mcp_audit_log by hand as a superuser and
-- revoke this.
GRANT CREATE ON SCHEMA public TO mcp_writer;

ALTER ROLE mcp_writer SET statement_timeout = '30s';
ALTER ROLE mcp_writer SET idle_in_transaction_session_timeout = '60s';

-- The audit table is created by mcp_writer, so mcp_readonly's SELECT on it
-- comes from mcp_writer's default privileges rather than from the grant above,
-- which only covered tables that already existed.
ALTER DEFAULT PRIVILEGES FOR ROLE mcp_writer IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_readonly;

-- What mcp_writer deliberately does NOT hold:
--
--   * TRUNCATE. The server refuses it in the parser as well, and this is the
--     backstop.
--   * Any privilege on other schemas, or on another database.
--   * Ownership of the seeded tables, so it cannot DROP or ALTER them.
--   * Superuser, so pg_read_file and friends fail regardless of the blocklist.
--
-- One more that would be worth adding on a real system: the audit table should
-- be append-only for this role. Granting INSERT but not UPDATE or DELETE on
-- mcp_audit_log specifically, after it exists, means a compromised write path
-- can add to the record but cannot edit it.
