#!/usr/bin/env python3
"""Explicit demo deploy step: render an intentionally public password into a same-origin Keycloak asset.

Run on the VM after staging, before enabling tandem-demo. No network, packages, or containers.
Never use a customer password: the entire generated file is public by design.
"""

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", required=True)
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/smb"))
    args = parser.parse_args()
    if os.environ.get("DATA_PROFILE", "standard") == "regulated":
        raise SystemExit("refusing: DATA_PROFILE=regulated")
    # Read only the data-profile setting; never execute/source an operator's .env as shell code.
    env_file = args.compose_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key == "DATA_PROFILE" and value.strip("\"'") == "regulated":
                raise SystemExit("refusing: DATA_PROFILE=regulated")
    password = os.environ.get("DEMO_JUDGE_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("refusing: DEMO_JUDGE_PASSWORD must be set and at least 12 characters (realm policy)")
    target = args.compose_dir / "themes/tandem-demo/login/resources/js/demo-credentials.js"
    if not target.is_file():
        raise SystemExit("stage the themes directory first")
    body = "// Intentionally public synthetic judge credentials; never use a private password here.\n"
    body += "window.tandemDemoCredentials = " + json.dumps({"email": "judge@example.com", "password": password}) + ";\n"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.chmod(0o644)  # Keycloak uid 1000 must be able to serve this public asset.
    temporary.replace(target)
    print("Public judge credential asset rendered; enable tandem-demo only on a synthetic demo realm.")


if __name__ == "__main__":
    main()
