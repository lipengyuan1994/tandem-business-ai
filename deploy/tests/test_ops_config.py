"""Offline YAML and host policy checks; real Compose/cloud-init schemas need their own tools."""

import json
import re
import subprocess
import unittest

from support import ROOT, OperationsCase


def yaml_load(text):
    # Reuse the project's locked, existing PyYAML in its verified native venv; install nothing.
    python = ROOT / "backend/.venv/bin/python"
    if not python.exists():
        raise unittest.SkipTest("project PyYAML runtime is unavailable")
    result = subprocess.run(  # noqa: S603 - repository-controlled script and synthetic paths
        [str(python), "-c", "import json,sys,yaml; print(json.dumps(yaml.safe_load(sys.stdin.read())))"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def cloud_config(cidrs):
    template = (ROOT / "deploy/terraform/modules/vultr-single-vm/user-data.yaml.tftpl").read_text()
    template = template.replace("${ssh_public_key}", "ssh-ed25519 synthetic example")
    template = re.sub(
        r"%\{ for cidr in admin_cidrs ~\}.*?%\{ endfor ~\}",
        "\n".join(f"  - ufw allow from {cidr} to any port 22 proto tcp" for cidr in cidrs),
        template,
        flags=re.S,
    )
    return yaml_load(template)


class HostConfigTests(unittest.TestCase):
    def test_cloud_init_parses_with_empty_ipv4_and_ipv6_admin_sources(self):
        for cidrs in ([], ["203.0.113.7/32"], ["203.0.113.7/32", "2001:db8::/64"]):
            config = cloud_config(cidrs)
            self.assertFalse(config["ssh_pwauth"])
            self.assertTrue(all(isinstance(cmd, str) for cmd in config["runcmd"]))
            self.assertNotIn("ufw allow 22/tcp", config["runcmd"])
            for cidr in cidrs:
                self.assertIn(f"ufw allow from {cidr} to any port 22 proto tcp", config["runcmd"])
            files = {entry["path"]: entry["content"] for entry in config["write_files"]}
            ssh = files["/etc/ssh/sshd_config.d/00-smb.conf"]
            self.assertIn("PasswordAuthentication no", ssh)
            self.assertIn("KbdInteractiveAuthentication no", ssh)
            self.assertIn("PermitRootLogin prohibit-password", ssh)
            self.assertIn("timedatectl set-ntp true", config["runcmd"])
            self.assertTrue(any("NTPSynchronized" in cmd and "exit 1" in cmd for cmd in config["runcmd"]))
            self.assertIn("OnCalendar=*-*-* *:00/5:00", files["/etc/systemd/system/smb-watchdog.timer"])
            self.assertIn("Persistent=true", files["/etc/systemd/system/smb-watchdog.timer"])
            self.assertIn("SystemMaxUse=256M", files["/etc/systemd/journald.conf.d/60-smb.conf"])
            installer = (ROOT / "deploy/scripts/install-host-ops.sh").read_text()
            for path in (
                "/etc/systemd/system/smb-watchdog.timer",
                "/etc/systemd/system/smb-watchdog.service",
                "/etc/systemd/journald.conf.d/60-smb.conf",
            ):
                self.assertIn(files[path].strip(), installer)

    def test_terraform_passes_same_admin_sources_to_host_and_provider(self):
        main = (ROOT / "deploy/terraform/modules/vultr-single-vm/main.tf").read_text()
        self.assertIn("admin_cidrs    = var.admin_cidrs", main)
        self.assertIn("for cidr in var.admin_cidrs", main)

    def test_compose_yaml_preserves_services_and_adds_drain_and_transaction_timeout(self):
        compose = yaml_load((ROOT / "deploy/compose/compose.prod.yml").read_text())
        services = compose["services"]
        self.assertEqual(set(services), {"postgres", "api", "worker", "migrate", "caddy", "backup", "keycloak"})
        for kind in ("api", "worker"):
            self.assertGreaterEqual(int(services[kind]["stop_grace_period"].rstrip("s")), 100)
        self.assertIn("idle_in_transaction_session_timeout=60s", services["postgres"]["command"])
        self.assertEqual([name for name, detail in services.items() if detail.get("ports")], ["caddy"])
        self.assertNotIn("ALERT_WEBHOOK_URL", services["api"]["environment"])


class HostStagingTests(OperationsCase):
    def test_staging_contains_existing_install_oneoff_and_watchdog(self):
        self.stage()
        for name in ("watchdog.sh", "watchdog.py", "install-host-ops.sh"):
            self.assertTrue((self.stack / name).exists())

    def test_generated_env_has_empty_commented_webhook(self):
        path = self.work / "synthetic.env"
        result = self.run_script("gen-env.sh", "--domain", "example.com", "--out", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = path.read_text()
        self.assertIn("\nALERT_WEBHOOK_URL=\n", contents)
        self.assertIn("test with watchdog.sh --test", contents)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
