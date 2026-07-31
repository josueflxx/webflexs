from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from admin_panel.forms.sales_document_type_forms import WarehouseForm
from catalog.models import Product
from core.models import AdminAuditLog, Company, ProductWarehouseStock, Warehouse
from core.services.company_context import SESSION_COMPANY_KEY


class WarehouseStockInitializationUiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="josueflexs",
            email="warehouse@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(name="Empresa stock UI")
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="principal",
            name="Principal",
        )
        self.product = Product.objects.create(
            sku="UI-STOCK-1",
            name="Producto UI",
            price=10,
            stock=6,
            tracks_stock=True,
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session[SESSION_COMPANY_KEY] = self.company.pk
        session.save()

    def test_confirmation_and_observation_are_required(self):
        response = self.client.post(
            reverse("admin_warehouse_stock_initialize", args=[self.warehouse.pk]),
            {
                "confirmation": "incorrecto",
                "observation": "Inventario validado",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProductWarehouseStock.objects.exists())

    def test_initialization_is_audited_and_does_not_enable_warehouse(self):
        response = self.client.post(
            reverse("admin_warehouse_stock_initialize", args=[self.warehouse.pk]),
            {
                "confirmation": "INICIALIZAR PRINCIPAL",
                "observation": "Inventario inicial revisado.",
            },
        )

        self.assertRedirects(
            response,
            reverse("admin_warehouse_edit", args=[self.warehouse.pk]),
        )
        balance = ProductWarehouseStock.objects.get(
            product=self.product,
            warehouse=self.warehouse,
        )
        self.assertEqual(balance.on_hand, 6)
        self.assertIsNotNone(balance.initialized_at)
        self.warehouse.refresh_from_db()
        self.assertFalse(self.warehouse.stock_balance_enabled)
        audit = AdminAuditLog.objects.get(action="warehouse_stock_initialize")
        self.assertEqual(audit.target_id, str(self.warehouse.pk))
        self.assertEqual(audit.details["observation"], "Inventario inicial revisado.")

    def test_warehouse_cannot_be_enabled_before_tracked_products_are_initialized(self):
        form = WarehouseForm(
            data={
                "code": self.warehouse.code,
                "name": self.warehouse.name,
                "is_active": "on",
                "stock_balance_enabled": "on",
                "notes": "",
            },
            instance=self.warehouse,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Faltan inicializar", str(form.errors["stock_balance_enabled"]))
