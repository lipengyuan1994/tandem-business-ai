#!/usr/bin/env bash
# Local installer AI configuration: validate routes or privately update an installation's .env.
# No provider calls. Uses the backend loader when available, otherwise the existing PyYAML parser.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "$here/.." && pwd)"
action="${1:-}" file="${2:-}"
[ -n "$file" ] || { echo "usage: install-ai.sh validate ROUTES_FILE | set-key ENV_FILE" >&2; exit 64; }
case "$action" in
  validate)
    validator=python3
    backend="$deploy/../backend"
    [ ! -x "$backend/.venv/bin/python" ] || validator="$backend/.venv/bin/python"
    if ! PYTHONDONTWRITEBYTECODE=1 "$validator" - "$backend" "$file" <<'PY'
import pathlib, sys
backend, path = map(pathlib.Path, sys.argv[1:])
try:
    if (backend / ".venv/bin/python").exists():
        sys.path.insert(0, str(backend))
        from app.modules.ai.gateway.routes import load_routes_file
        load_routes_file(path)
    else:
        import yaml
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or not all(isinstance(data.get(k), dict) for k in ("providers", "routes")):
            raise ValueError("expected providers and routes mappings")
except Exception:
    sys.exit(1)
PY
    then
      echo "--ai-routes validation failed: use a readable YAML route file with providers and routes; validation requires the backend venv or Python with the project's PyYAML" >&2
      exit 64
    fi
    ;;
  set-key)
    python3 - "$file" <<'PY'
import os, pathlib, sys, tempfile
path = pathlib.Path(sys.argv[1])
key = os.environ["VULTR_INFERENCE_API_KEY"]
if any(c in key for c in "\r\n\0"):
    sys.exit("VULTR_INFERENCE_API_KEY must be a single-line value")
lines = path.read_text().splitlines()
hosts = []
for line in lines:
    if line.startswith("EGRESS_ALLOWLIST="):
        hosts.extend(h.strip() for h in line.split("=", 1)[1].strip().strip("\"'").split(",") if h.strip())
if "api.vultrinference.com" not in hosts:
    hosts.append("api.vultrinference.com")
# Compose single-quoted values are literal, including dollar signs; escape quotes and backslashes.
quoted = "'" + key.replace("\\", "\\\\").replace("'", "\\'") + "'"
lines = [line for line in lines if not line.startswith(("VULTR_INFERENCE_API_KEY=", "EGRESS_ALLOWLIST="))]
lines += ["VULTR_INFERENCE_API_KEY=" + quoted, "EGRESS_ALLOWLIST=" + ",".join(hosts)]
fd, tmp = tempfile.mkstemp(prefix=".env.", dir=path.parent)
try:
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
    ;;
  *) echo "unknown install-ai action" >&2; exit 64 ;;
esac
