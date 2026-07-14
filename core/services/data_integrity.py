"""Read-only SQLite preservation and integrity-audit helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import stat
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_uri(path, mode="ro"):
    return f"file:{Path(path).resolve().as_posix()}?mode={mode}"


@contextmanager
def open_sqlite_read_only(path):
    connection = sqlite3.connect(_sqlite_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


def preserve_sqlite_database(source_path, output_root):
    """Create one consistent preservation snapshot and one writable work copy."""
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"No existe la base SQLite: {source}")

    snapshot_dir = Path(output_root).resolve() / f"snapshot_{utc_stamp()}"
    suffix = 1
    while snapshot_dir.exists():
        snapshot_dir = snapshot_dir.with_name(f"{snapshot_dir.name}_{suffix}")
        suffix += 1
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    preservation_path = snapshot_dir / "database_preservation.sqlite3"
    working_path = snapshot_dir / "database_working.sqlite3"

    source_connection = sqlite3.connect(_sqlite_uri(source), uri=True)
    target_connection = sqlite3.connect(preservation_path)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()

    shutil.copy2(preservation_path, working_path)
    preservation_sha = sha256_file(preservation_path)
    working_sha = sha256_file(working_path)
    if preservation_sha != working_sha:
        raise RuntimeError("Las copias SQLite no tienen el mismo hash inicial.")

    with open_sqlite_read_only(preservation_path) as connection:
        quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        foreign_key_count = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))

    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "sqlite_online_backup",
        "source": {
            "path": str(source),
            "size": source.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                source.stat().st_mtime,
                timezone.utc,
            ).isoformat(),
        },
        "preservation": {
            "path": str(preservation_path),
            "size": preservation_path.stat().st_size,
            "sha256": preservation_sha,
            "role": "preservation_do_not_modify",
            "filesystem_read_only": True,
        },
        "working_copy": {
            "path": str(working_path),
            "size": working_path.stat().st_size,
            "sha256": working_sha,
            "role": "repair_tests_only",
        },
        "verification": {
            "copies_match": True,
            "quick_check": quick_check,
            "foreign_key_violations": foreign_key_count,
        },
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    preservation_path.chmod(stat.S_IREAD)
    return manifest


def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _table_names(connection):
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _table_columns(connection, table):
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")}


def _safe_scalar(connection, sql):
    row = connection.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def _quality_metrics(connection, table_names):
    metrics = {}
    tables = set(table_names)
    if "core_company" in tables:
        metrics["companies"] = _safe_scalar(connection, "SELECT COUNT(*) FROM core_company")
        if "is_active" in _table_columns(connection, "core_company"):
            metrics["active_companies"] = _safe_scalar(
                connection,
                "SELECT COUNT(*) FROM core_company WHERE is_active = 1",
            )
    if "catalog_product" in tables:
        columns = _table_columns(connection, "catalog_product")
        metrics["products"] = _safe_scalar(connection, "SELECT COUNT(*) FROM catalog_product")
        if "supplier_ref_id" in columns:
            metrics["products_without_supplier_ref"] = _safe_scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_product WHERE supplier_ref_id IS NULL",
            )
        if {"supplier_ref_id", "supplier"}.issubset(columns):
            metrics["products_supplier_text_without_ref"] = _safe_scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_product "
                "WHERE supplier_ref_id IS NULL AND TRIM(COALESCE(supplier, '')) <> ''",
            )
        if "cost" in columns:
            metrics["products_zero_cost"] = _safe_scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_product WHERE cost = 0",
            )
        if "stock" in columns:
            metrics["products_negative_stock"] = _safe_scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_product WHERE stock < 0",
            )
    simple_counts = {
        "catalog_supplier": "suppliers",
        "catalog_pricelist": "price_lists",
        "catalog_pricelistitem": "price_list_items",
        "catalog_clampspecs": "clamp_specs",
        "accounts_clientprofile": "clients",
        "accounts_clientcompany": "client_company_links",
        "orders_order": "orders",
        "orders_orderrequest": "order_requests",
        "accounts_clientpayment": "payments",
        "accounts_clienttransaction": "transactions",
        "core_stockmovement": "stock_movements",
        "core_importexecution": "import_executions",
    }
    for table, label in simple_counts.items():
        if table in tables:
            metrics[label] = _safe_scalar(connection, f"SELECT COUNT(*) FROM {_quote_identifier(table)}")
    if "core_importexecution" in tables and "status" in _table_columns(connection, "core_importexecution"):
        metrics["failed_import_executions"] = _safe_scalar(
            connection,
            "SELECT COUNT(*) FROM core_importexecution WHERE status = 'failed'",
        )
    return metrics


def collect_integrity_report(database_path, include_rows=True):
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la base SQLite: {path}")

    with open_sqlite_read_only(path) as connection:
        tables = _table_names(connection)
        table_counts = {
            table: _safe_scalar(connection, f"SELECT COUNT(*) FROM {_quote_identifier(table)}")
            for table in tables
        }
        integrity_check = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        violation_rows = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        grouped = Counter((str(row[0]), str(row[2])) for row in violation_rows)
        by_table = Counter(str(row[0]) for row in violation_rows)
        quality = _quality_metrics(connection, tables)
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])

    return {
        "database": {
            "path": str(path),
            "size": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "sha256": sha256_file(path),
            "sqlite_version": sqlite3.sqlite_version,
            "user_version": user_version,
        },
        "integrity_check": integrity_check,
        "foreign_keys": {
            "total_violations": len(violation_rows),
            "by_table": dict(sorted(by_table.items())),
            "by_table_and_parent": [
                {"table": table, "missing_parent": parent, "count": count}
                for (table, parent), count in grouped.most_common()
            ],
            "rows": [
                {
                    "table": str(row[0]),
                    "rowid": row[1],
                    "missing_parent": str(row[2]),
                    "foreign_key_id": row[3],
                }
                for row in violation_rows
            ]
            if include_rows
            else [],
        },
        "quality_metrics": quality,
        "table_counts": table_counts,
    }


def _identity_definition(parent_table, columns):
    if parent_table == "catalog_product" and "sku" in columns:
        return "sku"
    if parent_table == "catalog_category":
        if "slug" in columns:
            return "slug"
        if "name" in columns:
            return "name"
    if parent_table == "catalog_supplier":
        if "normalized_name" in columns:
            return "normalized_name"
        if "name" in columns:
            return "name"
    return None


def compare_recovery_candidates(current_database, reference_database, sample_limit=20):
    """Classify dangling parents that can be mapped through stable business identity."""
    current_path = Path(current_database).resolve()
    reference_path = Path(reference_database).resolve()
    result = {
        "current_database": str(current_path),
        "reference_database": str(reference_path),
        "identity_overlap": {},
        "supplier_assignment_recovery": {},
        "groups": [],
        "summary": {
            "violation_rows": 0,
            "missing_unique_ids": 0,
            "identified_in_reference": 0,
            "matched_in_current": 0,
            "identified_violation_rows": 0,
            "matched_violation_rows": 0,
        },
    }
    with open_sqlite_read_only(current_path) as current, open_sqlite_read_only(reference_path) as reference:
        current_tables = set(_table_names(current))
        reference_tables = set(_table_names(reference))

        identity_entities = {
            "products": ("catalog_product", "sku"),
            "categories": ("catalog_category", "slug"),
            "suppliers": ("catalog_supplier", "normalized_name"),
        }
        for label, (table_name, identity_column) in identity_entities.items():
            if table_name not in current_tables or table_name not in reference_tables:
                continue
            if identity_column not in _table_columns(current, table_name):
                continue
            if identity_column not in _table_columns(reference, table_name):
                continue
            current_identity = defaultdict(list)
            current_row_count = 0
            for row in current.execute(
                f"SELECT id, {_quote_identifier(identity_column)} FROM {_quote_identifier(table_name)}"
            ):
                identity = str(row[1] or "").strip().casefold()
                if identity:
                    current_identity[identity].append(row[0])
                    current_row_count += 1
            reference_identity = defaultdict(list)
            reference_row_count = 0
            for row in reference.execute(
                f"SELECT id, {_quote_identifier(identity_column)} FROM {_quote_identifier(table_name)}"
            ):
                identity = str(row[1] or "").strip().casefold()
                if identity:
                    reference_identity[identity].append(row[0])
                    reference_row_count += 1
            shared = set(current_identity) & set(reference_identity)
            result["identity_overlap"][label] = {
                "identity_column": identity_column,
                "current_rows": current_row_count,
                "reference_rows": reference_row_count,
                "current_unique_identities": len(current_identity),
                "reference_unique_identities": len(reference_identity),
                "current_ambiguous_identities": sum(1 for values in current_identity.values() if len(values) > 1),
                "reference_ambiguous_identities": sum(1 for values in reference_identity.values() if len(values) > 1),
                "current_ambiguous_samples": [
                    {"identity": identity, "ids": values}
                    for identity, values in current_identity.items()
                    if len(values) > 1
                ][:sample_limit],
                "reference_ambiguous_samples": [
                    {"identity": identity, "ids": values}
                    for identity, values in reference_identity.items()
                    if len(values) > 1
                ][:sample_limit],
                "shared": len(shared),
                "current_only": len(set(current_identity) - set(reference_identity)),
                "reference_only": len(set(reference_identity) - set(current_identity)),
                "same_database_id": sum(
                    1 for identity in shared
                    if len(current_identity[identity]) == 1
                    and len(reference_identity[identity]) == 1
                    and current_identity[identity][0] == reference_identity[identity][0]
                ),
                "changed_database_id": sum(
                    1 for identity in shared
                    if len(current_identity[identity]) == 1
                    and len(reference_identity[identity]) == 1
                    and current_identity[identity][0] != reference_identity[identity][0]
                ),
            }

        product_columns = _table_columns(current, "catalog_product") if "catalog_product" in current_tables else set()
        reference_product_columns = (
            _table_columns(reference, "catalog_product")
            if "catalog_product" in reference_tables
            else set()
        )
        supplier_columns = _table_columns(current, "catalog_supplier") if "catalog_supplier" in current_tables else set()
        reference_supplier_columns = (
            _table_columns(reference, "catalog_supplier")
            if "catalog_supplier" in reference_tables
            else set()
        )
        if (
            {"id", "sku", "supplier_ref_id"}.issubset(product_columns)
            and {"id", "sku", "supplier_ref_id"}.issubset(reference_product_columns)
            and {"id", "normalized_name"}.issubset(supplier_columns)
            and {"id", "normalized_name"}.issubset(reference_supplier_columns)
        ):
            current_products = defaultdict(list)
            for row in current.execute("SELECT id, sku, supplier_ref_id FROM catalog_product"):
                identity = str(row[1] or "").strip().casefold()
                if identity:
                    current_products[identity].append({"id": row[0], "supplier_ref_id": row[2]})
            current_suppliers = defaultdict(list)
            for row in current.execute("SELECT id, normalized_name FROM catalog_supplier"):
                identity = str(row[1] or "").strip().casefold()
                if identity:
                    current_suppliers[identity].append(row[0])
            reference_assignments = list(
                reference.execute(
                    "SELECT product.sku, supplier.normalized_name "
                    "FROM catalog_product product "
                    "JOIN catalog_supplier supplier ON supplier.id = product.supplier_ref_id "
                    "WHERE product.supplier_ref_id IS NOT NULL"
                )
            )
            shared_products = 0
            missing_current_assignment = 0
            ambiguous_product_identity = 0
            ambiguous_supplier_identity = 0
            recoverable = []
            for row in reference_assignments:
                sku = str(row[0] or "").strip()
                supplier_identity = str(row[1] or "").strip()
                current_product_rows = current_products.get(sku.casefold(), [])
                if not current_product_rows:
                    continue
                shared_products += 1
                if len(current_product_rows) != 1:
                    ambiguous_product_identity += 1
                    continue
                current_product = current_product_rows[0]
                if current_product["supplier_ref_id"] is not None:
                    continue
                missing_current_assignment += 1
                current_supplier_ids = current_suppliers.get(supplier_identity.casefold(), [])
                if len(current_supplier_ids) > 1:
                    ambiguous_supplier_identity += 1
                    continue
                if len(current_supplier_ids) == 1:
                    recoverable.append(
                        {
                            "sku": sku,
                            "current_product_id": current_product["id"],
                            "supplier_identity": supplier_identity,
                            "current_supplier_id": current_supplier_ids[0],
                        }
                    )
            result["supplier_assignment_recovery"] = {
                "reference_assignments": len(reference_assignments),
                "shared_products": shared_products,
                "missing_current_assignment": missing_current_assignment,
                "ambiguous_current_product_identity": ambiguous_product_identity,
                "ambiguous_current_supplier_identity": ambiguous_supplier_identity,
                "recoverable_by_sku_and_supplier_identity": len(recoverable),
                "samples": recoverable[:sample_limit],
            }

        violations = [tuple(row) for row in current.execute("PRAGMA foreign_key_check")]
        grouped = defaultdict(list)
        for table, _rowid, parent, foreign_key_id in violations:
            grouped[(str(table), str(parent), int(foreign_key_id))].append(1)

        for (table, parent, foreign_key_id), rows in sorted(grouped.items()):
            group = {
                "table": table,
                "missing_parent": parent,
                "foreign_key_id": foreign_key_id,
                "violation_rows": len(rows),
                "status": "unsupported",
                "missing_unique_ids": 0,
                "identified_in_reference": 0,
                "matched_in_current": 0,
                "identified_violation_rows": 0,
                "matched_violation_rows": 0,
                "samples": [],
            }
            result["summary"]["violation_rows"] += len(rows)
            if table not in current_tables or parent not in current_tables or parent not in reference_tables:
                result["groups"].append(group)
                continue

            fk_rows = list(current.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})"))
            fk_row = next((row for row in fk_rows if int(row[0]) == foreign_key_id), None)
            if not fk_row:
                result["groups"].append(group)
                continue
            child_column = str(fk_row[3])
            parent_column = str(fk_row[4] or "id")
            parent_columns = _table_columns(current, parent)
            reference_parent_columns = _table_columns(reference, parent)
            identity_column = _identity_definition(parent, parent_columns & reference_parent_columns)
            if not identity_column:
                result["groups"].append(group)
                continue

            query = (
                f"SELECT child.{_quote_identifier(child_column)}, COUNT(*) "
                f"FROM {_quote_identifier(table)} child "
                f"LEFT JOIN {_quote_identifier(parent)} parent "
                f"ON parent.{_quote_identifier(parent_column)} = child.{_quote_identifier(child_column)} "
                f"WHERE child.{_quote_identifier(child_column)} IS NOT NULL "
                f"AND parent.{_quote_identifier(parent_column)} IS NULL "
                f"GROUP BY child.{_quote_identifier(child_column)}"
            )
            missing_value_counts = {row[0]: int(row[1]) for row in current.execute(query)}
            missing_ids = set(missing_value_counts)
            reference_identity = {
                row[0]: str(row[1] or "").strip()
                for row in reference.execute(
                    f"SELECT {_quote_identifier(parent_column)}, {_quote_identifier(identity_column)} "
                    f"FROM {_quote_identifier(parent)}"
                )
                if row[0] in missing_ids and str(row[1] or "").strip()
            }
            current_by_identity = defaultdict(list)
            for row in current.execute(
                f"SELECT {_quote_identifier(parent_column)}, {_quote_identifier(identity_column)} "
                f"FROM {_quote_identifier(parent)}"
            ):
                identity = str(row[1] or "").strip().casefold()
                if identity:
                    current_by_identity[identity].append(row[0])

            matched = []
            for old_id, identity in reference_identity.items():
                candidates = current_by_identity.get(identity.casefold(), [])
                if len(candidates) == 1:
                    matched.append(
                        {
                            "old_id": old_id,
                            "identity_field": identity_column,
                            "identity": identity,
                            "current_id": candidates[0],
                        }
                    )

            matched_old_ids = {row["old_id"] for row in matched}
            identified_violation_rows = sum(
                missing_value_counts[old_id]
                for old_id in reference_identity
            )
            matched_violation_rows = sum(
                missing_value_counts[old_id]
                for old_id in matched_old_ids
            )

            group.update(
                {
                    "status": "classified",
                    "child_column": child_column,
                    "parent_column": parent_column,
                    "identity_column": identity_column,
                    "missing_unique_ids": len(missing_ids),
                    "identified_in_reference": len(reference_identity),
                    "matched_in_current": len(matched),
                    "identified_violation_rows": identified_violation_rows,
                    "matched_violation_rows": matched_violation_rows,
                    "samples": matched[:sample_limit],
                }
            )
            result["summary"]["missing_unique_ids"] += len(missing_ids)
            result["summary"]["identified_in_reference"] += len(reference_identity)
            result["summary"]["matched_in_current"] += len(matched)
            result["summary"]["identified_violation_rows"] += identified_violation_rows
            result["summary"]["matched_violation_rows"] += matched_violation_rows
            result["groups"].append(group)
    return result


def write_integrity_report(report, output_dir, prefix=None):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    database_stem = Path(report["database"]["path"]).stem
    safe_prefix = prefix or database_stem
    json_path = output / f"{safe_prefix}_integrity.json"
    fk_csv_path = output / f"{safe_prefix}_foreign_keys.csv"
    counts_csv_path = output / f"{safe_prefix}_table_counts.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with fk_csv_path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=["table", "rowid", "missing_parent", "foreign_key_id"],
        )
        writer.writeheader()
        writer.writerows(report["foreign_keys"]["rows"])
    with counts_csv_path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.writer(target)
        writer.writerow(["table", "row_count"])
        writer.writerows(sorted(report["table_counts"].items()))
    return {
        "json": str(json_path),
        "foreign_keys_csv": str(fk_csv_path),
        "table_counts_csv": str(counts_csv_path),
    }
