#!/bin/bash
# Isolated databases in the existing, throwaway db-test service.
set -euo pipefail

if [[ $# -ne 2 ]] || [[ $1 != new && $1 != drop ]] || [[ ! $2 =~ ^[a-z0-9_]{1,20}$ ]]; then
    echo "Usage: $0 {new|drop} <name> (name: 1–20 lowercase letters, digits or underscores)" >&2
    exit 2
fi

action=$1
database="smb_$2"
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
compose=(docker compose -p smb -f "$project_root/deploy/compose/compose.dev.yml" --env-file "$project_root/.env")

psql_test() {
    "${compose[@]}" exec -T db-test psql -X -U postgres -v ON_ERROR_STOP=1 "$@"
}

if [[ $action == drop ]]; then
    # The validated suffix and mandatory prefix prevent targeting the default smb database.
    psql_test --dbname postgres -v database="$database" <<'SQL' >/dev/null
DROP DATABASE IF EXISTS :"database";
SQL
    exit 0
fi

exists=$(psql_test --dbname postgres -At -v database="$database" <<'SQL'
SELECT 1 FROM pg_database WHERE datname = :'database';
SQL
)
if [[ $exists != 1 ]]; then
    psql_test --dbname postgres -v database="$database" <<'SQL' >/dev/null
CREATE DATABASE :"database" OWNER smb_owner;
SQL
    psql_test --dbname "$database" -v database="$database" <<'SQL' >/dev/null
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO smb_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO smb_app;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
ALTER SCHEMA public OWNER TO smb_owner;
ALTER DEFAULT PRIVILEGES FOR ROLE smb_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO smb_app;
ALTER DEFAULT PRIVILEGES FOR ROLE smb_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO smb_app;
ALTER DEFAULT PRIVILEGES FOR ROLE smb_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO smb_app;
SQL
fi

# These are the synthetic local-test defaults used by the pytest plugin, never production credentials.
printf "export TEST_DATABASE_URL='postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/%s'\n" "$database"
printf "export TEST_DATABASE_MIGRATION_URL='postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/%s'\n" "$database"
