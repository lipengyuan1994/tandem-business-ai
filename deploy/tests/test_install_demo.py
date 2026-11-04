"""Unattended demo installer regressions; all external operations are synthetic fakes."""

import json

from support import OperationsCase


class DemoInstallTests(OperationsCase):
    def setUp(self):
        super().setUp()
        self.env.pop("DATA_PROFILE", None)
        self.args = [
            "--demo", "--name", "demo", "--domain", "auto",
            "--registry", "r.example.com/smb", "--registry-user", "synthetic",
            "--timezone", "UTC", "--object-storage-url", "https://storage.example.com",
            "--bucket", "synthetic", "--admin-cidr", "203.0.113.7/32",
        ]
        self.out = self.work / "install-demo"

    def fake_services(self):
        self.vm = self.work / "vm"
        self.vm.mkdir()
        self.env.update(
            VM=str(self.vm), VULTR_API_KEY="synthetic", REGISTRY_TOKEN="synthetic",
            OBJECT_STORAGE_ACCESS_KEY="synthetic", OBJECT_STORAGE_SECRET_KEY="synthetic",
        )
        self.stub("terraform", """
with open(os.environ['TRACE'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')
if 'output' in sys.argv: print('2001:db8::10' if sys.argv[-1] == 'ipv6' else '203.0.113.10')
""")
        self.stub("ssh-keygen", """
p = pathlib.Path(sys.argv[sys.argv.index('-f') + 1])
p.write_text('synthetic key')
p.with_suffix('.pub').write_text('synthetic public key')
""")
        self.stub("scp", """
if sys.argv[-2].endswith('/.env'):
    import shutil
    shutil.copyfile(sys.argv[-2], pathlib.Path(os.environ['VM']) / '.env')
""")
        self.stub("curl", """
with open(os.environ['TRACE'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')
if '/public/' in sys.argv[-1]:
    sys.exit(0 if (pathlib.Path(os.environ['VM']) / 'seeded').exists() else 22)
""")
        self.stub("python3", """
if len(sys.argv) > 2 and 'getaddrinfo' in sys.argv[2]:
    with open(os.environ['TRACE'], 'a') as f: f.write('DNS ' + sys.argv[-1] + '\\n')
    print(os.environ.get('DNS_IP', '203.0.113.10'))
elif os.environ.get('FORBID_DOMAIN_DERIVATION') and 'ipaddress.IPv4Address' in sys.argv[2]:
    sys.exit(91)
else: os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
""")
        self.stub("ssh", """
import subprocess
cmd = sys.argv[-1]
with open(os.environ['TRACE'], 'a') as f: f.write(json.dumps(cmd) + '\\n')
if 'bootstrap-business' in cmd: sys.exit(90)
if 'app.cli seed' in cmd:
    (pathlib.Path(os.environ['VM']) / 'seeded').touch()
elif 'app.cli demo-logins' in cmd:
    # Execute the actual remote shell command against a private fake VM directory.
    sys.exit(subprocess.run(['/bin/bash', '-c', cmd.replace('/opt/smb', os.environ['VM'])]).returncode)
elif 'backup.sh' in cmd and os.environ.get('BACKUP_FAIL'): sys.exit(17)
""")
        self.stub("docker", """
a = sys.argv[1:]
assert 'demo-logins' in a, 'unexpected Docker invocation in fake VM'
assert a[a.index('--user') + 1] == f'{os.getuid()}:{os.getgid()}'
assert a[a.index('-v') + 1] == f"{os.environ['VM']}/demo-logins:{os.environ['VM']}/demo-logins"
assert a[a.index('--out') + 1] == f"{os.environ['VM']}/demo-logins/logins.txt"
assert a[-4:] == ['--email', 'morgan@example.com', '--email', 'riley@example.com']
path = pathlib.Path(a[a.index('--out') + 1])
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f: f.write('morgan@example.com synthetic-demo-password\\n')
print('Demo login file: ' + str(path))
""")

    def install(self, *extra):
        return self.run_script("install.sh", *self.args, *extra, input="")

    def test_auto_requires_demo(self):
        self.args.remove("--demo")
        result = self.install("--dry-run")
        self.assertEqual(result.returncode, 64)
        self.assertIn("--domain auto requires --demo", result.stderr)
        self.assertFalse(self.out.exists())

    def test_demo_dry_run_needs_no_owner_and_creates_nothing(self):
        result = self.install("--dry-run", "--close-ssh")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            "<dashed-ipv4>.sslip.io", "rewrite DOMAIN and PUBLIC_URL", "resolved-domain",
            "no DNS records to create", "resolves to the VM's IPv4", "app.cli seed",
            'app.cli demo-logins', '--user "$(id -u):$(id -g)"',
            "--email morgan@example.com --email riley@example.com", "closing SSH",
            "backup.sh --kind install", "./smoke.sh", "no password output or confirmation pause",
        ):
            self.assertIn(expected, result.stdout)
        self.assertNotIn("bootstrap-business", result.stdout)
        self.assertNotIn("Create both records", result.stdout)
        self.assertEqual(result.stdout.count("/8]"), 8)
        self.assertFalse(self.out.exists())

    def test_demo_refuses_custom_businesses_and_owners(self):
        for flag, value in (
            ("--slug", "customer"), ("--business-name", "Customer"),
            ("--owner-email", "other@example.com"), ("--owner-name", "Other"),
        ):
            with self.subTest(flag=flag):
                result = self.install("--dry-run", flag, value)
                self.assertEqual(result.returncode, 64)
                self.assertIn("synthetic fixture", result.stderr)

    def test_demo_refuses_regulated_environment_and_resume_file(self):
        self.env["DATA_PROFILE"] = "regulated"
        self.assertEqual(self.install("--dry-run").returncode, 64)
        self.env.pop("DATA_PROFILE")
        self.out.mkdir()
        for value in ("regulated", '"regulated"', "'regulated'", ' "regulated" # private'):
            with self.subTest(value=value):
                (self.out / ".env").write_text(f"DATA_PROFILE={value}\n")
                result = self.install("--resume", "--dry-run")
                self.assertEqual(result.returncode, 64)
                self.assertIn("DATA_PROFILE=regulated", result.stderr)
                self.assertEqual(self.trace.read_text(), "")

    def test_fresh_unattended_demo_resolves_env_and_keeps_passwords_private(self):
        self.fake_services()
        result = self.install("--close-ssh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = (self.out / ".env").read_text()
        values = dict(line.split("=", 1) for line in saved.splitlines() if line and not line.startswith("#"))
        self.assertEqual(values["DOMAIN"], "203-0-113-10.sslip.io")
        self.assertEqual(values["PUBLIC_URL"], "https://203-0-113-10.sslip.io")
        self.assertNotIn("pending.invalid", saved)
        self.assertEqual((self.out / "resolved-domain").read_text(), "203-0-113-10.sslip.io\n")
        self.assertEqual((self.vm / ".env").read_text(), saved)
        self.assertEqual((self.out / ".env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.vm / "demo-logins").stat().st_mode & 0o777, 0o700)
        credentials = self.vm / "demo-logins/logins.txt"
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)
        output = result.stdout + result.stderr
        self.assertIn("/opt/smb/demo-logins/logins.txt", output)
        self.assertNotIn("synthetic-demo-password", output)
        for key in ("RESTIC_PASSWORD", "POSTGRES_PASSWORD", "APP_SECRET_KEY"):
            self.assertNotIn(values[key], output)
        self.assertFalse((self.out / "demo-logins").exists())
        trace = self.trace.read_text()
        self.assertIn("DNS 203-0-113-10.sslip.io", trace)
        self.assertIn("./smoke.sh https://203-0-113-10.sslip.io", trace)
        self.assertNotIn("bootstrap-business", trace)
        applies = [json.loads(line) for line in trace.splitlines() if line.startswith("[") and '"apply"' in line]
        self.assertEqual(len(applies), 2)
        self.assertIn("admin_cidrs = []", (self.out / "terraform.tfvars").read_text())

    def test_resume_keeps_resolved_domain_secrets_and_existing_logins_after_failure(self):
        self.fake_services()
        self.env["BACKUP_FAIL"] = "1"
        first = self.install()
        self.assertEqual(first.returncode, 17, first.stdout + first.stderr)
        self.assertNotIn("synthetic-demo-password", first.stdout + first.stderr)
        original = (self.out / ".env").read_bytes()
        credentials = (self.vm / "demo-logins/logins.txt").read_bytes()
        self.env.pop("BACKUP_FAIL")
        # A saved resolution must take precedence over deriving a name again.
        self.env["FORBID_DOMAIN_DERIVATION"] = "1"
        self.trace.write_text("")
        result = self.install("--resume")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("both demo businesses exist; skipping seed", result.stdout)
        self.assertEqual((self.out / ".env").read_bytes(), original)
        self.assertEqual((self.vm / "demo-logins/logins.txt").read_bytes(), credentials)
        self.assertNotIn("app.cli seed", self.trace.read_text())
        self.assertIn("app.cli demo-logins", self.trace.read_text())
        self.assertIn("DNS 203-0-113-10.sslip.io", self.trace.read_text())
        self.assertNotIn("synthetic-demo-password", result.stdout + result.stderr)

    def test_resume_repairs_placeholder_env_without_regenerating_secrets(self):
        self.fake_services()
        self.out.mkdir()
        original = "DATA_PROFILE=standard\nDOMAIN=pending.invalid\nPUBLIC_URL=https://pending.invalid\nRESTIC_PASSWORD=synthetic-backup-password\n"
        (self.out / ".env").write_text(original)
        result = self.install("--resume")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.out / ".env").read_text(),
            original.replace("pending.invalid", "203-0-113-10.sslip.io"),
        )
        self.assertIn("app.cli seed", self.trace.read_text())
        self.assertNotIn("synthetic-backup-password", result.stdout + result.stderr)

    def test_auto_still_refuses_wrong_dns_before_upload_or_seed(self):
        self.fake_services()
        self.env["DNS_IP"] = "203.0.113.99"
        self.stub("seq", "print(1)\n")
        self.stub("sleep", "pass\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not resolve to 203.0.113.10", result.stderr)
        self.assertNotIn("app.cli seed", self.trace.read_text())
        self.assertFalse((self.vm / ".env").exists())

    def test_demo_can_use_a_custom_domain(self):
        self.args[self.args.index("auto")] = "demo.example.com"
        self.fake_services()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DOMAIN=demo.example.com\n", (self.out / ".env").read_text())
        self.assertFalse((self.out / "resolved-domain").exists())
        self.assertIn("Create both records", result.stdout)

