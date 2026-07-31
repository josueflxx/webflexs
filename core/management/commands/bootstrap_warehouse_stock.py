"""Initialize legacy global stock in one explicitly selected warehouse."""

from django.core.management.base import BaseCommand, CommandError
from core.models import Warehouse
from core.services.warehouse_stock import (
    get_warehouse_stock_initialization_preview,
    initialize_warehouse_from_legacy_stock,
)


class Command(BaseCommand):
    help = (
        "Previsualiza o inicializa el stock global existente en un deposito. "
        "No activa automaticamente la escritura por deposito."
    )

    def add_arguments(self, parser):
        parser.add_argument("--warehouse", type=int, required=True, help="ID exacto del deposito destino.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Confirma la creacion de saldos. Sin esta opcion solo muestra la previsualizacion.",
        )

    def handle(self, *args, **options):
        warehouse_id = options["warehouse"]
        apply_changes = bool(options["apply"])
        try:
            warehouse = Warehouse.objects.select_related("company").get(pk=warehouse_id)
        except Warehouse.DoesNotExist as exc:
            raise CommandError(f"No existe el deposito {warehouse_id}.") from exc

        preview = get_warehouse_stock_initialization_preview(warehouse)

        mode = "APLICAR" if apply_changes else "PREVISUALIZAR"
        self.stdout.write(
            f"{mode}: {preview.created_count} productos -> "
            f"{warehouse.company.name} / {warehouse.name}; stock total {preview.stock_total}."
        )
        if preview.skipped_count:
            self.stdout.write(
                self.style.WARNING(
                    f"{preview.skipped_count} productos ya tienen saldo en este deposito y seran omitidos."
                )
            )
        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "No se modificaron datos. Repite el comando con --apply para confirmar."
                )
            )
            return

        result = initialize_warehouse_from_legacy_stock(warehouse)

        self.stdout.write(
            self.style.SUCCESS(
                f"Se inicializaron {result.created_count} productos. "
                "La funcion sigue desactivada hasta habilitarla en Configuracion."
            )
        )
