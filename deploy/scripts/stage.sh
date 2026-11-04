#!/usr/bin/env bash
# Assemble the files an installation needs into one directory, with the layout used on the VM (/opt/smb):
#   compose.yml  postgres-init.sh  realm-smb.prod.json  smoke.sh  upgrade.sh  restore.sh   (+ .env is added separately)
#   config/      AI route files from deploy/config. Never an ai-routes.yaml: that one is the operator's, created once
#                from ai-routes.example.yaml by install.sh (and upgrade.sh if missing) and never overwritten.
#   stage.sh DIR
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "$here/.." && pwd)"
dest="${1:?usage: stage.sh DIR}"

mkdir -p "$dest"
install -m 0644 "$deploy/compose/compose.prod.yml" "$dest/compose.yml"
install -m 0755 "$deploy/compose/postgres-init.sh" "$dest/postgres-init.sh"
install -m 0644 "$deploy/compose/realm-smb.prod.json" "$dest/realm-smb.prod.json"
for script in smoke.sh upgrade.sh restore.sh; do
  install -m 0755 "$here/$script" "$dest/$script"
done
mkdir -p "$dest/config"
for file in "$deploy"/config/*.yaml; do
  [ "$(basename "$file")" = ai-routes.yaml ] && continue
  install -m 0644 "$file" "$dest/config/$(basename "$file")"
done
echo "staged $(find "$dest" -maxdepth 1 -type f ! -name '.env' | wc -l | tr -d ' ') files and $(find "$dest/config" -type f | wc -l | tr -d ' ') config files in $dest"
