"""No container runs as root with more than it needs (docs/security/2026-11-03-rescan.md, F4).

The web container (Caddy) is the only internet-facing process and used to run as root with Docker's default
capabilities. It now runs as the fixed user 10003 with every capability dropped except NET_BIND_SERVICE, a read-only root
filesystem and no-new-privileges. NET_BIND_SERVICE stays because the upstream caddy binary carries it as a file
capability (`cap_net_bind_service=ep`): with it missing from the container's bounding set the kernel refuses to start the
binary ("exec /usr/bin/caddy: operation not permitted", verified for every combination with cap_drop ALL).

Volumes that the former root container filled still belong to root, and the new container then serves no certificate.
upgrade.sh hands them to uid 10003 before it starts the new version.

The compose tests ask Docker Compose to resolve the file (anchors, overrides), so they skip without the docker CLI (`config`
needs no running daemon). The rest is static or uses fakes, like the other deployment regressions.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT, OperationsCase

DOCKER = shutil.which("docker")
DOCKERFILES = ("api", "web", "backup")
WEB_UID = 10003
CHOWN_COMMAND = (
    "run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH "
    f"--entrypoint chown caddy -R -h {WEB_UID}:{WEB_UID} /data /config"
)


def compose_has_plugin():
    if not DOCKER:
        return False
    result = subprocess.run([DOCKER, "compose", "version"], capture_output=True, text=True, timeout=30, check=False)
    return result.returncode == 0


def resolved_services():
    """`docker compose config` of the production file with a generated, throwaway .env, as a dict by service."""
    with tempfile.TemporaryDirectory() as temp:
        env = Path(temp) / ".env"
        subprocess.run(
            [str(ROOT / "deploy/scripts/gen-env.sh"), "--domain", "example.com", "--local-backup", "--out", str(env)],
            capture_output=True,
            check=True,
            timeout=30,
        )
        result = subprocess.run(
            [DOCKER, "compose", "-f", str(ROOT / "deploy/compose/compose.prod.yml"), "--env-file", str(env), "config", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    return json.loads(result.stdout)["services"]


def runtime_stage(dockerfile):
    return (ROOT / f"deploy/docker/{dockerfile}.Dockerfile").read_text().split("AS runtime")[-1]


class ImagesRunAsAnUnprivilegedUserTests(unittest.TestCase):
    def last_user(self, dockerfile):
        users = re.findall(r"(?m)^USER\s+(\S+)", runtime_stage(dockerfile))
        self.assertTrue(users, f"{dockerfile}.Dockerfile has no USER instruction (the image would run as root)")
        return users[-1]

    def test_every_image_ends_as_a_named_non_root_user(self):
        for name in DOCKERFILES:
            with self.subTest(image=name):
                self.assertNotIn(self.last_user(name), ("root", "0", "0:0"))

    def test_web_image_creates_the_caddy_user_and_owns_the_two_volume_mount_points(self):
        text = runtime_stage("web")
        self.assertEqual(self.last_user("web"), "caddy")
        self.assertRegex(text, rf"addgroup -S -g {WEB_UID} caddy")
        self.assertRegex(text, rf"adduser -S -u {WEB_UID} -G caddy\b")
        # A new named volume takes its owner from the image directory it is mounted on: this is what makes a fresh
        # installation work without any chown step.
        self.assertIn("chown -R caddy:caddy /data /config", text)
        # USER comes before the build-time `caddy validate`, so the build proves the user can load the Caddyfile.
        self.assertLess(text.index("USER caddy"), text.index("caddy validate"))

    def test_web_image_keeps_the_stock_binary(self):
        # The file capability of /usr/bin/caddy is why NET_BIND_SERVICE must stay in compose.prod.yml; removing it
        # (setcap -r) would break the rollback to an older image under the new compose file.
        self.assertNotIn("setcap", runtime_stage("web"))


@unittest.skipUnless(compose_has_plugin(), "docker compose is not installed")
class ComposeHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.services = resolved_services()

    def test_every_service_drops_all_capabilities_and_cannot_gain_privileges(self):
        for name, service in self.services.items():
            with self.subTest(service=name):
                self.assertEqual(service.get("cap_drop"), ["ALL"])
                self.assertIn("no-new-privileges:true", service.get("security_opt") or [])
                self.assertFalse(service.get("privileged"))
                for key in ("pid", "ipc", "network_mode", "userns_mode"):
                    self.assertNotEqual(service.get(key), "host", key)

    def test_only_caddy_adds_a_capability_and_it_is_the_one_its_binary_needs(self):
        added = {name: service["cap_add"] for name, service in self.services.items() if service.get("cap_add")}
        self.assertEqual(added, {"caddy": ["NET_BIND_SERVICE"]})

    def test_caddy_runs_as_the_unprivileged_user_with_a_read_only_filesystem(self):
        caddy = self.services["caddy"]
        self.assertEqual(caddy["user"], f"{WEB_UID}:{WEB_UID}")
        self.assertIs(caddy["read_only"], True)
        self.assertFalse(caddy.get("tmpfs"), "Caddy writes only to its two volumes")
        self.assertEqual(
            sorted((volume["type"], volume["source"], volume["target"]) for volume in caddy["volumes"]),
            [("volume", "caddy_config", "/config"), ("volume", "caddy_data", "/data")],
        )
        self.assertEqual(sorted(port["target"] for port in caddy["ports"]), [80, 443])

    def test_the_uid_in_compose_is_the_uid_the_image_creates(self):
        self.assertEqual(self.services["caddy"]["user"].split(":")[0], str(WEB_UID))
        self.assertRegex(runtime_stage("web"), rf"adduser -S -u {WEB_UID}\b")

    def test_postgres_is_started_as_its_own_user_not_as_root(self):
        self.assertEqual(self.services["postgres"]["user"], "999:999")

    def test_application_containers_have_a_read_only_root_filesystem(self):
        for name in ("api", "worker", "migrate"):
            with self.subTest(service=name):
                self.assertIs(self.services[name]["read_only"], True)

    def test_only_caddy_publishes_ports_and_nothing_mounts_the_docker_socket(self):
        self.assertEqual(sorted(name for name, service in self.services.items() if service.get("ports")), ["caddy"])
        for name, service in self.services.items():
            for volume in service.get("volumes") or []:
                self.assertNotIn("docker.sock", volume.get("source", ""), name)


class UpgradeHandsCaddyVolumesToItsUserTests(OperationsCase):
    """upgrade.sh against a fake docker: the chown must come after the migration and before the new version starts."""

    def fake_docker(self):
        self.stub(
            "docker",
            """
a = sys.argv[1:]
env = dict(line.split('=', 1) for line in pathlib.Path('.env').read_text().splitlines() if '=' in line)
with open(os.environ['TRACE'], 'a') as f:
    f.write(('UP version=' + env['VERSION'] if 'up' in a else ' '.join(a)) + '\\n')
if 'config' in a: print('migrate\\napi\\nworker\\nbackup\\ncaddy')
elif 'ps' in a:
    print('backup|healthy' if a[-1] == 'backup' else 'api|Up (healthy)\\nworker|Up (healthy)\\nbackup|Up (healthy)\\ncaddy|Up (healthy)')
elif 'chown' in a and os.environ.get('CHOWN_FAIL'): sys.exit(1)
""",
        )

    def upgrade(self):
        return subprocess.run(
            ["/bin/bash", str(self.stack / "upgrade.sh"), "2.0.0"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def trace_lines(self):
        return self.trace.read_text().splitlines()

    def test_volumes_are_handed_over_after_the_migration_and_before_the_new_version_starts(self):
        self.stage()
        self.fake_docker()
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.trace_lines()
        chown = [i for i, line in enumerate(lines) if line.endswith(CHOWN_COMMAND)]
        self.assertEqual(len(chown), 1, lines)
        migrate = next(i for i, line in enumerate(lines) if " run --rm -T migrate" in f" {line}")
        first_up = next(i for i, line in enumerate(lines) if line.startswith("UP version=2.0.0"))
        self.assertLess(migrate, chown[0])
        self.assertLess(chown[0], first_up)

    def test_a_failed_handover_rolls_back_and_never_starts_the_new_version(self):
        self.stage()
        self.fake_docker()
        self.env["CHOWN_FAIL"] = "1"
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ROLLING BACK", result.stdout)
        self.assertIn("VERSION=1.0.0\n", (self.stack / ".env").read_text())
        ups = [line for line in self.trace_lines() if line.startswith("UP version=")]
        self.assertTrue(ups, "the rollback should start the previous version")
        self.assertEqual({line for line in ups}, {"UP version=1.0.0"}, "the new version must not start on volumes it cannot read")

    def test_the_handover_is_the_documented_command(self):
        # docs/runbooks/upgrade.md gives the same command for an upgrade done by hand.
        runbook = (ROOT / "docs/runbooks/upgrade.md").read_text()
        flattened = re.sub(r"\\\n\s*", "", runbook)
        self.assertIn(CHOWN_COMMAND.replace("run --rm", "docker compose run --rm", 1), re.sub(r"\s+", " ", flattened))


class RehearsalCoversTheRootOwnedVolumeTests(unittest.TestCase):
    def test_the_rehearsal_starts_the_upgrade_from_root_owned_caddy_volumes(self):
        text = (ROOT / "deploy/scripts/rehearse.sh").read_text()
        before_upgrade = text.split('step "upgrade $v1 -> $v2', 1)[0]
        self.assertIn("-R -h 0:0 /data /config", before_upgrade)
        self.assertIn("same certificate", text.split('step "upgrade $v1 -> $v2', 1)[1].split('step "failed upgrade', 1)[0])


if __name__ == "__main__":
    unittest.main()
