import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from core.services.data_integrity import preserve_sqlite_database, sha256_file
from core.services.data_integrity_quarantine import (
    QUARANTINE_CONFIRMATION,
    build_quarantine_plan,
    quarantine_orphans,
)


class DataIntegrityQuarantineTests(SimpleTestCase):
    databases = []

    @staticmethod
    def _create_database(path):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                "PRAGMA foreign_keys = OFF;"
                "CREATE TABLE parent_a (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
                "CREATE TABLE parent_b (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
                "CREATE TABLE child ("
                "id INTEGER PRIMARY KEY, parent_a_id INTEGER NOT NULL, "
                "parent_b_id INTEGER NOT NULL, payload BLOB, "
                "FOREIGN KEY(parent_a_id) REFERENCES parent_a(id), "
                "FOREIGN KEY(parent_b_id) REFERENCES parent_b(id)"
                ");"
                "INSERT INTO parent_a VALUES (1, 'A');"
                "INSERT INTO parent_b VALUES (1, 'B');"
                "INSERT INTO child VALUES (10, 999, 888, X'00FF');"
                "INSERT INTO child VALUES (11, 1, 1, X'AA');"
            )
            connection.commit()
        finally:
            connection.close()

    def _protected_database(self, temp_dir):
        source = Path(temp_dir) / "source.sqlite3"
        self._create_database(source)
        manifest = preserve_sqlite_database(source, Path(temp_dir) / "snapshots")
        return Path(manifest["working_copy"]["path"]), Path(manifest["preservation"]["path"])

    def test_dry_run_is_immutable_and_deduplicates_multi_fk_row(self):
        with TemporaryDirectory() as temp_dir:
            database, _preservation = self._protected_database(temp_dir)
            before = sha256_file(database)

            plan = build_quarantine_plan(database)

            self.assertEqual(plan["summary"]["foreign_key_violations_before"], 2)
            self.assertEqual(plan["summary"]["unique_rows_planned"], 1)
            self.assertEqual(len(plan["rows"][0]["violations"]), 2)
            self.assertEqual(plan["rows"][0]["row"]["payload"]["__type__"], "bytes")
            self.assertEqual(sha256_file(database), before)

    def test_apply_archives_full_row_and_leaves_database_valid(self):
        with TemporaryDirectory() as temp_dir:
            database, preservation = self._protected_database(temp_dir)
            preservation_hash = sha256_file(preservation)
            output = Path(temp_dir) / "quarantine"

            result = quarantine_orphans(
                database,
                output,
                apply=True,
                confirmation=QUARANTINE_CONFIRMATION,
            )

            self.assertTrue(result["summary"]["archive_verified"])
            self.assertEqual(result["summary"]["unique_rows_quarantined"], 1)
            self.assertEqual(result["summary"]["foreign_key_violations_after"], 0)
            self.assertEqual(sha256_file(preservation), preservation_hash)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM child").fetchone()[0], 1)
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            with closing(sqlite3.connect(result["archive"]["path"])) as archive:
                table, rowid, row_json = archive.execute(
                    "SELECT table_name, source_rowid, row_json FROM quarantined_row"
                ).fetchone()
            self.assertEqual((table, rowid), ("child", 10))
            self.assertEqual(json.loads(row_json)["payload"]["base64"], "AP8=")

    def test_apply_refuses_wrong_confirmation(self):
        with TemporaryDirectory() as temp_dir:
            database, _preservation = self._protected_database(temp_dir)
            with self.assertRaisesMessage(ValueError, QUARANTINE_CONFIRMATION):
                quarantine_orphans(database, Path(temp_dir) / "out", apply=True)
