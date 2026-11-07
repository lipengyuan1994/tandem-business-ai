"""Offline staging and public credential rendering; no Docker, downloads or network."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT


class JudgeThemeTests(unittest.TestCase):
    def test_stage_mounts_an_inactive_theme_and_preserves_rendered_password_on_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            subprocess.run(["bash", str(ROOT / "deploy/scripts/stage.sh"), str(dest)], check=True, capture_output=True)
            theme = dest / "themes/tandem-demo/login"
            self.assertIn("parent=keycloak.v2", (theme / "theme.properties").read_text())
            asset = theme / "resources/js/demo-credentials.js"
            self.assertIn("= null;", asset.read_text())
            for file in (ROOT / "deploy/compose/realm-smb.prod.json", ROOT / "deploy/keycloak/realm-smb.dev.json"):
                self.assertNotEqual(json.loads(file.read_text()).get("loginTheme"), "tandem-demo")
            self.assertIn("./themes:/opt/keycloak/themes:ro", (dest / "compose.yml").read_text())
            password = 'Public-"quoted"-\\-test-2026'
            env = {**os.environ, "DEMO_JUDGE_PASSWORD": password, "DATA_PROFILE": "standard"}
            run = subprocess.run([sys.executable, str(dest / "configure-demo-judge.py"), "--demo", "--compose-dir", str(dest)], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn(password, run.stdout + run.stderr)
            generated = asset.read_text()
            config = json.loads(generated.split(" = ", 1)[1].rstrip(";\n"))
            self.assertEqual(config, {"email": "judge@example.com", "password": password})
            self.assertEqual(asset.stat().st_mode & 0o777, 0o644)
            subprocess.run(["bash", str(ROOT / "deploy/scripts/stage.sh"), str(dest)], check=True, capture_output=True)
            self.assertEqual(asset.read_text(), generated)

    def test_renderer_refuses_regulated_profiles_and_missing_short_passwords(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            subprocess.run(["bash", str(ROOT / "deploy/scripts/stage.sh"), str(dest)], check=True, capture_output=True)
            args = [sys.executable, str(dest / "configure-demo-judge.py"), "--demo", "--compose-dir", str(dest)]
            for profile, password in (("regulated", "Public-test-2026"), ("standard", ""), ("standard", "short")):
                env = {**os.environ, "DATA_PROFILE": profile, "DEMO_JUDGE_PASSWORD": password}
                run = subprocess.run(args, env=env, capture_output=True, text=True)
                self.assertNotEqual(run.returncode, 0)
            (dest / ".env").write_text("DATA_PROFILE=regulated\n")
            run = subprocess.run(args, env={**os.environ, "DATA_PROFILE": "standard", "DEMO_JUDGE_PASSWORD": "Public-test-2026"}, capture_output=True)
            self.assertNotEqual(run.returncode, 0)
