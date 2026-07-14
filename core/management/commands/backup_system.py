"""Create a portable system backup."""

from django.core.management.base import BaseCommand, CommandError

from core.services.backups import create_system_backup


class Command(BaseCommand):
    help = "Crea un backup comprimido de la base y, opcionalmente, archivos media."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database-only",
            action="store_true",
            help="No incluye el directorio media.",
        )

    def handle(self, *args, **options):
        try:
            result = create_system_backup(include_media=not options["database_only"])
        except Exception as exc:
            raise CommandError(f"No se pudo crear el backup: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Backup creado: {result['manifest']}"))
