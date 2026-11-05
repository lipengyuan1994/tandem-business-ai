"""No sockets, Docker or external endpoints: commands and transports are substituted."""

import datetime as dt
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from support import ROOT

spec = importlib.util.spec_from_file_location("watchdog", ROOT / "deploy/scripts/watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def timestamp(hours=0):
    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).isoformat()


def good_backup():
    return {
        "result": "ok",
        "last_success": timestamp(),
        "maintenance": {kind: {"result": "ok", "last_success": timestamp()} for kind in ("prune", "check")},
    }


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "status.json"

    def test_only_transitions_alert_including_recovery(self):
        with patch.object(wd, "send_alert") as send:
            for issues in ([], [], ["disk: low"], ["disk: low"], []):
                wd.update_state(self.path, issues, {}, "https://example.com/hook")
            self.assertEqual(send.call_count, 3)
            self.assertEqual(send.call_args.args[1]["status"], "ok")
            self.assertIn("checked_at", json.loads(self.path.read_text()))

    def test_failed_delivery_is_retried_without_exposing_url(self):
        secret = "https://example.com/private-token"
        with (
            patch.object(wd, "send_alert", side_effect=[OSError(secret), None]) as send,
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            result = wd.update_state(self.path, ["disk: low"], {}, secret)
            self.assertEqual(result["delivery"], "failed")
            self.assertIsNone(result["notified_signature"])
            wd.update_state(self.path, ["disk: low"], {}, secret)
            self.assertEqual(send.call_count, 2)
            self.assertNotIn(secret, output.getvalue())

    def test_no_webhook_logs_and_persists_without_transport(self):
        with patch.object(wd.urllib.request, "build_opener") as transport:
            result = wd.update_state(self.path, [], {}, "")
            self.assertEqual(result["delivery"], "logging_only")
            transport.assert_not_called()

    def test_json_alert_payload(self):
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(wd.urllib.request, "build_opener", return_value=opener):
            payload = {"text": 'SMB watchdog: "failure"\nline', "status": "alert", "issues": ["jobs: dead"]}
            wd.send_alert("https://example.com/hook", payload)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), payload)
        self.assertIsNone(wd.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_test_option_sends_one_alert_without_checks_or_state(self):
        directory = Path(self.temp.name)
        # Entirely synthetic configuration: the real installation .env is never accessed.
        (directory / ".env").write_text("ALERT_WEBHOOK_URL=https://example.com/hook\n")
        with (
            patch.object(sys, "argv", ["watchdog", "--test", "--compose-dir", str(directory)]),
            patch.object(wd, "send_alert") as send,
            patch.object(wd, "collect") as collect,
        ):
            wd.main()
        send.assert_called_once_with(
            "https://example.com/hook", {"text": "SMB watchdog: test alert", "status": "test", "issues": []}
        )
        collect.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_env_is_data_not_shell_code(self):
        path = Path(self.temp.name) / "synthetic.env"
        path.write_text(
            "PUBLIC_URL='https://example.com' # comment\n"
            "ALERT_WEBHOOK_URL='https://example.com/$token?a=b'\nIGNORED=$(touch forbidden)\n"
        )
        self.assertEqual(
            wd.read_settings(path),
            {"PUBLIC_URL": "https://example.com", "ALERT_WEBHOOK_URL": "https://example.com/$token?a=b"},
        )
        self.assertFalse((Path(self.temp.name) / "forbidden").exists())

    def test_backup_age_failure_and_independent_maintenance(self):
        status = good_backup()
        self.assertEqual(wd.backup_issues(status), [])
        status["last_success"] = timestamp(7.1)
        status["maintenance"]["prune"]["result"] = "error"
        status["maintenance"]["check"]["last_success"] = timestamp(193)
        self.assertEqual(
            wd.backup_issues(status),
            [
                "backup: no success within 7 hours",
                "backup prune: failed or never ran",
                "backup check: no success within 8 days",
            ],
        )
        status["result"] = "error"
        self.assertIn("backup: last attempt failed or never ran", wd.backup_issues(status))
        self.assertEqual(len(wd.backup_issues({})), 6)

    def collect(self, *, bad=False, restarts=0, previous=None, command_failure=False):
        def command(args, input=None):
            if command_failure:
                raise OSError("private-token")
            if "config" in args:
                return "api\nworker\nmigrate\nbackup"
            if "ps" in args:
                return args[-1] + "-id"
            if "inspect" in args:
                if args[-2] == "{{.RestartCount}}":
                    return str(restarts)
                state = (
                    {"Status": "exited", "ExitCode": 0}
                    if args[-1] == "migrate-id"
                    else {"Status": "running", "Health": {"Status": "unhealthy" if bad else "healthy"}}
                )
                return json.dumps(state)
            if "journalctl" in args:
                return "oom-kill: killed process" if bad else ""
            if "backup.sh" in args:
                return json.dumps(good_backup())
            if "psql" in args:
                self.assertIn("BEGIN READ ONLY;", input)
                self.assertIn("SET LOCAL statement_timeout = '5s'", input)
                return json.dumps({"dead": int(bad), "old_pending": int(bad), "expired_running": int(bad)})
            if "timedatectl" in args:
                return "no" if bad else "yes"
            raise AssertionError(args)

        response = io.BytesIO(b'{"status":"ok"}')
        stat = Mock(f_bavail=1 if bad else 10**7, f_frsize=4096, f_favail=1 if bad else 10**6, f_files=10**7)
        tls = Mock()
        tls.__enter__ = Mock(return_value=tls)
        tls.__exit__ = Mock(return_value=False)
        tls.getpeercert.return_value = {"notAfter": "irrelevant"}
        raw = Mock()
        raw.__enter__ = Mock(return_value=raw)
        raw.__exit__ = Mock(return_value=False)
        context = Mock()
        context.wrap_socket.return_value = tls
        with (
            patch.object(wd, "command", side_effect=command),
            patch.object(wd.urllib.request, "urlopen", return_value=response),
            patch.object(wd.os, "statvfs", return_value=stat),
            patch.object(wd.socket, "create_connection", return_value=raw),
            patch.object(wd.ssl, "create_default_context", return_value=context),
            patch.object(wd.ssl, "cert_time_to_seconds", return_value=wd.time.time() + (13 if bad else 20) * 86400),
        ):
            return wd.collect(Path("/opt/smb"), "https://example.com", previous or {})

    def test_all_probes_healthy_with_successful_oneshot(self):
        issues, observations = self.collect()
        self.assertEqual(issues, [])
        self.assertEqual(observations["restarts"]["worker-id"], 0)

    def test_health_restart_oom_disk_jobs_cert_and_clock_alerts(self):
        issues, _ = self.collect(bad=True, restarts=1)
        for message in (
            "container worker: not running/healthy",
            "container api: restarted",
            "host: recent OOM kill",
            "jobs: dead",
            "jobs: old_pending",
            "jobs: expired_running",
            "certificate: expires within 14 days",
            "time sync: not synchronized",
            "disk /opt/smb: less than 5 GiB free",
            "disk /opt/smb: low free inodes",
        ):
            self.assertIn(message, issues)

    def test_unchanged_restart_counts_do_not_realert(self):
        previous = {"observations": {"restarts": {f"{name}-id": 1 for name in ("worker", "api", "backup")}}}
        self.assertEqual(self.collect(restarts=1, previous=previous)[0], [])

    def test_probe_errors_are_unknown_not_healthy_and_no_secrets(self):
        issues, _ = self.collect(command_failure=True)
        for name in ("containers", "OOM journal", "jobs", "backup", "time sync"):
            self.assertIn(f"{name}: check unavailable", issues)
        self.assertNotIn("private-token", json.dumps(issues))

    def test_shell_entrypoint_test_logs_without_network(self):
        directory = Path(self.temp.name)
        (directory / ".env").write_text("ALERT_WEBHOOK_URL=\n")
        result = subprocess.run(  # noqa: S603 - repository-controlled script and synthetic paths
            ["/bin/bash", str(ROOT / "deploy/scripts/watchdog.sh"), "--test", "--compose-dir", str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("logging only", result.stdout)
