"""Exercise the production body limit with an installed Caddy and a byte-counting HTTP upstream.

Only listener/upstream addresses change. No Docker, downloads or third-party Python libraries.
Known oversized Content-Length is rejected before proxying. Unknown-length bodies may reach the
API partially, but must deliver at most 128,000 bytes and must never arrive complete when oversized.
"""

import http.client
import shutil
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from caddy_harness import launch, stop

CADDY = shutil.which("caddy")
LIMIT = 128_000  # Caddy's KB unit is decimal, not KiB.
CLOSED = (BrokenPipeError, ConnectionResetError, http.client.RemoteDisconnected)


@unittest.skipUnless(CADDY, "native Caddy is unavailable; no downloads permitted")
class PublicBodyLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.condition = threading.Condition()

        class Upstream(BaseHTTPRequestHandler):
            def read_bytes(self, count, record):
                while count:
                    data = self.rfile.read1(min(count, 16_384))
                    if not data:
                        return False
                    record["bytes"] += len(data)
                    count -= len(data)
                return True

            def read_body(self, record):
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    while True:
                        line = self.rfile.readline()
                        if not line.endswith(b"\r\n"):
                            return False
                        count = int(line.split(b";", 1)[0], 16)
                        if count == 0:
                            # Framing completion matters, not just the number of decoded bytes.
                            while True:
                                trailer = self.rfile.readline()
                                if trailer == b"\r\n":
                                    return True
                                if not trailer:
                                    return False
                        if (
                            not self.read_bytes(count, record)
                            or self.rfile.read(2) != b"\r\n"
                        ):
                            return False
                length = self.headers.get("Content-Length")
                if length is None:
                    raise AssertionError("upstream request has no body framing")
                return self.read_bytes(int(length), record)

            def do_POST(self):
                record = {
                    "path": self.path,
                    "bytes": 0,
                    "complete": False,
                    "error": None,
                    "done": threading.Event(),
                }
                with cls.condition:
                    cls.requests.append(record)
                    cls.condition.notify_all()
                self.connection.settimeout(5)
                try:
                    record["complete"] = self.read_body(record)
                    self.send_response(200 if record["complete"] else 400)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                except CLOSED:
                    pass  # Caddy may abort the upstream upload when its reader hits the limit.
                except (OSError, ValueError, AssertionError) as error:
                    record["error"] = repr(error)
                finally:
                    record["done"].set()

            def log_message(self, *_args):
                pass

        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.addClassCleanup(cls.upstream.server_close)
        thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(cls.upstream.shutdown)
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        # The real Caddyfile; only the listeners and the upstream address change (see caddy_harness.local_config).
        process, log, run = launch("Caddyfile", Path(temp.name), upstream=cls.upstream.server_port)
        cls.addClassCleanup(stop, process, log)
        cls.port = run.port

    def upload(self, path, body, chunked):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        chunks = (body[i : i + 4096] for i in range(0, len(body), 4096))
        try:
            connection.request(
                "POST",
                path,
                body=chunks if chunked else body,
                headers={"Content-Type": "application/json"},
                encode_chunked=chunked,
            )
        except CLOSED:
            # Still try reading a 413 sent before Caddy closed the upload.
            pass
        try:
            return connection.getresponse().status
        except CLOSED:
            return None  # Explicitly accepted: the peer closed/reset the connection.
        finally:
            connection.close()

    def observations(self, path):
        # IDs and completion events avoid racing a global count from another request.
        with self.condition:
            self.condition.wait_for(
                lambda: any(r["path"] == path for r in self.requests), timeout=1
            )
            records = [r for r in self.requests if r["path"] == path]
        for record in records:
            self.assertTrue(record["done"].wait(6), record)
            self.assertIsNone(record["error"], record)
        return records

    def test_allowed_fixed_and_chunked_bodies_arrive_complete(self):
        for endpoint in ("chat", "appointment-requests"):
            for chunked in (False, True):
                for size in (32, LIMIT - 1, LIMIT):
                    with self.subTest(endpoint=endpoint, chunked=chunked, size=size):
                        path = (
                            f"/api/v1/public/check/{endpoint}?probe={uuid.uuid4().hex}"
                        )
                        body = b'{"extra":"' + b"x" * (size - 12) + b'"}'
                        self.assertEqual(len(body), size)
                        self.assertEqual(self.upload(path, body, chunked), 200)
                        records = self.observations(path)
                        self.assertEqual(len(records), 1)
                        self.assertEqual(records[0]["bytes"], size)
                        self.assertTrue(records[0]["complete"])

    def test_oversize_bodies_are_bounded_and_never_complete_at_api(self):
        for endpoint in ("chat", "appointment-requests"):
            for chunked in (False, True):
                for size in (LIMIT + 1, 1024 * 1024):
                    with self.subTest(endpoint=endpoint, chunked=chunked, size=size):
                        path = (
                            f"/api/v1/public/check/{endpoint}?probe={uuid.uuid4().hex}"
                        )
                        body = b'{"extra":"' + b"x" * (size - 12) + b'"}'
                        status = self.upload(path, body, chunked)
                        records = self.observations(path)
                        self.assertLessEqual(len(records), 1, records)
                        for record in records:
                            self.assertLessEqual(record["bytes"], LIMIT, record)
                            self.assertFalse(record["complete"], record)
                        self.assertIn(status, (413, None), records)
                        if not chunked:
                            # Content-Length allows rejection before the proxy contacts the API.
                            self.assertEqual(records, [], records)

    def test_non_public_requests_keep_their_existing_body_behavior(self):
        for chunked in (False, True):
            with self.subTest(chunked=chunked):
                path = f"/api/v1/private/check?probe={uuid.uuid4().hex}"
                body = b"x" * (LIMIT + 1)
                self.assertEqual(self.upload(path, body, chunked), 200)
                records = self.observations(path)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["bytes"], len(body))
                self.assertTrue(records[0]["complete"])


if __name__ == "__main__":
    unittest.main()
