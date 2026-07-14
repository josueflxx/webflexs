import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from core.services.data_integrity import preserve_sqlite_database, sha256_file
from core.services.data_integrity_repair import (
    APPLY_CONFIRMATION,
    repair_data_integrity,
    validate_protected_working_copy,
)


class DataIntegrityRepairTests(SimpleTestCase):
    databases = []

    @staticmethod
    def _create_database(path, *, current, ambiguous_product=False, collision=False):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                "PRAGMA foreign_keys = OFF;"
                "CREATE TABLE catalog_category ("
                "id INTEGER PRIMARY KEY, slug TEXT NOT NULL"
                ");"
                "CREATE TABLE catalog_supplier ("
                "id INTEGER PRIMARY KEY, normalized_name TEXT NOT NULL"
                ");"
                "CREATE TABLE catalog_product ("
                "id INTEGER PRIMARY KEY, sku TEXT NOT NULL, supplier_ref_id INTEGER, "
                "FOREIGN KEY(supplier_ref_id) REFERENCES catalog_supplier(id)"
                ");"
                "CREATE TABLE sample_child ("
                "id INTEGER PRIMARY KEY, product_id INTEGER, category_id INTEGER, "
                "UNIQUE(product_id, category_id), "
                "FOREIGN KEY(product_id) REFERENCES catalog_product(id), "
                "FOREIGN KEY(category_id) REFERENCES catalog_category(id)"
                ");"
            )
            if current:
                connection.execute("INSERT INTO catalog_category VALUES (20, 'category')")
                connection.execute("INSERT INTO catalog_supplier VALUES (5, 'SUPPLIER')")
                connection.execute("INSERT INTO catalog_product VALUES (10, 'SKU-1', NULL)")
                if ambiguous_product:
                    connection.execute("INSERT INTO catalog_product VALUES (11, 'sku-1', NULL)")
                connection.execute("INSERT INTO sample_child VALUES (1, 1, 2)")
                if collision:
                    connection.execute("INSERT INTO sample_child VALUES (2, 10, 20)")
            else:
                connection.execute("INSERT INTO catalog_category VALUES (2, 'category')")
                connection.execute("INSERT INTO catalog_supplier VALUES (7, 'SUPPLIER')")
                connection.execute("INSERT INTO catalog_product VALUES (1, 'SKU-1', 7)")
            connection.commit()
        finally:
            connection.close()

    def _protected_current(self, temp_dir, *, ambiguous_product=False, collision=False):
        source = Path(temp_dir) / "current_source.sqlite3"
        self._create_database(
            source,
            current=True,
            ambiguous_product=ambiguous_product,
            collision=collision,
        )
        manifest = preserve_sqlite_database(source, Path(temp_dir) / "snapshots")
        return Path(manifest["working_copy"]["path"]), manifest

    def test_dry_run_plans_repairs_without_modifying_target(self):
        with TemporaryDirectory() as temp_dir:
            current, _manifest = self._protected_current(temp_dir)
            reference = Path(temp_dir) / "reference.sqlite3"
            self._create_database(reference, current=False)
            hash_before = sha256_file(current)

            result = repair_data_integrity(current, reference)

            self.assertEqual(result["summary"]["planned_foreign_key_repairs"], 2)
            self.assertEqual(result["summary"]["planned_supplier_backfills"], 1)
            self.assertEqual(result["summary"]["planned_operations"], 3)
            self.assertEqual(result["summary"]["foreign_key_violations_before"], 2)
            self.assertEqual(sha256_file(current), hash_before)

    def test_apply_repairs_only_updates_and_preserves_row_counts(self):
        with TemporaryDirectory() as temp_dir:
            current, manifest = self._protected_current(temp_dir)
            reference = Path(temp_dir) / "reference.sqlite3"
            self._create_database(reference, current=False)
            preservation_hash = sha256_file(manifest["preservation"]["path"])

            result = repair_data_integrity(
                current,
                reference,
                apply=True,
                confirmation=APPLY_CONFIRMATION,
            )

            self.assertEqual(result["summary"]["applied_operations"], 3)
            self.assertEqual(result["summary"]["foreign_key_violations_after"], 0)
            self.assertTrue(result["summary"]["row_counts_unchanged"])
            self.assertTrue(result["summary"]["transaction_committed"])
            with closing(sqlite3.connect(current)) as connection:
                child = connection.execute(
                    "SELECT product_id, category_id FROM sample_child WHERE id = 1"
                ).fetchone()
                supplier_id = connection.execute(
                    "SELECT supplier_ref_id FROM catalog_product WHERE id = 10"
                ).fetchone()[0]
            self.assertEqual(child, (10, 20))
            self.assertEqual(supplier_id, 5)
            self.assertEqual(sha256_file(manifest["preservation"]["path"]), preservation_hash)

    def test_ambiguous_casefolded_sku_is_left_unresolved(self):
        with TemporaryDirectory() as temp_dir:
            current, _manifest = self._protected_current(temp_dir, ambiguous_product=True)
            reference = Path(temp_dir) / "reference.sqlite3"
            self._create_database(reference, current=False)

            result = repair_data_integrity(current, reference)

            product_repairs = [
                row
                for row in result["operations"]
                if row["operation_type"] == "foreign_key_repair"
                and row["parent_table"] == "catalog_product"
            ]
            unresolved_statuses = {row["status"] for row in result["unresolved"]}
            self.assertEqual(product_repairs, [])
            self.assertIn("ambiguous_current_target", unresolved_statuses)
            self.assertIn("ambiguous_current_product_identity", unresolved_statuses)

    def test_apply_rejects_unprotected_database(self):
        with TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / "current.sqlite3"
            reference = Path(temp_dir) / "reference.sqlite3"
            self._create_database(current, current=True)
            self._create_database(reference, current=False)

            with self.assertRaisesMessage(ValueError, "database_working.sqlite3"):
                repair_data_integrity(
                    current,
                    reference,
                    apply=True,
                    confirmation=APPLY_CONFIRMATION,
                )

    def test_apply_isolates_and_records_unique_constraint_collision(self):
        with TemporaryDirectory() as temp_dir:
            current, _manifest = self._protected_current(temp_dir, collision=True)
            reference = Path(temp_dir) / "reference.sqlite3"
            self._create_database(reference, current=False)

            result = repair_data_integrity(
                current,
                reference,
                apply=True,
                confirmation=APPLY_CONFIRMATION,
            )

            collisions = [
                row for row in result["operations"] if row["status"] == "collision"
            ]
            self.assertEqual(result["summary"]["collisions"], 1)
            self.assertEqual(len(collisions), 1)
            self.assertIn("UNIQUE constraint failed", collisions[0]["error"])
            self.assertEqual(result["summary"]["foreign_key_violations_after"], 1)
            self.assertTrue(result["summary"]["row_counts_unchanged"])

    def test_working_copy_manifest_must_match_initial_hash(self):
        with TemporaryDirectory() as temp_dir:
            current, _manifest = self._protected_current(temp_dir)
            with closing(sqlite3.connect(current)) as connection:
                connection.execute("UPDATE catalog_product SET sku = 'CHANGED' WHERE id = 10")
                connection.commit()

            with self.assertRaisesMessage(ValueError, "ya cambio"):
                validate_protected_working_copy(current)

            manifest_path = current.parent / "manifest.json"
            self.assertTrue(json.loads(manifest_path.read_text(encoding="utf-8")))
