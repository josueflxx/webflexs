import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.data_integrity import utc_stamp
from core.services.data_integrity_triage import collect_residual_triage, write_triage_report


class Command(BaseCommand):
    help = "Clasifica las violaciones FK residuales sin modificar la base SQLite."

    def add_arguments(self, parser):
        parser.add_argument("--database", required=True, help="Copia SQLite a clasificar.")
        parser.add_argument(
            "--reference-database",
            action="append",
            default=[],
            help="Backup historico valido. Puede repetirse.",
        )
        parser.add_argument("--output-dir", default="", help="Directorio de reportes.")

    def handle(self, *args, **options):
        output_dir = Path(
            options["output_dir"]
            or Path(settings.BASE_DIR)
            / "backups"
            / "integrity_audit"
            / f"triage_{utc_stamp()}"
        ).resolve()
        try:
            report = collect_residual_triage(
                options["database"],
                options["reference_database"],
            )
            files = write_triage_report(report, output_dir)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps(
                {"summary": report["summary"], "files": files},
                ensure_ascii=False,
                indent=2,
            )
        )
        self.stdout.write(self.style.SUCCESS("Triage residual generado sin modificar la base."))
