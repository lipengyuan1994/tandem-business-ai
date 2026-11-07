#!/usr/bin/env bash
# Smoke test of a running installation. External checks need only curl; with --compose-dir it also checks the
# containers on this host (health, published ports, last backup).
#   smoke.sh https://example.com
#   smoke.sh https://localhost:8443 --compose-dir /opt/smb [--env-file /opt/smb/.env]
# Exit code 0 only if every check passes. TLS errors are tolerated only for localhost (Caddy's internal CA).
set -uo pipefail

base="${1:-}"
[ -n "$base" ] || { echo "usage: smoke.sh BASE_URL [--compose-dir DIR] [--env-file FILE]" >&2; exit 64; }
shift
compose_dir="" env_file=""
demo_domain="${DEMO_SITE_DOMAIN:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --compose-dir) compose_dir="${2:?}"; shift ;;
    --env-file) env_file="${2:?}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
base="${base%/}"

curl_opts=(--silent --show-error --max-time 15)
case "$base" in https://localhost* | https://127.0.0.1*) curl_opts+=(--insecure) ;; esac

failures=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { # description command...
  local description="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$description"; else fail "$description"; fi
}
status_of() { curl "${curl_opts[@]}" --output /dev/null --write-out '%{http_code}' "$@"; }
header_of() { curl "${curl_opts[@]}" --dump-header - --output /dev/null "$1" | tr -d '\r' | grep -i "^$2:" | head -1; }

echo "Smoke test: $base"

ready=$(curl "${curl_opts[@]}" "$base/api/v1/readyz" || true)
if printf '%s' "$ready" | jq -e '.status == "ok"' >/dev/null 2>&1; then
  pass "api ready, migration $(printf '%s' "$ready" | jq -r .migration)"
else
  fail "api readyz is not ok: ${ready:-no response}"
fi
check "api liveness (/healthz)" test "$(status_of "$base/api/v1/healthz")" = 200
check "unauthenticated /auth/me is refused (401)" test "$(status_of "$base/api/v1/auth/me")" = 401
# The ai module is mounted and answers (it needs a session; whether AI is enabled depends on config/ai-routes.yaml).
check "AI status endpoint answers (/api/v1/ai/status is 401 without a session)" test "$(status_of "$base/api/v1/ai/status")" = 401
# A production install does not publish the API description or the interactive docs (they list every route). They are
# served only with APP_ENV=dev or test, so a 200 here means the stack is not in production mode. rehearse.sh switches the
# api to dev for the end-to-end tests and back before it runs this script again, so it never calls it in that window.
check "API description is not public (/api/v1/openapi.json is 404)" test "$(status_of "$base/api/v1/openapi.json")" = 404
check "API docs are not public (/api/v1/docs is 404)" test "$(status_of "$base/api/v1/docs")" = 404

issuer=$(curl "${curl_opts[@]}" "$base/auth/realms/smb/.well-known/openid-configuration" | jq -r .issuer 2>/dev/null || true)
check "keycloak realm issuer is $base/auth/realms/smb" test "$issuer" = "$base/auth/realms/smb"
login_status=$(status_of "$base/api/v1/auth/login?return_to=/today")
check "login redirects to keycloak (302)" test "$login_status" = 302
check "keycloak admin console is not public (404)" test "$(status_of "$base/auth/admin/master/console/")" = 404
check "keycloak admin path is not public (404)" test "$(status_of "$base/auth/admin/")" = 404
check "keycloak master realm is not public (404)" test "$(status_of "$base/auth/realms/master/.well-known/openid-configuration")" = 404

check "single-page app is served (200)" test "$(status_of "$base/today")" = 200
check "public chat page is served (200)" test "$(status_of "$base/p/example-cycle-works/chat")" = 200
check "public request page is served (200)" test "$(status_of "$base/p/example-cycle-works/request")" = 200
quote_page=$(curl "${curl_opts[@]}" --fail "$base/q/smoke.invalid" || true)
check "supplier quote route serves the public widget" test "$(printf '%s' "$quote_page" | grep -c 'id="widget"')" -gt 0
check "unknown asset is 404, not the app shell" test "$(status_of "$base/assets/does-not-exist.js")" = 404
check "HSTS header" header_of "$base/" strict-transport-security
check "X-Content-Type-Options header" header_of "$base/" x-content-type-options
check "Content-Security-Policy on the app" header_of "$base/today" content-security-policy
check "Content-Security-Policy on the api" header_of "$base/api/v1/healthz" content-security-policy
# The proxy does not name itself (docs/security/2026-11-03-rescan.md, F7). The api's own Server header is removed too.
no_header() { [ -z "$(header_of "$1" "$2")" ]; }
check "no Server header on the app" no_header "$base/today" server
check "no Server header on the api" no_header "$base/api/v1/healthz" server
check "no Server header on a missing asset" no_header "$base/assets/does-not-exist.js" server

# Plain HTTP only redirects to HTTPS, and the redirect says nothing about the server either (Caddy's own redirect sent
# "Server: Caddy"). With a default HTTPS port the HTTP address follows from it; otherwise --compose-dir finds it below.
http_url=""
case "${base#https://}" in *:*) ;; *) http_url="http://${base#https://}/today" ;; esac

if [ -n "$compose_dir" ]; then
  compose=(docker compose --project-directory "$compose_dir" -f "$compose_dir/compose.yml")
  [ -f "$compose_dir/compose.yml" ] || compose=(docker compose --project-directory "$compose_dir" -f "$compose_dir/compose.prod.yml")
  [ -z "$env_file" ] || compose+=(--env-file "$env_file")
  # Read Compose's resolved value (including its .env/--env-file), never source an operator file as shell code.
  if resolved_config=$("${compose[@]}" config --format json 2>/dev/null); then
    if ! demo_domain=$(printf '%s' "$resolved_config" | jq -er '.services.caddy.environment.DEMO_SITE_DOMAIN // ""'); then
      fail "could not read demo site configuration from Compose"
    fi
  else
    fail "could not resolve Compose configuration for demo site"
  fi
  # Local rehearsals on macOS have no timedatectl; Linux VM checks must report synchronization.
  if [ "$(uname -s)" = Linux ]; then
    check "host clock synchronized" test "$(timedatectl show --property=NTPSynchronized --value 2>/dev/null)" = yes
  fi
  echo "Containers ($compose_dir)"
  # Every service except the one-shot migrate must exist and be healthy (a stopped or missing one is a failure).
  status_lines=$("${compose[@]}" ps --all --format '{{.Service}}|{{.Status}}' 2>/dev/null || true)
  unhealthy=""
  for service in $("${compose[@]}" config --services 2>/dev/null | grep -vx migrate); do
    line=$(printf '%s\n' "$status_lines" | grep "^$service|" || true)
    detail="${line#*|}"
    [ -n "$line" ] || detail=missing
    case "$line" in *"(healthy)"*) ;; *) unhealthy="$unhealthy $service($detail)" ;; esac
  done
  if [ -z "$unhealthy" ]; then pass "all services healthy"; else fail "services not healthy:$unhealthy"; fi
  published=$("${compose[@]}" ps --format json 2>/dev/null | jq -rs '[.[] | select((.Publishers // []) | map(select(.PublishedPort > 0)) | length > 0) | .Service] | sort | join(",")')
  check "only caddy publishes ports (found: ${published:-none})" test "$published" = caddy
  if [ -z "$http_url" ]; then
    http_port=$("${compose[@]}" port caddy 80 2>/dev/null | head -1 | sed 's/.*://')
    [ -z "$http_port" ] || http_url="http://127.0.0.1:$http_port/today"
  fi

  # Nothing runs as root: the first process of every container is the service itself (postgres drops its privileges
  # with exec), and Caddy, the only internet-facing one, keeps nothing but the one capability its binary needs.
  as_root=""
  for service in $("${compose[@]}" config --services 2>/dev/null | grep -vx migrate); do
    uid=$("${compose[@]}" exec -T "$service" grep -m1 '^Uid:' /proc/1/status 2>/dev/null | awk '{print $2}')
    if [ -z "$uid" ] || [ "$uid" = 0 ]; then as_root="$as_root $service(uid ${uid:-unknown})"; fi
  done
  if [ -z "$as_root" ]; then pass "no service runs as root"; else fail "services running as root, or not readable:$as_root"; fi
  caddy_proc=$("${compose[@]}" exec -T caddy grep -E '^(Uid|CapEff|NoNewPrivs):' /proc/1/status 2>/dev/null | tr -d '\r' | awk '{print $1 $2}')
  caddy_uid=$(printf '%s\n' "$caddy_proc" | sed -n 's/^Uid://p')
  # 0x400 is bit 10, CAP_NET_BIND_SERVICE: the caddy binary has it as a file capability, so it must stay in the set.
  check "caddy keeps one capability, NET_BIND_SERVICE" test "$(printf '%s\n' "$caddy_proc" | grep -c '^CapEff:0000000000000400$')" -eq 1
  check "caddy cannot gain privileges (no-new-privileges)" test "$(printf '%s\n' "$caddy_proc" | grep -c '^NoNewPrivs:1$')" -eq 1
  # shellcheck disable=SC2016  # the awk program must reach the container unexpanded
  caddy_root_mount=$("${compose[@]}" exec -T caddy awk '$2 == "/" {print $4}' /proc/mounts 2>/dev/null | head -1)
  check "caddy root filesystem is read-only" test "${caddy_root_mount%%,*}" = ro
  # A volume that a former root container filled keeps root as owner: the new Caddy then serves no certificate.
  # upgrade.sh hands it over; docs/runbooks/upgrade.md has the command for a manual upgrade.
  volumes_ok=no
  if [ -n "$caddy_uid" ]; then
    foreign=$("${compose[@]}" exec -T caddy find /data /config ! -user "$caddy_uid" -print -quit 2>/dev/null)
    [ -n "$foreign" ] || volumes_ok=yes
  fi
  check "all files in caddy's volumes belong to its user (uid ${caddy_uid:-unknown})" test "$volumes_ok" = yes
  backup=$("${compose[@]}" exec -T backup backup.sh --status 2>/dev/null || true)
  if printf '%s' "$backup" | jq -e '.result == "ok"' >/dev/null 2>&1; then
    pass "last backup ok ($(printf '%s' "$backup" | jq -r '.last_success'))"
  else
    fail "no successful backup recorded: ${backup:-no status}"
  fi
fi

if [ -n "$demo_domain" ] && [ "$demo_domain" != '(demo_site_disabled)' ]; then
  demo_url="https://$demo_domain/"
  check "demo clinic site answers 200" test "$(status_of "$demo_url")" = 200
  check "demo clinic Content-Security-Policy" header_of "$demo_url" content-security-policy
  check "demo clinic HSTS" header_of "$demo_url" strict-transport-security
  check "demo clinic nosniff" header_of "$demo_url" x-content-type-options
  check "demo clinic Referrer-Policy" header_of "$demo_url" referrer-policy
  check "demo clinic has no Server header" no_header "$demo_url" server
else
  echo "  SKIP  demo clinic site (DEMO_SITE_DOMAIN unset)"
fi

if [ -n "$http_url" ]; then
  redirect=$(curl "${curl_opts[@]}" --dump-header - --output /dev/null "$http_url" | tr -d '\r')
  check "plain HTTP redirects to HTTPS (308)" test "$(printf '%s\n' "$redirect" | sed -n '1s/^[^ ]* \([0-9]*\).*/\1/p')" = 308
  check "the HTTP redirect has no Server header" test -z "$(printf '%s\n' "$redirect" | grep -i '^server:' || true)"
fi

if [ "$failures" -eq 0 ]; then echo "SMOKE PASSED"; else echo "SMOKE FAILED: $failures check(s)" >&2; fi
[ "$failures" -eq 0 ]
