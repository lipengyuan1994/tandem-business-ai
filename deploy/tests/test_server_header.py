"""Caddy does not name itself (docs/security/2026-11-03-rescan.md, F7).

`Server: Caddy` told every scanner which proxy runs in front of the application. The site-level `-Server` removed it
from the site's own responses, but not from Caddy's built-in HTTP-to-HTTPS redirect, from the answer to a request for
an unknown Host (a scanner that uses the IP address), or from the upstream's own Server header on proxied responses.

The live tests run an installed Caddy with each real Caddyfile (see caddy_harness.py) and look at every kind of
response; they skip when Caddy is not installed, the static tests do not.

One kind is deliberately not asserted: a response that Caddy builds itself from an error (a 502 while the api is down, a
413 for an oversized chunked body) keeps `Server: Caddy`. `handle_errors` could remove it, but Caddy then logs such a
failure at debug level instead of error level, and the masked error log of test_capability_logging.py goes silent. The
last test below keeps `handle_errors` out until that trade changes.
"""

import unittest

from caddy_harness import CADDY, CADDYFILES, COMPOSE, caddy


@unittest.skipUnless(CADDY, "native Caddy is unavailable; no downloads permitted")
class NoResponseNamesTheServerTests(unittest.TestCase):
    def assert_no_server_header(self, response, what):
        status, headers = response
        self.assertIsNotNone(status, f"{what}: no answer")
        self.assertNotIn("server", headers, f"{what}: Server: {headers.get('server')}")

    def test_routes_answer_without_a_server_header(self):
        # The stand-in upstream is Python's http.server, which sends `Server: BaseHTTP/...` itself: that must go too.
        routes = {
            "app": ("GET", "/today", 200),
            "api": ("GET", "/api/v1/healthz", 200),
            "api, POST": ("POST", "/api/v1/public/example-cycle-works/chat", 200),
            "widget page": ("GET", "/p/example-cycle-works/chat", 200),
            "supplier page": ("GET", "/q/abc.def", 200),
            "keycloak realm": ("GET", "/auth/realms/smb/.well-known/openid-configuration", 200),
            "keycloak admin, blocked": ("GET", "/auth/admin/", 404),
            "keycloak master realm, blocked": ("GET", "/auth/realms/master/account/", 404),
        }
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                with caddy(name, upstream_up=True, keycloak_seen=[]) as run:
                    for what, (method, route, expected) in routes.items():
                        response = run.request(method, route, {"Content-Type": "application/json"}, b"{}")
                        self.assertEqual(response[0], expected, f"{name} {what}")
                        self.assert_no_server_header(response, f"{name} {what}")

    def test_production_static_files_answer_without_a_server_header(self):
        with caddy("Caddyfile", upstream_up=True) as run:
            for route, expected in (("/", 200), ("/assets/app.js", 200), ("/assets/missing.js", 404)):
                response = run.request("GET", route)
                self.assertEqual(response[0], expected, route)
                self.assert_no_server_header(response, route)
            # A file_server "not found" error would carry the header; the missing asset is answered by a route instead.
            self.assertEqual(run.request("GET", "/assets/app.js")[1].get("cache-control"), "public, max-age=31536000, immutable")

    def test_plain_http_redirects_to_https_without_a_server_header(self):
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                with caddy(name, upstream_up=True) as run:
                    for method in ("GET", "HEAD", "POST"):
                        status, headers = run.request(method, "/today?x=1", port=run.redirect_port)
                        self.assertEqual(status, 308, method)  # what Caddy's built-in redirect answered
                        self.assertEqual(headers["location"], "https://127.0.0.1/today?x=1", method)
                        self.assertNotIn("server", headers, method)
                    # Any Host is redirected to itself, as before; an ACME challenge path is just another path here
                    # (Caddy answers a pending challenge before any route is reached).
                    for host, route in (("shop.example.com", "/q/abc.def"), ("203.0.113.7", "/.well-known/acme-challenge/x")):
                        status, headers = run.request("GET", route, {"Host": host}, port=run.redirect_port)
                        self.assertEqual((status, headers["location"]), (308, f"https://{host}{route}"))
                        self.assertNotIn("server", headers, host)

    def test_a_request_for_another_host_gets_no_answer(self):
        # Without the catch-all site Caddy answered 200, empty, with `Server: Caddy`.
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                with caddy(name, upstream_up=True) as run:
                    self.assertEqual(run.request("GET", "/today")[0], 200)  # the configured host still works
                    for host in ("203.0.113.7", "other.example", "localhost.example.com"):
                        for method in ("GET", "POST"):
                            self.assertEqual(run.request(method, "/today", {"Host": host}), (None, {}), f"{method} {host}")

    def test_health_listener_has_no_server_header(self):
        with caddy("Caddyfile", upstream_up=True) as run:
            status, headers = run.request("GET", "/healthz", port=run.health_port)
            self.assertEqual(status, 200)
            self.assertNotIn("server", headers)


class ServerHeaderConfigurationTests(unittest.TestCase):
    """Static: the text of the two files, so the rules are checked even where Caddy is not installed."""

    @staticmethod
    def read(name):
        return (COMPOSE / name).read_text()

    def test_caddys_own_redirect_is_off_and_replaced_by_an_explicit_site(self):
        for name in CADDYFILES:
            config = self.read(name)
            with self.subTest(caddyfile=name):
                self.assertRegex(config, r"(?m)^\tauto_https disable_redirects$")
                self.assertRegex(config, r"(?m)^http:// \{\n\theader -Server\n\tredir https://\{host\}\{uri\} 308\n\}$")

    def test_requests_for_other_hosts_are_dropped(self):
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                self.assertRegex(self.read(name), r"(?m)^https:// \{\n\tabort\n\}$")

    def test_the_main_site_removes_the_header(self):
        production = self.read("Caddyfile").split("\n{$DOMAIN} {", 1)[1].split("\n}\n", 1)[0]
        development = self.read("Caddyfile.dev").split("\nlocalhost {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("\n\theader {\n", production)
        self.assertIn("\n\t\t-Server\n", production)  # inside the header block that also sets HSTS and the rest
        self.assertIn("\n\theader -Server\n", development)

    def test_production_health_listener_and_trust_store(self):
        config = self.read("Caddyfile")
        health = config.split("\n:8081 {", 1)[1].split("\n}", 1)[0]
        self.assertIn("\theader -Server\n", health)
        # Only matters for `localhost`: the internal CA must not try to write the container's (read-only) trust store.
        self.assertRegex(config, r"(?m)^\tskip_install_trust$")

    def test_handle_errors_stays_out_until_the_error_log_survives_it(self):
        # Verified with Caddy 2.11.7: with an error route, a failed request is logged at debug instead of error level.
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                self.assertNotRegex(self.read(name), r"(?m)^\s*handle_errors\b")


if __name__ == "__main__":
    unittest.main()
