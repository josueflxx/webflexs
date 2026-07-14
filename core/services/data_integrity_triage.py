"""Residual-integrity triage and corroborated category-link recovery."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from core.services.data_integrity import (
    _quote_identifier,
    _table_columns,
    _table_names,
    open_sqlite_read_only,
    sha256_file,
)
from core.services.data_integrity_repair import (
    _planned_operation,
    apply_repair_plan,
    build_repair_plan,
)


TABLE_POLICIES = {
    "catalog_product_categories": (
        "catalogo",
        "high",
        "Reparar solo mediante un mapeo de categoria corroborado por productos.",
    ),
    "catalog_categoryproductorder": (
        "catalogo",
        "high",
        "Reparar junto con la relacion de categoria equivalente.",
    ),
    "catalog_pricelistitem": (
        "precios",
        "high",
        "Reconstruir la identidad del producto o revisar manualmente el precio.",
    ),
    "catalog_clampspecs": (
        "abrazaderas",
        "high",
        "Reconciliar las medidas con un producto unico; no crear productos automaticamente.",
    ),
    "catalog_clampmeasurerequest": (
        "abrazaderas",
        "critical",
        "Revisar la solicitud y normalizar a NULL solo si no existe producto equivalente.",
    ),
    "orders_orderitem": (
        "pedidos",
        "critical",
        "Conservar el snapshot comercial y revisar si la FK debe quedar en NULL.",
    ),
    "orders_orderrequestitem": (
        "pedidos",
        "critical",
        "Conservar el snapshot de solicitud y revisar si la FK debe quedar en NULL.",
    ),
    "core_fiscaldocumentitem": (
        "documentos",
        "critical",
        "Conservar el snapshot fiscal y revisar si la FK debe quedar en NULL.",
    ),
    "core_stockmovement": (
        "stock",
        "critical",
        "Bloqueante: reconciliar el movimiento con un producto o aislar datos de prueba.",
    ),
    "core_catalogexceltemplatesheet_categories": (
        "exportacion",
        "high",
        "Reasignar manualmente la categoria de la plantilla.",
    ),
    "catalog_brandsubrubroproductorder": (
        "catalogo",
        "medium",
        "Revisar el orden de producto dentro del subrubro.",
    ),
}


def _row_counts(connection):
    return {
        table: int(
            connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
            ).fetchone()[0]
        )
        for table in _table_names(connection)
    }


def _category_evidence(connection):
    required = {
        "catalog_category",
        "catalog_product",
        "catalog_product_categories",
        "catalog_categoryproductorder",
    }
    if not required.issubset(_table_names(connection)):
        return {
            "status": "missing_required_tables",
            "accepted": [],
            "rejected": [],
            "repairable_violations": 0,
        }

    products = {
        row[0]: row[1]
        for row in connection.execute("SELECT id, category_id FROM catalog_product")
    }
    categories = {
        row[0]: {"id": row[0], "name": str(row[1]), "slug": str(row[2])}
        for row in connection.execute("SELECT id, name, slug FROM catalog_category")
    }
    missing_memberships = defaultdict(set)
    for category_id, product_id in connection.execute(
        "SELECT category_id, product_id FROM catalog_product_categories "
        "WHERE category_id NOT IN (SELECT id FROM catalog_category)"
    ):
        missing_memberships[category_id].add(product_id)
    order_memberships = defaultdict(set)
    for category_id, product_id in connection.execute(
        "SELECT category_id, product_id FROM catalog_categoryproductorder"
    ):
        order_memberships[category_id].add(product_id)

    preliminarily_accepted = []
    rejected = []
    for old_category_id, product_ids in sorted(missing_memberships.items()):
        reasons = []
        if len(product_ids) < 2:
            reasons.append("insufficient_product_support")
        if order_memberships.get(old_category_id, set()) != product_ids:
            reasons.append("independent_product_sets_do_not_match")
        missing_product_ids = sorted(product_id for product_id in product_ids if product_id not in products)
        if missing_product_ids:
            reasons.append("missing_current_products")
        target_ids = {
            products[product_id]
            for product_id in product_ids
            if product_id in products and products[product_id] is not None
        }
        if any(products.get(product_id) is None for product_id in product_ids):
            reasons.append("product_without_canonical_category")
        if len(target_ids) != 1:
            reasons.append("products_do_not_share_one_canonical_category")
        target_id = next(iter(target_ids)) if len(target_ids) == 1 else None
        if target_id is not None and target_id not in categories:
            reasons.append("canonical_category_is_missing")

        row = {
            "old_category_id": old_category_id,
            "target_category_id": target_id,
            "target_name": categories.get(target_id, {}).get("name", ""),
            "target_slug": categories.get(target_id, {}).get("slug", ""),
            "product_support": len(product_ids) - len(missing_product_ids),
            "product_total": len(product_ids),
            "independent_sets_match": order_memberships.get(old_category_id, set()) == product_ids,
            "missing_product_ids": missing_product_ids[:20],
            "reasons": reasons,
        }
        if reasons:
            rejected.append(row)
        else:
            preliminarily_accepted.append(row)

    target_claims = Counter(row["target_category_id"] for row in preliminarily_accepted)
    valid_pairs = {}
    for table in ("catalog_product_categories", "catalog_categoryproductorder"):
        valid_pairs[table] = set(
            connection.execute(
                f"SELECT category_id, product_id FROM {_quote_identifier(table)} "
                "WHERE category_id IN (SELECT id FROM catalog_category)"
            )
        )

    accepted = []
    for row in preliminarily_accepted:
        old_id = row["old_category_id"]
        target_id = row["target_category_id"]
        reasons = []
        if target_claims[target_id] != 1:
            reasons.append("target_claimed_by_multiple_old_categories")
        product_ids = missing_memberships[old_id]
        collision_tables = [
            table
            for table, pairs in valid_pairs.items()
            if any((target_id, product_id) in pairs for product_id in product_ids)
        ]
        if collision_tables:
            reasons.append("existing_target_pair_collision")
        if reasons:
            row["reasons"] = reasons
            row["collision_tables"] = collision_tables
            rejected.append(row)
            continue
        row["confidence"] = "strong"
        row["corroboration"] = (
            "same_exact_product_set_in_two_tables_and_one_unique_current_canonical_category"
        )
        row["repairable_rows"] = len(product_ids) * 2
        accepted.append(row)

    return {
        "status": "classified",
        "accepted": accepted,
        "rejected": rejected,
        "accepted_mappings": len(accepted),
        "rejected_mappings": len(rejected),
        "repairable_violations": sum(row["repairable_rows"] for row in accepted),
    }


def collect_residual_triage(database_path, reference_databases=(), sample_limit=3):
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la base SQLite: {path}")

    with open_sqlite_read_only(path) as connection:
        integrity_check = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        grouped = defaultdict(list)
        for table, rowid, parent, foreign_key_id in violations:
            grouped[(str(table), str(parent), int(foreign_key_id))].append(rowid)

        groups = []
        risk_counts = Counter()
        domain_counts = Counter()
        non_nullable_violations = 0
        for (table, parent, foreign_key_id), rowids in sorted(grouped.items()):
            fk_rows = [
                row
                for row in connection.execute(
                    f"PRAGMA foreign_key_list({_quote_identifier(table)})"
                )
                if int(row[0]) == foreign_key_id
            ]
            child_column = str(fk_rows[0][3]) if len(fk_rows) == 1 else ""
            on_delete = str(fk_rows[0][6]) if len(fk_rows) == 1 else ""
            table_info = {
                str(row[1]): row
                for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
            }
            nullable = bool(child_column in table_info and not int(table_info[child_column][3]))
            if not nullable:
                non_nullable_violations += len(rowids)
            domain, risk, recommendation = TABLE_POLICIES.get(
                table,
                ("other", "medium", "Revisar manualmente antes de adoptar la base."),
            )
            risk_counts[risk] += len(rowids)
            domain_counts[domain] += len(rowids)

            missing_values = Counter()
            samples = []
            for rowid in rowids:
                cursor = connection.execute(
                    f"SELECT rowid AS __rowid__, * FROM {_quote_identifier(table)} WHERE rowid = ?",
                    (rowid,),
                )
                row = cursor.fetchone()
                if row is None:
                    continue
                columns = [description[0] for description in cursor.description]
                payload = dict(zip(columns, tuple(row)))
                missing_values[payload.get(child_column)] += 1
                if len(samples) < sample_limit:
                    samples.append(payload)
            groups.append(
                {
                    "table": table,
                    "child_column": child_column,
                    "parent_table": parent,
                    "foreign_key_id": foreign_key_id,
                    "violations": len(rowids),
                    "distinct_missing_ids": len(missing_values),
                    "nullable": nullable,
                    "on_delete": on_delete,
                    "domain": domain,
                    "risk": risk,
                    "automatic_action_allowed": False,
                    "recommendation": recommendation,
                    "missing_id_samples": [value for value, _count in missing_values.most_common(20)],
                    "row_samples": samples,
                }
            )
        category_evidence = _category_evidence(connection)

    reference_evidence = []
    for reference_database in reference_databases:
        plan = build_repair_plan(path, reference_database)
        reference_evidence.append(
            {
                "path": str(Path(reference_database).resolve()),
                "sha256": plan["reference_database"]["sha256"],
                "planned_foreign_key_repairs": plan["summary"]["planned_foreign_key_repairs"],
                "unresolved_by_status": plan["summary"]["unresolved_by_status"],
            }
        )

    critical_violations = risk_counts["critical"]
    blockers = []
    if violations:
        blockers.append("foreign_key_violations_remain")
    if non_nullable_violations:
        blockers.append("non_nullable_foreign_keys_are_broken")
    if critical_violations:
        blockers.append("transactional_or_stock_relations_require_review")
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "path": str(path),
            "sha256": sha256_file(path),
            "integrity_check": integrity_check,
        },
        "summary": {
            "foreign_key_violations": len(violations),
            "groups": len(groups),
            "non_nullable_violations": non_nullable_violations,
            "critical_violations": critical_violations,
            "by_risk": dict(sorted(risk_counts.items())),
            "by_domain": dict(sorted(domain_counts.items())),
            "internally_corroborated_category_repairs": category_evidence.get(
                "repairable_violations", 0
            ),
            "adoption_ready": not blockers,
            "adoption_blockers": blockers,
        },
        "reference_evidence": reference_evidence,
        "category_evidence": category_evidence,
        "groups": sorted(groups, key=lambda row: (-row["violations"], row["table"])),
    }


def build_internal_category_repair_plan(database_path):
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la base SQLite: {path}")
    target_hash = sha256_file(path)
    with open_sqlite_read_only(path) as connection:
        integrity_check = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_check != ["ok"]:
            raise ValueError("La base objetivo no supera PRAGMA integrity_check.")
        violations_before = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
        row_counts = _row_counts(connection)
        evidence = _category_evidence(connection)
        operations = []
        for mapping in evidence["accepted"]:
            for table in ("catalog_product_categories", "catalog_categoryproductorder"):
                for rowid, old_category_id in connection.execute(
                    f"SELECT rowid, category_id FROM {_quote_identifier(table)} "
                    "WHERE category_id = ? ORDER BY rowid",
                    (mapping["old_category_id"],),
                ):
                    operations.append(
                        _planned_operation(
                            operation_type="foreign_key_repair",
                            table=table,
                            rowid=rowid,
                            column="category_id",
                            parent_table="catalog_category",
                            foreign_key_id=0 if table == "catalog_product_categories" else 1,
                            old_id=old_category_id,
                            new_id=mapping["target_category_id"],
                            identity_column="corroborated_product_set",
                            identity=mapping["target_slug"],
                        )
                    )

    remaining = max(violations_before - len(operations), 0)
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run",
        "target_database": {
            "path": str(path),
            "sha256_before": target_hash,
            "sha256_after": target_hash,
            "integrity_check": integrity_check,
            "foreign_key_violations": violations_before,
        },
        "reference_database": {
            "path": "internal:corroborated-category-product-sets",
            "sha256": target_hash,
            "integrity_check": integrity_check,
            "foreign_key_violations": violations_before,
            "type": "internal_corroboration",
        },
        "summary": {
            "foreign_key_violations_before": violations_before,
            "foreign_key_violations_after": violations_before,
            "planned_foreign_key_repairs": len(operations),
            "planned_supplier_backfills": 0,
            "planned_operations": len(operations),
            "applied_operations": 0,
            "collisions": 0,
            "stale_operations": 0,
            "unresolved": remaining,
            "unresolved_by_status": {"outside_internal_category_scope": remaining},
            "supplier_backfill": {"status": "not_applicable"},
            "transaction_committed": False,
            "row_counts_unchanged": True,
        },
        "category_evidence": evidence,
        "row_counts_before": row_counts,
        "row_counts_after": dict(row_counts),
        "operations": operations,
        "unresolved": [],
    }


def repair_internal_category_links(database_path, *, apply=False, confirmation=""):
    plan = build_internal_category_repair_plan(database_path)
    if not apply:
        return plan
    return apply_repair_plan(plan, confirmation)


def build_nullable_orphan_repair_plan(database_path):
    """Plan SET NULL repairs only where SQLite or the Django model declares it."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la base SQLite: {path}")
    target_hash = sha256_file(path)
    django_set_null_fields = set()
    try:
        from django.apps import apps
        from django.db.models.deletion import SET_NULL

        for model in apps.get_models():
            for field in model._meta.local_fields:
                remote_field = getattr(field, "remote_field", None)
                if remote_field and field.null and remote_field.on_delete is SET_NULL:
                    django_set_null_fields.add((model._meta.db_table, field.column))
    except Exception:
        # The service remains usable outside a configured Django process when
        # SQLite itself carries an explicit ON DELETE SET NULL declaration.
        django_set_null_fields = set()

    with open_sqlite_read_only(path) as connection:
        integrity_check = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_check != ["ok"]:
            raise ValueError("La base objetivo no supera PRAGMA integrity_check.")
        violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        row_counts = _row_counts(connection)
        operations = []
        for table, rowid, parent, foreign_key_id in violations:
            fk_rows = [
                row
                for row in connection.execute(
                    f"PRAGMA foreign_key_list({_quote_identifier(table)})"
                )
                if int(row[0]) == int(foreign_key_id)
            ]
            if len(fk_rows) != 1:
                continue
            fk_row = fk_rows[0]
            child_column = str(fk_row[3] or "")
            on_delete = str(fk_row[6] or "").upper()
            table_info = {
                str(row[1]): row
                for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
            }
            nullable = bool(child_column in table_info and not int(table_info[child_column][3]))
            declared_by_django = (str(table), child_column) in django_set_null_fields
            if (
                not nullable
                or (on_delete != "SET NULL" and not declared_by_django)
                or rowid is None
            ):
                continue
            child_row = connection.execute(
                f"SELECT {_quote_identifier(child_column)} "
                f"FROM {_quote_identifier(table)} WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if child_row is None or child_row[0] is None:
                continue
            operations.append(
                _planned_operation(
                    operation_type="foreign_key_repair",
                    table=str(table),
                    rowid=rowid,
                    column=child_column,
                    parent_table=str(parent),
                    foreign_key_id=int(foreign_key_id),
                    old_id=child_row[0],
                    new_id=None,
                    identity_column="declared_fk_policy",
                    identity=(
                        "Django SET_NULL"
                        if declared_by_django
                        else "SQLite ON DELETE SET NULL"
                    ),
                )
            )

    remaining = max(len(violations) - len(operations), 0)
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run",
        "target_database": {
            "path": str(path),
            "sha256_before": target_hash,
            "sha256_after": target_hash,
            "integrity_check": integrity_check,
            "foreign_key_violations": len(violations),
        },
        "reference_database": {
            "path": "internal:declared-on-delete-set-null",
            "sha256": target_hash,
            "integrity_check": integrity_check,
            "foreign_key_violations": len(violations),
            "type": "schema_declared_nullability",
        },
        "summary": {
            "foreign_key_violations_before": len(violations),
            "foreign_key_violations_after": len(violations),
            "planned_foreign_key_repairs": len(operations),
            "planned_supplier_backfills": 0,
            "planned_operations": len(operations),
            "applied_operations": 0,
            "collisions": 0,
            "stale_operations": 0,
            "unresolved": remaining,
            "unresolved_by_status": {"non_nullable_or_non_set_null": remaining},
            "supplier_backfill": {"status": "not_applicable"},
            "transaction_committed": False,
            "row_counts_unchanged": True,
        },
        "row_counts_before": row_counts,
        "row_counts_after": dict(row_counts),
        "operations": operations,
        "unresolved": [],
    }


def repair_nullable_orphans(database_path, *, apply=False, confirmation=""):
    plan = build_nullable_orphan_repair_plan(database_path)
    if not apply:
        return plan
    return apply_repair_plan(plan, confirmation)


def write_triage_report(report, output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "residual_triage.json"
    groups_path = output / "residual_groups.csv"
    categories_path = output / "category_evidence.csv"
    summary_path = output / "SUMMARY.md"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    group_fields = [
        "table",
        "child_column",
        "parent_table",
        "foreign_key_id",
        "violations",
        "distinct_missing_ids",
        "nullable",
        "on_delete",
        "domain",
        "risk",
        "automatic_action_allowed",
        "recommendation",
    ]
    with groups_path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=group_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["groups"])
    category_fields = [
        "status",
        "old_category_id",
        "target_category_id",
        "target_name",
        "target_slug",
        "product_support",
        "product_total",
        "independent_sets_match",
        "confidence",
        "repairable_rows",
        "reasons",
    ]
    category_rows = []
    for status in ("accepted", "rejected"):
        for row in report["category_evidence"].get(status, []):
            category_rows.append({"status": status, **row})
    with categories_path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=category_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(category_rows)

    summary = report["summary"]
    lines = [
        "# Triage residual de integridad SQLite",
        "",
        f"- Violaciones FK: {summary['foreign_key_violations']}",
        f"- Grupos afectados: {summary['groups']}",
        f"- Relaciones no anulables rotas: {summary['non_nullable_violations']}",
        f"- Relaciones criticas: {summary['critical_violations']}",
        f"- Reparaciones de categoria corroboradas internamente: {summary['internally_corroborated_category_repairs']}",
        f"- Lista para adopcion: {summary['adoption_ready']}",
        "",
        "## Bloqueantes",
        "",
    ]
    lines.extend(f"- {blocker}" for blocker in summary["adoption_blockers"])
    lines.extend(
        [
            "",
            "> Ninguna accion residual se autoriza automaticamente por este informe.",
            "",
        ]
    )
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "json": str(json_path),
        "groups_csv": str(groups_path),
        "category_evidence_csv": str(categories_path),
        "summary": str(summary_path),
    }
