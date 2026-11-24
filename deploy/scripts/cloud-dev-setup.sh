#!/bin/bash
# Development setup for a Claude cloud machine (or any root Linux box with Docker): docs/CLOUD_DEV.md.
#   deploy/scripts/cloud-dev-setup.sh            # dependencies, full dev stack, demo data
#   deploy/scripts/cloud-dev-setup.sh --deps-only # dependencies and the test database only (enough for `make test`)
#   deploy/scripts/cloud-dev-setup.sh --no-seed   # full stack without loading the synthetic businesses
# Safe to run again: each step checks what is already there.
set -euo pipefail

deps_only=0
seed=1
for arg in "$@"; do
    case $arg in
        --deps-only) deps_only=1 ;;
        --no-seed) seed=0 ;;
        -h | --help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 64 ;;
    esac
done

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
step() { printf '\n==> %s\n' "$*"; }
die() { echo "cloud-dev-setup: $*" >&2; exit 1; }

# The cloud machines re-sign outbound TLS with this CA; containers that download packages need it (compose.cloud.yml).
export CLOUD_CA_DIR=${CLOUD_CA_DIR:-/root/.ccr}
compose=(docker compose -p smb -f deploy/compose/compose.dev.yml)
if [[ -f $CLOUD_CA_DIR/ca-bundle.crt ]]; then
    compose+=(-f deploy/compose/compose.cloud.yml)
fi
compose+=(--env-file .env)

step "Checking tools"
# The frontend targets Node 24 (deploy/docker/web.Dockerfile). Some dictation tests fail on Node 22, which is the
# default `node` on the cloud machines; /opt/node24 is installed next to it.
node_major() { node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'; }
node_hint=""
if [[ $(node_major) != 24 && -x /opt/node24/bin/node ]]; then
    export PATH=/opt/node24/bin:$PATH
    # shellcheck disable=SC2016 # printed for the user to paste, so $PATH must stay literal
    node_hint='export PATH=/opt/node24/bin:$PATH'
fi
[[ $(node_major) == 24 ]] || echo "warning: node $(node --version 2>/dev/null) is not Node 24; some frontend tests may fail"
for tool in docker uv pnpm python3 curl jq; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
done
python3 -c 'import sys; sys.exit(sys.version_info[:2] < (3, 13))' 2>/dev/null \
    || echo "note: system python3 is older than 3.13; uv will fetch 3.13 for the backend"

step "Docker daemon"
if ! docker info >/dev/null 2>&1; then
    [[ $EUID -eq 0 ]] || die "Docker is not running and starting it needs root"
    # Docker Hub refuses anonymous pulls from shared cloud addresses (429); Google's mirror serves the same images.
    if [[ ! -s /etc/docker/daemon.json ]]; then
        mkdir -p /etc/docker
        echo '{"registry-mirrors": ["https://mirror.gcr.io"]}' >/etc/docker/daemon.json
    elif ! jq -e '."registry-mirrors"' /etc/docker/daemon.json >/dev/null; then
        tmp=$(mktemp)
        jq '. + {"registry-mirrors": ["https://mirror.gcr.io"]}' /etc/docker/daemon.json >"$tmp"
        mv "$tmp" /etc/docker/daemon.json
    fi
    nohup dockerd >/var/log/dockerd.log 2>&1 &
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
    docker info >/dev/null 2>&1 || die "dockerd did not start; see /var/log/dockerd.log"
fi
echo "docker $(docker version --format '{{.Server.Version}}')"

step "Images"
pull() { # pull IMAGE [SOURCE]: fetch SOURCE (default IMAGE) and tag it as IMAGE when they differ
    local image=$1 source=${2:-$1}
    docker image inspect "$image" >/dev/null 2>&1 && return 0
    for attempt in 1 2 3; do
        docker pull -q "$source" >/dev/null && break
        [[ $attempt -lt 3 ]] || return 1
        sleep $((attempt * 10))
    done
    [[ $source == "$image" ]] || docker tag "$source" "$image"
}
# quay.io and ghcr.io blobs are blocked on the cloud network; the same images are published on Docker Hub.
pull pgvector/pgvector:0.8.7-pg17-bookworm
if [[ $deps_only -eq 0 ]]; then
    pull python:3.13-slim
    pull node:24-alpine
    pull caddy:2.11.7-alpine
    pull quay.io/keycloak/keycloak:26.8.1 keycloak/keycloak:26.8.1
    pull ghcr.io/astral-sh/uv:0.12 astral/uv:0.12
fi

step ".env"
if [[ -f .env ]]; then echo ".env exists, left as it is"; else cp .env.example .env && echo "created .env from .env.example (dummy values)"; fi

step "Backend dependencies (uv)"
(cd backend && uv sync --frozen)

step "Frontend dependencies (pnpm)"
(cd frontend && CI=1 pnpm install --frozen-lockfile)

if [[ $deps_only -eq 1 ]]; then
    step "Test database"
    "${compose[@]}" up -d --wait db-test
    printf '\nReady for tests: make backend-test, make frontend-test (or make check).\n'
    [[ -z $node_hint ]] || printf 'Use Node 24 in your shell first: %s\n' "$node_hint"
    exit 0
fi

step "Dev stack (postgres, keycloak, api, worker, web, caddy)"
"${compose[@]}" up -d --build
echo "waiting for the api to migrate and answer..."
for _ in $(seq 1 90); do
    curl -skf https://localhost:8443/api/v1/readyz >/dev/null 2>&1 && break
    sleep 2
done
curl -skf https://localhost:8443/api/v1/readyz || die "api not ready after 3 minutes; run: make dev-logs"
echo

if [[ $seed -eq 1 ]]; then
    step "Synthetic demo businesses"
    if ! out=$("${compose[@]}" exec -T api python -m app.cli seed 2>&1); then
        # A second run finds the businesses already there and exits non-zero; that is fine here.
        grep -q 'already exists' <<<"$out" || { echo "$out" >&2; die "seeding failed"; }
    fi
    echo "$out"
fi

cat <<'EOF'

Done.
  App:        https://localhost:8443 (self-signed; curl -k)
  Keycloak:   http://localhost:8081/auth (admin / admin_dev, dev only)
  Logins:     make demo-logins   (passwords go to .demo-logins/logins.txt)
  Tests:      make backend-test, make frontend-test, make check
  Logs, stop: make dev-logs, make dev-down
AI calls use the scripted fake provider (no API keys needed).
EOF
[[ -z $node_hint ]] || printf 'Use Node 24 in your shell before frontend commands: %s\n' "$node_hint"
