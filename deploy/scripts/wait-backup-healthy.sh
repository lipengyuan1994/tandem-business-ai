#!/usr/bin/env bash
# Wait for Docker to observe backup health after a successful backup. Pass the Compose command as arguments.
#   wait-backup-healthy.sh docker compose -f compose.yml
# BACKUP_HEALTH_TIMEOUT defaults to 120 seconds; missing health telemetry is never success.
set -euo pipefail

timeout="${BACKUP_HEALTH_TIMEOUT:-120}"
[[ "$timeout" =~ ^[1-9][0-9]*$ ]] || { echo "invalid BACKUP_HEALTH_TIMEOUT" >&2; exit 64; }
[ "$#" -gt 0 ] || { echo "usage: wait-backup-healthy.sh COMPOSE_COMMAND..." >&2; exit 64; }
deadline=$((SECONDS + timeout))
while :; do
  if status=$("$@" ps --all --format '{{.Service}}|{{.Health}}' backup) &&
    [ "$status" = 'backup|healthy' ]; then
    exit 0
  fi
  remaining=$((deadline - SECONDS))
  [ "$remaining" -gt 0 ] || break
  delay=5
  [ "$remaining" -ge "$delay" ] || delay="$remaining"
  sleep "$delay"
done
echo "backup service not healthy after ${timeout}s; inspect docker compose ps and backup logs before retrying" >&2
exit 1
