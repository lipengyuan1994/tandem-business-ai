#!/usr/bin/env bash
# Restore the databases from a backup snapshot (runbook: docs/runbooks/backup-restore.md). Run on the VM in /opt/smb.
#   restore.sh --drill [SNAPSHOT]          restore into scratch databases, compare with the live ones, change nothing
#   restore.sh SNAPSHOT --yes              REAL restore: stop api/worker/keycloak, restore into new databases, swap them
#                                          in (the old databases are kept as *_pre_restore_<time>), start, smoke test
# SNAPSHOT is a restic snapshot id or "latest". Environment: COMPOSE_DIR (default /opt/smb or this directory).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dir="${COMPOSE_DIR:-}"
if [ -z "$dir" ]; then if [ -f "$here/compose.yml" ]; then dir="$here"; else dir=/opt/smb; fi; fi
cd "$dir"
compose_file=compose.yml
[ -f "$compose_file" ] || compose_file=compose.prod.yml
compose=(docker compose -f "$compose_file")

drill=false yes=false snapshot=""
while [ $# -gt 0 ]; do
  case "$1" in
    --drill) drill=true ;;
    --yes) yes=true ;;
    -*) echo "unknown option: $1" >&2; exit 64 ;;
    *) snapshot="$1" ;;
  esac
  shift
done
snapshot="${snapshot:-latest}"
say() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if $drill; then
  say "DRILL: restoring snapshot $snapshot into scratch databases (live data is not touched)"
  started=$(date +%s)
  differ=false
  details_args=()
  for db in smb keycloak; do
    rc=0
    "${compose[@]}" exec -T backup restore-db.sh --db "$db" --snapshot "$snapshot" --target "${db}_drill" --compare-with "$db" ${details_args[@]+"${details_args[@]}"} >/dev/null || rc=$?
    case "$rc" in
      0) ;;
      10) differ=true ;;
      *) say "DRILL FAILED: restoring $db exited with status $rc (snapshot unreadable, repository unreachable or restore error)"; exit 1 ;;
    esac
    details_args=(--append-details)
  done
  say "drill finished in $(($(date +%s) - started)) s; details for both databases: docker compose exec backup cat /state/restore-last.json"
  say "scratch databases smb_drill and keycloak_drill remain; drop them with: docker compose exec postgres psql -U postgres -c 'drop database smb_drill'"
  if $differ; then say "row counts differ from the live databases: per-table differences were printed above on stderr; expected if data changed since the snapshot was taken"; fi
  exit 0
fi

$yes || { echo "A real restore replaces the live databases. Re-run with --yes to confirm (or use --drill to rehearse)." >&2; exit 1; }
# Pin "latest" to a snapshot id BEFORE the safety backup, which would otherwise become the newest snapshot.
if [ "$snapshot" = latest ]; then
  snapshot=$("${compose[@]}" exec -T backup restic snapshots --host smb --latest 1 --json | jq -er '.[0].short_id') \
    || { say "cannot list snapshots (repository unreachable?)"; exit 1; }
  say "latest snapshot is $snapshot"
fi
say "safety backup of the current state"
"${compose[@]}" exec -T backup backup.sh --kind pre-restore || say "WARNING: safety backup failed; continuing because you asked to restore"

say "stopping api, worker, keycloak and caddy"
"${compose[@]}" stop caddy api worker keycloak

details_args=()
for db in smb keycloak; do
  say "restoring $db from $snapshot"
  # --no-deps: do not restart keycloak (a dependency of backup) while the databases are being swapped.
  "${compose[@]}" run --rm -T --no-deps backup restore-db.sh --db "$db" --snapshot "$snapshot" --target "${db}_restore" --swap ${details_args[@]+"${details_args[@]}"} >/dev/null
  details_args=(--append-details)
done

# Realm import skips an existing realm. Check restored client secrets against the current app environment before
# exposing the stack. Only SHA-256 digests enter SQL; passwords never enter arguments, query output or error logs.
say "checking restored Keycloak client secrets against the current environment"
matching_clients=$("${compose[@]}" run --rm -T --no-deps --entrypoint python api -c '
import hashlib, os
conditions = []
for client, key in (("smb-app", "OIDC_CLIENT_SECRET"), ("smb-admin", "KEYCLOAK_ADMIN_CLIENT_SECRET")):
    digest = hashlib.sha256(os.environ[key].encode()).hexdigest()
    conditions.append("(c.client_id = '\''%s'\'' and encode(sha256(convert_to(c.secret, '\''UTF8'\'')), '\''hex'\'') = '\''%s'\'')" % (client, digest))
print("select count(*) from client c join realm r on c.realm_id = r.id where r.name = '\''smb'\'' and (" + " or ".join(conditions) + ");")
' | "${compose[@]}" exec -T postgres psql --no-psqlrc -U postgres -d keycloak -At --set ON_ERROR_STOP=1) \
  || { say "cannot verify restored Keycloak secrets; stack remains stopped; follow docs/runbooks/backup-restore.md"; exit 1; }
if [ "$matching_clients" != 2 ]; then
  say "restored Keycloak client secrets differ from .env; stack remains stopped. Reconcile BOTH clients using docs/runbooks/backup-restore.md before starting and testing the stack."
  exit 1
fi

# A snapshot may predate migrations of the installed version: bring the schema forward before the app starts.
say "applying migrations of the installed version to the restored database"
"${compose[@]}" run --rm -T --no-deps migrate
say "starting the stack"
"${compose[@]}" up -d
"$here/wait-backup-healthy.sh" "${compose[@]}"
public_url=$(grep '^PUBLIC_URL=' .env | head -1 | cut -d= -f2)
smoke="$here/smoke.sh"
[ -x "$smoke" ] || smoke="$dir/smoke.sh"
for _ in $(seq 1 60); do
  if "$smoke" "$public_url" --compose-dir "$dir" >/dev/null 2>&1; then
    say "restore complete; smoke test passed. Previous databases were kept as *_pre_restore_<time>."
    exit 0
  fi
  sleep 5
done
say "smoke test did not pass after 5 minutes: run smoke.sh by hand and follow docs/runbooks/incident.md"
exit 1
