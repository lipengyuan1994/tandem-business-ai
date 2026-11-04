#!/usr/bin/env bash
# Restore one database from a restic snapshot into a NEW database, verify it, and optionally swap it into place.
# Runs inside the smb-backup container as the PostgreSQL superuser (restore needs CREATE DATABASE and extensions).
#   restore-db.sh --db smb --snapshot latest --target smb_restore [--compare-with smb] [--swap]
# Never overwrites an existing database in place: --swap renames the live database aside first.
# Exit codes: 0 ok, 10 restored but row counts differ from --compare-with, anything else = failure.
# Environment: PGHOST, POSTGRES_PASSWORD, RESTIC_* as for backup.sh, BACKUP_STATE_DIR.
set -euo pipefail

STATE_DIR="${BACKUP_STATE_DIR:-/state}"
PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-postgres}"
PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required for restore}"
export PGHOST PGUSER PGPASSWORD

db="" snapshot=latest target="" compare_with="" swap=false
while [ $# -gt 0 ]; do
  case "$1" in
    --db) db="${2:?}"; shift ;;
    --snapshot) snapshot="${2:?}"; shift ;;
    --target) target="${2:?}"; shift ;;
    --compare-with) compare_with="${2:?}"; shift ;;
    --swap) swap=true ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
case "$db" in smb) owner=smb_owner ;; keycloak) owner=keycloak ;; *) echo "--db must be smb or keycloak" >&2; exit 64 ;; esac
[ -n "$target" ] || target="${db}_restore"
[[ "$target" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "invalid target name" >&2; exit 64; }
case "$target" in smb | keycloak | postgres | template0 | template1)
  echo "target must not be a live or system database ($target)" >&2
  exit 64
  ;;
esac

log() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
psql_q() { psql --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 "$@"; }

# Exact row count per table in the public schema, sorted, as "table|count" lines.
row_counts() {
  psql_q --dbname "$1" --command "
    select table_name || '|' || (xpath('/row/c/text()',
      query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text
    from information_schema.tables
    where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name"
}

started=$(date +%s)
work="${STATE_DIR:?}/work/restore-$$"
mkdir -p "$work"
trap 'rm -rf "${work:?}"' EXIT

log "fetching $db.dump from snapshot $snapshot"
restic restore "${snapshot}:$STATE_DIR/work/dumps" --target "$work" --include "$db.dump" --quiet >&2
[ -s "$work/$db.dump" ] || { log "snapshot has no $db.dump"; exit 1; }

log "creating database $target"
psql_q --dbname postgres --command "drop database if exists \"$target\" with (force)"
psql_q --dbname postgres --command "create database \"$target\" owner $owner"
if [ "$db" = smb ]; then
  # Same database-level privileges as deploy/compose/postgres-init.sh (a custom-format dump does not carry them).
  psql_q --dbname postgres --command "revoke all on database \"$target\" from public; grant connect, temporary on database \"$target\" to smb_app"
fi
# As superuser, pg_restore creates the extensions and restores object ownership and grants from the dump.
pg_restore --dbname "$target" --exit-on-error "$work/$db.dump"
restored_at=$(date +%s)

version=$(psql_q --dbname "$target" --command "select version_num from alembic_version" 2>/dev/null || true)
if [ "$db" = smb ] && [ -z "$version" ]; then log "restored smb database has no alembic_version"; exit 1; fi
counts=$(row_counts "$target")
tables=$(printf '%s\n' "$counts" | grep -c . || true)
total=$(printf '%s\n' "$counts" | awk -F'|' '{s+=$2} END {print s+0}')
log "restored: $tables tables, $total rows, migration ${version:-n/a}"

compared=null
if [ -n "$compare_with" ]; then
  if diff <(row_counts "$compare_with") <(printf '%s\n' "$counts") >/dev/null; then
    compared=true
    log "row counts identical to $compare_with"
  else
    compared=false
    log "ROW COUNTS DIFFER from $compare_with:"
    diff <(row_counts "$compare_with") <(printf '%s\n' "$counts") >&2 || true
  fi
fi

swapped=false
if # Exit 10 = restored fine but the row counts differ from --compare-with (callers decide whether that matters).
if [ "$compared" = false ]; then exit 10; fi && $swap; then
  suffix=$(date -u +%Y%m%d%H%M%S)
  log "swapping: $db -> ${db}_pre_restore_$suffix, $target -> $db (api, worker and keycloak must be stopped)"
  psql_q --dbname postgres --command "select pg_terminate_backend(pid) from pg_stat_activity where datname in ('$db', '$target') and pid <> pg_backend_pid()" >/dev/null
  if [ "$(psql_q --dbname postgres --command "select 1 from pg_database where datname = '$db'")" = 1 ]; then
    psql_q --dbname postgres --command "alter database \"$db\" rename to \"${db}_pre_restore_$suffix\""
  fi
  psql_q --dbname postgres --command "alter database \"$target\" rename to \"$db\""
  swapped=true
fi

finished=$(date +%s)
mkdir -p "$STATE_DIR"
jq -n --arg db "$db" --arg snapshot "$snapshot" --arg target "$target" --arg version "$version" \
  --argjson tables "$tables" --argjson rows "$total" --argjson compared "$compared" --argjson swapped "$swapped" \
  --argjson restore_seconds "$((restored_at - started))" --argjson total_seconds "$((finished - started))" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{at: $at, database: $db, snapshot: $snapshot, target: $target, migration: $version, tables: $tables, rows: $rows,
    counts_identical_to_source: $compared, swapped: $swapped, restore_seconds: $restore_seconds, total_seconds: $total_seconds}' \
  >"$STATE_DIR/restore-last.json"
cat "$STATE_DIR/restore-last.json"
[ "$compared" != false ]
