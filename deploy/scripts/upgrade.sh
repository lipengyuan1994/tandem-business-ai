#!/usr/bin/env bash
# Upgrade an installation to a new image version, with automatic rollback if the smoke test fails.
#   upgrade.sh 0.1.1                 (run on the VM in /opt/smb, or set COMPOSE_DIR)
# Steps: backup now -> set VERSION -> pull -> migrate -> give Caddy's volumes to its user -> AI config -> up -> wait
# healthy -> smoke test. On failure the previous VERSION is started again. Safe because migrations are expand/contract
# (docs/roadmap/04-repository-and-conventions.md).
# Environment: COMPOSE_DIR (default: this script's directory, or /opt/smb), SKIP_PULL=1 for locally built images,
#              HEALTH_TIMEOUT (seconds, default 300).
set -euo pipefail

new_version="${1:-}"
[[ "$new_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]] || { echo "usage: upgrade.sh VERSION (e.g. 0.1.1)" >&2; exit 64; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dir="${COMPOSE_DIR:-}"
if [ -z "$dir" ]; then if [ -f "$here/compose.yml" ]; then dir="$here"; else dir=/opt/smb; fi; fi
cd "$dir"
compose_file=compose.yml
[ -f "$compose_file" ] || compose_file=compose.prod.yml
[ -f .env ] || { echo "no .env in $dir" >&2; exit 1; }
compose=(docker compose -f "$compose_file")
log_file="$dir/upgrade.log"
health_timeout="${HEALTH_TIMEOUT:-300}"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { printf '%s upgrade: %s\n' "$(now)" "$*"; }
record() { printf '%s | %s -> %s | %s\n' "$(now)" "$previous" "$new_version" "$1" >>"$log_file"; }

set_version() {
  local tmp
  tmp=$(mktemp "$dir/.env.XXXXXX")
  chmod 600 "$tmp"
  awk -v v="$1" -v m="$2" '
    /^VERSION=/ {next} /^MIGRATION_VERSION=/ {next} {print}
    END {print "VERSION=" v; print "MIGRATION_VERSION=" m}' .env >"$tmp"
  mv "$tmp" .env
  printf '%s\n' "$1" >"$dir/VERSION"
}

# Caddy runs as uid 10003 (docs/security/2026-11-03-rescan.md, F4). Volumes that a former root container filled
# (certificates, ACME account) still belong to root: the new container then starts, cannot read its certificate and
# serves none (checked: "permission denied"). So the volumes are handed over before it starts. Idempotent and quick.
# The one-off container gets only what chown needs: CHOWN to change the owner, DAC_READ_SEARCH to enter directories that
# are mode 0700 and already belong to 10003 (without it a second run fails). It also works under an older compose.yml.
caddy_uid=10003
prepare_caddy_volumes() {
  "${compose[@]}" run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH \
    --entrypoint chown caddy -R -h "$caddy_uid:$caddy_uid" /data /config
}

wait_healthy() {
  local deadline=$((SECONDS + health_timeout)) bad
  while [ "$SECONDS" -lt "$deadline" ]; do
    bad=$("${compose[@]}" ps --all --format '{{.Service}}|{{.Status}}' | grep -v '^migrate|' | grep -v '(healthy)' || true)
    [ -z "$bad" ] && return 0
    sleep 5
  done
  say "not healthy after ${health_timeout}s: $(printf '%s' "$bad" | tr '\n' ' ')"
  return 1
}

previous=$(grep '^VERSION=' .env | head -1 | cut -d= -f2)
[ -n "$previous" ] || previous=unknown
previous_migration=$(sed -n 's/^MIGRATION_VERSION=//p' .env | head -1)
previous_migration="${previous_migration:-$previous}"
public_url=$(grep '^PUBLIC_URL=' .env | head -1 | cut -d= -f2)
[ "$previous" != "$new_version" ] || { echo "already at $new_version" >&2; exit 1; }
say "upgrading $previous -> $new_version ($public_url)"

say "1/5 backup before upgrade"
"${compose[@]}" exec -T backup backup.sh --kind pre-upgrade

set_version "$new_version" "$new_version"
migration_version="$previous_migration"
rolling_back=false
rollback() {
  $rolling_back && return 0
  rolling_back=true
  say "ROLLING BACK to $previous"
  set_version "$previous" "$migration_version"
  # Persist the migration image independently of the app version, including across Compose down/up and systemd restarts.
  "${compose[@]}" up -d --remove-orphans
  wait_healthy || say "rollback is also not healthy: follow docs/runbooks/incident.md"
  record "FAILED, rolled back to $previous"
}

# If the script is interrupted (SSH connection lost, Ctrl-C, kill) after VERSION was changed, go back to the old version.
on_interrupt() {
  say "interrupted"
  rollback
  exit 1
}
trap on_interrupt HUP INT TERM

if [ "${SKIP_PULL:-0}" != 1 ]; then
  say "2/5 pull images"
  "${compose[@]}" pull || { rollback; exit 1; }
else
  say "2/5 pull skipped (SKIP_PULL=1, local images)"
fi

say "3/5 migrate database"
"${compose[@]}" run --rm -T migrate || { rollback; exit 1; }
migration_version="$new_version"

say "4/5 start new version"
# The AI route file (mounted at /config): copy new files from a staged config/ next to this script when it is run from
# a stage directory; create ai-routes.yaml from the example only if it is missing. Never overwrite ai-routes.yaml.
sync_ai_config() {
  mkdir -p "$dir/config"
  if [ -d "$here/config" ] && [ "$here" != "$dir" ]; then
    for file in "$here"/config/*.yaml; do
      [ "$(basename "$file")" = ai-routes.yaml ] && continue
      install -m 0644 "$file" "$dir/config/$(basename "$file")"
    done
  fi
  if [ ! -f "$dir/config/ai-routes.yaml" ] && [ -f "$dir/config/ai-routes.example.yaml" ]; then
    install -m 0644 "$dir/config/ai-routes.example.yaml" "$dir/config/ai-routes.yaml"
    say "    created config/ai-routes.yaml from the example (AI stays off until a provider key is set)"
  fi
}
sync_ai_config || { rollback; exit 1; }
say "    caddy volumes: owner $caddy_uid"
prepare_caddy_volumes || { rollback; exit 1; }
"${compose[@]}" up -d --remove-orphans || { rollback; exit 1; }
wait_healthy || { rollback; exit 1; }

say "5/5 smoke test"
smoke="$here/smoke.sh"
[ -x "$smoke" ] || smoke="$dir/smoke.sh"
if "$smoke" "$public_url" --compose-dir "$dir"; then
  record "OK"
  say "upgraded to $new_version"
else
  rollback
  exit 1
fi
