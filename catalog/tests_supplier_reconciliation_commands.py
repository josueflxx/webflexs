from decimal import Decimal
from io import StringIO
from pathlib import Path
import tempfile

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from catalog.models import Product, ProductSupplier, Supplier, SupplierCostHistory


class SupplierReconciliationCommandTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Proveedor conciliacion")

    def _product_offer(self, sku):
        product = Product.objects.create(
            sku=sku,
            name=f"Producto {sku}",
            supplier=self.supplier.name,
            supplier_ref=self.supplier,
            cost=Decimal("100.00"),
            price=Decimal("150.00"),
        )
        offer = ProductSupplier.objects.create(
            product=product,
            supplier=self.supplier,
            current_cost=Decimal("100.0000"),
            final_cost=Decimal("100.0000"),
            is_preferred=True,
        )
        return product, offer

    def test_backfill_applies_unique_codes_and_skips_shared_references(self):
        _safe_product, safe_offer = self._product_offer("SAFE-001")
        _shared_a, shared_offer_a = self._product_offer("SHARED-001")
        _shared_b, shared_offer_b = self._product_offer("SHARED-002")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "BASE"
        sheet.append(["Código", "Referencia"])
        sheet.append(["SAFE-001", "EXT-001"])
        sheet.append(["SHARED-001", "EXT-SHARED"])
        sheet.append(["SHARED-002", "EXT-SHARED"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "base.xlsx"
            workbook.save(path)
            call_command(
                "backfill_supplier_codes_from_base",
                str(path),
                "--apply",
                stdout=StringIO(),
            )

        safe_offer.refresh_from_db()
        shared_offer_a.refresh_from_db()
        shared_offer_b.refresh_from_db()
        self.assertEqual(safe_offer.supplier_code, "EXT-001")
        self.assertEqual(shared_offer_a.supplier_code, "")
        self.assertEqual(shared_offer_b.supplier_code, "")

    def test_cost_reconciliation_ignores_equal_decimals_with_different_scales(self):
        _product, offer = self._product_offer("COST-001")
        call_command("reconcile_preferred_supplier_costs", "--apply", stdout=StringIO())
        offer.refresh_from_db()
        self.assertEqual(offer.current_cost, Decimal("100.0000"))
        self.assertFalse(SupplierCostHistory.objects.filter(product_supplier=offer).exists())
