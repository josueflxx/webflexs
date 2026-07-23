import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import ProductSupplier
from catalog.services.product_suppliers import upsert_product_supplier_offer


class Command(BaseCommand):
    help = (
        "Compara el costo vivo de Product con el costo de su proveedor preferido. "
        "Por defecto solo informa; --apply toma Product.cost como fuente oficial y registra historial."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplicar la conciliacion.")
        parser.add_argument("--report", help="Ruta opcional para guardar el informe JSON.")

    def handle(self, *args, **options):
        preferred_offers = list(
            ProductSupplier.objects.filter(is_preferred=True)
            .select_related("product", "supplier")
            .order_by("id")
        )
        # Compare Decimal values in Python. SQLite can report false mismatches when
        # the columns use different decimal scales (for example 100.0000 vs 100.00).
        mismatches = [
            offer for offer in preferred_offers if offer.current_cost != offer.product.cost
        ]
        examples = [
            {
                "offer_id": offer.pk,
                "product_id": offer.product_id,
                "sku": offer.product.sku,
                "supplier": offer.supplier.name,
                "offer_cost": str(offer.current_cost),
                "product_cost": str(offer.product.cost),
            }
            for offer in mismatches[:200]
        ]
        applied = 0
        if options["apply"]:
            with transaction.atomic():
                for offer in mismatches:
                    _updated_offer, history = upsert_product_supplier_offer(
                        product=offer.product,
                        supplier=offer.supplier,
                        current_cost=offer.product.cost,
                        currency=offer.currency,
                        supplier_code=offer.supplier_code,
                        supplier_description=offer.supplier_description,
                        discount_percentage=offer.discount_percentage,
                        bonus_percentage=offer.bonus_percentage,
                        tax_percentage=offer.tax_percentage,
                        minimum_purchase_quantity=offer.minimum_purchase_quantity,
                        is_available=offer.is_available,
                        lead_time_days=offer.lead_time_days,
                        price_list_date=offer.price_list_date,
                        source="preferred_cost_reconciliation",
                        source_file="",
                        source_row=None,
                        changed_by=None,
                        reason="Conciliacion inicial Product.cost -> proveedor preferido",
                        is_preferred=True,
                        status=offer.status,
                        match_confidence=offer.match_confidence,
                        match_method=offer.match_method or "legacy_supplier_ref",
                        notes=offer.notes,
                    )
                    if history:
                        applied += 1

        report = {
            "mode": "apply" if options["apply"] else "preview",
            "source_of_truth": "Product.cost",
            "mismatches": len(mismatches),
            "applied": applied,
            "examples": examples,
        }
        output = json.dumps(report, ensure_ascii=False, indent=2)
        self.stdout.write(output)
        if options.get("report"):
            report_path = Path(options["report"]).expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(output + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Informe guardado en {report_path}"))
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Costos conciliados: {applied}"))
        else:
            self.stdout.write(self.style.WARNING("Vista previa: no se modificaron costos."))
