#!/usr/bin/env bash
# `make backup-test`: seed a throwaway database, back it up with the real backup image into a local restic repository,
# restore into a scratch database, and require identical per-table row counts. Records durations.
#   backup-test.sh [VERSION]      (images smb/smb-api, smb/smb-backup must exist: `make images`)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
export VERSION="${1:-${VERSION:-0.1.0}}"
export REGISTRY="${REGISTRY:-smb}"
compose=(docker compose -f "$root/deploy/compose/compose.backup-test.yml")
result_dir="$root/backups"
mkdir -p "$result_dir"

cleanup() { "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

step() { printf '\n== %s\n' "$*"; }
psql_pg() { "${compose[@]}" exec -T -e PGPASSWORD=bt_postgres postgres psql -U postgres -v ON_ERROR_STOP=1 -qAt "$@"; }

step "database, migrations, demo data"
"${compose[@]}" up -d --wait postgres
"${compose[@]}" run --rm -T migrate
"${compose[@]}" run --rm -T seed

# A marker table in the keycloak database so its round trip is not empty.
psql_pg -d keycloak -c "create table backup_test_marker as select g as n from generate_series(1, 25) g; alter table backup_test_marker owner to keycloak"

step "backup"
t0=$(date +%s)
"${compose[@]}" run --rm -T backup backup.sh --kind test
backup_seconds=$(($(date +%s) - t0))

step "restore into scratch databases and compare row counts with the source"
smb_json=$("${compose[@]}" run --rm -T backup restore-db.sh --db smb --snapshot latest --target smb_restore --compare-with smb 2>/dev/null)
kc_json=$("${compose[@]}" run --rm -T backup restore-db.sh --db keycloak --snapshot latest --target keycloak_restore --compare-with keycloak 2>/dev/null)

step "negative control: the comparison must fail when the source changed after the backup"
psql_pg -d keycloak -c "insert into backup_test_marker values (999)"
if "${compose[@]}" run --rm -T backup restore-db.sh --db keycloak --snapshot latest --target keycloak_restore --compare-with keycloak >/dev/null 2>&1; then
  echo "FAIL: restore-db.sh did not notice a row-count difference" >&2
  exit 1
fi
echo "row-count difference detected, as it should be"

step "negative control: a failing dump must not be recorded as a success (review finding)"
before=$("${compose[@]}" run --rm -T backup cat /state/last-success)
if "${compose[@]}" run --rm -T -e SMB_OWNER_PASSWORD=wrong-password backup backup.sh --kind failing >/dev/null 2>&1; then
  echo "FAIL: backup.sh exited 0 although pg_dump could not log in" >&2
  exit 1
fi
after=$("${compose[@]}" run --rm -T backup cat /state/last-success)
status=$("${compose[@]}" run --rm -T backup backup.sh --status | jq -r .result)
[ "$before" = "$after" ] && [ "$status" = error ] || { echo "FAIL: failed backup was recorded as '$status' / last-success moved" >&2; exit 1; }
echo "failed backup reported as error and last-success unchanged"

step "negative control: restore refuses to target the other live database, and unreadable snapshots are failures"
rc=0
"${compose[@]}" run --rm -T backup restore-db.sh --db smb --target keycloak >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 64 ] || { echo "FAIL: restore-db.sh --target keycloak exited $rc instead of 64" >&2; exit 1; }
[ "$(psql_pg -d keycloak -c 'select count(*) from backup_test_marker')" -gt 0 ] || { echo "FAIL: keycloak database was touched" >&2; exit 1; }
rc=0
"${compose[@]}" run --rm -T backup restore-db.sh --db smb --snapshot deadbeef --target smb_never >/dev/null 2>&1 || rc=$?
{ [ "$rc" -ne 0 ] && [ "$rc" -ne 10 ]; } || { echo "FAIL: an unreadable snapshot exited $rc (success or 'counts differ')" >&2; exit 1; }
echo "guards hold (exit codes 64 and $rc)"

step "restored database works for the application role (grants, row-level security)"
business_id=$(psql_pg -d smb -c "select id from businesses order by created_at limit 1")
customers=$(psql_pg -U smb_app -d smb_restore -c "select 1" >/dev/null 2>&1 \
  && "${compose[@]}" exec -T -e PGPASSWORD=bt_app postgres psql -h localhost -U smb_app -d smb_restore -qAt \
       -c "select set_config('app.business_id', '$business_id', false)" -c "select count(*) from customers" | tail -1)
[ "${customers:-0}" -gt 0 ] || { echo "FAIL: smb_app cannot read tenant rows in the restored database" >&2; exit 1; }
echo "smb_app reads $customers customers of the business through row-level security"

jq -n --argjson smb "$smb_json" --argjson keycloak "$kc_json" --argjson backup_seconds "$backup_seconds" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg version "$VERSION" \
  '{at: $at, version: $version, backup_seconds: $backup_seconds, smb: $smb, keycloak: $keycloak,
    result: (if $smb.counts_identical_to_source and $keycloak.counts_identical_to_source then "pass" else "FAIL" end)}' \
  | tee "$result_dir/backup-test-last.json"
jq -e '.result == "pass"' "$result_dir/backup-test-last.json" >/dev/null
printf '\nbackup-test: PASS (backup %s s, smb restore %s s)\n' "$backup_seconds" "$(jq -r .smb.restore_seconds "$result_dir/backup-test-last.json")"
