"""
Management command to normalize product net prices and default IVA rate to 21%.

Fixes the issue where SaaS imports populated product.price with precio_final (cost * 2 * 1.21)
instead of net price (cost * 2).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Product


class Command(BaseCommand):
    help = "Normalize product prices to net (cost * 2) and default IVA to 21%"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes to the database. Defaults to dry-run mode.",
        )
        parser.add_argument(
            "--set-all-cost-x2",
            action="store_true",
            help="Force price = cost * 2 for all products with cost > 0 where ratio is around 2.42",
        )

    def handle(self, *args, **options):
        apply_changes = options.get("apply", False)
        mode_label = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.WARNING(f"Running fix_product_net_prices in {mode_label} mode..."))

        total_scanned = 0
        price_fixed_count = 0
        iva_fixed_count = 0

        qs = Product.objects.all().order_by("id")
        products_to_update = []

        for p in qs.iterator(chunk_size=1000):
            total_scanned += 1
            updated = False

            # 1. Check IVA rate: default to 21% if missing or None
            if p.iva_rate is None:
                p.iva_rate = Decimal("21.00")
                iva_fixed_count += 1
                updated = True

            # 2. Check price: if cost > 0 and price / cost is between 2.38 and 2.45
            # (which is cost * 2 * 1.21 = 2.42), restore net price to cost * 2
            if p.cost and p.cost > 0 and p.price and p.price > 0:
                ratio = round(float(p.price / p.cost), 3)
                if 2.38 <= ratio <= 2.45:
                    new_price = (p.cost * Decimal("2.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if p.price != new_price:
                        p.price = new_price
                        price_fixed_count += 1
                        updated = True

            if updated:
                products_to_update.append(p)
                if len(products_to_update) >= 500 and apply_changes:
                    with transaction.atomic():
                        Product.objects.bulk_update(products_to_update, ["price", "iva_rate"])
                    products_to_update = []

        if products_to_update and apply_changes:
            with transaction.atomic():
                Product.objects.bulk_update(products_to_update, ["price", "iva_rate"])

        self.stdout.write(self.style.SUCCESS(
            f"Done! Scanned: {total_scanned} products.\n"
            f"- Prices corrected (from cost*2*1.21 to cost*2): {price_fixed_count}\n"
            f"- IVA rate defaulted to 21%: {iva_fixed_count}\n"
            f"- Mode: {mode_label}"
        ))
