"""The smoke test checks that a production install does not publish the API description (docs/security/2026-11-03-rescan.md, F5).

With APP_ENV=prod the api answers 404 for /api/v1/openapi.json and /api/v1/docs (it still serves them with dev and test).
smoke.sh fails when either answers anything else. The rehearsal runs the api with APP_ENV=dev for the end-to-end tests,
so the check is only valid while the stack is in production mode: rehearse.sh must switch back before it calls
smoke.sh and must not call it in between. These tests use a fake curl and read rehearse.sh; they start nothing.
"""

import re
import unittest

from support import ROOT, OperationsCase


class SmokeChecksThatTheApiDescriptionIsNotPublicTests(OperationsCase):
    def smoke(self, status):
        """Run smoke.sh against a fake curl that answers `status` for the two paths and 200 for everything else."""
        self.stub(
            "curl",
            """
a = sys.argv[1:]
url = next(x for x in reversed(a) if x.startswith('http'))
path = url.split('://', 1)[1].partition('/')[2]
with open(os.environ['TRACE'], 'a') as f: f.write(url + '\\n')
status = os.environ['DESCRIPTION_STATUS'] if path in ('api/v1/openapi.json', 'api/v1/docs') else '200'
if '--write-out' in a: print(status, end='')
elif '--dump-header' in a: print('HTTP/2 ' + status + '\\r\\n\\r\\n', end='')
else: print('{}')
""",
        )
        self.env["DESCRIPTION_STATUS"] = status
        return self.run_script("smoke.sh", "https://example.test")

    def test_passes_when_both_paths_are_404(self):
        result = self.smoke("404")
        self.assertIn("PASS  API description is not public (/api/v1/openapi.json is 404)", result.stdout)
        self.assertIn("PASS  API docs are not public (/api/v1/docs is 404)", result.stdout)

    def test_fails_when_the_stack_serves_them(self):
        for status in ("200", "401", "307", "500"):
            with self.subTest(status=status):
                result = self.smoke(status)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FAIL  API description is not public", result.stderr)
                self.assertIn("FAIL  API docs are not public", result.stderr)

    def test_asks_for_exactly_those_two_paths_on_the_given_address(self):
        self.smoke("404")
        asked = self.trace.read_text().splitlines()
        self.assertIn("https://example.test/api/v1/openapi.json", asked)
        self.assertIn("https://example.test/api/v1/docs", asked)


class RehearsalRunsTheSmokeTestOnlyInProductionModeTests(unittest.TestCase):
    """rehearse.sh sets APP_ENV=dev around the end-to-end tests (the dev-only account helper refuses prod)."""

    text = (ROOT / "deploy/scripts/rehearse.sh").read_text()

    def test_no_smoke_test_runs_while_the_api_is_in_dev_mode(self):
        for match in re.finditer(r"set_env APP_ENV dev\n(.*?)set_env APP_ENV prod\n", self.text, re.S):
            self.assertNotIn("smoke.sh", match.group(1), "the smoke test would fail: dev mode serves the API description")
            self.assertNotIn("upgrade.sh", match.group(1))
        self.assertEqual(len(re.findall(r"set_env APP_ENV dev\n", self.text)), len(re.findall(r"set_env APP_ENV prod\n", self.text)))

    def test_the_smoke_test_that_follows_the_end_to_end_tests_proves_the_switch_back(self):
        after_switch = self.text.split("set_env APP_ENV prod\n", 1)[1]
        self.assertIn('up -d --wait api worker', after_switch.split("smoke.sh", 1)[0], "recreate the api before the smoke test")
        self.assertIn("smoke.sh", after_switch)


if __name__ == "__main__":
    unittest.main()
