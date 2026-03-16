from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Category, Product
from core.models import AdminCompanyAccess, Company, SalesDocumentType, Warehouse
from core.services.company_context import get_default_company, get_user_companies
from orders.models import Order


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

    def test_security_policy_headers_are_present(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Content-Security-Policy", response)
        self.assertIn("Permissions-Policy", response)
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])


class ActiveCompanyMiddlewareTests(TestCase):
    def test_staff_with_multiple_companies_can_access_home_without_selecting_company(self):
        default_company = get_default_company()
        Company.objects.create(name="Empresa Secundaria Home", slug="empresa-secundaria-home", is_active=True)
        staff = User.objects.create_user(
            username="staff_home_access",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "productos FLEXS")

    def test_staff_with_multiple_companies_still_needs_company_for_admin_panel(self):
        default_company = get_default_company()
        Company.objects.create(name="Empresa Secundaria Admin", slug="empresa-secundaria-admin", is_active=True)
        staff = User.objects.create_user(
            username="staff_admin_requires_company",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("select_company"), response["Location"])


class StaffCompanyAccessConfigTests(TestCase):
    @override_settings(
        ADMIN_COMPANY_ACCESS={"staff_acl": ["flexs"]},
        ADMIN_COMPANY_ACCESS_REQUIRE_EXPLICIT=True,
    )
    def test_staff_company_mapping_limits_access(self):
        company_flexs = Company.objects.filter(slug__iexact="flexs").first() or get_default_company()
        company_ubolt = Company.objects.filter(slug__iexact="ubolt").first()
        if not company_ubolt:
            company_ubolt = Company.objects.create(name="Ubolt ACL", slug="ubolt", is_active=True)

        staff = User.objects.create_user(
            username="staff_acl",
            password="secret123",
            is_staff=True,
        )

        companies = list(get_user_companies(staff))

        self.assertEqual([company.pk for company in companies], [company_flexs.pk])
        self.assertNotIn(company_ubolt.pk, [company.pk for company in companies])


class StaffCompanyAccessModelTests(TestCase):
    def test_staff_company_db_scope_limits_access(self):
        company_flexs = Company.objects.filter(slug__iexact="flexs").first() or get_default_company()
        company_ubolt = Company.objects.filter(slug__iexact="ubolt").first()
        if not company_ubolt:
            company_ubolt = Company.objects.create(name="Ubolt DB ACL", slug="ubolt", is_active=True)

        staff = User.objects.create_user(
            username="staff_db_acl",
            password="secret123",
            is_staff=True,
        )
        AdminCompanyAccess.objects.create(user=staff, company=company_ubolt, is_active=True)

        companies = list(get_user_companies(staff))

        self.assertEqual([company.pk for company in companies], [company_ubolt.pk])
        self.assertNotIn(company_flexs.pk, [company.pk for company in companies])

    @patch("core.services.company_context.admin_company_access_table_available", return_value=False)
    def test_staff_company_scope_falls_back_gracefully_when_scope_table_is_missing(self, _mock_scope_table):
        company_flexs = Company.objects.filter(slug__iexact="flexs").first() or get_default_company()
        company_ubolt = Company.objects.filter(slug__iexact="ubolt").first()
        if not company_ubolt:
            company_ubolt = Company.objects.create(name="Ubolt Missing Table", slug="ubolt", is_active=True)
        staff = User.objects.create_user(
            username="staff_db_acl_missing_table",
            password="secret123",
            is_staff=True,
        )

        companies = list(get_user_companies(staff))

        self.assertEqual(
            {company.pk for company in companies},
            {company_flexs.pk, company_ubolt.pk},
        )


class ApiCompanyScopeTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.filter(slug__iexact="flexs").first() or get_default_company()
        self.company_b = Company.objects.filter(slug__iexact="ubolt").first()
        if not self.company_b:
            self.company_b = Company.objects.create(name="Ubolt API", slug="ubolt", is_active=True)

        self.staff = User.objects.create_user("staff_api_scope", password="secret123", is_staff=True)
        self.client.force_login(self.staff)

        self.client_user_a = User.objects.create_user("cliente_api_a", password="secret123")
        self.profile_a = ClientProfile.objects.create(user=self.client_user_a, company_name="Cliente API A")
        self.client_company_a = ClientCompany.objects.create(
            client_profile=self.profile_a,
            company=self.company_a,
            is_active=True,
        )

        self.client_user_b = User.objects.create_user("cliente_api_b", password="secret123")
        self.profile_b = ClientProfile.objects.create(user=self.client_user_b, company_name="Cliente API B")
        self.client_company_b = ClientCompany.objects.create(
            client_profile=self.profile_b,
            company=self.company_b,
            is_active=True,
        )

        Order.objects.create(
            user=self.client_user_a,
            company=self.company_a,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            client_company="Cliente API A",
            client_company_ref=self.client_company_a,
        )
        Order.objects.create(
            user=self.client_user_b,
            company=self.company_b,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("200.00"),
            total=Decimal("200.00"),
            client_company="Cliente API B",
            client_company_ref=self.client_company_b,
        )

    def _set_active_company(self, company):
        session = self.client.session
        session["active_company_id"] = company.pk
        session.save()

    def test_staff_client_api_uses_active_company_scope(self):
        self._set_active_company(self.company_a)

        response = self.client.get(reverse("api_v1:clients"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["company_name"], "Cliente API A")

    def test_staff_order_api_requires_active_company_when_multiple_companies(self):
        session = self.client.session
        session.pop("active_company_id", None)
        session.save()

        response = self.client.get(reverse("api_v1:orders"))

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertTrue(payload["requires_company"])


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
