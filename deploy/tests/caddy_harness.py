"""Run an installed Caddy against a real Caddyfile of this repository (helper for the deployment regression tests).

Only the listener, the upstream addresses and the file root change; the Caddyfile text is otherwise untouched, so the
tests exercise the shipped rules. No Docker, downloads or third-party libraries. Tests using it skip without Caddy.
"""

import contextlib
import http.client
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from support import ROOT

CADDY = shutil.which("caddy")
COMPOSE = ROOT / "deploy/compose"
CADDYFILES = ("Caddyfile", "Caddyfile.dev")
CLOSED = (BrokenPipeError, ConnectionResetError, http.client.RemoteDisconnected)


def free_ports(count):
    """Distinct free ports: every socket stays bound until all are chosen, so none is handed out twice."""
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def start_upstream(seen=None):
    """A local HTTP server answering 200 to GET and POST; each request path is appended to `seen`."""

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if seen is not None:
                seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_GET = do_POST = respond

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def local_config(name, ports, upstream, keycloak, srv):
    """The real Caddyfile with only the listeners, upstream addresses and file root changed.

    Both files end with a plain-HTTP redirect site (`http://`, port 80) and a catch-all for other Hosts (`https://`,
    port 443). Both have no host name. Here the redirect site gets its own port and the catch-all shares the main
    site's port, next to the main site's `127.0.0.1`.
    """
    config = (COMPOSE / name).read_text()
    if name == "Caddyfile":
        config = config.replace("{$DOMAIN} {", f"http://127.0.0.1:{ports['site']} {{")
        config = config.replace(":8081 {", f":{ports['health']} {{")
        config = config.replace("root * /srv", f"root * {srv}")
    else:
        # The development file serves https://localhost with Caddy's internal CA; plain http is enough here.
        config = config.replace("\tlocal_certs\n", "").replace("\ttls internal\n", "")
        config = config.replace("localhost {", f"http://127.0.0.1:{ports['site']} {{")
        config = config.replace("web:5173", f"127.0.0.1:{upstream}")
    # (A tree that predates these sites still loads, so older code can be tested with SMB_TEST_ROOT.)
    config = config.replace("\nhttp:// {", f"\nhttp://:{ports['redirect']} {{")
    config = config.replace("\nhttps:// {", f"\nhttp://:{ports['site']} {{")
    config = config.replace("api:8000", f"127.0.0.1:{upstream}")
    return config.replace("keycloak:8080", f"127.0.0.1:{keycloak}")


class Run:
    def __init__(self, port, redirect_port, health_port):
        self.port = port
        self.redirect_port = redirect_port  # the plain-HTTP site that redirects to HTTPS
        self.health_port = health_port  # production only
        self.log = ""  # filled in when the `caddy()` block ends

    def request(self, method, path, headers=None, body=None, port=None):
        """(status, lower-cased response headers); (None, {}) if Caddy closed the connection without answering.

        `port` addresses another listener of this Caddy (`redirect_port`, `health_port`); the default is the site.
        """
        connection = http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=5)
        try:
            try:
                connection.request(method, path, body=body, headers=headers or {})
            except CLOSED:
                pass  # Caddy may answer 413 and close while a large body is still being sent
            try:
                response = connection.getresponse()
                response.read()
                return response.status, {k.lower(): v for k, v in response.getheaders()}
            except CLOSED:
                return None, {}
        finally:
            connection.close()


def launch(name, temp, upstream=None, keycloak=None, attempts=5):
    """Start Caddy on the real Caddyfile `name`; returns (process, log file, Run).

    `upstream`, `keycloak`: ports of running stand-ins, or None, and the address then points at a port nobody listens on.
    The four ports are free when they are chosen, but the operating system also hands them out to client sockets, and
    Caddy binds them a moment later: if one is gone by then, the start is repeated with new ports.
    """
    srv = temp / "srv"
    log_path = temp / "caddy.log"
    last_log = ""
    for _ in range(attempts):
        site, health, redirect, dead = free_ports(4)  # nothing listens on `dead`
        config = local_config(
            name,
            {"site": site, "health": health, "redirect": redirect},
            upstream or dead,
            keycloak or dead,
            srv,
        )
        (temp / "Caddyfile").write_text(config)
        log = open(log_path, "w+")
        process = subprocess.Popen(
            [CADDY, "run", "--config", str(temp / "Caddyfile"), "--adapter", "caddyfile"],
            env=dict(os.environ, DOMAIN="example.com", XDG_DATA_HOME=str(temp), XDG_CONFIG_HOME=str(temp)),
            stdout=log,
            stderr=log,
        )
        for _ in range(200):
            if process.poll() is not None:
                break
            try:
                socket.create_connection(("127.0.0.1", site), timeout=0.1).close()
                return process, log, Run(site, redirect, health)
            except OSError:
                time.sleep(0.05)
        else:
            process.kill()
            process.wait()
            log.close()
            raise AssertionError("Caddy did not start")
        log.seek(0)
        last_log = log.read()
        log.close()
        if "address already in use" not in last_log:
            break
    raise AssertionError(last_log)


def stop(process, log):
    process.terminate()
    try:
        process.wait(10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    log.close()


@contextlib.contextmanager
def caddy(name, upstream_up, keycloak_seen=None):
    """Run Caddy with one Caddyfile; `run.log` holds everything it wrote once the block has ended.

    `upstream_up`: the api (and, in development, the web server) answer; otherwise nothing listens there.
    `keycloak_seen`: a list; a Keycloak stand-in is started and every path that reaches it is appended.
    """
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        srv = temp / "srv"
        srv.mkdir()
        (srv / "widget.html").write_text("<!doctype html><title>widget</title>")
        (srv / "index.html").write_text("<!doctype html><title>app</title>")
        (srv / "assets").mkdir()
        (srv / "assets" / "app.js").write_text("// fingerprinted build output")
        servers = []
        try:
            # Started first, so the free ports chosen in launch() cannot collide with their ports.
            api = start_upstream() if upstream_up else None
            keycloak = start_upstream(keycloak_seen) if keycloak_seen is not None else None
            servers = [s for s in (api, keycloak) if s is not None]
            process, log, run = launch(
                name, temp, api.server_port if api else None, keycloak.server_port if keycloak else None
            )
            try:
                yield run
            finally:
                stop(process, log)
            run.log = (temp / "caddy.log").read_text()
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
