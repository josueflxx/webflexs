import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from catalog.models import Product
from catalog.services.clamp_parser import SPEC_FIELDS
from catalog.services.clamp_specs import plan_clamp_specs, sync_product_clamp_specs


class Command(BaseCommand):
    help = "Revisa fichas de abrazaderas. No escribe salvo con --apply y --report (respaldo JSON)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplicar cambios seguros; requiere --report.")
        parser.add_argument("--dry-run", action="store_true", help="Solo revisar (predeterminado).")
        parser.add_argument("--force", action="store_true", help="Actualizar fichas automáticas completas; nunca borrar medidas ni modificar fichas manuales.")
        parser.add_argument("--report", help="Archivo JSON nuevo con valores anteriores y propuesta por SKU.")

    def handle(self, *args, **options):
        if options["apply"] and (options["dry_run"] or not options["report"]):
            raise CommandError("--apply requiere --report y no admite --dry-run.")
        report_path = Path(options["report"]) if options["report"] else None
        if report_path and report_path.exists():
            raise CommandError("El informe ya existe. Elegí otro archivo para conservar el respaldo.")
        products = Product.objects.filter(
            Q(name__icontains="ABRAZADERA") | Q(category__name__icontains="ABRAZADERA")
            | Q(categories__name__icontains="ABRAZADERA")
        ).select_related("clamp_specs").distinct().order_by("pk")
        report = {"created_at": timezone.now().isoformat(), "mode": "apply" if options["apply"] else "dry-run", "products": []}
        counts = {}
        with transaction.atomic():
            candidates = list(products)
            if options["apply"]:
                list(Product.objects.filter(pk__in=[p.pk for p in candidates]).select_for_update().values_list("pk", flat=True))
                candidates = list(products)
            for product in candidates:
                existing = getattr(product, "clamp_specs", None)
                plan = plan_clamp_specs(product, existing, refresh=options["force"])
                counts[plan["status"]] = counts.get(plan["status"], 0) + 1
                before = {field: getattr(existing, field) for field in (*SPEC_FIELDS, "parse_confidence", "parse_warnings", "manual_override")} if existing else None
                report["products"].append({"id": product.pk, "sku": product.sku, "before": before, **plan})
            report["summary"] = counts
            # Persist before-images before modifying any row. Never overwrite a backup.
            if report_path:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                with report_path.open("x", encoding="utf-8") as target:
                    json.dump(report, target, ensure_ascii=False, indent=2)
            if options["apply"]:
                for product, row in zip(candidates, report["products"]):
                    if row["changes"]:
                        sync_product_clamp_specs(product, refresh=options["force"])
        self.stdout.write(self.style.SUCCESS(f"{'Aplicado' if options['apply'] else 'Vista previa, sin cambios'}: {counts}"))
        if report_path:
            self.stdout.write(f"Informe y valores anteriores: {report_path}")
