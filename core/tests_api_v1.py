from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientProfile
from catalog.models import Category, Product
from orders.models import Order


class ApiV1Tests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="api_staff",
            password="secret123",
            is_staff=True,
            is_superuser=True,
        )
        self.client_user = User.objects.create_user(
            username="api_client",
            password="secret123",
            email="client@example.com",
        )
        self.other_user = User.objects.create_user(
            username="api_other",
            password="secret123",
            email="other@example.com",
        )

        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name="Cliente API",
            cuit_dni="20-12345678-9",
            is_approved=True,
        )
        self.other_profile = ClientProfile.objects.create(
            user=self.other_user,
            company_name="Otro Cliente",
            cuit_dni="27-12345678-1",
            is_approved=True,
        )

        self.category_active = Category.objects.create(name="Categoria API", is_active=True)
        self.category_inactive = Category.objects.create(name="Categoria Inactiva API", is_active=False)

        self.product_visible = Product.objects.create(
            sku="API-SKU-001",
            name="Producto Visible API",
            supplier="Proveedor API",
            cost=Decimal("100.00"),
            price=Decimal("150.00"),
            stock=10,
            category=self.category_active,
            is_active=True,
        )
        self.product_hidden = Product.objects.create(
            sku="API-SKU-002",
            name="Producto Oculto API",
            supplier="Proveedor API",
            cost=Decimal("80.00"),
            price=Decimal("120.00"),
            stock=5,
            category=self.category_inactive,
            is_active=True,
        )
        self.product_inactive = Product.objects.create(
            sku="API-SKU-003",
            name="Producto Inactivo API",
            supplier="Proveedor API",
            cost=Decimal("90.00"),
            price=Decimal("140.00"),
            stock=5,
            category=self.category_active,
            is_active=False,
        )

        self.order_client = Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("1000.00"),
            total=Decimal("1000.00"),
        )
        self.order_other = Order.objects.create(
            user=self.other_user,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal("700.00"),
            total=Decimal("700.00"),
        )

    def _results(self, response):
        payload = response.json()
        return payload.get("results", payload)

    def test_health_requires_auth(self):
        response = self.client.get(reverse("api_v1:health"))
        self.assertIn(response.status_code, (401, 403))

    def test_catalog_products_client_scope_hides_cost_and_non_visible(self):
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("api_v1:catalog_products"))

        self.assertEqual(response.status_code, 200)
        rows = self._results(response)
        skus = {row["sku"] for row in rows}
        self.assertIn(self.product_visible.sku, skus)
        self.assertNotIn(self.product_hidden.sku, skus)
        self.assertNotIn(self.product_inactive.sku, skus)
        self.assertNotIn("cost", rows[0])

    def test_catalog_products_staff_includes_cost(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("api_v1:catalog_products"))

        self.assertEqual(response.status_code, 200)
        rows = self._results(response)
        skus = {row["sku"] for row in rows}
        self.assertIn(self.product_visible.sku, skus)
        self.assertIn(self.product_hidden.sku, skus)
        self.assertIn(self.product_inactive.sku, skus)
        self.assertIn("cost", rows[0])

    def test_clients_endpoint_staff_only(self):
        self.client.force_login(self.client_user)
        forbidden = self.client.get(reverse("api_v1:clients"))
        self.assertIn(forbidden.status_code, (401, 403))

        self.client.force_login(self.staff_user)
        allowed = self.client.get(reverse("api_v1:clients"))
        self.assertEqual(allowed.status_code, 200)
        rows = self._results(allowed)
        usernames = {row["username"] for row in rows}
        self.assertIn(self.client_user.username, usernames)
        self.assertIn(self.other_user.username, usernames)

    def test_orders_endpoint_client_only_sees_own_orders(self):
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("api_v1:orders"))

        self.assertEqual(response.status_code, 200)
        rows = self._results(response)
        order_ids = {row["id"] for row in rows}
        self.assertIn(self.order_client.id, order_ids)
        self.assertNotIn(self.order_other.id, order_ids)

    def test_orders_endpoint_staff_can_filter_by_user(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse("api_v1:orders"),
            {"user_id": str(self.client_user.id)},
        )

        self.assertEqual(response.status_code, 200)
        rows = self._results(response)
        order_ids = {row["id"] for row in rows}
        self.assertIn(self.order_client.id, order_ids)
        self.assertNotIn(self.order_other.id, order_ids)

