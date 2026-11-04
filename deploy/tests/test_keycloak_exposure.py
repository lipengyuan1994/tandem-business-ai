"""Keycloak administration, including the master realm, is never proxied by Caddy (docs/security/2026-11-03-rescan.md).

The master realm holds the administrator's own login and its public `admin-cli` client accepts a password there, so
reaching `/auth/realms/master/...` from the Internet would allow online guessing of the admin password even though
`/auth/admin` (the console and REST API) is blocked. Administration happens through an SSH tunnel
(docs/runbooks/access.md). A stand-in for Keycloak records every path that reaches it: blocked paths must never
arrive, and the application realm (a positive control) must.
"""

import unittest

from caddy_harness import CADDY, CADDYFILES, caddy

BLOCKED = (
    "/auth/admin",
    "/auth/admin/",
    "/auth/admin/master/console/",
    "/auth/admin/realms",
    "/auth//admin/",
    "/auth/./admin/",
    "/auth/%61dmin/",
    "/AUTH/ADMIN/",
    "/auth/realms/master",
    "/auth/realms/master/",
    "/auth/realms/master/.well-known/openid-configuration",
    "/auth/realms/master/protocol/openid-connect/certs",
    "/auth/realms/master/protocol/openid-connect/token",
    "/auth/realms/master/account/",
    "/auth/realms/MASTER/account/",
    "/auth/realms/%6daster/account/",
)
ALLOWED = "/auth/realms/smb/.well-known/openid-configuration"


@unittest.skipUnless(CADDY, "native Caddy is unavailable; no downloads permitted")
class KeycloakAdministrationIsNotProxiedTests(unittest.TestCase):
    def test_admin_console_and_master_realm_never_reach_keycloak(self):
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        password_grant = b"grant_type=password&client_id=admin-cli&username=nobody&password=none"
        for name in CADDYFILES:
            with self.subTest(caddyfile=name):
                reached = []
                with caddy(name, upstream_up=True, keycloak_seen=reached) as run:
                    for path in BLOCKED:
                        self.assertEqual(run.request("GET", path)[0], 404, path)
                    token = "/auth/realms/master/protocol/openid-connect/token"
                    self.assertEqual(run.request("POST", token, form, password_grant)[0], 404)
                    self.assertEqual(run.request("GET", ALLOWED)[0], 200)  # positive control
                self.assertEqual(reached, [ALLOWED], f"{name}: a blocked path reached Keycloak")


if __name__ == "__main__":
    unittest.main()
