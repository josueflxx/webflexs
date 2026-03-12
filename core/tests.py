from decimal import Decimal

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product
from core.models import Company, SalesDocumentType, Warehouse
from core.services.company_context import get_default_company


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
    def _activate_company(self):
        company = get_default_company()
        session = self.client.session
        session["active_company_id"] = company.pk
        session.save()

    def test_catalog_scope_returns_suggestions(self):
        Category.objects.create(name="ABT716 Bujes", slug="abt716-bujes", is_active=True)
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
        self.assertIn("cat:abt716-bujes", values)

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
        self._activate_company()

        response = self.client.get(
            reverse("search_suggestions"),
            {"scope": "admin_products", "q": "ZZ-ADMIN"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        values = [item["value"] for item in payload["suggestions"]]
        self.assertIn("ZZ-ADMIN-01", values)

    def test_admin_scope_matches_compact_product_tokens(self):
        Product.objects.create(
            sku="BA03041",
            name="1/2 B.ARM FORD SOP BISAGRA CAPOT F-14000",
            price=Decimal("100.00"),
            cost=Decimal("60.00"),
            stock=1,
            is_active=True,
        )
        staff = User.objects.create_user("admin_compact_tester", password="secret123", is_staff=True)
        self.client.force_login(staff)
        self._activate_company()

        response = self.client.get(
            reverse("search_suggestions"),
            {"scope": "admin_products", "q": "F14000"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        values = [item["value"] for item in payload["suggestions"]]
        self.assertIn("BA03041", values)


class ObservabilityMiddlewareTests(TestCase):
    def test_request_id_header_is_present(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response)
        self.assertTrue(str(response["X-Request-ID"]).strip())


class SalesDocumentTypeSeedTests(TestCase):
    def test_defaults_are_seeded_for_each_company(self):
        companies = list(Company.objects.all())

        self.assertGreaterEqual(len(companies), 1)

        for company in companies:
            self.assertTrue(
                Warehouse.objects.filter(company=company, code="principal").exists(),
                f"Falta deposito principal para {company}",
            )
            self.assertTrue(
                SalesDocumentType.objects.filter(company=company, code="cotizacion", enabled=True).exists(),
                f"Falta cotizacion para {company}",
            )
            self.assertTrue(
                SalesDocumentType.objects.filter(company=company, code="pedido", enabled=True).exists(),
                f"Falta pedido para {company}",
            )
            self.assertTrue(
                SalesDocumentType.objects.filter(company=company, code="remito", enabled=True).exists(),
                f"Falta remito para {company}",
            )
            self.assertTrue(
                SalesDocumentType.objects.filter(company=company, code="recibo", enabled=True).exists(),
                f"Falta recibo para {company}",
            )
            self.assertEqual(
                SalesDocumentType.objects.filter(
                    company=company,
                    document_behavior="Factura",
                    is_default=True,
                ).count(),
                1,
                f"Debe existir un tipo factura predeterminado para {company}",
            )
