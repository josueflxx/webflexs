from decimal import Decimal

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product


class GlobalNumberFormatTests(TestCase):
    def test_floatformat_adds_thousands_separator(self):
        rendered = Template("{{ value|floatformat:2 }}").render(
            Context({"value": Decimal("1234567.89")})
        ).strip()

        self.assertEqual(rendered, "1.234.567,89")

    def test_floatformat_without_decimals_keeps_grouping(self):
        rendered = Template("{{ value|floatformat:0 }}").render(
            Context({"value": Decimal("1234567")})
        ).strip()

        self.assertEqual(rendered, "1.234.567")


class SearchSuggestionsTests(TestCase):
    def test_catalog_scope_returns_suggestions(self):
        Product.objects.create(
            sku="ABT71665100P",
            name="ABRAZADERA TREFILADA DE 7/16 X 65 X 100 PLANA",
            price=Decimal("100.00"),
            cost=Decimal("60.00"),
            stock=5,
            is_active=True,
        )

        response = self.client.get(
            reverse("search_suggestions"),
            {"scope": "catalog", "q": "ABT716"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        values = [item["value"] for item in payload["suggestions"]]
        self.assertIn("ABT71665100P", values)

    def test_admin_scope_requires_staff(self):
        response = self.client.get(
            reverse("search_suggestions"),
            {"scope": "admin_products", "q": "abraz"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_scope_returns_for_staff(self):
        Product.objects.create(
            sku="ZZ-ADMIN-01",
            name="Producto Admin",
            price=Decimal("100.00"),
            cost=Decimal("60.00"),
            stock=1,
            is_active=True,
        )
        staff = User.objects.create_user("admin_tester", password="secret123", is_staff=True)
        self.client.force_login(staff)

        response = self.client.get(
            reverse("search_suggestions"),
            {"scope": "admin_products", "q": "ZZ-ADMIN"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        values = [item["value"] for item in payload["suggestions"]]
        self.assertIn("ZZ-ADMIN-01", values)


class ObservabilityMiddlewareTests(TestCase):
    def test_request_id_header_is_present(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response)
        self.assertTrue(str(response["X-Request-ID"]).strip())
