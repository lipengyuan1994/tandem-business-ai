#!/usr/bin/env bash
# Host-side checks; Python uses only Ubuntu's existing standard library.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/watchdog.py" "$@"
