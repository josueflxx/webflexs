import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.data_integrity import utc_stamp
from core.services.data_integrity_quarantine import (
    QUARANTINE_CONFIRMATION,
    quarantine_orphans,
    write_quarantine_report,
)


class Command(BaseCommand):
    help = "Archiva y retira filas huerfanas irrecuperables solo sobre una copia protegida."

    def add_arguments(self, parser):
        parser.add_argument("--database", required=True, help="Copia SQLite protegida.")
        parser.add_argument("--output-dir", default="", help="Directorio de cuarentena.")
        parser.add_argument("--apply", action="store_true", help="Aplica la cuarentena.")
        parser.add_argument("--confirm", default="", help="Frase requerida para aplicar.")

    def handle(self, *args, **options):
        database = Path(options["database"]).resolve()
        configured = Path(settings.DATABASES.get("default", {}).get("NAME", "")).resolve()
        if options["apply"] and database == configured:
            raise CommandError("Se rehusa aplicar la cuarentena sobre la base configurada.")
        if options["apply"] and options["confirm"] != QUARANTINE_CONFIRMATION:
            raise CommandError(
                f"Usa --confirm {QUARANTINE_CONFIRMATION} para aplicar sobre la copia."
            )
        output_dir = Path(
            options["output_dir"]
            or Path(settings.BASE_DIR)
            / "backups"
            / "integrity_audit"
            / f"quarantine_{utc_stamp()}"
        ).resolve()
        try:
            result = quarantine_orphans(
                database,
                output_dir,
                apply=options["apply"],
                confirmation=options["confirm"],
            )
            files = write_quarantine_report(result, output_dir)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps(
                {"mode": result["mode"], "summary": result["summary"], "files": files},
                ensure_ascii=False,
                indent=2,
            )
        )
        message = "Cuarentena aplicada y verificada." if options["apply"] else "Dry-run de cuarentena generado."
        self.stdout.write(self.style.SUCCESS(message))
