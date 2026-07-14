import json
import sqlite3
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from core.services.data_integrity import (
    collect_integrity_report,
    compare_recovery_candidates,
    preserve_sqlite_database,
    write_integrity_report,
)


class DataIntegrityServiceTests(SimpleTestCase):
    databases = []

    @staticmethod
    def _create_database(
        path,
        product_id,
        sku,
        child_product_id=None,
        supplier=False,
        assign_supplier=False,
    ):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                "PRAGMA foreign_keys = OFF;"
                "CREATE TABLE catalog_product (id INTEGER PRIMARY KEY, sku TEXT UNIQUE);"
                "CREATE TABLE sample_child ("
                "id INTEGER PRIMARY KEY, product_id INTEGER, "
                "FOREIGN KEY(product_id) REFERENCES catalog_product(id)"
                ");"
            )
            if supplier:
                connection.execute(
                    "ALTER TABLE catalog_product ADD COLUMN supplier_ref_id INTEGER"
                )
                connection.execute(
                    "CREATE TABLE catalog_supplier ("
                    "id INTEGER PRIMARY KEY, normalized_name TEXT UNIQUE)"
                )
                connection.execute(
                    "INSERT INTO catalog_supplier(id, normalized_name) VALUES (5, 'SUPPLIER')"
                )
            connection.execute(
                "INSERT INTO catalog_product(id, sku) VALUES (?, ?)",
                (product_id, sku),
            )
            if supplier and assign_supplier:
                connection.execute(
                    "UPDATE catalog_product SET supplier_ref_id = 5 WHERE id = ?",
                    (product_id,),
                )
            if child_product_id is not None:
                connection.execute(
                    "INSERT INTO sample_child(id, product_id) VALUES (1, ?)",
                    (child_product_id,),
                )
            connection.commit()
        finally:
            connection.close()

    def test_preservation_creates_matching_verified_copies(self):
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.sqlite3"
            self._create_database(source, product_id=1, sku="SKU-1")
            manifest = preserve_sqlite_database(source, Path(temp_dir) / "snapshots")

            self.assertTrue(manifest["verification"]["copies_match"])
            self.assertEqual(manifest["verification"]["quick_check"], ["ok"])
            self.assertEqual(
                manifest["preservation"]["sha256"],
                manifest["working_copy"]["sha256"],
            )
            Path(manifest["preservation"]["path"]).chmod(stat.S_IWRITE)

    def test_audit_reports_dangling_foreign_key_and_writes_files(self):
        with TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "broken.sqlite3"
            self._create_database(database, product_id=10, sku="CURRENT", child_product_id=99)
            report = collect_integrity_report(database)
            files = write_integrity_report(report, Path(temp_dir) / "reports")

            self.assertEqual(report["integrity_check"], ["ok"])
            self.assertEqual(report["foreign_keys"]["total_violations"], 1)
            self.assertTrue(Path(files["json"]).exists())
            loaded = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
            self.assertEqual(loaded["foreign_keys"]["total_violations"], 1)

    def test_comparison_maps_old_id_through_stable_sku(self):
        with TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / "current.sqlite3"
            reference = Path(temp_dir) / "reference.sqlite3"
            self._create_database(
                current,
                product_id=10,
                sku="SKU-1",
                child_product_id=1,
                supplier=True,
            )
            self._create_database(
                reference,
                product_id=1,
                sku="SKU-1",
                supplier=True,
                assign_supplier=True,
            )

            comparison = compare_recovery_candidates(current, reference)

            self.assertEqual(comparison["summary"]["missing_unique_ids"], 1)
            self.assertEqual(comparison["summary"]["identified_in_reference"], 1)
            self.assertEqual(comparison["summary"]["matched_in_current"], 1)
            self.assertEqual(comparison["summary"]["matched_violation_rows"], 1)
            self.assertEqual(comparison["identity_overlap"]["products"]["shared"], 1)
            self.assertEqual(
                comparison["supplier_assignment_recovery"]["recoverable_by_sku_and_supplier_identity"],
                1,
            )
