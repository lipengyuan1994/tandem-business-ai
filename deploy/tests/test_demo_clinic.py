"""Demo-site admission and widget CSP checks; no listeners, downloads or cloud calls."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import unittest

from support import ROOT, OperationsCase

CADDY = shutil.which("caddy")


@unittest.skipUnless(CADDY, "native Caddy unavailable; adaptation unverified")
class DemoClinicAdaptationTests(unittest.TestCase):
    def adapt(self, domain):
        env = dict(os.environ, DOMAIN="app.example.com")
        env.pop("DEMO_SITE_DOMAIN", None)
        if domain is not None:
            env["DEMO_SITE_DOMAIN"] = domain
        result = subprocess.run(
            [CADDY, "adapt", "--config", str(ROOT / "deploy/compose/Caddyfile"), "--adapter", "caddyfile"],
            env=env, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_unset_and_compose_empty_default_create_no_demo_route_root_or_tls_subject(self):
        compose = (ROOT / "deploy/compose/compose.prod.yml").read_text()
        self.assertIn("DEMO_SITE_DOMAIN: ${DEMO_SITE_DOMAIN:-(demo_site_disabled)}", compose)
        for value in (None, "(demo_site_disabled)"):
            with self.subTest(value=value):
                config = self.adapt(value)
                encoded = json.dumps(config)
                self.assertNotIn("/opt/demo-site", encoded)
                self.assertNotIn("demo_site_disabled", encoded)
                self.assertNotIn("clinic.example.com", encoded)
                self.assertIn('"app.example.com"', encoded)

    def test_enabled_host_isolated_from_main_root_with_strict_widget_csp(self):
        config = self.adapt("clinic.example.com")
        routes = [r for s in config["apps"]["http"]["servers"].values() for r in s["routes"]]
        route = next(r for r in routes if {"host": ["clinic.example.com"]} in r.get("match", []))
        encoded = json.dumps(route)
        self.assertIn("/opt/demo-site/dental", encoded)
        self.assertNotIn("reverse_proxy", encoded)
        self.assertNotIn('"/srv"', encoded)
        self.assertNotIn("disable_certificates", json.dumps(config))
        # The site root is a directory: the missing-file 404 must also try index.html, or "/" answers 404.
        self.assertIn('"try_files": ["{http.request.uri.path}", "{http.request.uri.path}index.html"]', encoded)

        def objects(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from objects(child)
            elif isinstance(value, list):
                for child in value:
                    yield from objects(child)

        headers = next(o["response"] for o in objects(route) if o.get("handler") == "headers")
        self.assertIn("Server", headers["delete"])
        for name, value in (("Strict-Transport-Security", "max-age=31536000"),
                            ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")):
            self.assertEqual(headers["set"][name], [value])
        csp = headers["set"]["Content-Security-Policy"][0]
        for directive in ("script-src 'self' https://app.example.com", "frame-src 'self' https://app.example.com",
                          "connect-src 'self' https://app.example.com", "frame-ancestors 'none'", "object-src 'none'"):
            self.assertIn(directive + ";", csp + ";")
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        # A changed loader must not silently lose its stylesheet under this strict parent policy.
        loader = (ROOT / "frontend/public/embed.js").read_text()
        style = json.loads(re.search(r'style.textContent = (".*?");', loader).group(1))
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        self.assertIn(f"style-src 'self' 'sha256-{digest}'", csp)


class DemoClinicSmokeTests(OperationsCase):
    def run_smoke(self, domain="", status="200", csp=True, compose=False):
        self.env.update(DEMO_SITE_DOMAIN=domain, CLINIC_STATUS=status, CLINIC_CSP="1" if csp else "0",
                        SMOKE_DEMO_WAIT_SECONDS="0")
        self.stub("curl", """
a = sys.argv[1:]
url = next(x for x in reversed(a) if x.startswith('http'))
with open(os.environ['TRACE'], 'a') as f: f.write(url + '\\n')
demo = url.startswith('https://clinic.example.com/')
status = os.environ['CLINIC_STATUS'] if demo else '200'
if '--write-out' in a: print(status, end='')
elif '--dump-header' in a:
    print('HTTP/2 ' + status)
    if demo and os.environ['CLINIC_CSP'] == '1': print("Content-Security-Policy: default-src 'self'; frame-ancestors 'none'")
else: print('{}')
""")
        args = ("--compose-dir", str(self.work)) if compose else ()
        return self.run_script("smoke.sh", "https://app.example.com", *args)

    def test_disabled_skips_without_requesting_clinic(self):
        for domain in ("", "(demo_site_disabled)"):
            result = self.run_smoke(domain)
            self.assertIn("SKIP  demo clinic site (DEMO_SITE_DOMAIN unset)", result.stdout)
            self.assertNotIn("clinic.example.com", self.trace.read_text())

    def test_enabled_checks_status_and_csp(self):
        result = self.run_smoke("clinic.example.com")
        self.assertIn("PASS  demo clinic site answers 200", result.stdout)
        self.assertIn("PASS  demo clinic Content-Security-Policy", result.stdout)
        self.assertIn("https://clinic.example.com/", self.trace.read_text())

    def test_compose_resolved_env_enables_site_without_exported_variable(self):
        self.stub("docker", """
a = sys.argv[1:]
if 'config' in a and '--format' in a:
    print(json.dumps({'services': {'caddy': {'environment': {'DEMO_SITE_DOMAIN': 'clinic.example.com'}}}}))
elif 'ps' in a and 'json' in a: print('[]')
""")
        result = self.run_smoke(compose=True)
        self.assertIn("PASS  demo clinic site answers 200", result.stdout)
        self.assertNotIn("SKIP  demo clinic site", result.stdout)

    def test_compose_resolution_failure_is_reported(self):
        self.stub("docker", "sys.exit(1)")
        result = self.run_smoke(compose=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL  could not resolve Compose configuration for demo site", result.stderr)

    def test_bad_status_or_missing_csp_fail_the_corresponding_check(self):
        for status, csp, check in (("404", True, "site answers 200"), ("200", False, "Content-Security-Policy")):
            with self.subTest(status=status, csp=csp):
                result = self.run_smoke("clinic.example.com", status, csp)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FAIL  demo clinic " + check, result.stderr)
