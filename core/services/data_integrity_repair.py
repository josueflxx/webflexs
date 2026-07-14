"""Protected, evidence-based repair helpers for damaged SQLite foreign keys."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from core.services.data_integrity import (
    _identity_definition,
    _quote_identifier,
    _table_columns,
    _table_names,
    open_sqlite_read_only,
    sha256_file,
)


APPLY_CONFIRMATION = "WORKING_COPY_ONLY"


def _normalise_identity(value):
    return str(value or "").strip().casefold()


def _database_checks(connection):
    return {
        "integrity_check": [str(row[0]) for row in connection.execute("PRAGMA integrity_check")],
        "foreign_key_violations": sum(1 for _ in connection.execute("PRAGMA foreign_key_check")),
    }


def _table_row_counts(connection):
    return {
        table: int(
            connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
            ).fetchone()[0]
        )
        for table in _table_names(connection)
    }


def _resolve_parent_column(connection, table, declared_column):
    if declared_column:
        return str(declared_column)
    primary_key_columns = [
        (int(row[5]), str(row[1]))
        for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
        if int(row[5] or 0) > 0
    ]
    if len(primary_key_columns) != 1:
        return None
    return primary_key_columns[0][1]


def _shared_identity_column(current, reference, parent_table):
    current_columns = _table_columns(current, parent_table)
    reference_columns = _table_columns(reference, parent_table)
    shared_columns = current_columns & reference_columns
    return _identity_definition(parent_table, shared_columns)


def _identity_maps(connection, table, id_column, identity_column):
    by_id = {}
    by_identity = defaultdict(list)
    sql = (
        f"SELECT {_quote_identifier(id_column)}, {_quote_identifier(identity_column)} "
        f"FROM {_quote_identifier(table)}"
    )
    for database_id, raw_identity in connection.execute(sql):
        identity = _normalise_identity(raw_identity)
        if not identity:
            continue
        by_id[database_id] = {
            "normalised": identity,
            "display": str(raw_identity).strip(),
        }
        by_identity[identity].append(database_id)
    return by_id, by_identity


def _child_values(connection, table, column, rowids):
    values = {}
    concrete_rowids = [rowid for rowid in rowids if rowid is not None]
    for offset in range(0, len(concrete_rowids), 800):
        chunk = concrete_rowids[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        sql = (
            f"SELECT rowid, {_quote_identifier(column)} "
            f"FROM {_quote_identifier(table)} WHERE rowid IN ({placeholders})"
        )
        for rowid, value in connection.execute(sql, chunk):
            values[rowid] = value
    return values


def _unresolved_operation(
    *,
    operation_type,
    table,
    rowid,
    column="",
    parent_table="",
    foreign_key_id="",
    old_id=None,
    identity_column="",
    identity="",
    status,
    error="",
):
    return {
        "operation_type": operation_type,
        "table": table,
        "rowid": rowid,
        "column": column,
        "parent_table": parent_table,
        "foreign_key_id": foreign_key_id,
        "old_id": old_id,
        "new_id": None,
        "identity_column": identity_column,
        "identity": identity,
        "status": status,
        "error": error,
    }


def _planned_operation(
    *,
    operation_type,
    table,
    rowid,
    column,
    parent_table,
    foreign_key_id="",
    old_id=None,
    new_id,
    identity_column,
    identity,
):
    return {
        "operation_type": operation_type,
        "table": table,
        "rowid": rowid,
        "column": column,
        "parent_table": parent_table,
        "foreign_key_id": foreign_key_id,
        "old_id": old_id,
        "new_id": new_id,
        "identity_column": identity_column,
        "identity": identity,
        "status": "planned",
        "error": "",
    }


def _build_foreign_key_operations(current, reference, violations):
    operations = []
    unresolved = []
    current_tables = set(_table_names(current))
    reference_tables = set(_table_names(reference))
    grouped = defaultdict(list)
    for table, rowid, parent, foreign_key_id in violations:
        grouped[(str(table), str(parent), int(foreign_key_id))].append(rowid)

    identity_cache = {}
    for (table, parent, foreign_key_id), rowids in sorted(grouped.items()):
        common = {
            "operation_type": "foreign_key_repair",
            "table": table,
            "parent_table": parent,
            "foreign_key_id": foreign_key_id,
        }
        if (
            table not in current_tables
            or parent not in current_tables
            or parent not in reference_tables
        ):
            for rowid in rowids:
                unresolved.append(
                    _unresolved_operation(
                        **common,
                        rowid=rowid,
                        status="missing_required_table",
                    )
                )
            continue

        fk_rows = [
            row
            for row in current.execute(
                f"PRAGMA foreign_key_list({_quote_identifier(table)})"
            )
            if int(row[0]) == foreign_key_id
        ]
        if len(fk_rows) != 1:
            for rowid in rowids:
                unresolved.append(
                    _unresolved_operation(
                        **common,
                        rowid=rowid,
                        status="composite_or_missing_foreign_key",
                    )
                )
            continue

        fk_row = fk_rows[0]
        child_column = str(fk_row[3] or "")
        parent_column = _resolve_parent_column(current, parent, fk_row[4])
        identity_column = _shared_identity_column(current, reference, parent)
        if not child_column or not parent_column or not identity_column:
            for rowid in rowids:
                unresolved.append(
                    _unresolved_operation(
                        **common,
                        rowid=rowid,
                        column=child_column,
                        identity_column=identity_column or "",
                        status="unsupported_parent_identity",
                    )
                )
            continue

        cache_key = (parent, parent_column, identity_column)
        if cache_key not in identity_cache:
            reference_by_id, reference_by_identity = _identity_maps(
                reference,
                parent,
                parent_column,
                identity_column,
            )
            _current_by_id, current_by_identity = _identity_maps(
                current,
                parent,
                parent_column,
                identity_column,
            )
            identity_cache[cache_key] = (
                reference_by_id,
                reference_by_identity,
                current_by_identity,
            )
        reference_by_id, reference_by_identity, current_by_identity = identity_cache[cache_key]
        child_values = _child_values(current, table, child_column, rowids)

        for rowid in rowids:
            old_id = child_values.get(rowid)
            base = {
                **common,
                "rowid": rowid,
                "column": child_column,
                "old_id": old_id,
                "identity_column": identity_column,
            }
            if rowid is None or rowid not in child_values:
                unresolved.append(
                    _unresolved_operation(
                        **base,
                        status="missing_child_row",
                    )
                )
                continue
            reference_identity = reference_by_id.get(old_id)
            if not reference_identity:
                unresolved.append(
                    _unresolved_operation(
                        **base,
                        status="missing_reference_identity",
                    )
                )
                continue
            identity = reference_identity["normalised"]
            display_identity = reference_identity["display"]
            if len(reference_by_identity[identity]) != 1:
                unresolved.append(
                    _unresolved_operation(
                        **base,
                        identity=display_identity,
                        status="ambiguous_reference_identity",
                    )
                )
                continue
            candidates = current_by_identity.get(identity, [])
            if not candidates:
                unresolved.append(
                    _unresolved_operation(
                        **base,
                        identity=display_identity,
                        status="missing_current_target",
                    )
                )
                continue
            if len(candidates) != 1:
                unresolved.append(
                    _unresolved_operation(
                        **base,
                        identity=display_identity,
                        status="ambiguous_current_target",
                    )
                )
                continue
            operations.append(
                _planned_operation(
                    **base,
                    new_id=candidates[0],
                    identity=display_identity,
                )
            )
    return operations, unresolved


def _build_supplier_backfills(current, reference):
    operation_type = "supplier_backfill"
    product_table = "catalog_product"
    supplier_table = "catalog_supplier"
    required_tables = {product_table, supplier_table}
    if not required_tables.issubset(_table_names(current)) or not required_tables.issubset(
        _table_names(reference)
    ):
        return [], [], {"status": "skipped_missing_tables"}

    required_product_columns = {"id", "sku", "supplier_ref_id"}
    required_supplier_columns = {"id", "normalized_name"}
    if not required_product_columns.issubset(_table_columns(current, product_table)):
        return [], [], {"status": "skipped_missing_current_columns"}
    if not required_product_columns.issubset(_table_columns(reference, product_table)):
        return [], [], {"status": "skipped_missing_reference_columns"}
    if not required_supplier_columns.issubset(_table_columns(current, supplier_table)):
        return [], [], {"status": "skipped_missing_current_supplier_columns"}
    if not required_supplier_columns.issubset(_table_columns(reference, supplier_table)):
        return [], [], {"status": "skipped_missing_reference_supplier_columns"}

    current_products = defaultdict(list)
    for rowid, product_id, sku, supplier_id in current.execute(
        "SELECT rowid, id, sku, supplier_ref_id FROM catalog_product"
    ):
        identity = _normalise_identity(sku)
        if identity:
            current_products[identity].append(
                {
                    "rowid": rowid,
                    "id": product_id,
                    "sku": str(sku).strip(),
                    "supplier_ref_id": supplier_id,
                }
            )

    reference_products = defaultdict(list)
    for product_id, sku, supplier_id in reference.execute(
        "SELECT id, sku, supplier_ref_id FROM catalog_product"
    ):
        identity = _normalise_identity(sku)
        if identity:
            reference_products[identity].append(
                {
                    "id": product_id,
                    "sku": str(sku).strip(),
                    "supplier_ref_id": supplier_id,
                }
            )

    reference_suppliers_by_id, reference_suppliers_by_identity = _identity_maps(
        reference,
        supplier_table,
        "id",
        "normalized_name",
    )
    _current_suppliers_by_id, current_suppliers_by_identity = _identity_maps(
        current,
        supplier_table,
        "id",
        "normalized_name",
    )

    operations = []
    unresolved = []
    statistics = Counter()
    statistics["reference_product_identities"] = len(reference_products)
    for sku_identity, reference_rows in reference_products.items():
        current_rows = current_products.get(sku_identity, [])
        if not current_rows:
            statistics["not_present_in_current"] += 1
            continue
        if len(reference_rows) != 1:
            if any(row["supplier_ref_id"] is None for row in current_rows):
                unresolved.append(
                    _unresolved_operation(
                        operation_type=operation_type,
                        table=product_table,
                        rowid="",
                        column="supplier_ref_id",
                        parent_table=supplier_table,
                        identity_column="sku",
                        identity=reference_rows[0]["sku"],
                        status="ambiguous_reference_product_identity",
                    )
                )
            statistics["ambiguous_reference_product_identity"] += 1
            continue
        if len(current_rows) != 1:
            if any(row["supplier_ref_id"] is None for row in current_rows):
                unresolved.append(
                    _unresolved_operation(
                        operation_type=operation_type,
                        table=product_table,
                        rowid="",
                        column="supplier_ref_id",
                        parent_table=supplier_table,
                        identity_column="sku",
                        identity=reference_rows[0]["sku"],
                        status="ambiguous_current_product_identity",
                    )
                )
            statistics["ambiguous_current_product_identity"] += 1
            continue

        current_product = current_rows[0]
        if current_product["supplier_ref_id"] is not None:
            statistics["already_assigned"] += 1
            continue
        reference_supplier_id = reference_rows[0]["supplier_ref_id"]
        if reference_supplier_id is None:
            statistics["reference_without_assignment"] += 1
            continue
        supplier_identity = reference_suppliers_by_id.get(reference_supplier_id)
        base = {
            "operation_type": operation_type,
            "table": product_table,
            "rowid": current_product["rowid"],
            "column": "supplier_ref_id",
            "parent_table": supplier_table,
            "old_id": None,
            "identity_column": "normalized_name",
        }
        if not supplier_identity:
            unresolved.append(
                _unresolved_operation(
                    **base,
                    identity=current_product["sku"],
                    status="missing_reference_supplier_identity",
                )
            )
            statistics["missing_reference_supplier_identity"] += 1
            continue
        normalised_supplier = supplier_identity["normalised"]
        display_supplier = supplier_identity["display"]
        if len(reference_suppliers_by_identity[normalised_supplier]) != 1:
            unresolved.append(
                _unresolved_operation(
                    **base,
                    identity=display_supplier,
                    status="ambiguous_reference_supplier_identity",
                )
            )
            statistics["ambiguous_reference_supplier_identity"] += 1
            continue
        current_supplier_ids = current_suppliers_by_identity.get(normalised_supplier, [])
        if not current_supplier_ids:
            unresolved.append(
                _unresolved_operation(
                    **base,
                    identity=display_supplier,
                    status="missing_current_supplier",
                )
            )
            statistics["missing_current_supplier"] += 1
            continue
        if len(current_supplier_ids) != 1:
            unresolved.append(
                _unresolved_operation(
                    **base,
                    identity=display_supplier,
                    status="ambiguous_current_supplier_identity",
                )
            )
            statistics["ambiguous_current_supplier_identity"] += 1
            continue
        operations.append(
            _planned_operation(
                **base,
                new_id=current_supplier_ids[0],
                identity=display_supplier,
            )
        )
        statistics["planned"] += 1
    statistics["status"] = "classified"
    return operations, unresolved, dict(statistics)


def build_repair_plan(target_database, reference_database):
    """Build a read-only repair plan using unique historical business identities."""
    target_path = Path(target_database).resolve()
    reference_path = Path(reference_database).resolve()
    if not target_path.is_file():
        raise FileNotFoundError(f"No existe la base objetivo: {target_path}")
    if not reference_path.is_file():
        raise FileNotFoundError(f"No existe la base historica: {reference_path}")
    if target_path == reference_path:
        raise ValueError("La base objetivo y la historica deben ser archivos distintos.")

    target_hash_before = sha256_file(target_path)
    reference_hash = sha256_file(reference_path)
    with open_sqlite_read_only(target_path) as current, open_sqlite_read_only(
        reference_path
    ) as reference:
        target_checks = _database_checks(current)
        reference_checks = _database_checks(reference)
        if target_checks["integrity_check"] != ["ok"]:
            raise ValueError("La base objetivo no supera PRAGMA integrity_check.")
        if reference_checks["integrity_check"] != ["ok"]:
            raise ValueError("La base historica no supera PRAGMA integrity_check.")
        if reference_checks["foreign_key_violations"] != 0:
            raise ValueError("La base historica contiene claves foraneas rotas.")

        violations = [tuple(row) for row in current.execute("PRAGMA foreign_key_check")]
        row_counts = _table_row_counts(current)
        foreign_key_operations, foreign_key_unresolved = _build_foreign_key_operations(
            current,
            reference,
            violations,
        )
        supplier_operations, supplier_unresolved, supplier_statistics = (
            _build_supplier_backfills(current, reference)
        )

    operations = foreign_key_operations + supplier_operations
    unresolved = foreign_key_unresolved + supplier_unresolved
    unresolved_counts = Counter(row["status"] for row in unresolved)
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run",
        "target_database": {
            "path": str(target_path),
            "sha256_before": target_hash_before,
            "sha256_after": target_hash_before,
            **target_checks,
        },
        "reference_database": {
            "path": str(reference_path),
            "sha256": reference_hash,
            **reference_checks,
        },
        "summary": {
            "foreign_key_violations_before": len(violations),
            "foreign_key_violations_after": len(violations),
            "planned_foreign_key_repairs": len(foreign_key_operations),
            "planned_supplier_backfills": len(supplier_operations),
            "planned_operations": len(operations),
            "applied_operations": 0,
            "collisions": 0,
            "stale_operations": 0,
            "unresolved": len(unresolved),
            "unresolved_by_status": dict(sorted(unresolved_counts.items())),
            "supplier_backfill": supplier_statistics,
            "transaction_committed": False,
            "row_counts_unchanged": True,
        },
        "row_counts_before": row_counts,
        "row_counts_after": dict(row_counts),
        "operations": operations,
        "unresolved": unresolved,
    }


def validate_protected_working_copy(database_path):
    """Require a fresh working copy produced by the preservation service."""
    path = Path(database_path).resolve()
    if path.name != "database_working.sqlite3":
        raise ValueError("La aplicacion solo acepta un archivo database_working.sqlite3.")
    manifest_path = path.parent / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("La copia de trabajo no tiene manifest.json de preservacion.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    working = manifest.get("working_copy") or {}
    preservation = manifest.get("preservation") or {}
    declared_working = Path(working.get("path", "")).resolve()
    preservation_path = Path(preservation.get("path", "")).resolve()
    if declared_working != path:
        raise ValueError("El manifiesto no corresponde a la copia de trabajo indicada.")
    if not preservation_path.is_file():
        raise ValueError("No existe la copia de preservacion declarada por el manifiesto.")
    if sha256_file(preservation_path) != preservation.get("sha256"):
        raise ValueError("La copia de preservacion no coincide con su hash declarado.")
    if sha256_file(path) != working.get("sha256"):
        raise ValueError("La copia de trabajo ya cambio respecto del snapshot original.")
    return manifest


def _apply_operation(connection, operation, position):
    savepoint = f"repair_{position}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        table = _quote_identifier(operation["table"])
        column = _quote_identifier(operation["column"])
        if operation["operation_type"] == "supplier_backfill":
            sql = f"UPDATE {table} SET {column} = ? WHERE rowid = ? AND {column} IS NULL"
            parameters = (operation["new_id"], operation["rowid"])
        else:
            sql = f"UPDATE {table} SET {column} = ? WHERE rowid = ? AND {column} = ?"
            parameters = (
                operation["new_id"],
                operation["rowid"],
                operation["old_id"],
            )
        cursor = connection.execute(sql, parameters)
        connection.execute(f"RELEASE {savepoint}")
        if cursor.rowcount == 1:
            operation["status"] = "applied"
            return "applied"
        operation["status"] = "stale"
        operation["error"] = "La fila o el valor original ya no coincide."
        return "stale"
    except sqlite3.IntegrityError as exc:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        operation["status"] = "collision"
        operation["error"] = str(exc)
        return "collision"


def apply_repair_plan(plan, confirmation):
    """Apply a previously generated plan under one guarded SQLite transaction."""
    if confirmation != APPLY_CONFIRMATION:
        raise ValueError(f"La aplicacion requiere --confirm {APPLY_CONFIRMATION}.")
    target_path = Path(plan["target_database"]["path"]).resolve()
    validate_protected_working_copy(target_path)
    if sha256_file(target_path) != plan["target_database"]["sha256_before"]:
        raise ValueError("La copia objetivo cambio despues de generar el plan.")

    connection = sqlite3.connect(target_path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA busy_timeout = 5000")
    outcomes = Counter()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row_counts_before = _table_row_counts(connection)
        foreign_keys_before = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
        for position, operation in enumerate(plan["operations"], start=1):
            outcomes[_apply_operation(connection, operation, position)] += 1

        row_counts_after = _table_row_counts(connection)
        foreign_keys_after = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
        integrity_after = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if row_counts_after != row_counts_before:
            raise RuntimeError("La reparacion altero la cantidad de filas de alguna tabla.")
        if foreign_keys_after > foreign_keys_before:
            raise RuntimeError("La reparacion incremento las violaciones de claves foraneas.")
        if integrity_after != ["ok"]:
            raise RuntimeError("La copia reparada no supera PRAGMA integrity_check.")
        connection.commit()
    except Exception:
        connection.rollback()
        for operation in plan["operations"]:
            if operation["status"] == "applied":
                operation["status"] = "rolled_back"
        raise
    finally:
        connection.close()

    plan["mode"] = "apply"
    plan["target_database"]["sha256_after"] = sha256_file(target_path)
    plan["target_database"]["integrity_check_after"] = integrity_after
    plan["target_database"]["foreign_key_violations"] = foreign_keys_after
    plan["summary"].update(
        {
            "foreign_key_violations_before": foreign_keys_before,
            "foreign_key_violations_after": foreign_keys_after,
            "applied_operations": outcomes["applied"],
            "collisions": outcomes["collision"],
            "stale_operations": outcomes["stale"],
            "transaction_committed": True,
            "row_counts_unchanged": row_counts_after == row_counts_before,
        }
    )
    plan["row_counts_before"] = row_counts_before
    plan["row_counts_after"] = row_counts_after
    return plan


def repair_data_integrity(
    target_database,
    reference_database,
    *,
    apply=False,
    confirmation="",
):
    plan = build_repair_plan(target_database, reference_database)
    if not apply:
        return plan
    return apply_repair_plan(plan, confirmation)


REPAIR_CSV_FIELDS = [
    "operation_type",
    "table",
    "rowid",
    "column",
    "parent_table",
    "foreign_key_id",
    "old_id",
    "new_id",
    "identity_column",
    "identity",
    "status",
    "error",
]


def write_repair_report(result, output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "repair_result.json"
    operations_path = output / "repair_operations.csv"
    unresolved_path = output / "repair_unresolved.csv"
    summary_path = output / "SUMMARY.md"

    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for path, rows in (
        (operations_path, result["operations"]),
        (unresolved_path, result["unresolved"]),
    ):
        with path.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=REPAIR_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    summary = result["summary"]
    lines = [
        "# Reparacion protegida de integridad SQLite",
        "",
        f"- Modo: `{result['mode']}`",
        f"- Base objetivo: `{result['target_database']['path']}`",
        f"- Backup historico: `{result['reference_database']['path']}`",
        f"- Violaciones FK antes: {summary['foreign_key_violations_before']}",
        f"- Violaciones FK despues: {summary['foreign_key_violations_after']}",
        f"- Reparaciones FK planificadas: {summary['planned_foreign_key_repairs']}",
        f"- Proveedores planificados: {summary['planned_supplier_backfills']}",
        f"- Operaciones aplicadas: {summary['applied_operations']}",
        f"- Colisiones omitidas: {summary['collisions']}",
        f"- Casos sin resolver: {summary['unresolved']}",
        f"- Cantidades de filas sin cambios: {summary['row_counts_unchanged']}",
        f"- Transaccion confirmada: {summary['transaction_committed']}",
        "",
        "> El proceso solo actualiza claves identificadas de forma univoca; no crea ni elimina filas.",
        "",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "json": str(json_path),
        "operations_csv": str(operations_path),
        "unresolved_csv": str(unresolved_path),
        "summary": str(summary_path),
    }
