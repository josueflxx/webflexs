from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from catalog.models import Product, ProductSupplier, Supplier, SupplierCostHistory
from catalog.services.product_timeline import build_product_timeline
from core.models import (
    AdminAuditLog,
    Company,
    ProductWarehouseStock,
    STOCK_MOVEMENT_OUT,
    StockMovement,
    Warehouse,
)


class ProductTimelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="timeline-user")
        self.company = Company.objects.create(name="Empresa timeline")
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="principal",
            name="Principal",
        )
        self.product = Product.objects.create(
            sku="TIMELINE-1",
            name="Producto timeline",
            price=Decimal("100.00"),
            cost=Decimal("50.00"),
            iva_rate=Decimal("21.00"),
            stock=8,
            tracks_stock=True,
        )
        self.balance = ProductWarehouseStock.objects.create(
            product=self.product,
            warehouse=self.warehouse,
            on_hand=Decimal("8.000"),
            initialized_at=timezone.now(),
        )
        self.supplier = Supplier.objects.create(name="Proveedor timeline")
        self.offer = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            current_cost=Decimal("50.00"),
            is_preferred=True,
        )
        SupplierCostHistory.objects.create(
            product_supplier=self.offer,
            previous_cost=Decimal("45.00"),
            new_cost=Decimal("50.00"),
            difference_amount=Decimal("5.00"),
            difference_percentage=Decimal("11.11"),
            changed_by=self.user,
            reason="Actualizacion de lista",
        )
        StockMovement.objects.create(
            source_key="timeline-stock-1",
            company=self.company,
            warehouse=self.warehouse,
            product=self.product,
            movement_type=STOCK_MOVEMENT_OUT,
            quantity=Decimal("2.000"),
            notes="Salida de prueba",
            created_by=self.user,
        )
        AdminAuditLog.objects.create(
            user=self.user,
            company=self.company,
            action="product_edit",
            target_type="product",
            target_id=str(self.product.pk),
            details={
                "before": {"cost": "45.00", "price": "90.00"},
                "after": {"cost": "50.00", "price": "100.00"},
            },
        )

    def test_timeline_merges_stock_cost_and_audit_sources(self):
        entries = build_product_timeline(self.product)
        kinds = {entry["kind"] for entry in entries}

        self.assertIn("stock", kinds)
        self.assertIn("cost", kinds)
        self.assertIn("audit", kinds)
        self.assertEqual(
            entries,
            sorted(entries, key=lambda entry: entry["occurred_at"], reverse=True),
        )

    def test_cost_information_is_redacted_without_permission(self):
        entries = build_product_timeline(self.product, include_costs=False)

        self.assertNotIn("cost", {entry["kind"] for entry in entries})
        audit_entry = next(entry for entry in entries if entry["kind"] == "audit")
        self.assertNotIn("Costo", audit_entry["description"])
        self.assertIn("Precio neto", audit_entry["description"])
