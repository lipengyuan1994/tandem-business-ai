#!/bin/bash
# Creates the application roles and databases (docs/roadmap/05-data-model.md#tenancy-and-row-level-security).
# Runs once, on first start of an empty data directory. Passwords come from the environment.
set -euo pipefail
: "${SMB_OWNER_PASSWORD:?}" "${SMB_APP_PASSWORD:?}" "${KEYCLOAK_DB_PASSWORD:?}"
# Passwords reach psql through its own environment access (\getenv), not through the command line, where any
# local user could read them in the process list.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv owner_pw SMB_OWNER_PASSWORD
\getenv app_pw SMB_APP_PASSWORD
\getenv kc_pw KEYCLOAK_DB_PASSWORD
CREATE ROLE smb_owner LOGIN BYPASSRLS PASSWORD :'owner_pw';
CREATE ROLE smb_app LOGIN PASSWORD :'app_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw';
CREATE DATABASE smb OWNER smb_owner;
CREATE DATABASE keycloak OWNER keycloak;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname smb <<'SQL'
REVOKE ALL ON DATABASE smb FROM PUBLIC;
GRANT CONNECT ON DATABASE smb TO smb_app;  -- no TEMPORARY: the app needs no temporary tables (defence in depth for SECURITY DEFINER functions)
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO smb_app;
-- Extensions need superuser on plain PostgreSQL; create them here so migrations only need ownership.
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
ALTER SCHEMA public OWNER TO smb_owner;
ALTER DEFAULT PRIVILEGES FOR ROLE smb_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO smb_app;
ALTER DEFAULT PRIVILEGES FOR ROLE smb_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO smb_app;
ALTER DEFAULT PRIVILEGES FOR ROLE smb_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO smb_app;
SQL
