#!/usr/bin/env bash
# Static checks of everything under deploy/ (part of `make check`; needs no cloud account and starts no stack):
# scripts (shellcheck), Compose validity and published ports, Caddyfile, realm template, Terraform, cloud-init schema.
# DEPLOY_CHECK_NO_DOWNLOADS=1 uses installed tools/cached providers and skips checks that install packages.
# DEPLOY_CHECK_OFFLINE=1 also forbids Docker/network/listeners and reports skipped runtime/schema checks.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "$here/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp:?}"' EXIT
fail=0
no_downloads="${DEPLOY_CHECK_NO_DOWNLOADS:-0}"
offline="${DEPLOY_CHECK_OFFLINE:-0}"
# An explicitly offline run must not discover Docker or Caddy in regression imports (some tests start listeners).
# Keep only native Python and local static-check tools, plus the OS utilities. No environment file is read.
if [ "$offline" = 1 ]; then
  mkdir "$tmp/bin"
  for tool in python3 shellcheck jq; do
    tool_path=$(command -v "$tool" || true)
    [ -z "$tool_path" ] || ln -s "$tool_path" "$tmp/bin/$tool"
  done
  export PATH="$tmp/bin:/usr/bin:/bin:/usr/sbin:/sbin"
fi
ok() { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=1; }
skip() { printf '  skip  %s\n' "$1"; }

echo "deploy checks"

# --- shell scripts ---
if command -v shellcheck >/dev/null; then
  if shellcheck "$deploy"/scripts/*.sh "$deploy"/scripts/lib/*.sh; then ok "shellcheck: all scripts"; else bad "shellcheck"; fi
else skip "shellcheck not installed"; fi

# --- deployment regressions (live HTTP tests self-skip when native Caddy is absent) ---
if python3 -I -m unittest discover -s "$deploy/tests" -p 'test_*.py' >"$tmp/tests.log" 2>&1; then
  ok "deployment regressions (live Caddy tests self-skip if unavailable)"
  grep -E '^Ran |^OK' "$tmp/tests.log"
else cat "$tmp/tests.log"; bad "deployment regressions"; fi

# --- Compose ---
if [ "$offline" = 1 ]; then
  skip "Docker Compose resolution, Caddy runtime, Terraform provider and cloud-init schema (offline mode; YAML regressions still run)"
else
"$here/gen-env.sh" --domain localhost --https-port 8443 --http-port 8080 --local-backup --out "$tmp/.env" >/dev/null
for file in compose.prod.yml compose.backup-test.yml; do
  if VERSION=0.0.0 docker compose -f "$deploy/compose/$file" --env-file "$tmp/.env" config --quiet 2>"$tmp/err"; then ok "compose config: $file"; else bad "compose config: $file: $(head -3 "$tmp/err")"; fi
done
published=$(docker compose -f "$deploy/compose/compose.prod.yml" --env-file "$tmp/.env" config --format json |
  jq -r '[.services | to_entries[] | select((.value.ports // []) | length > 0) | .key] | sort | join(",")')
if [ "$published" = caddy ]; then ok "only caddy publishes ports"; else bad "services with published ports: $published"; fi
if ! docker compose -f "$deploy/compose/compose.prod.yml" --env-file /dev/null config --quiet >/dev/null 2>&1; then ok "compose refuses to start without required secrets"; else bad "compose accepted an empty environment"; fi
unpinned=$(grep -hE '^\s+image: ' "$deploy/compose/compose.prod.yml" | grep -v 'REGISTRY' | grep -v '@sha256:' || true)
if [ -z "$unpinned" ]; then ok "third-party images pinned by digest"; else bad "unpinned images: $unpinned"; fi

# --- container hardening (docs/security/2026-11-03-rescan.md, F4; trivy does not read Compose files, so it is checked here) ---
resolved=$(docker compose -f "$deploy/compose/compose.prod.yml" --env-file "$tmp/.env" config --format json)
loose=$(printf '%s' "$resolved" | jq -r '[.services | to_entries[]
  | select((.value.cap_drop // []) != ["ALL"] or ((.value.security_opt // []) | index("no-new-privileges:true") | not) or (.value.privileged // false))
  | .key] | join(" ")')
if [ -z "$loose" ]; then ok "every service drops all capabilities and cannot gain privileges"; else bad "default capabilities or no no-new-privileges: $loose"; fi
if printf '%s' "$resolved" | jq -e '(.services.caddy | .user == "10003:10003" and .cap_add == ["NET_BIND_SERVICE"] and .read_only == true)
    and ([.services[] | select(.cap_add != null)] | length == 1)' >/dev/null; then
  ok "caddy: user 10003, read-only root filesystem, NET_BIND_SERVICE as the only added capability"
else bad "caddy: expected user 10003:10003, read_only, and cap_add [NET_BIND_SERVICE] (no other service adds one)"; fi
as_root=""
for dockerfile in "$deploy"/docker/*.Dockerfile; do
  image_user=$(grep -E '^USER[[:space:]]' "$dockerfile" | tail -1 | awk '{print $2}')
  case "${image_user:-root}" in root | 0 | 0:*) as_root="$as_root $(basename "$dockerfile")" ;; esac
done
if [ -z "$as_root" ]; then ok "every image ends as a non-root USER"; else bad "images that run as root:$as_root"; fi

# --- Caddyfile ---
if command -v caddy >/dev/null; then
  if DOMAIN=example.com DEMO_SITE_DOMAIN='(demo_site_disabled)' caddy validate --config "$deploy/compose/Caddyfile" --adapter caddyfile >"$tmp/caddy.log" 2>&1; then ok "Caddyfile validates (native Caddy)"; else cat "$tmp/caddy.log"; bad "Caddyfile"; fi
  for demo_domain in '(demo_site_disabled)' clinic.example.com; do
    if DOMAIN=example.com DEMO_SITE_DOMAIN="$demo_domain" caddy adapt --config "$deploy/compose/Caddyfile" --adapter caddyfile >"$tmp/caddy.json" 2>"$tmp/caddy.log"; then
      ok "Caddyfile adapts (DEMO_SITE_DOMAIN=${demo_domain:-unset}, native Caddy)"
    else cat "$tmp/caddy.log"; bad "Caddyfile (DEMO_SITE_DOMAIN=${demo_domain:-unset})"; fi
  done
  # The development file is only adapted (parsed): validating it would start Caddy's internal CA.
  if caddy adapt --config "$deploy/compose/Caddyfile.dev" --adapter caddyfile >/dev/null 2>"$tmp/caddy-dev.log"; then ok "Caddyfile.dev parses (native Caddy)"; else cat "$tmp/caddy-dev.log"; bad "Caddyfile.dev"; fi
elif docker image inspect caddy:2.11.7-alpine >/dev/null 2>&1 || { [ "$no_downloads" != 1 ] && docker pull -q caddy:2.11.7-alpine >/dev/null 2>&1; }; then
  if docker run --rm -e DOMAIN=example.com -e 'DEMO_SITE_DOMAIN=(demo_site_disabled)' -v "$deploy/compose/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.7-alpine \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then ok "Caddyfile validates"; else bad "Caddyfile"; fi
  for demo_domain in '(demo_site_disabled)' clinic.example.com; do
    if docker run --rm -e DOMAIN=example.com -e DEMO_SITE_DOMAIN="$demo_domain" -v "$deploy/compose/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.7-alpine \
      caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then ok "Caddyfile adapts (DEMO_SITE_DOMAIN=${demo_domain:-unset})"; else bad "Caddyfile (DEMO_SITE_DOMAIN=${demo_domain:-unset})"; fi
  done
  if docker run --rm -v "$deploy/compose/Caddyfile.dev:/etc/caddy/Caddyfile:ro" caddy:2.11.7-alpine \
    caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then ok "Caddyfile.dev parses"; else bad "Caddyfile.dev"; fi
else skip "Caddyfile (caddy image unavailable)"; fi

# --- Keycloak realm template ---
realm="$deploy/compose/realm-smb.prod.json"
if jq -e '(tostring | contains("localhost") | not) and (.users | length == 1) and .users[0].serviceAccountClientId == "smb-admin"
          and ([.clients[].secret] | all(startswith("${")))' "$realm" >/dev/null; then
  ok "production realm: no localhost, no test users, secrets are placeholders"
else bad "production realm template"; fi

# --- Terraform ---
if command -v terraform >/dev/null; then
  if terraform -chdir="$deploy/terraform" fmt -check -recursive >/dev/null; then ok "terraform fmt"; else bad "terraform fmt (run: terraform fmt -recursive deploy/terraform)"; fi
  if { [ "$no_downloads" = 1 ] || terraform -chdir="$deploy/terraform/envs/demo" init -backend=false -input=false >/dev/null 2>&1; } &&
    terraform -chdir="$deploy/terraform/envs/demo" validate >/dev/null 2>"$tmp/err"; then ok "terraform validate (no plan or apply)"; else bad "terraform validate: $(head -3 "$tmp/err")"; fi

  # --- cloud-init ---
  echo 'templatefile("../../modules/vultr-single-vm/user-data.yaml.tftpl", { ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE0 example", admin_cidrs = ["203.0.113.7/32", "2001:db8::/64"] })' |
    terraform -chdir="$deploy/terraform/envs/demo" console -no-color | sed '1d;$d' >"$tmp/user-data.yaml"
  if grep -v '^[[:space:]]*#' "$tmp/user-data.yaml" | grep -qiE 'secret|api[_-]?key|token|password[:=]|CHANGE_ME'; then bad "cloud-init template contains secret-like text"; else ok "cloud-init template contains no secrets"; fi
  if [ "$no_downloads" = 1 ]; then
    skip "cloud-init schema (no-download mode: container check installs packages)"
  elif docker image inspect ubuntu:24.04 >/dev/null 2>&1; then
    if docker run --rm -v "$tmp/user-data.yaml:/user-data.yaml:ro" ubuntu:24.04 sh -c \
      'apt-get update -qq >/dev/null 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloud-init >/dev/null 2>&1 && cloud-init schema --config-file /user-data.yaml' >/dev/null 2>&1; then
      ok "cloud-init schema (cloud-init in ubuntu:24.04)"
    else bad "cloud-init schema"; fi
  else skip "cloud-init schema (pull ubuntu:24.04 once to enable; it downloads cloud-init on each run)"; fi
else skip "terraform not installed"; fi

fi # offline

# --- installer dry run ---
if "$here/install.sh" --dry-run --name check --domain check.example.com --registry r.example.com/smb --registry-user u \
  --owner-email o@example.com --owner-name O --business-name B --slug b --timezone UTC \
  --object-storage-url https://s.example.com --bucket b --admin-cidr 203.0.113.7/32 --out-dir "$tmp" >"$tmp/dry.txt" 2>&1 && [ ! -e "$tmp/install-check" ]; then
  ok "install.sh --dry-run prints every step and creates nothing ($(grep -c '^\[[0-9]/8\]' "$tmp/dry.txt") steps)"
else bad "install.sh --dry-run"; fi

if [ "$fail" -eq 0 ]; then echo "deploy checks: ALL PASSED"; else echo "deploy checks: FAILED" >&2; exit 1; fi
