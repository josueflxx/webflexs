"""Reversible quarantine for unrecoverable SQLite foreign-key orphan rows."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from core.services.data_integrity import (
    _quote_identifier,
    _table_names,
    open_sqlite_read_only,
    sha256_file,
)
from core.services.data_integrity_repair import (
    _table_row_counts,
    validate_protected_working_copy,
)


QUARANTINE_CONFIRMATION = "QUARANTINE_ORPHANS"


def _json_value(value):
    if isinstance(value, bytes):
        return {"__type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_digest(table, rowid, row_data, violations):
    payload = {
        "table": table,
        "source_rowid": rowid,
        "row": row_data,
        "violations": violations,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _database_checks(connection):
    return {
        "integrity_check": [str(row[0]) for row in connection.execute("PRAGMA integrity_check")],
        "foreign_key_violations": sum(1 for _ in connection.execute("PRAGMA foreign_key_check")),
    }


def build_quarantine_plan(database_path):
    """Describe all unique child rows currently reported by foreign_key_check."""
    target = Path(database_path).resolve()
    validate_protected_working_copy(target)

    with open_sqlite_read_only(target) as connection:
        checks = _database_checks(connection)
        if checks["integrity_check"] != ["ok"]:
            raise RuntimeError("La copia no supera PRAGMA integrity_check.")

        raw_violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        grouped = defaultdict(list)
        for table, rowid, parent, foreign_key_id in raw_violations:
            if rowid is None:
                raise RuntimeError(
                    f"No se puede cuarentenar {table}: SQLite no informa rowid para la fila rota."
                )
            grouped[(str(table), int(rowid))].append(
                {
                    "missing_parent": str(parent),
                    "foreign_key_id": int(foreign_key_id),
                }
            )

        rows = []
        by_table = Counter()
        for (table, rowid), violations in sorted(grouped.items()):
            columns = [
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_quote_identifier(table)})"
                )
            ]
            if not columns:
                raise RuntimeError(f"No se pudo leer el esquema de {table}.")
            select_columns = ", ".join(_quote_identifier(column) for column in columns)
            record = connection.execute(
                f"SELECT {select_columns} FROM {_quote_identifier(table)} WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if record is None:
                raise RuntimeError(f"La fila {table}[rowid={rowid}] ya no existe.")
            row_data = {
                column: _json_value(record[position])
                for position, column in enumerate(columns)
            }
            ordered_violations = sorted(
                violations,
                key=lambda item: (item["foreign_key_id"], item["missing_parent"]),
            )
            rows.append(
                {
                    "table": table,
                    "source_rowid": rowid,
                    "row": row_data,
                    "violations": ordered_violations,
                    "row_sha256": _row_digest(table, rowid, row_data, ordered_violations),
                }
            )
            by_table[table] += 1
        row_counts = _table_row_counts(connection)

    return {
        "version": 1,
        "mode": "dry_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_database": {
            "path": str(target),
            "sha256_before": sha256_file(target),
            "sha256_after": "",
        },
        "summary": {
            "foreign_key_violations_before": len(raw_violations),
            "foreign_key_violations_after": len(raw_violations),
            "unique_rows_planned": len(rows),
            "unique_rows_quarantined": 0,
            "rows_by_table": dict(sorted(by_table.items())),
            "archive_verified": False,
            "transaction_committed": False,
        },
        "row_counts_before": row_counts,
        "row_counts_after": dict(row_counts),
        "rows": rows,
    }


def _create_archive(plan, output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "quarantined_rows.sqlite3"
    if archive_path.exists():
        raise FileExistsError(f"El archivo de cuarentena ya existe: {archive_path}")

    connection = sqlite3.connect(archive_path)
    try:
        connection.executescript(
            "CREATE TABLE quarantine_metadata ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL"
            ");"
            "CREATE TABLE quarantined_row ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "table_name TEXT NOT NULL, source_rowid INTEGER NOT NULL, "
            "row_json TEXT NOT NULL, violations_json TEXT NOT NULL, "
            "row_sha256 TEXT NOT NULL, "
            "UNIQUE(table_name, source_rowid)"
            ");"
        )
        metadata = {
            "version": "1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_database": plan["target_database"]["path"],
            "source_sha256": plan["target_database"]["sha256_before"],
            "foreign_key_violations": str(plan["summary"]["foreign_key_violations_before"]),
            "unique_rows": str(plan["summary"]["unique_rows_planned"]),
        }
        connection.executemany(
            "INSERT INTO quarantine_metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.executemany(
            "INSERT INTO quarantined_row("
            "table_name, source_rowid, row_json, violations_json, row_sha256"
            ") VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["table"],
                    row["source_rowid"],
                    _canonical_json(row["row"]),
                    _canonical_json(row["violations"]),
                    row["row_sha256"],
                )
                for row in plan["rows"]
            ],
        )
        connection.commit()
    finally:
        connection.close()

    verification = sqlite3.connect(f"file:{archive_path.as_posix()}?mode=ro", uri=True)
    try:
        archived_rows = list(
            verification.execute(
                "SELECT table_name, source_rowid, row_json, violations_json, row_sha256 "
                "FROM quarantined_row ORDER BY table_name, source_rowid"
            )
        )
    finally:
        verification.close()
    if len(archived_rows) != len(plan["rows"]):
        raise RuntimeError("El archivo de cuarentena no contiene todas las filas planificadas.")
    for table, rowid, row_json, violations_json, stored_digest in archived_rows:
        calculated = _row_digest(table, rowid, json.loads(row_json), json.loads(violations_json))
        if calculated != stored_digest:
            raise RuntimeError(f"Hash invalido en la cuarentena para {table}[rowid={rowid}].")

    return {
        "path": str(archive_path),
        "sha256": sha256_file(archive_path),
        "rows": len(archived_rows),
        "verified": True,
    }


def apply_quarantine_plan(plan, output_dir, confirmation):
    if confirmation != QUARANTINE_CONFIRMATION:
        raise ValueError(
            f"La aplicacion requiere --confirm {QUARANTINE_CONFIRMATION}."
        )
    target = Path(plan["target_database"]["path"]).resolve()
    validate_protected_working_copy(target)
    if sha256_file(target) != plan["target_database"]["sha256_before"]:
        raise ValueError("La copia objetivo cambio despues de generar el plan.")

    archive = _create_archive(plan, output_dir)
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        before_counts = _table_row_counts(connection)
        before_fk = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
        deleted = Counter()
        for row in plan["rows"]:
            cursor = connection.execute(
                f"DELETE FROM {_quote_identifier(row['table'])} WHERE rowid = ?",
                (row["source_rowid"],),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"La fila {row['table']}[rowid={row['source_rowid']}] cambio antes de retirarla."
                )
            deleted[row["table"]] += 1

        after_counts = _table_row_counts(connection)
        expected_counts = dict(before_counts)
        for table, count in deleted.items():
            expected_counts[table] -= count
        if after_counts != expected_counts:
            raise RuntimeError("Las cantidades finales no coinciden con el plan de cuarentena.")
        after_fk = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
        integrity_after = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if after_fk != 0:
            raise RuntimeError(
                f"La cuarentena dejaria {after_fk} violaciones FK; se revirtio la transaccion."
            )
        if integrity_after != ["ok"]:
            raise RuntimeError("La copia resultante no supera PRAGMA integrity_check.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    plan["mode"] = "apply"
    plan["archive"] = archive
    plan["target_database"]["sha256_after"] = sha256_file(target)
    plan["summary"].update(
        {
            "foreign_key_violations_before": before_fk,
            "foreign_key_violations_after": after_fk,
            "unique_rows_quarantined": sum(deleted.values()),
            "archive_verified": True,
            "transaction_committed": True,
        }
    )
    plan["row_counts_before"] = before_counts
    plan["row_counts_after"] = after_counts
    return plan


def quarantine_orphans(database_path, output_dir, *, apply=False, confirmation=""):
    plan = build_quarantine_plan(database_path)
    if not apply:
        return plan
    return apply_quarantine_plan(plan, output_dir, confirmation)


def write_quarantine_report(result, output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "quarantine_result.json"
    csv_path = output / "quarantine_index.csv"
    summary_path = output / "SUMMARY.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=["table", "source_rowid", "row_sha256", "violations"],
        )
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow(
                {
                    "table": row["table"],
                    "source_rowid": row["source_rowid"],
                    "row_sha256": row["row_sha256"],
                    "violations": _canonical_json(row["violations"]),
                }
            )
    summary = result["summary"]
    lines = [
        "# Cuarentena reversible de residuos SQLite",
        "",
        f"- Modo: `{result['mode']}`",
        f"- Base objetivo: `{result['target_database']['path']}`",
        f"- Violaciones FK antes: {summary['foreign_key_violations_before']}",
        f"- Violaciones FK despues: {summary['foreign_key_violations_after']}",
        f"- Filas unicas planificadas: {summary['unique_rows_planned']}",
        f"- Filas cuarentenadas: {summary['unique_rows_quarantined']}",
        f"- Archivo verificado: {summary['archive_verified']}",
        f"- Transaccion confirmada: {summary['transaction_committed']}",
    ]
    if result.get("archive"):
        lines.extend(
            [
                f"- Archivo SQLite: `{result['archive']['path']}`",
                f"- SHA-256 archivo: `{result['archive']['sha256']}`",
            ]
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "summary": str(summary_path)}
