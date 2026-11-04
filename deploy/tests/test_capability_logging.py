"""Capability tokens must not reach Caddy's logs (docs/security/2026-11-03-rescan.md).

Caddy writes no access log, but its error log records the URI and the Referer header of every request that fails, for
example the 502 it answers while the api restarts. Supplier quote links carry a bearer token in their path
(/q/<token> and /api/v1/public/quotes/<token>), the OIDC callback a one-time code and state, and the marketing
unsubscribe link a token, in the query string. Both Caddyfiles mask these before anything is written.

The live tests run an installed Caddy with each Caddyfile (see caddy_harness.py), once with the api stopped and once
with it answering, send requests that carry recognisable secrets, and read everything Caddy wrote. They skip when Caddy
is not installed; the static tests do not.
"""

import base64
import re
import unittest

from caddy_harness import CADDY, CADDYFILES, COMPOSE, caddy

# Shaped like a real token: base64url payload, a dot, base64url signature.
PAYLOAD = (
    base64.urlsafe_b64encode(
        b"quote1|11111111-2222-4333-8444-555555555555|66666666-7777-4888-9999-000000000000|"
        b"77777777-8888-4999-aaaa-bbbbbbbbbbbb|1793000000"
    )
    .decode()
    .rstrip("=")
)
SIGNATURE = "Zm9yLXRlc3RzLW9ubHktbm90LWEtcmVhbC1zaWduYXR1cmU"
TOKEN = f"{PAYLOAD}.{SIGNATURE}"
CODE, STATE, SESSION_STATE, UNSUBSCRIBE = (
    "oidc-code-4f9d2a",
    "oidc-state-71c3be",
    "oidc-session-state-2d8e",
    "unsubscribe-token-9b6a",
)
SECRETS = (TOKEN, PAYLOAD, SIGNATURE, CODE, STATE, SESSION_STATE, UNSUBSCRIBE)
# Values that send no path or query to another origin (or none at all).
SAFE_REFERRER_POLICIES = {
    "no-referrer",
    "same-origin",
    "origin",
    "strict-origin",
    "origin-when-cross-origin",
    "strict-origin-when-cross-origin",
}


def send_capability_requests(run):
    """Every way a capability reaches Caddy: path, query, and the Referer of the page's own requests."""
    page = {"Referer": f"http://127.0.0.1:{run.port}/q/{TOKEN}"}
    json = {"Content-Type": "application/json"}
    quote_api = f"/api/v1/public/quotes/{TOKEN}"
    callback = f"code={CODE}&state={STATE}&session_state={SESSION_STATE}"
    return {
        "page": run.request("GET", f"/q/{TOKEN}", page),
        "page, upper case": run.request("GET", f"/Q/{TOKEN}", page),
        "page with a query": run.request("GET", f"/q/{TOKEN}?utm_source=mail"),
        "quote api": run.request("GET", quote_api, page),
        "quote api, upper case": run.request("GET", f"/API/V1/PUBLIC/QUOTES/{TOKEN}", page),
        "quote submit": run.request("POST", quote_api, {**page, **json}, b'{"declined": true}'),
        "quote submit, oversize": run.request("POST", quote_api, {**page, **json}, b"x" * 200_000),
        "oidc callback": run.request("GET", f"/api/v1/auth/callback?{callback}"),
        "unsubscribe": run.request("GET", f"/api/v1/public/example-cycle-works/unsubscribe?token={UNSUBSCRIBE}"),
        "missing asset": run.request("GET", "/assets/missing.js", page),
    }


@unittest.skipUnless(CADDY, "native Caddy is unavailable; no downloads permitted")
class CaddyLogsCarryNoCapabilitiesTests(unittest.TestCase):
    def assert_no_secret_in_log(self, log, where):
        for secret in SECRETS:
            self.assertNotIn(secret, log, f"{where}: a capability reached Caddy's log")

    @staticmethod
    def scenario(name, upstream_up):
        with caddy(name, upstream_up) as run:
            results = send_capability_requests(run)
        return results, run.log

    def test_error_log_masks_tokens_when_the_api_is_down(self):
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                results, log = self.scenario(name, upstream_up=False)
                self.assertEqual(results["quote api"][0], 502, results)  # the failure that makes Caddy log
                self.assert_no_secret_in_log(log, name)
                # The masking must not switch logging off: the failure is still recorded, secret removed.
                self.assertIn('"logger":"http.log.error"', log)
                self.assertIn("/api/v1/public/quotes/[redacted]", log)
                self.assertIn("/q/[redacted]", log)  # also the Referer header of the failed request
                self.assertIn("code=[redacted]&state=[redacted]&session_state=[redacted]", log)
                self.assertIn("unsubscribe?token=[redacted]", log)

    def test_nothing_secret_is_logged_while_the_api_answers(self):
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                results, log = self.scenario(name, upstream_up=True)
                self.assertEqual(results["quote api"][0], 200, results)
                # No access log exists today; if one is ever added it must carry the same masking.
                self.assert_no_secret_in_log(log, name)

    def test_capability_responses_are_not_cached_and_leak_no_referrer(self):
        quote_api = f"/api/v1/public/quotes/{TOKEN}"
        with caddy("Caddyfile", upstream_up=True) as run:
            for method in ("GET", "POST"):
                status, headers = run.request(method, quote_api, {"Content-Type": "application/json"}, b"{}")
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("cache-control"), "no-store", method)
            for path in (f"/q/{TOKEN}", quote_api):
                _, headers = run.request("GET", path)
                self.assertIn(headers.get("referrer-policy"), SAFE_REFERRER_POLICIES, path)
                self.assertFalse(any(TOKEN in value for value in headers.values()), f"{path} echoes the token")


class CaddyfileMaskingRulesTests(unittest.TestCase):
    """Static: the rule text in both files, applied with Python's re (the pattern syntax is the same in Go)."""

    @staticmethod
    def rules(name):
        config = (COMPOSE / name).read_text()
        return re.findall(r"^\s*(request>(?:uri|headers>Referer) regexp \S+ \S+)$", config, re.M)

    def test_both_files_carry_the_same_two_rules(self):
        production, development = self.rules("Caddyfile"), self.rules("Caddyfile.dev")
        self.assertEqual([r.split()[0] for r in production], ["request>uri", "request>headers>Referer"])
        self.assertEqual(production, development, "the two Caddyfiles must mask the same things")

    def test_rule_masks_every_capability_and_keeps_the_rest_readable(self):
        _, _, pattern, replacement = self.rules("Caddyfile")[0].split()
        self.assertEqual(replacement, "${1}[redacted]")

        def mask(text):
            return re.sub(pattern, r"\g<1>[redacted]", text)

        cases = {
            f"/q/{TOKEN}": "/q/[redacted]",
            f"/Q/{TOKEN}?utm=1": "/Q/[redacted]?utm=1",
            f"/api/v1/public/quotes/{TOKEN}": "/api/v1/public/quotes/[redacted]",
            f"https://shop.example.com/q/{TOKEN}": "https://shop.example.com/q/[redacted]",
            f"/api/v1/auth/callback?code={CODE}&state={STATE}&iss=x": (
                "/api/v1/auth/callback?code=[redacted]&state=[redacted]&iss=x"
            ),
            f"/api/v1/public/s/unsubscribe?token={UNSUBSCRIBE}": "/api/v1/public/s/unsubscribe?token=[redacted]",
            "/api/v1/healthz": "/api/v1/healthz",
            "/q": "/q",
            "/p/example-cycle-works/chat": "/p/example-cycle-works/chat",
        }
        for original, expected in cases.items():
            self.assertEqual(mask(original), expected)

    def test_no_other_logging_is_configured_without_the_masking(self):
        # One `log` block per file: the global one that holds the masking. A site-level `log` (an access log) would
        # write every URI unmasked: give it the same two rules and update this count.
        for name in CADDYFILES:
            config = (COMPOSE / name).read_text()
            self.assertEqual(len(re.findall(r"^\s*log\b", config, re.M)), 1, name)


if __name__ == "__main__":
    unittest.main()
