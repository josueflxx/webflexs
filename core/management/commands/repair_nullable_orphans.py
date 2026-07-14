import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.data_integrity import utc_stamp
from core.services.data_integrity_repair import APPLY_CONFIRMATION, write_repair_report
from core.services.data_integrity_triage import repair_nullable_orphans


class Command(BaseCommand):
    help = "Normaliza FKs rotas a NULL solo cuando el esquema declara ON DELETE SET NULL."

    def add_arguments(self, parser):
        parser.add_argument("--database", required=True, help="Copia SQLite protegida.")
        parser.add_argument("--output-dir", default="", help="Directorio de reportes.")
        parser.add_argument("--apply", action="store_true", help="Aplica el plan protegido.")
        parser.add_argument("--confirm", default="", help="Frase requerida para aplicar.")

    def handle(self, *args, **options):
        database = Path(options["database"]).resolve()
        configured = Path(settings.DATABASES.get("default", {}).get("NAME", "")).resolve()
        if options["apply"] and database == configured:
            raise CommandError("Se rehusa aplicar la normalizacion sobre la base configurada.")
        if options["apply"] and options["confirm"] != APPLY_CONFIRMATION:
            raise CommandError(f"Usa --confirm {APPLY_CONFIRMATION} para aplicar sobre la copia.")
        output_dir = Path(
            options["output_dir"]
            or Path(settings.BASE_DIR)
            / "backups"
            / "integrity_audit"
            / f"nullable_repair_{utc_stamp()}"
        ).resolve()
        try:
            result = repair_nullable_orphans(
                database,
                apply=options["apply"],
                confirmation=options["confirm"],
            )
            files = write_repair_report(result, output_dir)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps(
                {"mode": result["mode"], "summary": result["summary"], "files": files},
                ensure_ascii=False,
                indent=2,
            )
        )
        message = "FKs anulables normalizadas y verificadas." if options["apply"] else "Dry-run de FKs anulables generado."
        self.stdout.write(self.style.SUCCESS(message))
