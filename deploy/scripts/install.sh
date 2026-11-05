#!/usr/bin/env bash
# Install SMB Operations on a new Vultr VM (docs/roadmap/10-vultr-infrastructure.md#install-flow). Demo or customer.
#
#   VULTR_API_KEY=... REGISTRY_TOKEN=... deploy/scripts/install.sh \
#     --name smb-demo --domain demo.example.com --registry sjc.vultrcr.com/smb --registry-user smb-ro \
#     --owner-email owner@example.com --owner-name "Owner Name" \
#     --business-name "Example Cycle Works" --slug example-cycle-works --timezone America/Denver \
#     --object-storage-url https://sjc1.vultrobjects.com --bucket smb-backups --admin-cidr 203.0.113.7/32 [--close-ssh] [--dry-run]
# If a run stops part-way (DNS not ready, slow first boot), fix the cause and repeat the same command with --resume:
# it reuses ./install-<name>/ and skips what is already done.
#
# --demo seeds synthetic businesses and writes login passwords only on the VM. --domain auto requires --demo
# and resolves to <dashed-ipv4>.sslip.io (third-party DNS for demos/tests only). No terminal is needed with --demo.
# Secrets come from the environment only (VULTR_API_KEY, REGISTRY_TOKEN, OBJECT_STORAGE_ACCESS_KEY,
# OBJECT_STORAGE_SECRET_KEY, optional VULTR_INFERENCE_API_KEY). --ai-routes FILE supplies the operator's AI routes.
# Local secrets stay in ./install-<name>/.env (mode 600) and the generated SSH key.
# --dry-run prints every step and runs nothing: no Vultr call, no files, no SSH.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "$here/.." && pwd)"

name="" domain="" region=sjc plan=vhp-4c-8gb-amd registry="" registry_user="" version=0.1.0
owner_email="" owner_name="" business_name="" slug="" timezone=""
object_storage_url="" bucket="" env_dir="$deploy/terraform/envs/demo" out_root="$PWD"
admin_cidrs=() close_ssh=false dry_run=false resume=false demo=false auto_domain=false ai_routes=""

usage() { sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-64}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name) name="${2:?}"; shift ;;
    --domain) domain="${2:?}"; shift ;;
    --demo) demo=true ;;
    --region) region="${2:?}"; shift ;;
    --plan) plan="${2:?}"; shift ;;
    --registry) registry="${2:?}"; shift ;;
    --registry-user) registry_user="${2:?}"; shift ;;
    --version) version="${2:?}"; shift ;;
    --owner-email) owner_email="${2:?}"; shift ;;
    --owner-name) owner_name="${2:?}"; shift ;;
    --business-name) business_name="${2:?}"; shift ;;
    --slug) slug="${2:?}"; shift ;;
    --timezone) timezone="${2:?}"; shift ;;
    --object-storage-url) object_storage_url="${2:?}"; shift ;;
    --bucket) bucket="${2:?}"; shift ;;
    --admin-cidr) admin_cidrs+=("${2:?}"); shift ;;
    --close-ssh) close_ssh=true ;;
    --out-dir) out_root="${2:?}"; shift ;;
    --dry-run) dry_run=true ;;
    --resume) resume=true ;;
    --ai-routes) ai_routes="${2:?}"; shift ;;
    -h | --help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
  shift
done

if [ "$domain" = auto ]; then
  $demo || { echo "--domain auto requires --demo" >&2; exit 64; }
  auto_domain=true
  domain=pending.invalid
fi
if $demo; then
  [ "${DATA_PROFILE:-standard}" != regulated ] || { echo "--demo refuses DATA_PROFILE=regulated" >&2; exit 64; }
  if [ -n "$slug$business_name$owner_email$owner_name" ]; then
    echo "--demo uses only the synthetic fixture businesses and staff; omit --slug, --business-name, --owner-email and --owner-name" >&2
    exit 64
  fi
fi
required=(name domain registry registry_user timezone object_storage_url bucket)
$demo || required+=(owner_email owner_name business_name slug)
missing=()
for v in "${required[@]}"; do
  [ -n "${!v}" ] || missing+=("--${v//_/-}")
done
if [ ${#missing[@]} -gt 0 ]; then echo "missing: ${missing[*]}" >&2; usage; fi
[[ "$name" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "--name must be 2-31 lowercase letters, digits, hyphens" >&2; exit 64; }
[[ "$domain" =~ ^[a-zA-Z0-9.-]+$ ]] || { echo "invalid --domain" >&2; exit 64; }
if ! $demo; then
  [[ "$slug" =~ ^[a-z0-9-]+$ ]] || { echo "invalid --slug" >&2; exit 64; }
fi
# The installer reaches the VM only over SSH (waits for first boot, uploads the stack, bootstraps), and the firewall
# opens port 22 only for --admin-cidr. Without one the VM would be created and billed but never configured.
if [ ${#admin_cidrs[@]} -eq 0 ]; then
  if $dry_run; then
    echo "WARNING: no --admin-cidr given; a real run refuses to start (SSH would stay closed and the install could not finish)." >&2
  else
    echo "--admin-cidr is required (your public address, e.g. 203.0.113.7/32): the installer needs SSH. Add --close-ssh to close it at the end." >&2
    exit 64
  fi
fi

# Terraform resolves -state/-var-file relative to -chdir, not the caller's directory.
# abspath does not create directories, preserving --dry-run's no-files guarantee.
out_root=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$out_root")
out="$out_root/install-$name"
key="$out/id_ed25519"
known_hosts="$out/known_hosts"
tfvars="$out/terraform.tfvars"
state="$out/terraform.tfstate"
resolved_domain="$out/resolved-domain"
if $resume && $auto_domain && [ -f "$resolved_domain" ]; then
  domain=$(cat "$resolved_domain")
  [[ "$domain" =~ ^[0-9]+-[0-9]+-[0-9]+-[0-9]+\.sslip\.io$ ]] || { echo "invalid saved auto domain" >&2; exit 1; }
fi
if $demo && [ -f "$out/.env" ] && grep -Eq "^[[:space:]]*(export[[:space:]]+)?DATA_PROFILE[[:space:]]*=[[:space:]]*['\"]?regulated['\"]?[[:space:]]*(#.*)?$" "$out/.env"; then
  echo "--demo refuses DATA_PROFILE=regulated in $out/.env" >&2
  exit 64
fi
ssh_opts=(-i "$key" -o "UserKnownHostsFile=$known_hosts" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes)

step() { printf '\n[%s] %s\n' "$1" "$2"; }
note() { printf '      %s\n' "$*"; }
run() { # echo, and execute unless dry-run
  if $dry_run; then printf '      [dry-run] %s\n' "$(printf '%q ' "$@")"; else "$@"; fi
}
remote() { # ssh to the VM as smb; the command string is meant to be expanded on the VM
  if $dry_run; then
    printf '      [dry-run] ssh smb@<ip> %s\n' "$*"
  else
    # shellcheck disable=SC2029
    ssh "${ssh_opts[@]}" "smb@$ip" "$@"
  fi
}

$dry_run && echo "DRY RUN: nothing below is executed. No Vultr API call is made."
echo "Installation '$name' on $domain ($region, $plan), version $version, images from $registry"

step 1/8 "Preflight"
for tool in terraform ssh scp ssh-keygen curl openssl jq python3; do
  if command -v "$tool" >/dev/null 2>&1; then note "found $tool"; else
    if $dry_run; then note "MISSING $tool (a real run would stop here)"; else echo "required tool missing: $tool" >&2; exit 1; fi
  fi
done
if ! $dry_run; then
  if [ -e "$out/.env" ] && ! $resume; then
    echo "$out already holds an installation. Use --resume to continue it, or choose another --name." >&2
    exit 1
  fi
  for secret in VULTR_API_KEY REGISTRY_TOKEN OBJECT_STORAGE_ACCESS_KEY OBJECT_STORAGE_SECRET_KEY; do
    [ -n "${!secret:-}" ] || { echo "environment variable $secret is required" >&2; exit 1; }
  done
else
  note "a real run requires VULTR_API_KEY, REGISTRY_TOKEN, OBJECT_STORAGE_ACCESS_KEY, OBJECT_STORAGE_SECRET_KEY in the environment"
fi

# Validate before creating files or cloud resources; never echo file contents or validation values.
if [ -n "$ai_routes" ]; then
  "$here/install-ai.sh" validate "$ai_routes"
  note "AI route file validated locally"
fi

step 2/8 "Generate this installation's secrets and SSH key into $out"
if $resume && ! $dry_run && [ -e "$out/.env" ]; then
  note "--resume: reusing $out/.env"
  if [ ! -f "$key" ]; then
    note "no SSH key in $out (restoring on a new computer?): generating a new one"
    ssh-keygen -q -t ed25519 -N "" -C "$name-install" -f "$key"
  fi
else
  run mkdir -p "$out"
  run chmod 700 "$out"
  run ssh-keygen -q -t ed25519 -N "" -C "$name-install" -f "$key"
  run "$here/gen-env.sh" --domain "$domain" --out "$out/.env" --registry "$registry" --version "$version"
  if ! $dry_run; then
    # Backups go to Vultr Object Storage through restic's S3 backend.
    repo="s3:${object_storage_url%/}/$bucket"
    tmp=$(mktemp "$out/.env.XXXXXX")
    # Keys travel through the environment, not the command line (process lists are readable by other users).
    REPO="$repo" awk '
      /^RESTIC_REPOSITORY=/ {print "RESTIC_REPOSITORY=" ENVIRON["REPO"]; next}
      /^OBJECT_STORAGE_ACCESS_KEY=/ {print "OBJECT_STORAGE_ACCESS_KEY=" ENVIRON["OBJECT_STORAGE_ACCESS_KEY"]; next}
      /^OBJECT_STORAGE_SECRET_KEY=/ {print "OBJECT_STORAGE_SECRET_KEY=" ENVIRON["OBJECT_STORAGE_SECRET_KEY"]; next}
      {print}' "$out/.env" >"$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$out/.env"
    if $demo; then
      note "Backup password remains in $out/.env (mode 600); store this file safely offline to preserve restore access."
    else
      echo
      echo "      RESTIC_PASSWORD (shown once; store it offline, without it backups cannot be restored):"
      echo "      $(grep '^RESTIC_PASSWORD=' "$out/.env" | cut -d= -f2)"
      read -r -p "      Press Enter once it is stored safely: " _
    fi
  else
    note "set RESTIC_REPOSITORY=s3:${object_storage_url%/}/$bucket and the object storage keys in .env"
    if $demo; then
      note "keep the backup password in the private .env; no password output or confirmation pause"
    else
      note "print RESTIC_PASSWORD once for offline storage, then wait for confirmation"
    fi
  fi
fi

# Apply an explicitly supplied inference key on fresh installs and resumes without exposing its value.
if [ -n "${VULTR_INFERENCE_API_KEY:-}" ]; then
  if $dry_run; then
    note "write the inference key privately to both .env copies and add api.vultrinference.com to EGRESS_ALLOWLIST"
  else
    "$here/install-ai.sh" set-key "$out/.env"
  fi
fi

step 3/8 "Create the VM, firewall group and SSH key with Terraform (local state: $state)"
cidr_list=$(printf '"%s",' "${admin_cidrs[@]:-}")
cidr_list="[${cidr_list%,}]"
[ "${#admin_cidrs[@]}" -gt 0 ] || cidr_list="[]"
if ! $dry_run; then
  printf 'name = "%s"\nregion = "%s"\nplan = "%s"\nadmin_cidrs = %s\nssh_public_key = "%s"\n' \
    "$name" "$region" "$plan" "$cidr_list" "$(cat "$key.pub")" >"$tfvars"
  export TF_VAR_vultr_api_key="$VULTR_API_KEY"
fi
note "terraform -chdir=$env_dir init"
note "terraform -chdir=$env_dir apply -state=$state -var-file=$tfvars"
if ! $dry_run; then
  terraform -chdir="$env_dir" init -input=false >/dev/null
  terraform -chdir="$env_dir" apply -input=false -auto-approve -state="$state" -var-file="$tfvars"
  ip=$(terraform -chdir="$env_dir" output -state="$state" -raw ipv4)
  ip6=$(terraform -chdir="$env_dir" output -state="$state" -raw ipv6)
else
  ip="<ipv4>" ip6="<ipv6>"
fi

if $auto_domain; then
  if ! $dry_run; then
    if ! $resume || [ ! -f "$resolved_domain" ]; then
      domain=$(python3 -c 'import ipaddress,sys; print(str(ipaddress.IPv4Address(sys.argv[1])).replace(".", "-") + ".sslip.io")' "$ip")
    fi
    # gen-env.sh derives exactly DOMAIN and PUBLIC_URL; preserve all generated secrets and other settings.
    tmp=$(mktemp "$out/.env.XXXXXX")
    DOMAIN="$domain" awk '
      /^DOMAIN=/ {print "DOMAIN=" ENVIRON["DOMAIN"]; next}
      /^PUBLIC_URL=/ {print "PUBLIC_URL=https://" ENVIRON["DOMAIN"]; next}
      {print}' "$out/.env" >"$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$out/.env"
    printf '%s\n' "$domain" >"$resolved_domain"
  else
    note "resolve auto domain to <dashed-ipv4>.sslip.io after Terraform (reuse $resolved_domain on --resume)"
    note "rewrite DOMAIN and PUBLIC_URL in $out/.env, preserving secrets; save $resolved_domain"
    [ "$domain" != pending.invalid ] || domain="<dashed-ipv4>.sslip.io"
  fi
  step 4/8 "DNS: automatic sslip.io domain (third-party service; demos/tests only)"
  note "$domain -> $ip; no DNS records to create"
else
  step 4/8 "DNS: you create the records, the installer waits for them"
  note "A     $domain -> $ip"
  note "AAAA  $domain -> $ip6"
  note "Create both records now."
fi
if ! $dry_run; then
  note "Waiting up to 30 minutes for $domain to resolve to $ip ..."
  for _ in $(seq 1 180); do
    resolved=$(python3 -c "import socket,sys; print(' '.join(sorted({a[4][0] for a in socket.getaddrinfo(sys.argv[1], 443, socket.AF_INET)})))" "$domain" 2>/dev/null || true)
    [ "$resolved" = "$ip" ] && break
    sleep 10
  done
  [ "$resolved" = "$ip" ] || { echo "DNS for $domain does not resolve to $ip yet; re-run after creating the record" >&2; exit 1; }
else
  note "wait until $domain resolves to the VM's IPv4 address (up to 30 minutes)"
fi

step 5/8 "Wait for first-boot setup, upload the stack, log in to the registry, start it"
note "ssh smb@$ip: wait for /opt/smb/READY (cloud-init installs Docker; a few minutes)"
if ! $dry_run; then
  for _ in $(seq 1 90); do remote test -f /opt/smb/READY 2>/dev/null && break; sleep 10; done
  remote test -f /opt/smb/READY || { echo "VM is not ready after 15 minutes; see docs/runbooks/install.md (troubleshooting)" >&2; exit 1; }
fi
stage="$out/stage"
run "$here/stage.sh" "$stage"
note "scp -r $stage/* and .env to smb@$ip:/opt/smb (.env mode 600; config/ never carries ai-routes.yaml)"
if ! $dry_run; then
  scp "${ssh_opts[@]}" -q -r "$stage"/* "smb@$ip:/opt/smb/"
  scp "${ssh_opts[@]}" -q "$out/.env" "smb@$ip:/opt/smb/.env"
  remote chmod 600 /opt/smb/.env
fi
# Only an explicit --ai-routes replaces the operator's route file, including on --resume.
if [ -n "$ai_routes" ]; then
  if $dry_run; then
    note "upload the supplied AI route file to /opt/smb/config/ai-routes.yaml"
  else
    scp "${ssh_opts[@]}" -q "$ai_routes" "smb@$ip:/opt/smb/config/ai-routes.yaml"
    remote chmod 644 /opt/smb/config/ai-routes.yaml
  fi
else
  remote "cd /opt/smb && { [ -f config/ai-routes.yaml ] || cp config/ai-routes.example.yaml config/ai-routes.yaml; }"
fi
note "docker login ${registry%%/*} with the read-only registry token (sent over stdin)"
if ! $dry_run; then
  # shellcheck disable=SC2029
  printf '%s' "$REGISTRY_TOKEN" | ssh "${ssh_opts[@]}" "smb@$ip" "$(printf 'docker login %q -u %q --password-stdin' "${registry%%/*}" "$registry_user")" >/dev/null
fi
remote "cd /opt/smb && docker compose pull && docker compose up -d"
# shellcheck disable=SC2016 # Evaluate the clock state on the VM, never on the installer host.
remote 'test "$(timedatectl show --property=NTPSynchronized --value)" = yes && systemctl is-active --quiet smb-watchdog.timer'

step 6/8 "Wait for the API, then create businesses and logins"
note "wait for https://$domain/api/v1/readyz (certificate issuance can take a minute)"
if ! $dry_run; then
  for _ in $(seq 1 60); do curl -fsS --max-time 10 "https://$domain/api/v1/readyz" >/dev/null 2>&1 && break; sleep 10; done
  curl -fsS --max-time 10 "https://$domain/api/v1/readyz" >/dev/null || { echo "API not ready after 10 minutes" >&2; exit 1; }
fi
if $demo; then
  demo_seeded=false
  if $resume && ! $dry_run &&
    curl -fsS --max-time 10 -o /dev/null "https://$domain/api/v1/public/example-cycle-works/profile" 2>/dev/null &&
    curl -fsS --max-time 10 -o /dev/null "https://$domain/api/v1/public/example-garden-store/profile" 2>/dev/null; then
    demo_seeded=true
    note "--resume: both demo businesses exist; skipping seed; demo-logins still validates their synthetic identity"
  fi
  $demo_seeded || remote 'cd /opt/smb && docker compose run --rm -T api python -m app.cli seed'
  # id and PWD must expand on the VM, not on the installer's computer.
  # shellcheck disable=SC2016
  remote 'cd /opt/smb && install -d -m 700 demo-logins && docker compose run --rm -T --user "$(id -u):$(id -g)" -v "$PWD/demo-logins:/opt/smb/demo-logins" api python -m app.cli demo-logins --out /opt/smb/demo-logins/logins.txt --email morgan@example.com --email riley@example.com'
  note "Demo login passwords stay on the VM in /opt/smb/demo-logins/logins.txt (mode 600, folder mode 700)."
else
  business_exists=false
  if $resume && ! $dry_run && curl -fsS --max-time 10 -o /dev/null "https://$domain/api/v1/public/$slug/profile" 2>/dev/null; then
    business_exists=true
    note "--resume: business '$slug' already exists; skipping bootstrap (no new temporary password is issued)"
  fi
  # printf %q quotes each value for the VM's bash, so names such as "Example's Workshop" are passed through intact.
  # shellcheck disable=SC2312
  bootstrap_args=$(printf -- '--name %q --slug %q --timezone %q --owner-email %q --owner-name %q' \
    "$business_name" "$slug" "$timezone" "$owner_email" "$owner_name")
  bootstrap_cmd="cd /opt/smb && docker compose run --rm -T api python -m app.cli bootstrap-business $bootstrap_args"
  if $business_exists; then
    temp_password=""
  elif ! $dry_run; then
    # Refuse before creating credentials if there is no private terminal to deliver them to.
    if ! { true >/dev/tty; } 2>/dev/null; then
      echo "bootstrap requires a controlling terminal to deliver the owner password securely" >&2
      exit 1
    fi
    bootstrap_out=$(remote "$bootstrap_cmd")
    temp_password=$(printf '%s\n' "$bootstrap_out" | sed -n 's/^temporary_password=//p')
    [ -n "$temp_password" ] || { echo "bootstrap did not return an owner password; recover it before continuing" >&2; exit 1; }
    # Deliver on the controlling terminal, never redirected installer output, before any fallible follow-up work.
    printf '\nFirst login for %s at https://%s\nTemporary password: %s\nStore it now; first login requires a new password and TOTP.\n' \
      "$owner_email" "$domain" "$temp_password" >/dev/tty
    unset temp_password bootstrap_out
  else
    note "ssh smb@$ip: docker compose run --rm api python -m app.cli bootstrap-business --name '$business_name' --slug '$slug' ..."
    temp_password=""
  fi
fi

step 7/8 "Smoke test and first backup to Object Storage"
remote "cd /opt/smb && docker compose exec -T backup backup.sh --kind install"
remote "cd /opt/smb && ./wait-backup-healthy.sh docker compose"
remote "cd /opt/smb && ./smoke.sh https://$domain --compose-dir /opt/smb"

step 8/8 "Finish"
if $close_ssh; then
  note "closing SSH: terraform apply with admin_cidrs = []"
  if ! $dry_run; then
    printf 'name = "%s"\nregion = "%s"\nplan = "%s"\nadmin_cidrs = []\nssh_public_key = "%s"\n' "$name" "$region" "$plan" "$(cat "$key.pub")" >"$tfvars"
    terraform -chdir="$env_dir" apply -input=false -auto-approve -state="$state" -var-file="$tfvars" >/dev/null
  fi
else
  note "SSH stays as configured. To close it later: re-run terraform apply with admin_cidrs = [] (docs/runbooks/access.md)"
fi

echo
echo "Installed: https://$domain"
if ! $dry_run; then
  if $demo; then
    note "Demo logins: /opt/smb/demo-logins/logins.txt on the VM (mode 600); sign in as Morgan or Riley and set up TOTP."
    note "Keep $out safe: it holds .env (including the backup password), the SSH key and the Terraform state."
  else
    cat <<EOT

First login for the owner ($owner_email):
  1. Open https://$domain and choose "Sign in".
  2. Username: $owner_email. The temporary password was delivered on your terminal immediately after bootstrap (none is issued for an existing business).
  3. Keycloak then asks for a new password and to set up an authenticator app (TOTP). Both are mandatory.
Keep $out safe: it holds .env (secrets), the SSH key and the Terraform state.
EOT
  fi
fi
