"""Load-test session setup uses synthetic state and HTTPX transports; no network."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from support import ROOT

try:
    import httpx
except ImportError:
    httpx = None

if httpx is None:

    class ProjectVenvTests(unittest.TestCase):
        def test_loadtest_in_project_venv(self):
            python = ROOT / "backend/.venv/bin/python"
            if not python.exists():
                self.skipTest("project HTTPX runtime is unavailable")
            self.assertNotEqual(
                os.environ.get("SMB_LOADTEST_VENV"),
                "1",
                "project venv cannot import httpx; refusing recursive subprocess launch",
            )
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "deploy/tests",
                    "-p",
                    "test_loadtest.py",
                ],
                cwd=ROOT,
                env={**os.environ, "SMB_LOADTEST_VENV": "1"},
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

else:
    spec = importlib.util.spec_from_file_location(
        "loadtest", ROOT / "deploy/scripts/loadtest.py"
    )
    lt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lt)

    BUSINESSES = [
        {
            "business_id": "11111111-1111-4111-8111-111111111111",
            "business_name": "Example Cycle Works",
            "roles": ["owner"],
        },
        {
            "business_id": "22222222-2222-4222-8222-222222222222",
            "business_name": "Example Garden",
            "roles": ["admin"],
        },
    ]

    def me(active=None, memberships=None):
        return {
            "memberships": BUSINESSES if memberships is None else memberships,
            "active_business_id": active,
            "csrf_token": "synthetic-csrf",
        }

    class BusinessMatchingTests(unittest.TestCase):
        def test_exact_case_insensitive_unique_substring_and_id(self):
            for query in (
                "Example Cycle Works",
                "EXAMPLE CYCLE WORKS",
                "cycle",
                BUSINESSES[0]["business_id"],
            ):
                with self.subTest(query=query):
                    self.assertEqual(
                        lt.match_business(BUSINESSES, query), BUSINESSES[0]
                    )

        def test_exact_name_takes_precedence_over_substring(self):
            longer = {**BUSINESSES[0], "business_name": "Example Garden Annex"}
            self.assertEqual(
                lt.match_business([longer, BUSINESSES[1]], "example garden"),
                BUSINESSES[1],
            )

        def test_ambiguous_missing_and_duplicate_exact_names_list_choices(self):
            duplicate = {**BUSINESSES[1], "business_name": "EXAMPLE CYCLE WORKS"}
            for memberships, query, reason in (
                (BUSINESSES, "Example", "ambiguous"),
                (BUSINESSES, "missing", "No membership"),
                ([BUSINESSES[0], duplicate], "Example Cycle Works", "ambiguous"),
            ):
                with self.subTest(query=query), self.assertRaises(SystemExit) as error:
                    lt.match_business(memberships, query)
                self.assertIn(reason, str(error.exception))
                for membership in memberships:
                    self.assertIn(membership["business_name"], str(error.exception))

    class SessionTests(unittest.TestCase):
        def client(self, handler):
            client = httpx.Client(
                base_url="https://example.com",
                cookies={"smb_session": "synthetic-old"},
                transport=httpx.MockTransport(handler),
            )
            self.addCleanup(client.close)
            return client

        def test_no_active_business_requires_selection(self):
            requests = []

            def handler(request):
                requests.append(request.url.path)
                return httpx.Response(200, json=me())

            with self.assertRaises(SystemExit) as error:
                lt.prepare_session(self.client(handler), None)
            message = str(error.exception)
            for text in (
                "no active business",
                "--business",
                "browser",
                "saving",
                *[b["business_name"] for b in BUSINESSES],
            ):
                self.assertIn(text, message)
            self.assertEqual(requests, ["/api/v1/auth/me"])

        def test_single_membership_auto_switches(self):
            def handler(request):
                if request.method == "GET":
                    return httpx.Response(200, json=me(memberships=BUSINESSES[:1]))
                self.assertEqual(request.url.path, "/api/v1/auth/active-business")
                self.assertEqual(request.headers["X-CSRF-Token"], "synthetic-csrf")
                self.assertEqual(
                    json.loads(request.content),
                    {"business_id": BUSINESSES[0]["business_id"]},
                )
                return httpx.Response(
                    200,
                    headers={
                        "Set-Cookie": "smb_session=synthetic-new; Path=/; Secure; HttpOnly"
                    },
                )

            client = self.client(handler)
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                lt.prepare_session(client, None)
            self.assertIn("Automatically", output.getvalue())
            self.assertIn(BUSINESSES[0]["business_name"], output.getvalue())
            self.assertEqual(client.cookies.get("smb_session"), "synthetic-new")

        def test_active_business_is_preserved_without_switch(self):
            for choice in (None, "cycle"):

                def handler(request):
                    self.assertEqual(request.method, "GET")
                    return httpx.Response(200, json=me(BUSINESSES[0]["business_id"]))

                with (
                    self.subTest(choice=choice),
                    patch("sys.stdout", new_callable=io.StringIO) as output,
                ):
                    lt.prepare_session(self.client(handler), choice)
                self.assertEqual(
                    output.getvalue().strip(), "Business: Example Cycle Works"
                )

        def test_expired_session_message(self):
            client = self.client(lambda request: httpx.Response(401))
            with self.assertRaisesRegex(
                SystemExit, "session in --state is not signed in or has expired"
            ):
                lt.prepare_session(client, None)

        def test_failed_switch_has_clear_status_and_code(self):
            def handler(request):
                if request.method == "GET":
                    return httpx.Response(200, json=me())
                return httpx.Response(
                    403, json={"code": "identity.membership_inactive"}
                )

            with self.assertRaisesRegex(
                SystemExit,
                "/api/v1/auth/active-business.*HTTP 403.*identity.membership_inactive",
            ):
                lt.prepare_session(self.client(handler), "cycle")

        def test_switch_without_rotated_cookie_exits(self):
            def handler(request):
                return httpx.Response(200, json=me())

            with self.assertRaisesRegex(
                SystemExit, "did not return a new session cookie"
            ):
                lt.prepare_session(self.client(handler), "cycle")

        def test_main_uses_rotated_cookie_for_discovery_and_all_workers_without_changing_state(
            self,
        ):
            real_client = httpx.Client
            requests = []
            verifies = []

            def handler(request):
                requests.append(request.url.path)
                expected_cookie = (
                    "synthetic-old" if len(requests) <= 2 else "synthetic-new"
                )
                self.assertEqual(
                    request.headers["cookie"], f"smb_session={expected_cookie}"
                )
                if len(requests) == 1:
                    self.assertEqual(request.url.path, "/api/v1/auth/me")
                    return httpx.Response(200, json=me(BUSINESSES[1]["business_id"]))
                if len(requests) == 2:
                    self.assertEqual(request.method, "POST")
                    self.assertEqual(request.url.path, "/api/v1/auth/active-business")
                    self.assertEqual(request.headers["X-CSRF-Token"], "synthetic-csrf")
                    self.assertEqual(
                        json.loads(request.content),
                        {"business_id": BUSINESSES[0]["business_id"]},
                    )
                    return httpx.Response(
                        200,
                        headers={
                            "Set-Cookie": "smb_session=synthetic-new; Path=/; Secure; HttpOnly"
                        },
                    )
                return httpx.Response(200, json={"items": []})

            def client(**kwargs):
                verifies.append(kwargs["verify"])
                return real_client(transport=httpx.MockTransport(handler), **kwargs)

            def thread(*, target, args):
                # Run each real worker synchronously for one iteration, with no sleeps or clock dependence.
                def start():
                    with patch.object(lt.time, "monotonic", side_effect=[0, 2]):
                        target(*args)

                return Mock(start=start)

            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "state.json"
                state_text = json.dumps(
                    {"cookies": [{"name": "smb_session", "value": "synthetic-old"}]}
                )
                state.write_text(state_text)
                for base, insecure, verify in (
                    ("https://example.com", False, True),
                    ("https://localhost:8443", False, False),
                    ("https://example.com", True, False),
                ):
                    requests.clear()
                    verifies.clear()
                    argv = [
                        "loadtest",
                        "--base",
                        base,
                        "--state",
                        str(state),
                        "--business",
                        "cycle",
                        "--staff",
                        "2",
                        "--seconds",
                        "1",
                        "--out",
                        str(Path(directory) / "result.json"),
                    ]
                    if insecure:
                        argv.append("--insecure")
                    with (
                        self.subTest(base=base, insecure=insecure),
                        patch.object(sys, "argv", argv),
                        patch.object(lt.httpx, "Client", side_effect=client),
                        patch.object(lt.threading, "Thread", side_effect=thread),
                        patch.object(lt.time, "monotonic", return_value=0),
                        patch.object(lt.time, "sleep"),
                        patch("sys.stdout", new_callable=io.StringIO) as output,
                    ):
                        self.assertEqual(lt.main(), 0)
                    self.assertEqual(verifies, [verify] * 3)
                    self.assertEqual(
                        len(requests), 5
                    )  # me, switch, discovery, two workers
                    self.assertTrue(
                        output.getvalue().startswith("Business: Example Cycle Works\n")
                    )
                    for secret in ("synthetic-old", "synthetic-new", "synthetic-csrf"):
                        self.assertNotIn(secret, output.getvalue())
                    self.assertEqual(state.read_text(), state_text)

    class DiscoveryAndTLSTests(unittest.TestCase):
        def test_discover_error_includes_endpoint_status_and_optional_code(self):
            for response in (
                httpx.Response(403, json={"code": "auth.no_active_business"}),
                httpx.Response(503, text="Service unavailable"),
                httpx.Response(502, json=["unexpected"]),
            ):
                with (
                    self.subTest(status=response.status_code),
                    httpx.Client(
                        base_url="https://example.com",
                        transport=httpx.MockTransport(
                            lambda request, reply=response: reply
                        ),
                    ) as client,
                    self.assertRaises(SystemExit) as error,
                ):
                    lt.discover(client)
                self.assertIn("/api/v1/crm/customers", str(error.exception))
                self.assertIn(f"HTTP {response.status_code}", str(error.exception))
                if response.status_code == 403:
                    self.assertIn("auth.no_active_business", str(error.exception))

        def test_tls_verify_decision(self):
            for host in (
                "localhost",
                "127.0.0.1",
                "[::1]",
                "app.localhost",
                "LOCALHOST",
            ):
                with self.subTest(host=host):
                    self.assertFalse(lt.verify_tls(f"https://{host}:8443"))
            for host in (
                "example.com",
                "localhost.example.com",
                "127.0.0.2",
                "notlocalhost",
            ):
                with self.subTest(host=host):
                    self.assertTrue(lt.verify_tls(f"https://{host}"))
                    self.assertFalse(lt.verify_tls(f"https://{host}", insecure=True))


if __name__ == "__main__":
    unittest.main()
