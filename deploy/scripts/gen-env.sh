#!/usr/bin/env bash
# Create a production .env from deploy/compose/env.prod.template with freshly generated secrets.
#   gen-env.sh --domain example.com --out ./install-demo/.env [--registry host/path] [--version 0.1.0]
#              [--local-backup] [--http-port 8080] [--https-port 8443] [--app-env dev]
# Writes the file with mode 600, refuses to overwrite one, and prints only non-secret facts.
# The restic password is NOT printed here: the installer prints it once for offline storage (install.sh).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="$here/../compose/env.prod.template"
domain="" out="" registry="" version="" local_backup=false https_port="" http_port="" app_env=""

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) domain="${2:?}"; shift ;;
    --out) out="${2:?}"; shift ;;
    --registry) registry="${2:?}"; shift ;;
    --version) version="${2:?}"; shift ;;
    --https-port) https_port="${2:?}"; shift ;;
    --http-port) http_port="${2:?}"; shift ;;
    --app-env) app_env="${2:?}"; shift ;;
    --local-backup) local_backup=true ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
[ -n "$domain" ] && [ -n "$out" ] || { echo "usage: gen-env.sh --domain DOMAIN --out FILE [options]" >&2; exit 64; }
[ ! -e "$out" ] || { echo "refusing to overwrite $out" >&2; exit 1; }
[[ "$domain" =~ ^[a-zA-Z0-9.-]+$ ]] || { echo "invalid domain: $domain" >&2; exit 64; }

secret() { openssl rand -hex "${1:-24}"; }

public_url="https://$domain"
[ -z "$https_port" ] || public_url="$public_url:$https_port"

umask 077
mkdir -p "$(dirname "$out")"
{
  while IFS= read -r line; do
    case "$line" in
      DOMAIN=*) echo "DOMAIN=$domain" ;;
      PUBLIC_URL=*) echo "PUBLIC_URL=$public_url" ;;
      REGISTRY=*) echo "REGISTRY=${registry:-smb}" ;;
      VERSION=*) echo "VERSION=${version:-0.1.0}" ;;
      HTTPS_PORT=*) echo "HTTPS_PORT=${https_port:-443}" ;;
      HTTP_PORT=*) echo "HTTP_PORT=${http_port:-80}" ;;
      APP_ENV=*) echo "APP_ENV=${app_env:-prod}" ;;
      RESTIC_REPOSITORY=*) if $local_backup; then echo "RESTIC_REPOSITORY=/state/restic-repo"; else echo "$line"; fi ;;
      APP_SECRET_KEY=*) echo "APP_SECRET_KEY=$(secret 32)" ;;
      *=CHANGE_ME_*) echo "${line%%=*}=$(secret 24)" ;;
      *) echo "$line" ;;
    esac
  done <"$template"
} >"$out"
chmod 600 "$out"
if grep -v '^#' "$out" | grep -q CHANGE_ME; then
  rm -f "$out"
  echo "template still has unfilled placeholders" >&2
  exit 1
fi
echo "wrote $out (mode 600): DOMAIN=$domain PUBLIC_URL=$public_url VERSION=${version:-0.1.0}"
