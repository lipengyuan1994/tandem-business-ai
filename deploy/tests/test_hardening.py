"""Offline deployment regressions: python3 -m unittest discover -s deploy/tests -v.

External commands are fakes; these tests never provision, download, or contact a service.
Set SMB_TEST_ROOT to a separate source tree to run the same regressions against older code.
"""

import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from support import ROOT, OperationsCase


class OperationsTests(OperationsCase):
    def test_restore_promotes_with_or_without_original_database(self):
        self.stub(
            "restic",
            """
a = sys.argv
dest = pathlib.Path(a[a.index('--target') + 1])
(dest / a[a.index('--include') + 1]).write_text('synthetic dump')
""",
        )
        self.stub("pg_restore", "pass\n")
        self.stub("jq", "print('{}')\n")
        self.stub(
            "psql",
            """
sql = sys.argv[sys.argv.index('--command') + 1]
with open(os.environ['TRACE'], 'a') as f: f.write(sql + '\\n')
db = os.environ['RESTORE_DB']
if 'from pg_database' in sql:
    if os.environ['ORIGINAL_EXISTS'] == 'yes': print(1)
elif sql.startswith('alter database "' + db + '"') and os.environ['ORIGINAL_EXISTS'] == 'no':
    sys.exit(1)
elif 'select version_num' in sql: print('revision_1')
elif 'information_schema.tables' in sql: print('synthetic_table|2')
""",
        )
        for db in ("smb", "keycloak"):
            for exists in ("yes", "no"):
                with self.subTest(db=db, original=exists):
                    self.trace.write_text("")
                    self.env.update(
                        RESTORE_DB=db,
                        ORIGINAL_EXISTS=exists,
                        POSTGRES_PASSWORD="synthetic",
                        BACKUP_STATE_DIR=str(self.work / "state"),
                    )
                    result = self.run_script("lib/restore-db.sh", "--db", db, "--swap")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    sql = self.trace.read_text()
                    self.assertIn(
                        f'alter database "{db}_restore" rename to "{db}"', sql
                    )
                    self.assertEqual(
                        f'alter database "{db}" rename to' in sql, exists == "yes"
                    )

    def backup_stubs(self):
        self.env.update(
            BACKUP_STATE_DIR=str(self.work / "state"),
            RESTIC_REPOSITORY="/synthetic",
            SMB_OWNER_PASSWORD="synthetic",
            KEYCLOAK_DB_PASSWORD="synthetic",
        )
        self.stub(
            "flock",
            """
with open(os.environ['TRACE'], 'a') as f: f.write('flock ' + ' '.join(sys.argv[1:]) + '\\n')
sys.exit(1 if '-n' in sys.argv or os.environ.get('LOCK_FAIL') else 0)
""",
        )
        self.stub(
            "pg_dump",
            """
pathlib.Path(sys.argv[sys.argv.index('--file') + 1]).write_text('synthetic dump')
with open(os.environ['TRACE'], 'a') as f: f.write('dump\\n')
""",
        )
        self.stub(
            "restic",
            """
if sys.argv[1] == 'snapshots': print('[{"short_id":"synthetic"}]')
with open(os.environ['TRACE'], 'a') as f: f.write('restic ' + sys.argv[1] + '\\n')
""",
        )
        self.stub("stat", "print(pathlib.Path(sys.argv[-1]).stat().st_size)\n")
        self.stub(
            "date",
            """
import datetime, time
if sys.argv[-1] == '+%s': print(int(time.time()))
else: print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
""",
        )

    def test_safety_backups_wait_and_take_their_own_snapshot(self):
        self.backup_stubs()
        for kind in ("pre-upgrade", "pre-restore", "install", "final", "manual"):
            with self.subTest(kind=kind):
                self.trace.write_text("")
                result = self.run_script("backup.sh", "--kind", kind)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.trace.read_text().splitlines()[0], "flock 9")
                self.assertEqual(self.trace.read_text().count("dump\n"), 2)
                status = json.loads(
                    (self.work / "state/backup-status.json").read_text()
                )
                self.assertEqual((status["result"], status["kind"]), ("ok", kind))

    def test_failed_safety_lock_does_not_report_success(self):
        self.backup_stubs()
        self.env["LOCK_FAIL"] = "1"
        result = self.run_script("backup.sh", "--kind", "pre-upgrade")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("dump\n", self.trace.read_text())

    def test_scheduled_contention_still_skips(self):
        self.backup_stubs()
        result = self.run_script("backup.sh", "--kind", "scheduled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dump\n", self.trace.read_text())

    def test_rollback_persists_migration_image_for_fresh_start(self):
        self.stage()
        self.stub(
            "docker",
            """
a = sys.argv[1:]
env = dict(line.split('=', 1) for line in pathlib.Path('.env').read_text().splitlines() if '=' in line)
with open(os.environ['TRACE'], 'a') as f: f.write(' '.join(a) + '\\n')
if 'config' in a: print('migrate\\napi\\nworker\\nbackup')
elif 'ps' in a: print('api|(healthy)\\nworker|(healthy)')
elif 'run' in a and 'migrate' in a:
    if os.environ.get('MIGRATION_FAIL'): sys.exit(1)
    pathlib.Path('schema').write_text(env.get('MIGRATION_VERSION', env['VERSION']))
elif 'up' in a:
    if env['VERSION'] == '2.0.0': sys.exit(1)  # Migration succeeded; new app fails.
    if '--no-deps' not in a and pathlib.Path('schema').exists():
        sys.exit(0 if env.get('MIGRATION_VERSION', env['VERSION']) == pathlib.Path('schema').read_text() else 1)
""",
        )
        result = subprocess.run(
            ["/bin/bash", str(self.stack / "upgrade.sh"), "2.0.0"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        saved = (self.stack / ".env").read_text()
        self.assertIn("VERSION=1.0.0\n", saved)
        self.assertIn("MIGRATION_VERSION=2.0.0\n", saved)
        config = (self.stack / "compose.yml").read_text()
        self.assertIn("${MIGRATION_VERSION:-${VERSION:?VERSION is required}}", config)
        # Independent invocation, with normal dependencies, models down/up or systemd restart.
        restart = subprocess.run(
            [str(self.bin / "docker"), "compose", "up", "-d"],
            cwd=self.stack,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(restart.returncode, 0, restart.stderr)
        # A failed subsequent migration must retain the already required newer image.
        self.env["MIGRATION_FAIL"] = "1"
        subprocess.run(
            ["/bin/bash", str(self.stack / "upgrade.sh"), "3.0.0"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertIn("MIGRATION_VERSION=2.0.0\n", (self.stack / ".env").read_text())

    def test_restore_rejects_either_rotated_client_secret_before_startup(self):
        self.stage()
        self.env.update(
            OIDC_CLIENT_SECRET="current-app",
            KEYCLOAK_ADMIN_CLIENT_SECRET="current-admin",
        )
        self.stub(
            "docker",
            """
a = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as f: f.write(' '.join(a[:4]) + ' | ' + ('up' if 'up' in a else '') + '\\n')
if '--entrypoint' in a and 'python' in a:
    code = a[a.index('-c') + 1]
    exec(code)
elif 'psql' in a:
    sql = sys.stdin.read()
    pathlib.Path(os.environ['TRACE'] + '.sql').write_text(sql)
    # Independently compare each restored secret with the SQL generated from the current environment.
    import hashlib, sqlite3
    secrets = ['current-app', 'current-admin']
    if os.environ.get('ROTATED_CLIENT'):
        secrets[int(os.environ['ROTATED_CLIENT'])] = 'snapshot-secret'
    db = sqlite3.connect(':memory:')
    db.executescript('create table realm(id text, name text); create table client(realm_id text, client_id text, secret text);')
    db.execute('insert into realm values (?, ?)', ('realm-1', 'smb'))
    db.executemany('insert into client values (?, ?, ?)', [('realm-1', 'smb-app', secrets[0]), ('realm-1', 'smb-admin', secrets[1])])
    db.create_function('convert_to', 2, lambda s, enc: s.encode(enc))
    db.create_function('sha256', 1, lambda b: hashlib.sha256(b).digest())
    db.create_function('encode', 2, lambda b, enc: b.hex())
    print(db.execute(sql).fetchone()[0])
""",
        )
        for rotated in ("0", "1", ""):
            with self.subTest(rotated=rotated):
                self.env["ROTATED_CLIENT"] = rotated
                self.trace.write_text("")
                result = subprocess.run(
                    ["/bin/bash", str(self.stack / "restore.sh"), "synthetic", "--yes"],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if rotated:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertNotIn(" | up\n", self.trace.read_text())
                    self.assertNotIn("restore complete", result.stdout)
                    self.assertIn("reconcile", result.stdout.lower())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("restore complete", result.stdout)
                sql_path = Path(str(self.trace) + ".sql")
                self.assertTrue(
                    sql_path.is_file(), "restore never checked the client secrets"
                )
                sql = sql_path.read_text()
                self.assertIn("r.name = 'smb'", sql)
                for secret in ("current-app", "current-admin"):
                    self.assertNotIn(secret, sql)
                    self.assertIn(hashlib.sha256(secret.encode()).hexdigest(), sql)

    def install_args(self):
        return [
            "--name",
            "check",
            "--domain",
            "check.example.com",
            "--registry",
            "r.example.com/smb",
            "--registry-user",
            "synthetic",
            "--owner-email",
            "owner@example.com",
            "--owner-name",
            "Owner",
            "--business-name",
            "Synthetic",
            "--slug",
            "check",
            "--timezone",
            "UTC",
            "--object-storage-url",
            "https://storage.example.com",
            "--bucket",
            "synthetic",
            "--admin-cidr",
            "203.0.113.7/32",
            "--out-dir",
            "./nested/installs",
        ]

    def test_installer_relative_output_is_absolute_without_creating_files(self):
        result = self.run_script("install.sh", "--dry-run", *self.install_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.work / "nested/installs/install-check"
        self.assertIn(f"-state={expected}/terraform.tfstate", result.stdout)
        self.assertIn(f"-var-file={expected}/terraform.tfvars", result.stdout)
        self.assertFalse((self.work / "nested").exists())

    def test_access_passes_absolute_paths_to_terraform_chdir(self):
        out = self.work / "nested/installs/install-check"
        out.mkdir(parents=True)
        (out / "terraform.tfvars").write_text('name = "check"\nadmin_cidrs = []\n')
        (out / "terraform.tfstate").write_text("synthetic state")
        self.env["VULTR_API_KEY"] = "synthetic"
        self.stub(
            "terraform",
            """
for arg in sys.argv:
    if arg.startswith(('-state=', '-var-file=')):
        path = pathlib.Path(arg.split('=', 1)[1])
        if not path.is_absolute() or not path.exists(): sys.exit(9)
if 'output' in sys.argv: print('203.0.113.10')
""",
        )
        result = self.run_script(
            "access.sh",
            "close",
            "--name",
            "check",
            "--out-dir",
            "./nested/installs",
            "--yes",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_owner_password_delivered_before_backup_failure_only_on_terminal(self):
        out = self.work / "nested/installs/install-check"
        out.mkdir(parents=True)
        (out / ".env").write_text("VERSION=1.0.0\n")
        (out / "id_ed25519").write_text("synthetic key")
        (out / "id_ed25519.pub").write_text("synthetic public key")
        self.env.update(
            VULTR_API_KEY="synthetic",
            REGISTRY_TOKEN="synthetic",
            OBJECT_STORAGE_ACCESS_KEY="synthetic",
            OBJECT_STORAGE_SECRET_KEY="synthetic",
        )
        self.stub("terraform", "if 'output' in sys.argv: print('203.0.113.10')\n")
        self.stub("scp", "pass\n")
        self.stub("curl", "sys.exit(1 if 'profile' in sys.argv[-1] else 0)\n")
        # Use a local Python stub for DNS; all other python3 invocations must retain normal behavior.
        self.stub(
            "python3",
            """
if 'getaddrinfo' in sys.argv[2]: print('203.0.113.10')
else: os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
""",
        )
        self.stub(
            "ssh",
            """
cmd = sys.argv[-1]
if 'bootstrap-business' in cmd: print('temporary_password=synthetic-owner-password')
elif 'backup.sh' in cmd: sys.exit(17)
""",
        )
        # Substitute only the terminal device: sandbox policy forbids writes to /dev/tty.
        # Keep stdout/stderr captured independently to verify that logs never receive the password.
        copied = self.work / "deploy-copy"
        shutil.copytree(ROOT / "deploy", copied)
        scripts = copied / "scripts"
        installer = scripts / "install.sh"
        terminal = self.work / "owner-terminal"
        terminal.touch(mode=0o600)
        installer.write_text(installer.read_text().replace("/dev/tty", str(terminal)))
        result = subprocess.run(
            ["/bin/bash", str(installer), "--resume", *self.install_args()],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertIn("synthetic-owner-password", terminal.read_text())
        self.assertNotIn("synthetic-owner-password", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
