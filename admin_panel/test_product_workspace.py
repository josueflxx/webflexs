from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductSupplier, Supplier, SupplierCostHistory
from core.models import AdminCompanyAccess, Company
from core.services.company_context import SESSION_COMPANY_KEY


class ProductWorkspaceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa workspace")
        self.product = Product.objects.create(
            sku="WORKSPACE-1",
            name="Producto workspace",
            cost=Decimal("50.00"),
            price=Decimal("100.00"),
            iva_rate=Decimal("21.00"),
            stock=5,
        )
        supplier = Supplier.objects.create(name="Proveedor workspace")
        offer = ProductSupplier.objects.create(
            product=self.product,
            supplier=supplier,
            current_cost=Decimal("50.00"),
            is_preferred=True,
        )
        SupplierCostHistory.objects.create(
            product_supplier=offer,
            previous_cost=Decimal("40.00"),
            new_cost=Decimal("50.00"),
            difference_amount=Decimal("10.00"),
        )

    def _select_company(self):
        session = self.client.session
        session[SESSION_COMPANY_KEY] = self.company.pk
        session.save()

    def test_superuser_sees_commercial_center_and_final_price(self):
        admin = User.objects.create_superuser(
            username="workspace-admin",
            email="workspace-admin@example.com",
            password="test-password",
        )
        self.client.force_login(admin)
        self._select_company()

        response = self.client.get(reverse("admin_product_workspace", args=[self.product.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historial comercial unificado")
        self.assertContains(response, "$121,00")
        self.assertContains(response, "Proveedor workspace")
        self.assertContains(response, "Cambio de costo")

    def test_operator_without_price_capability_does_not_receive_cost_history(self):
        operator = User.objects.create_user(
            username="workspace-operator",
            password="test-password",
            is_staff=True,
        )
        AdminCompanyAccess.objects.create(user=operator, company=self.company)
        self.client.force_login(operator)
        self._select_company()

        response = self.client.get(reverse("admin_product_workspace", args=[self.product.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Costo vigente")
        self.assertNotContains(response, "Cambio de costo")
        self.assertNotContains(response, "Proveedor workspace")
