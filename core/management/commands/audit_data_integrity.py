import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.data_integrity import (
    collect_integrity_report,
    compare_recovery_candidates,
    utc_stamp,
    write_integrity_report,
)


class Command(BaseCommand):
    help = "Audita bases SQLite en modo lectura y genera reportes de integridad."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            action="append",
            default=[],
            help="Ruta SQLite a auditar. Puede repetirse.",
        )
        parser.add_argument(
            "--reference-database",
            action="append",
            default=[],
            help="Backup usado para clasificar IDs huerfanos por SKU/slug. Puede repetirse.",
        )
        parser.add_argument("--output-dir", default="", help="Directorio de reportes.")

    def handle(self, *args, **options):
        configured_database = settings.DATABASES.get("default", {})
        databases = list(options["database"] or [])
        if not databases:
            if configured_database.get("ENGINE") != "django.db.backends.sqlite3":
                raise CommandError("La base configurada no es SQLite; indica --database.")
            databases = [str(configured_database.get("NAME"))]

        output_dir = Path(options["output_dir"] or (
            Path(settings.BASE_DIR) / "backups" / "integrity_audit" / f"report_{utc_stamp()}"
        )).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        index = {"databases": [], "comparisons": []}
        current_database = Path(databases[0]).resolve()
        used_prefixes = set()
        for position, database in enumerate(databases, start=1):
            path = Path(database).resolve()
            prefix = path.stem
            if prefix in used_prefixes:
                prefix = f"{prefix}_{position}"
            used_prefixes.add(prefix)
            try:
                report = collect_integrity_report(path)
                files = write_integrity_report(report, output_dir, prefix=prefix)
            except Exception as exc:
                raise CommandError(f"No se pudo auditar {path}: {exc}") from exc
            index["databases"].append(
                {
                    "path": str(path),
                    "sha256": report["database"]["sha256"],
                    "integrity_check": report["integrity_check"],
                    "foreign_key_violations": report["foreign_keys"]["total_violations"],
                    "quality_metrics": report["quality_metrics"],
                    "files": files,
                }
            )
            self.stdout.write(
                f"{path.name}: FK={report['foreign_keys']['total_violations']} "
                f"integrity={','.join(report['integrity_check'][:3])}"
            )

        for reference in options["reference_database"] or []:
            reference_path = Path(reference).resolve()
            try:
                comparison = compare_recovery_candidates(current_database, reference_path)
            except Exception as exc:
                raise CommandError(f"No se pudo comparar {reference_path}: {exc}") from exc
            comparison_path = output_dir / f"recovery_{reference_path.stem}.json"
            comparison_path.write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            index["comparisons"].append(
                {
                    "reference": str(reference_path),
                    "summary": comparison["summary"],
                    "file": str(comparison_path),
                }
            )

        index_path = output_dir / "audit_index.json"
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary_lines = [
            "# Auditoria de integridad SQLite",
            "",
            "## Bases auditadas",
            "",
            "| Base | Integrity check | Violaciones FK | Productos | Proveedores |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in index["databases"]:
            metrics = row.get("quality_metrics") or {}
            summary_lines.append(
                "| {name} | {integrity} | {fk} | {products} | {suppliers} |".format(
                    name=Path(row["path"]).name,
                    integrity=", ".join(row.get("integrity_check") or []),
                    fk=row.get("foreign_key_violations", 0),
                    products=metrics.get("products", "-"),
                    suppliers=metrics.get("suppliers", "-"),
                )
            )
        summary_lines.extend(
            [
                "",
                "## Clasificacion contra backups",
                "",
                "| Backup de referencia | Violaciones | Filas identificadas | Filas con destino actual unico |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in index["comparisons"]:
            comparison_summary = row.get("summary") or {}
            summary_lines.append(
                "| {name} | {violations} | {identified} | {recoverable} |".format(
                    name=Path(row["reference"]).name,
                    violations=comparison_summary.get("violation_rows", 0),
                    identified=comparison_summary.get("identified_violation_rows", 0),
                    recoverable=comparison_summary.get("matched_violation_rows", 0),
                )
            )
        summary_lines.extend(
            [
                "",
                "> Este reporte solo clasifica candidatos. No modifica la base y no autoriza una reparacion automatica.",
                "",
            ]
        )
        summary_path = output_dir / "SUMMARY.md"
        summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Reportes generados en {output_dir}"))
        self.stdout.write(str(index_path))
        self.stdout.write(str(summary_path))
