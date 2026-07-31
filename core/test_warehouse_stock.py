from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.models import (
    FISCAL_DOC_TYPE_FA,
    FISCAL_STATUS_AUTHORIZED,
    SALES_BEHAVIOR_FACTURA,
    SALES_BILLING_MODE_AFIP_WSFE,
    Company,
    FiscalDocument,
    FiscalPointOfSale,
    ProductWarehouseStock,
    SalesDocumentType,
    SiteSettings,
    StockMovement,
    Warehouse,
)
from core.services.sales_documents import ensure_stock_movements_for_order_document
from orders.models import Order, OrderItem


class WarehouseStockDualWriteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.company = Company.objects.create(
            name="Stock por deposito test",
            slug="stock-deposito-test",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="principal",
            name="Principal",
            stock_balance_enabled=True,
        )
        self.point_of_sale = FiscalPointOfSale.objects.create(
            company=self.company,
            number="9",
        )
        user = User.objects.create_user(username="warehouse-stock-client")
        profile = ClientProfile.objects.create(user=user, company_name="Cliente Stock Deposito")
        client_company = ClientCompany.objects.create(
            client_profile=profile,
            company=self.company,
        )
        self.order = Order.objects.create(
            user=user,
            company=self.company,
            client_company_ref=client_company,
            client_company=profile.company_name,
        )
        self.product = Product.objects.create(
            sku="WAREHOUSE-STOCK-1",
            name="Producto con saldo",
            price=Decimal("100.00"),
            cost=Decimal("50.00"),
            iva_rate=Decimal("21.00"),
            stock=10,
            tracks_stock=True,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=2,
            unit_price_base=self.product.price,
            price_at_purchase=self.product.price,
        )
        self.document_type = SalesDocumentType.objects.create(
            company=self.company,
            code="factura-stock-deposito",
            name="Factura stock deposito",
            document_behavior=SALES_BEHAVIOR_FACTURA,
            billing_mode=SALES_BILLING_MODE_AFIP_WSFE,
            fiscal_doc_type=FISCAL_DOC_TYPE_FA,
            generate_stock_movement=True,
            default_warehouse=self.warehouse,
        )
        self.document = FiscalDocument.objects.create(
            source_key="warehouse-stock-invoice",
            company=self.company,
            order=self.order,
            point_of_sale=self.point_of_sale,
            sales_document_type=self.document_type,
            doc_type=FISCAL_DOC_TYPE_FA,
            status=FISCAL_STATUS_AUTHORIZED,
            cae="12345678901234",
        )
        settings = SiteSettings.get_settings()
        settings.warehouse_stock_enabled = True
        settings.save(update_fields=["warehouse_stock_enabled"])

    def tearDown(self):
        cache.clear()

    def _apply(self):
        return ensure_stock_movements_for_order_document(
            order=self.order,
            company=self.company,
            sales_document_type=self.document_type,
            fiscal_document=self.document,
        )

    def test_authorized_invoice_updates_legacy_and_warehouse_balance_once(self):
        balance = ProductWarehouseStock.objects.create(
            product=self.product,
            warehouse=self.warehouse,
            on_hand=Decimal("10.000"),
            initialized_at=timezone.now(),
        )

        self.assertEqual(len(self._apply()), 1)
        self.product.refresh_from_db()
        balance.refresh_from_db()
        movement = StockMovement.objects.get()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(balance.on_hand, Decimal("8.000"))
        self.assertIsNotNone(movement.warehouse_balance_applied_at)
        self.assertEqual(movement.warehouse_balance_error, "")

        self._apply()
        self.product.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(balance.on_hand, Decimal("8.000"))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_missing_initialization_never_loses_legacy_stock_effect(self):
        self._apply()

        self.product.refresh_from_db()
        movement = StockMovement.objects.get()
        self.assertEqual(self.product.stock, 8)
        self.assertIsNone(movement.warehouse_balance_applied_at)
        self.assertIn("no fue inicializado", movement.warehouse_balance_error)
        self.assertFalse(ProductWarehouseStock.objects.exists())


class BootstrapWarehouseStockCommandTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Bootstrap stock test")
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="principal",
            name="Principal",
        )
        self.first = Product.objects.create(
            sku="BOOT-1",
            name="Bootstrap uno",
            price=10,
            stock=7,
        )
        self.second = Product.objects.create(
            sku="BOOT-2",
            name="Bootstrap dos",
            price=20,
            stock=4,
        )

    def test_command_is_preview_only_without_apply(self):
        output = StringIO()
        call_command(
            "bootstrap_warehouse_stock",
            warehouse=self.warehouse.pk,
            stdout=output,
        )

        self.assertFalse(ProductWarehouseStock.objects.exists())
        self.assertIn("No se modificaron datos", output.getvalue())

    def test_command_initializes_selected_warehouse_without_enabling_it(self):
        call_command(
            "bootstrap_warehouse_stock",
            warehouse=self.warehouse.pk,
            apply=True,
            stdout=StringIO(),
        )

        balances = ProductWarehouseStock.objects.filter(warehouse=self.warehouse)
        self.assertEqual(balances.count(), 2)
        self.assertEqual(
            balances.get(product=self.first).on_hand,
            Decimal("7.000"),
        )
        self.assertEqual(
            balances.get(product=self.second).on_hand,
            Decimal("4.000"),
        )
        self.warehouse.refresh_from_db()
        self.assertFalse(self.warehouse.stock_balance_enabled)


class ProductCommercialAvailabilityTests(TestCase):
    def test_unsellable_product_is_not_catalog_visible(self):
        product = Product.objects.create(
            sku="NOT-FOR-SALE",
            name="No disponible para venta",
            price=10,
            is_sellable=False,
        )

        self.assertFalse(
            Product.catalog_visible(include_uncategorized=True).filter(pk=product.pk).exists()
        )
