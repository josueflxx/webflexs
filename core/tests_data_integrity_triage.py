import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from core.services.data_integrity import preserve_sqlite_database, sha256_file
from core.services.data_integrity_repair import APPLY_CONFIRMATION
from core.services.data_integrity_triage import (
    build_internal_category_repair_plan,
    build_nullable_orphan_repair_plan,
    collect_residual_triage,
    repair_internal_category_links,
    repair_nullable_orphans,
)


class DataIntegrityTriageTests(SimpleTestCase):
    databases = []

    @staticmethod
    def _create_database(path, *, product_count=2, mismatched_order=False):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                "PRAGMA foreign_keys = OFF;"
                "CREATE TABLE catalog_category ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL"
                ");"
                "CREATE TABLE catalog_product ("
                "id INTEGER PRIMARY KEY, sku TEXT NOT NULL, category_id INTEGER, "
                "FOREIGN KEY(category_id) REFERENCES catalog_category(id)"
                ");"
                "CREATE TABLE catalog_product_categories ("
                "id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, category_id INTEGER NOT NULL, "
                "UNIQUE(product_id, category_id), "
                "FOREIGN KEY(product_id) REFERENCES catalog_product(id), "
                "FOREIGN KEY(category_id) REFERENCES catalog_category(id)"
                ");"
                "CREATE TABLE catalog_categoryproductorder ("
                "id INTEGER PRIMARY KEY, sort_order INTEGER NOT NULL, "
                "category_id INTEGER NOT NULL, product_id INTEGER NOT NULL, "
                "UNIQUE(category_id, product_id), "
                "FOREIGN KEY(product_id) REFERENCES catalog_product(id), "
                "FOREIGN KEY(category_id) REFERENCES catalog_category(id)"
                ");"
            )
            connection.execute(
                "INSERT INTO catalog_category VALUES (20, 'Current category', 'current-category')"
            )
            for position in range(1, product_count + 1):
                product_id = 9 + position
                connection.execute(
                    "INSERT INTO catalog_product VALUES (?, ?, 20)",
                    (product_id, f"SKU-{position}"),
                )
                connection.execute(
                    "INSERT INTO catalog_product_categories VALUES (?, ?, 2)",
                    (position, product_id),
                )
                if not mismatched_order or position != product_count:
                    connection.execute(
                        "INSERT INTO catalog_categoryproductorder VALUES (?, ?, 2, ?)",
                        (position, position, product_id),
                    )
            connection.commit()
        finally:
            connection.close()

    def _protected_database(self, temp_dir, **kwargs):
        source = Path(temp_dir) / "source.sqlite3"
        self._create_database(source, **kwargs)
        manifest = preserve_sqlite_database(source, Path(temp_dir) / "snapshots")
        return Path(manifest["working_copy"]["path"])

    def test_triage_reports_strong_internal_category_evidence(self):
        with TemporaryDirectory() as temp_dir:
            database = self._protected_database(temp_dir)

            report = collect_residual_triage(database)

            self.assertEqual(report["summary"]["foreign_key_violations"], 4)
            self.assertEqual(report["summary"]["internally_corroborated_category_repairs"], 4)
            self.assertFalse(report["summary"]["adoption_ready"])
            self.assertEqual(report["category_evidence"]["accepted_mappings"], 1)

    def test_internal_category_apply_preserves_rows_and_repairs_all_links(self):
        with TemporaryDirectory() as temp_dir:
            database = self._protected_database(temp_dir)

            plan = build_internal_category_repair_plan(database)
            result = repair_internal_category_links(
                database,
                apply=True,
                confirmation=APPLY_CONFIRMATION,
            )

            self.assertEqual(plan["summary"]["planned_operations"], 4)
            self.assertEqual(result["summary"]["applied_operations"], 4)
            self.assertEqual(result["summary"]["foreign_key_violations_after"], 0)
            self.assertTrue(result["summary"]["row_counts_unchanged"])

    def test_single_product_or_mismatched_sets_are_not_accepted(self):
        scenarios = ({"product_count": 1}, {"product_count": 2, "mismatched_order": True})
        for scenario in scenarios:
            with self.subTest(scenario=scenario), TemporaryDirectory() as temp_dir:
                database = self._protected_database(temp_dir, **scenario)
                hash_before = sha256_file(database)

                plan = build_internal_category_repair_plan(database)

                self.assertEqual(plan["summary"]["planned_operations"], 0)
                self.assertEqual(sha256_file(database), hash_before)

    def test_nullable_set_null_policy_is_applied_without_deleting_snapshot(self):
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "nullable_source.sqlite3"
            connection = sqlite3.connect(source)
            try:
                connection.executescript(
                    "PRAGMA foreign_keys = OFF;"
                    "CREATE TABLE catalog_product (id INTEGER PRIMARY KEY, sku TEXT NOT NULL);"
                    "CREATE TABLE orders_orderitem ("
                    "id INTEGER PRIMARY KEY, product_id INTEGER NULL, product_sku TEXT NOT NULL, "
                    "FOREIGN KEY(product_id) REFERENCES catalog_product(id)"
                    ");"
                    "INSERT INTO catalog_product VALUES (10, 'CURRENT');"
                    "INSERT INTO orders_orderitem VALUES (1, 1, 'MISSING-SNAPSHOT');"
                )
                connection.commit()
            finally:
                connection.close()
            manifest = preserve_sqlite_database(source, Path(temp_dir) / "snapshots")
            database = Path(manifest["working_copy"]["path"])

            plan = build_nullable_orphan_repair_plan(database)
            result = repair_nullable_orphans(
                database,
                apply=True,
                confirmation=APPLY_CONFIRMATION,
            )

            self.assertEqual(plan["summary"]["planned_operations"], 1)
            self.assertEqual(result["summary"]["foreign_key_violations_after"], 0)
            with closing(sqlite3.connect(database)) as repaired:
                row = repaired.execute(
                    "SELECT product_id, product_sku FROM orders_orderitem WHERE id = 1"
                ).fetchone()
            self.assertEqual(row, (None, "MISSING-SNAPSHOT"))
