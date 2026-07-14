import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.data_integrity import preserve_sqlite_database


class Command(BaseCommand):
    help = "Crea copias consistentes de preservacion y trabajo de una base SQLite."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="", help="Ruta de la base SQLite de origen.")
        parser.add_argument("--output-root", default="", help="Directorio raiz de snapshots.")

    def handle(self, *args, **options):
        configured_database = settings.DATABASES.get("default", {})
        if configured_database.get("ENGINE") != "django.db.backends.sqlite3" and not options["database"]:
            raise CommandError("La base configurada no es SQLite; indica --database explicitamente.")
        database = options["database"] or configured_database.get("NAME")
        output_root = options["output_root"] or Path(settings.BASE_DIR) / "backups" / "integrity_audit"
        try:
            manifest = preserve_sqlite_database(database, output_root)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2))
        self.stdout.write(self.style.SUCCESS("Snapshot SQLite preservado y verificado."))
