#!/usr/bin/env bash
# Open or close SSH (port 22) in an installation's Vultr firewall group, without hand-writing Terraform commands.
#   VULTR_API_KEY=... access.sh open  --name acme [--cidr 203.0.113.7/32] [--out-dir DIR] [--yes]
#   VULTR_API_KEY=... access.sh close --name acme [--out-dir DIR] [--yes]
# `open` without --cidr uses your current public IPv4 address (/32). Existing allowed addresses are kept.
# Terraform shows its plan and asks for confirmation; read it: it must only add or remove firewall rules.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_dir="$here/../terraform/envs/demo"
action="${1:-}"
shift || true
name="" cidr="" out_root="$PWD" yes=false
while [ $# -gt 0 ]; do
  case "$1" in
    --name) name="${2:?}"; shift ;;
    --cidr) cidr="${2:?}"; shift ;;
    --out-dir) out_root="${2:?}"; shift ;;
    --yes) yes=true ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
case "$action" in open | close) ;; *) echo "usage: access.sh open|close --name NAME [--cidr CIDR] [--out-dir DIR] [--yes]" >&2; exit 64 ;; esac
[ -n "$name" ] || { echo "--name is required" >&2; exit 64; }
: "${VULTR_API_KEY:?VULTR_API_KEY must be set in the environment}"

out_root=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$out_root")
out="$out_root/install-$name"
tfvars="$out/terraform.tfvars"
state="$out/terraform.tfstate"
[ -f "$tfvars" ] && [ -f "$state" ] || { echo "no installation named '$name' in $out_root (looked for $out)" >&2; exit 1; }

current=$(sed -n 's/^admin_cidrs *= *\[\(.*\)\]/\1/p' "$tfvars" | tr -d ' "' | tr ',' '\n' | grep -v '^$' || true)
if [ "$action" = open ]; then
  if [ -z "$cidr" ]; then
    ip=$(curl -4 -fsS --max-time 10 https://ifconfig.me) || { echo "cannot detect your IPv4 address; pass --cidr" >&2; exit 1; }
    cidr="$ip/32"
  fi
  [[ "$cidr" =~ ^[0-9a-fA-F:.]+/[0-9]+$ ]] || { echo "invalid CIDR: $cidr" >&2; exit 64; }
  wanted=$(printf '%s\n%s\n' "$current" "$cidr" | grep -v '^$' | sort -u)
else
  wanted=""
fi
list=$(printf '%s\n' "$wanted" | grep -v '^$' | sed 's/.*/"&"/' | paste -sd, - || true)
echo "SSH allowed sources after this change: [${list}]"

tmp=$(mktemp "$out/.tfvars.XXXXXX")
grep -v '^admin_cidrs' "$tfvars" >"$tmp"
printf 'admin_cidrs = [%s]\n' "$list" >>"$tmp"
chmod 600 "$tmp"

export TF_VAR_vultr_api_key="$VULTR_API_KEY"
terraform -chdir="$env_dir" init -input=false >/dev/null
args=(-state="$state" -var-file="$tmp")
$yes && args+=(-auto-approve)
terraform -chdir="$env_dir" apply "${args[@]}"
mv "$tmp" "$tfvars"
echo "done. Connect with: ssh -i $out/id_ed25519 smb@$(terraform -chdir="$env_dir" output -state="$state" -raw ipv4)"
