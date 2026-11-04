#!/usr/bin/env bash
# Back up the smb and keycloak databases: pg_dump -Fc -> restic (encrypted, deduplicated) -> repository.
# Runs inside the smb-backup container (scheduled by backup.crontab) and by hand:
#   docker compose exec backup backup.sh --kind manual
# Modes: (default) backup | --maintenance (prune + integrity check) | --startup (backup only if stale)
#        | --healthcheck | --status
# Environment: PGHOST, SMB_OWNER_PASSWORD, KEYCLOAK_DB_PASSWORD, RESTIC_REPOSITORY, RESTIC_PASSWORD,
#              AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (S3 repositories), BACKUP_STATE_DIR (default /state).
set -euo pipefail

STATE_DIR="${BACKUP_STATE_DIR:-/state}"
STATUS_FILE="$STATE_DIR/backup-status.json"
SUCCESS_FILE="$STATE_DIR/last-success"
WORK_DIR="${STATE_DIR:?}/work/dumps"
HEALTH_MAX_AGE_SECONDS="${BACKUP_HEALTH_MAX_AGE_SECONDS:-93600}" # 26 hours
STALE_AFTER_SECONDS="${BACKUP_STALE_AFTER_SECONDS:-21600}"       # 6 hours
PGHOST="${PGHOST:-postgres}"
export PGHOST

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s backup: %s\n' "$(now)" "$*" >&2; }

age_of_last_success() {
  [ -s "$SUCCESS_FILE" ] || { echo -1; return; }
  echo $(($(date +%s) - $(cat "$SUCCESS_FILE")))
}

clean_dumps() { rm -f "${WORK_DIR:?}"/*.dump; }

write_status() { # result error snapshot kind duration smb_bytes keycloak_bytes
  local tmp="$STATUS_FILE.tmp"
  local last_success=null repo_type=local
  case "$RESTIC_REPOSITORY" in s3:* | rest:* | sftp:* | b2:* | azure:* | gs:*) repo_type="${RESTIC_REPOSITORY%%:*}" ;; esac
  if [ -s "$SUCCESS_FILE" ]; then
    last_success="\"$(date -u -d "@$(cat "$SUCCESS_FILE")" +%Y-%m-%dT%H:%M:%SZ)\""
  fi
  jq -n --arg result "$1" --arg error "$2" --arg snapshot "$3" --arg kind "$4" \
    --argjson duration "${5:-0}" --argjson smb "${6:-0}" --argjson kc "${7:-0}" \
    --arg attempt "$(now)" --argjson last_success "$last_success" --arg repo "$repo_type" \
    '{last_attempt: $attempt, last_success: $last_success, result: $result, error: (if $error == "" then null else $error end),
      snapshot_id: (if $snapshot == "" then null else $snapshot end), kind: $kind, duration_seconds: $duration,
      dump_bytes: {smb: $smb, keycloak: $kc}, repository_type: $repo}' >"$tmp"
  mv "$tmp" "$STATUS_FILE"
}

ensure_repository() {
  if ! restic cat config >/dev/null 2>&1; then
    log "repository not found; initialising"
    restic init >/dev/null || return 1
  fi
}

dump_database() { # name role password
  local db="$1" role="$2" password="$3"
  PGPASSWORD="$password" pg_dump --host "$PGHOST" --username "$role" --format=custom --compress=6 \
    --file "$WORK_DIR/$db.dump" "$db" || return 1
}

run_backup() {
  local kind="$1" started finished snapshot smb_bytes kc_bytes
  started=$(date +%s)
  mkdir -p "$WORK_DIR"
  clean_dumps
  trap clean_dumps EXIT
  # run_backup is called on the left of `||`, which switches errexit off inside it: every step must return explicitly,
  # otherwise a failed dump or upload would still be recorded as a success.
  ensure_repository || return 1
  dump_database smb smb_owner "${SMB_OWNER_PASSWORD:?SMB_OWNER_PASSWORD is required}" || return 1
  dump_database keycloak keycloak "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD is required}" || return 1
  smb_bytes=$(stat -c %s "$WORK_DIR/smb.dump") || return 1
  kc_bytes=$(stat -c %s "$WORK_DIR/keycloak.dump") || return 1
  [ "$smb_bytes" -gt 0 ] && [ "$kc_bytes" -gt 0 ] || { log "empty dump"; return 1; }
  restic backup --host smb --tag "$kind" --quiet "$WORK_DIR" || return 1
  snapshot=$(restic snapshots --host smb --latest 1 --json | jq -er '.[0].short_id') || return 1
  finished=$(date +%s)
  echo "$finished" >"$SUCCESS_FILE"
  write_status ok "" "$snapshot" "$kind" "$((finished - started))" "$smb_bytes" "$kc_bytes"
  log "ok: snapshot $snapshot ($kind), smb $smb_bytes bytes, keycloak $kc_bytes bytes, $((finished - started)) s"
}

run_maintenance() {
  ensure_repository
  restic forget --host smb --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune --quiet
  restic check --read-data-subset=5%
  log "maintenance ok"
}

main() {
  local mode=backup kind=scheduled age
  while [ $# -gt 0 ]; do
    case "$1" in
      --kind) kind="${2:?--kind needs a value}"; shift ;;
      --maintenance | --startup | --healthcheck | --status) mode="${1#--}" ;;
      *) echo "usage: backup.sh [--kind NAME] [--maintenance|--startup|--healthcheck|--status]" >&2; exit 64 ;;
    esac
    shift
  done
  mkdir -p "$STATE_DIR"

  case "$mode" in
    healthcheck)
      age=$(age_of_last_success)
      [ "$age" -ge 0 ] && [ "$age" -le "$HEALTH_MAX_AGE_SECONDS" ]
      return
      ;;
    status)
      if [ -s "$STATUS_FILE" ]; then cat "$STATUS_FILE"; else echo '{"result":"never_run"}'; fi
      return
      ;;
  esac

  exec 9>"$STATE_DIR/backup.lock"
  # Explicit safety backups must finish their own backup before their caller can proceed.
  if [ "$mode" = backup ] && [ "$kind" != scheduled ] && [ "$kind" != nightly ]; then
    flock 9 || { log "could not acquire the backup lock"; return 1; }
  elif ! flock -n 9; then
    log "another backup or maintenance run is in progress; skipping"
    return 0
  fi

  case "$mode" in
    startup)
      age=$(age_of_last_success)
      if [ "$age" -ge 0 ] && [ "$age" -le "$STALE_AFTER_SECONDS" ]; then
        log "last backup is $age s old; not repeating it at start"
        return 0
      fi
      # A failed first backup must not stop the scheduler: the status file and health check report it.
      run_backup startup || { write_status error "backup at start failed (see container log)" "" startup 0 0 0; return 0; }
      ;;
    maintenance) run_maintenance ;;
    backup) run_backup "$kind" || { write_status error "backup failed (see container log)" "" "$kind" 0 0 0; return 1; } ;;
  esac
}

main "$@"
