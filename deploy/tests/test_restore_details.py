"""Restore details and caller exit semantics, with synthetic command fakes only."""

import json

from support import ROOT, OperationsCase


class RestoreDetailsTests(OperationsCase):
    def setUp(self):
        super().setUp()
        self.state = self.work / "state"
        self.env.update(BACKUP_STATE_DIR=str(self.state), POSTGRES_PASSWORD="synthetic")
        self.stub(
            "restic",
            """
a = sys.argv
dest = pathlib.Path(a[a.index('--target') + 1])
(dest / a[a.index('--include') + 1]).write_text('synthetic dump')
""",
        )
        self.stub("pg_restore", "pass\n")
        self.stub(
            "psql",
            """
a = sys.argv
sql = a[a.index('--command') + 1]
db = a[a.index('--dbname') + 1]
with open(os.environ['TRACE'], 'a') as f: f.write(sql + '\\n')
if 'information_schema.tables' in sql:
    rows = 7 if db in os.environ.get('DIFFER_DATABASES', '').split(',') else 5
    print('customers|' + str(rows) + '\\njobs|2')
elif 'version_num' in sql: print('synthetic_revision')
elif 'from pg_database' in sql: print(1)
elif 'alter database' in sql and os.environ.get('FAIL_SWAP') == '1': sys.exit(3)
""",
        )

    def details(self):
        return json.loads((self.state / "restore-last.json").read_text())

    def restore_db(self, db="smb", *args):
        return self.run_script(
            "lib/restore-db.sh", "--db", db, "--snapshot", "synthetic",
            "--target", f"{db}_drill", "--compare-with", db, *args,
        )

    def assert_entry(self, entry, db, identical, swapped=False, target=None):
        self.assertEqual(entry["database"], db)
        self.assertEqual(entry["snapshot"], "synthetic")
        self.assertEqual(entry["target"], target or f"{db}_drill")
        self.assertEqual(entry["tables"], 2)
        self.assertEqual(entry["rows"], 7)
        self.assertIs(entry["counts_identical_to_source"], identical)
        self.assertIs(entry["swapped"], swapped)
        for key in ("restore_seconds", "total_seconds"):
            self.assertIsInstance(entry[key], int)
            self.assertGreaterEqual(entry[key], 0)
        self.assertGreaterEqual(entry["total_seconds"], entry["restore_seconds"])
        self.assertRegex(entry["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_details_written_for_identical_and_differing_counts(self):
        for db in ("smb", "keycloak"):
            for differ in (False, True):
                with self.subTest(db=db, differ=differ):
                    self.env["DIFFER_DATABASES"] = db if differ else ""
                    result = self.restore_db(db)
                    self.assertEqual(result.returncode, 10 if differ else 0, result.stderr)
                    entries = self.details()
                    self.assertEqual(len(entries), 1)
                    self.assert_entry(entries[0], db, not differ)
                    if differ:
                        self.assertIn("ROW COUNTS DIFFER", result.stderr)
                        self.assertIn("customers|7", result.stderr)
                        self.assertIn("customers|5", result.stderr)

    def test_mismatch_with_swap_records_details_without_promoting(self):
        self.env["DIFFER_DATABASES"] = "smb"
        result = self.restore_db("smb", "--swap")
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assert_entry(self.details()[0], "smb", False)
        self.assertNotIn("alter database", self.trace.read_text())

    def test_swap_failure_still_records_comparison(self):
        self.env["FAIL_SWAP"] = "1"
        result = self.restore_db("smb", "--swap")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assert_entry(self.details()[0], "smb", True)

    def fake_docker(self):
        self.env["RESTORE_HELPER"] = str(ROOT / "deploy/scripts/lib/restore-db.sh")
        self.stub(
            "docker",
            """
import subprocess
a = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as f: f.write('docker ' + ' '.join(a) + '\\n')
if 'restore-db.sh' in a:
    sys.exit(subprocess.run(['/bin/bash', os.environ['RESTORE_HELPER'],
                             *a[a.index('restore-db.sh') + 1:]], check=False).returncode)
if 'psql' in a:
    sys.stdin.read()
    print(2)
elif 'ps' in a: print('backup|healthy')
""",
        )

    def test_drill_summary_covers_both_databases_and_replaces_previous_run(self):
        self.stage()
        self.fake_docker()
        for differing_db in ("smb", "keycloak", ""):
            with self.subTest(differing_db=differing_db):
                self.env["DIFFER_DATABASES"] = differing_db
                result = self.run_script("restore.sh", "--drill", "synthetic")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                entries = self.details()
                self.assertEqual([e["database"] for e in entries], ["smb", "keycloak"])
                for entry in entries:
                    self.assert_entry(entry, entry["database"], entry["database"] != differing_db)
                self.assertIn("/state/restore-last.json", result.stdout)
                if differing_db:
                    self.assertIn("per-table differences", result.stdout)
                    self.assertIn("stderr", result.stdout)
                    self.assertIn("expected if data changed", result.stdout)

    def test_real_restore_summary_includes_both_swaps(self):
        self.stage()
        self.fake_docker()
        result = self.run_script(str(self.stack / "restore.sh"), "synthetic", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        entries = self.details()
        self.assertEqual([e["database"] for e in entries], ["smb", "keycloak"])
        for entry in entries:
            db = entry["database"]
            self.assert_entry(entry, db, None, swapped=True, target=f"{db}_restore")
