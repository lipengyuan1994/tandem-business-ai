"""Exercise backup maintenance failure records without a repository or database."""

import datetime as dt
import json
import time

from support import OperationsCase


class BackupMonitoringTests(OperationsCase):
    def setUp(self):
        super().setUp()
        self.state = self.work / "state"
        self.state.mkdir()
        self.env.update(BACKUP_STATE_DIR=str(self.state), RESTIC_REPOSITORY="/synthetic")
        self.stub("flock", "pass\n")
        self.stub(
            "date",
            """
import datetime, time
if sys.argv[-1] == '+%s': print(int(time.time()))
else: print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
""",
        )
        self.stub(
            "restic",
            """
with open(os.environ['TRACE'], 'a') as f: f.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1] == os.environ.get('FAIL_STEP'): sys.exit(2)
""",
        )

    def status(self):
        result = self.run_script("backup.sh", "--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_maintenance_success_and_failure_have_independent_timestamps(self):
        backup = {"result": "ok", "last_success": dt.datetime.now(dt.UTC).isoformat()}
        (self.state / "backup-status.json").write_text(json.dumps(backup))
        result = self.run_script("backup.sh", "--maintenance")
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.status()
        for kind in ("prune", "check"):
            self.assertEqual(first["maintenance"][kind]["result"], "ok")
            self.assertIsNotNone(first["maintenance"][kind]["last_success"])
        for step, kind in (("forget", "prune"), ("check", "check")):
            with self.subTest(step=step):
                before = self.status()
                self.env["FAIL_STEP"] = step
                result = self.run_script("backup.sh", "--maintenance")
                self.assertNotEqual(result.returncode, 0)
                status = self.status()
                self.assertEqual(status["result"], "ok")
                self.assertEqual(status["last_success"], backup["last_success"])
                self.assertEqual(status["maintenance"][kind]["result"], "error")
                self.assertEqual(
                    status["maintenance"][kind]["last_success"], before["maintenance"][kind]["last_success"]
                )
                other = "check" if kind == "prune" else "prune"
                self.assertEqual(status["maintenance"][other]["result"], "ok")
        self.assertIn("--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune", self.trace.read_text())

    def test_health_fails_after_seven_hours_or_failed_attempt(self):
        for hours, result, expected in ((6.9, "ok", 0), (7.1, "ok", 1), (1, "error", 1)):
            with self.subTest(hours=hours, result=result):
                (self.state / "last-success").write_text(str(int(time.time() - hours * 3600)))
                (self.state / "backup-status.json").write_text(json.dumps({"result": result}))
                run = self.run_script("backup.sh", "--healthcheck")
                self.assertEqual(run.returncode, expected, run.stderr)

    def test_status_before_first_run_exposes_missing_maintenance(self):
        result = self.status()
        self.assertEqual(result["result"], "never_run")
        for value in result["maintenance"].values():
            self.assertEqual(value["result"], "never_run")
