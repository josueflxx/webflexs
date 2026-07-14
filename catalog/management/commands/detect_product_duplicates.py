import json

from django.core.management.base import BaseCommand

from catalog.services.duplicate_detection import refresh_duplicate_reviews


class Command(BaseCommand):
    help = "Detecta posibles productos duplicados sin fusionarlos ni modificarlos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Crea o refresca filas pendientes en la cola de revision.",
        )

    def handle(self, *args, **options):
        result = refresh_duplicate_reviews(apply=options["apply"])
        summary = {key: value for key, value in result.items() if key != "items"}
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        self.stdout.write(
            self.style.SUCCESS(
                "Cola de duplicados actualizada; no se fusiono ningun producto."
                if options["apply"]
                else "Dry-run completado; no se modificaron productos."
            )
        )
