import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.data_integrity import utc_stamp
from core.services.data_integrity_repair import (
    APPLY_CONFIRMATION,
    repair_data_integrity,
    write_repair_report,
)


class Command(BaseCommand):
    help = "Planifica o aplica una reparacion protegida de claves SQLite sobre una copia de trabajo."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="", help="Copia SQLite objetivo.")
        parser.add_argument(
            "--reference-database",
            required=True,
            help="Backup historico integro y sin violaciones FK.",
        )
        parser.add_argument("--output-dir", default="", help="Directorio de reportes.")
        parser.add_argument("--apply", action="store_true", help="Aplica el plan a la copia protegida.")
        parser.add_argument("--confirm", default="", help="Frase de confirmacion para --apply.")

    def handle(self, *args, **options):
        configured_database = settings.DATABASES.get("default", {})
        if configured_database.get("ENGINE") != "django.db.backends.sqlite3" and not options["database"]:
            raise CommandError("La base configurada no es SQLite; indica --database.")
        database = Path(options["database"] or configured_database.get("NAME")).resolve()
        reference = Path(options["reference_database"]).resolve()
        configured_path = Path(configured_database.get("NAME", "")).resolve()

        if options["apply"] and database == configured_path:
            raise CommandError("Se rehusa aplicar una reparacion sobre la base SQLite configurada.")
        if options["apply"] and options["confirm"] != APPLY_CONFIRMATION:
            raise CommandError(f"Usa --confirm {APPLY_CONFIRMATION} para aplicar sobre la copia.")

        output_dir = Path(
            options["output_dir"]
            or Path(settings.BASE_DIR)
            / "backups"
            / "integrity_audit"
            / f"repair_{utc_stamp()}"
        ).resolve()
        try:
            result = repair_data_integrity(
                database,
                reference,
                apply=options["apply"],
                confirmation=options["confirm"],
            )
            files = write_repair_report(result, output_dir)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        payload = {
            "mode": result["mode"],
            "summary": result["summary"],
            "target_database": result["target_database"],
            "reference_database": result["reference_database"],
            "files": files,
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        message = "Reparacion aplicada y verificada." if options["apply"] else "Dry-run generado sin modificar la base."
        self.stdout.write(self.style.SUCCESS(message))
