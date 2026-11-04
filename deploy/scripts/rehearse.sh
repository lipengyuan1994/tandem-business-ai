#!/usr/bin/env bash
# Local production rehearsal (task P4-07): run the production images with compose.prod.yml on this machine, on
# https://localhost:8443 with Caddy's internal CA, and prove the whole life cycle:
#   bootstrap -> seed -> smoke -> end-to-end tests -> upgrade rc1 -> rc2 -> failed upgrade rolls back -> backup restore.
#   deploy/scripts/rehearse.sh [--keep] [--skip-e2e] [--skip-build]
# Needs: Docker, pnpm (for the e2e tests). Stop the dev stack first (`make dev-down`): both use port 8443.
# Everything lives in deploy/rehearsal/ (gitignored) and the Docker project smb-prod; it is removed at the end unless --keep.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
dir="$root/deploy/rehearsal"
v1=0.1.0-rc1 v2=0.1.0-rc2
keep=false skip_e2e=false skip_build=false
for arg in "$@"; do
  case "$arg" in
    --keep) keep=true ;;
    --skip-e2e) skip_e2e=true ;;
    --skip-build) skip_build=true ;;
    *) echo "unknown option: $arg" >&2; exit 64 ;;
  esac
done

compose=(docker compose -f "$dir/compose.yml" --env-file "$dir/.env")
log="$root/backups/rehearsal-last.log"
mkdir -p "$root/backups"
: >"$log"
t_start=$(date +%s)

step() { printf '\n==== %s\n' "$*" | tee -a "$log"; }
note() { printf '%s\n' "$*" | tee -a "$log"; }
set_env() { # KEY VALUE: rewrite one line of the rehearsal .env
  local tmp
  tmp=$(mktemp "$dir/.env.XXXXXX")
  chmod 600 "$tmp"
  awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$dir/.env" >"$tmp"
  mv "$tmp" "$dir/.env"
}
teardown() {
  if $keep; then note "left running (--keep): $dir"; return; fi
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "${dir:?}"
}
on_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] || note "REHEARSAL FAILED (exit $rc); stack left in place for inspection: $dir"
  exit "$rc"
}
trap on_exit EXIT

if curl -sk --max-time 3 -o /dev/null https://localhost:8443/ 2>/dev/null; then
  echo "something already answers on https://localhost:8443 (the dev stack?). Run 'make dev-down' first." >&2
  exit 1
fi

if ! $skip_build; then
  step "build images $v1 and $v2"
  make -C "$root" images VERSION="$v1" >>"$log" 2>&1
  make -C "$root" images VERSION="$v2" >>"$log" 2>&1
  docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E "smb/smb-(api|web|backup):($v1|$v2) " | tee -a "$log"
fi

step "stage files and generate secrets in $dir"
rm -rf "${dir:?}"
"$here/stage.sh" "$dir" | tee -a "$log"
"$here/gen-env.sh" --domain localhost --http-port 8080 --https-port 8443 --local-backup --version "$v1" --out "$dir/.env" | sed 's#/.*/##' | tee -a "$log"

step "start the production stack ($v1)"
"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait --wait-timeout 300 2>&1 | tail -3 | tee -a "$log"
"${compose[@]}" ps --format 'table {{.Service}}\t{{.Status}}' | tee -a "$log"

step "bootstrap a business and seed the demo data"
"${compose[@]}" run --rm -T api python -m app.cli bootstrap-business --name "Rehearsal Workshop" --slug rehearsal-workshop \
  --timezone America/Denver --owner-email owner@example.com --owner-name "Rehearsal Owner" 2>&1 | sed -E 's/(temporary_password=).*/\1<hidden>/' | tee -a "$log"
"${compose[@]}" run --rm -T api python -m app.cli seed 2>&1 | tee -a "$log"

step "System page data: the api container can read the backup status and knows its version"
"${compose[@]}" exec -T api python -c "
from app.modules.admin_ops.service import read_backup_status
from app.platform.config import get_settings
s = read_backup_status()
assert s.available and s.result == 'ok', s
assert get_settings().app_version == '$v1', get_settings().app_version
print('backup', s.result, 'version', get_settings().app_version)" | tee -a "$log"

step "smoke test"
"$dir/smoke.sh" https://localhost:8443 --compose-dir "$dir" --env-file "$dir/.env" | tee -a "$log"

if ! $skip_e2e; then
  step "end-to-end tests against the production images (api and worker restarted with APP_ENV=dev for the dev-only account helper)"
  set_env APP_ENV dev
  "${compose[@]}" up -d --wait api worker >/dev/null 2>&1
  (cd "$root/frontend" && E2E_COMPOSE_FILE="$dir/compose.yml" E2E_ENV_FILE="$dir/.env" E2E_FRESH_ACCOUNTS=1 pnpm exec playwright test 2>&1) | tail -25 | tee -a "$log"
  set_env APP_ENV prod
  "${compose[@]}" up -d --wait api worker >/dev/null 2>&1
  "$dir/smoke.sh" https://localhost:8443 --compose-dir "$dir" >/dev/null
  note "back in production mode; smoke test passed"
fi

step "an installation made before Caddy ran as a non-root user: its volumes belong to root and the upgrade must hand them over"
# What the former root container left behind. A non-root Caddy cannot read these files and would serve no certificate.
served_certificate() { echo | openssl s_client -connect localhost:8443 -servername localhost 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true; }
certificate_before=$(served_certificate)
[ -n "$certificate_before" ] || { note "FAIL: Caddy served no certificate before the upgrade"; exit 1; }
"${compose[@]}" stop caddy >/dev/null 2>&1
"${compose[@]}" run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH --entrypoint chown caddy -R -h 0:0 /data /config >>"$log" 2>&1
note "caddy stopped; its volumes now belong to root"

step "upgrade $v1 -> $v2 (local images, no pull)"
COMPOSE_DIR="$dir" SKIP_PULL=1 "$dir/upgrade.sh" "$v2" | tee -a "$log"
grep -q "^VERSION=$v2$" "$dir/.env"
note "upgrade.log: $(tail -1 "$dir/upgrade.log")"
[ "$(served_certificate)" = "$certificate_before" ] || { note "FAIL: the certificate changed during the upgrade"; exit 1; }
note "same certificate before and after: the upgrade handed Caddy's volumes to uid 10003"

step "failed upgrade must roll back (image 0.1.0-rc9 does not exist)"
if COMPOSE_DIR="$dir" SKIP_PULL=1 "$dir/upgrade.sh" 0.1.0-rc9 >>"$log" 2>&1; then
  note "FAIL: upgrade to a missing image reported success"
  exit 1
fi
grep -q "^VERSION=$v2$" "$dir/.env"
tail -1 "$dir/upgrade.log" | grep -q "0.1.0-rc9 | FAILED, rolled back to $v2"
"$dir/smoke.sh" https://localhost:8443 --compose-dir "$dir" >/dev/null
note "rolled back to $v2 and healthy; upgrade.log: $(tail -1 "$dir/upgrade.log")"

step "rollback after a migration was applied (rc3 = rc2 + one new database revision, and an api that cannot start)"
probe="$dir/probe-build"
mkdir -p "$probe"
head_rev=$("${compose[@]}" exec -T postgres psql -U postgres -d smb -Atc "select version_num from alembic_version")
cat >"$probe/20261026_0900_rehearsal_probe.py" <<PY
"""Rehearsal probe: an extra revision that the previous image does not know."""
from alembic import op

revision = "20261026_0900_rehearsal_probe"
down_revision = "$head_rev"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("select 1")


def downgrade() -> None:
    pass
PY
cat >"$probe/Dockerfile" <<DOCKER
FROM smb/smb-api:$v2
COPY 20261026_0900_rehearsal_probe.py /srv/migrations/versions/
CMD ["python", "-c", "raise SystemExit(1)"]
DOCKER
docker build -q -t smb/smb-api:0.1.0-rc3 "$probe" >>"$log" 2>&1
docker tag "smb/smb-web:$v2" smb/smb-web:0.1.0-rc3
docker tag "smb/smb-backup:$v2" smb/smb-backup:0.1.0-rc3
if COMPOSE_DIR="$dir" SKIP_PULL=1 HEALTH_TIMEOUT=60 "$dir/upgrade.sh" 0.1.0-rc3 >>"$log" 2>&1; then
  note "FAIL: upgrade to an api that cannot start reported success"
  exit 1
fi
applied=$("${compose[@]}" exec -T postgres psql -U postgres -d smb -Atc "select version_num from alembic_version")
[ "$applied" = 20261026_0900_rehearsal_probe ] || { note "FAIL: the probe migration was not applied ($applied)"; exit 1; }
grep -q "^VERSION=$v2$" "$dir/.env"
tail -1 "$dir/upgrade.log" | grep -q "0.1.0-rc3 | FAILED, rolled back to $v2"
"$dir/smoke.sh" https://localhost:8443 --compose-dir "$dir" >/dev/null
note "database is at the newer revision ($applied), the old version runs again and is healthy; upgrade.log: $(tail -1 "$dir/upgrade.log")"
step "restart the rolled-back stack with its persisted migration image"
"${compose[@]}" down
"${compose[@]}" up -d --wait --wait-timeout 300
"$dir/smoke.sh" https://localhost:8443 --compose-dir "$dir" >/dev/null
rm -rf "${probe:?}"

step "restore drill, then a real restore of the latest snapshot"
COMPOSE_DIR="$dir" "$dir/restore.sh" --drill latest | tee -a "$log"
t_restore=$(date +%s)
COMPOSE_DIR="$dir" "$dir/restore.sh" latest --yes | tee -a "$log"
note "real restore took $(($(date +%s) - t_restore)) s including the smoke test"
"${compose[@]}" exec -T backup cat /state/restore-last.json | tee -a "$log"

step "final smoke test"
"$dir/smoke.sh" https://localhost:8443 --compose-dir "$dir" | tee -a "$log"

note ""
note "REHEARSAL PASSED in $(($(date +%s) - t_start)) s (log: backups/rehearsal-last.log)"
teardown
