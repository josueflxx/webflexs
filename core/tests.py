from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Category, Product
from core.models import (
    AdminCompanyAccess,
    Company,
    FISCAL_DOC_TYPE_FC,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_NCC,
    FiscalPointOfSale,
    FiscalDocument,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BILLING_MODE_INTERNAL_DOCUMENT,
    SalesDocumentType,
    UserActivity,
    Warehouse,
)
from core.services.company_context import get_default_company, get_user_companies
from core.services.fiscal import validate_credit_note_relationship
from core.services.fiscal_documents import create_local_fiscal_document_from_order
from core.services.sales_documents import resolve_sales_document_type
from orders.models import Order
from orders.models import OrderItem


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


class AdminPresenceTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="staff_presence_owner",
            password="secret123",
            is_staff=True,
        )
        self.other_online = User.objects.create_user(
            username="staff_presence_online",
            password="secret123",
            is_staff=True,
        )
        self.other_idle = User.objects.create_user(
            username="staff_presence_idle",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    @override_settings(
        ADMIN_ONLINE_WINDOW_SECONDS=300,
        ADMIN_IDLE_WINDOW_SECONDS=90,
        ADMIN_PRESENCE_EXCLUDED_USERS=(),
    )
    def test_admin_presence_exposes_online_idle_and_offline_statuses(self):
        now = timezone.now()
        owner_activity, _ = UserActivity.objects.update_or_create(
            user=self.staff,
            defaults={"is_online": True},
        )
        UserActivity.objects.filter(pk=owner_activity.pk).update(last_activity=now - timedelta(seconds=10))
        online_activity, _ = UserActivity.objects.update_or_create(
            user=self.other_online,
            defaults={"is_online": True},
        )
        UserActivity.objects.filter(pk=online_activity.pk).update(last_activity=now - timedelta(seconds=25))
        idle_activity, _ = UserActivity.objects.update_or_create(
            user=self.other_idle,
            defaults={"is_online": True},
        )
        UserActivity.objects.filter(pk=idle_activity.pk).update(last_activity=now - timedelta(seconds=150))

        response = self.client.get(reverse("admin_presence"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("changed"))
        self.assertIn("digest", payload)
        rows = {row["username"]: row for row in payload["admins"]}
        self.assertEqual(rows["staff_presence_owner"]["status"], "online")
        self.assertEqual(rows["staff_presence_online"]["status"], "online")
        self.assertEqual(rows["staff_presence_idle"]["status"], "idle")
        self.assertEqual(rows["staff_presence_idle"]["status_label"], "Inactivo")
        self.assertIn("Ultima actividad", rows["staff_presence_idle"]["last_seen_label"])

    @override_settings(ADMIN_PRESENCE_EXCLUDED_USERS=())
    def test_admin_presence_returns_compact_payload_when_digest_matches(self):
        UserActivity.objects.update_or_create(
            user=self.staff,
            defaults={"is_online": True, "last_activity": timezone.now()},
        )
        first = self.client.get(reverse("admin_presence"))
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        self.assertTrue(first_payload.get("changed"))
        digest = first_payload.get("digest")
        self.assertTrue(digest)

        second = self.client.get(reverse("admin_presence"), {"digest": digest})
        self.assertEqual(second.status_code, 200)
        second_payload = second.json()
        self.assertFalse(second_payload.get("changed"))
        self.assertEqual(second_payload.get("digest"), digest)
        self.assertNotIn("admins", second_payload)

    def test_admin_presence_touch_marks_staff_as_online(self):
        UserActivity.objects.update_or_create(
            user=self.staff,
            defaults={"is_online": False, "last_activity": timezone.now() - timedelta(minutes=30)},
        )
        response = self.client.post(reverse("admin_presence_touch"))
        self.assertEqual(response.status_code, 200)
        activity = UserActivity.objects.get(user=self.staff)
        self.assertTrue(activity.is_online)


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


class SalesDocumentTypeChannelOverrideTests(TestCase):
    def setUp(self):
        self.company = get_default_company()

    def test_allows_multiple_defaults_for_same_behavior_when_channel_differs(self):
        base_kwargs = {
            "company": self.company,
            "document_behavior": SALES_BEHAVIOR_PEDIDO,
            "billing_mode": SALES_BILLING_MODE_INTERNAL_DOCUMENT,
            "internal_doc_type": "PED",
            "enabled": True,
            "is_default": True,
        }
        SalesDocumentType.objects.create(
            code="pedido-default-catalogo-test",
            name="Pedido Default Catalogo Test",
            letter="PDT",
            default_origin_channel="catalog",
            display_order=2101,
            **base_kwargs,
        )
        SalesDocumentType.objects.create(
            code="pedido-default-whatsapp-test",
            name="Pedido Default WhatsApp Test",
            letter="PDW",
            default_origin_channel="whatsapp",
            display_order=2102,
            **base_kwargs,
        )

        self.assertTrue(
            SalesDocumentType.objects.filter(
                company=self.company,
                document_behavior=SALES_BEHAVIOR_PEDIDO,
                is_default=True,
                default_origin_channel="catalog",
            ).exists()
        )
        self.assertTrue(
            SalesDocumentType.objects.filter(
                company=self.company,
                document_behavior=SALES_BEHAVIOR_PEDIDO,
                is_default=True,
                default_origin_channel="whatsapp",
            ).exists()
        )

    def test_resolver_prioritizes_channel_default_then_fallback_default(self):
        general_default = SalesDocumentType.objects.filter(
            company=self.company,
            document_behavior=SALES_BEHAVIOR_FACTURA,
            is_default=True,
            default_origin_channel="",
        ).first()
        if not general_default:
            general_default = SalesDocumentType.objects.create(
                company=self.company,
                code="factura-general-default-test",
                name="Factura General Default Test",
                letter="FG",
                document_behavior=SALES_BEHAVIOR_FACTURA,
                billing_mode=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
                internal_doc_type="PED",
                enabled=True,
                is_default=True,
                default_origin_channel="",
                display_order=2200,
            )

        catalog_default = SalesDocumentType.objects.create(
            company=self.company,
            code="factura-catalogo-default-test",
            name="Factura Catalogo Default Test",
            letter="FC",
            document_behavior=SALES_BEHAVIOR_FACTURA,
            billing_mode=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
            internal_doc_type="PED",
            enabled=True,
            is_default=True,
            default_origin_channel="catalog",
            display_order=2201,
        )

        resolved_catalog = resolve_sales_document_type(
            company=self.company,
            behavior=SALES_BEHAVIOR_FACTURA,
            origin_channel="catalog",
        )
        resolved_whatsapp = resolve_sales_document_type(
            company=self.company,
            behavior=SALES_BEHAVIOR_FACTURA,
            origin_channel="whatsapp",
        )

        self.assertIsNotNone(resolved_catalog)
        self.assertEqual(resolved_catalog.pk, catalog_default.pk)
        self.assertIsNotNone(resolved_whatsapp)
        self.assertEqual(resolved_whatsapp.pk, general_default.pk)


class FiscalDocumentSnapshotTests(TestCase):
    def setUp(self):
        self.company = get_default_company()
        self.company.legal_name = self.company.legal_name or "Empresa Test Snapshot SA"
        self.company.cuit = self.company.cuit or "30712345678"
        self.company.tax_condition = self.company.tax_condition or "responsable_inscripto"
        self.company.fiscal_address = self.company.fiscal_address or "Calle Fiscal 100"
        self.company.fiscal_city = self.company.fiscal_city or "San Martin"
        self.company.fiscal_province = self.company.fiscal_province or "Buenos Aires"
        self.company.postal_code = self.company.postal_code or "1650"
        self.company.save()

        self.user = User.objects.create_user("snapshot_cliente", password="secret123")
        self.client_profile = ClientProfile.objects.create(
            user=self.user,
            company_name="Cliente Snapshot SRL",
            document_type="cuit",
            document_number="30700111223",
            iva_condition="responsable_inscripto",
            fiscal_address="Domicilio Cliente 123",
            fiscal_city="San Martin",
            fiscal_province="Buenos Aires",
            postal_code="1650",
            phone="1122334455",
        )
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
        )
        self.point_of_sale = FiscalPointOfSale.objects.create(
            company=self.company,
            number="7",
            is_active=True,
            is_default=True,
        )
        self.product = Product.objects.create(
            sku="SNAP-001",
            name="Producto Snapshot",
            price=Decimal("100.00"),
            cost=Decimal("60.00"),
            stock=10,
            is_active=True,
        )
        self.order = Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("200.00"),
            discount_amount=Decimal("10.00"),
            discount_percentage=Decimal("5.00"),
            total=Decimal("190.00"),
            client_company=self.client_profile.company_name,
            client_cuit=self.client_profile.document_number,
            client_address=self.client_profile.fiscal_address,
            client_phone=self.client_profile.phone,
            client_company_ref=self.client_company,
            origin_channel=Order.ORIGIN_ADMIN,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=2,
            unit_price_base=Decimal("100.00"),
            discount_percentage_used=Decimal("5.00"),
            price_at_purchase=Decimal("95.00"),
            subtotal=Decimal("190.00"),
        )

    def test_local_fiscal_document_stores_snapshot_payload(self):
        document, created = create_local_fiscal_document_from_order(
            order=self.order,
            company=self.company,
            doc_type=FISCAL_DOC_TYPE_FB,
            point_of_sale=self.point_of_sale,
            issue_mode="manual",
            require_invoice_ready=False,
        )

        self.assertTrue(created)
        self.assertIsInstance(document.request_payload, dict)
        snapshot = document.request_payload.get("snapshot")
        self.assertIsInstance(snapshot, dict)
        self.assertEqual(snapshot.get("version"), 1)
        self.assertEqual(snapshot.get("emitter", {}).get("company_id"), self.company.id)
        self.assertEqual(snapshot.get("emitter", {}).get("point_of_sale"), self.point_of_sale.number)
        self.assertEqual(snapshot.get("client", {}).get("client_profile_id"), self.client_profile.id)
        self.assertEqual(snapshot.get("operation", {}).get("order_id"), self.order.id)
        self.assertEqual(snapshot.get("generation", {}).get("doc_type"), FISCAL_DOC_TYPE_FB)

    def test_existing_fiscal_document_keeps_original_snapshot(self):
        document, _ = create_local_fiscal_document_from_order(
            order=self.order,
            company=self.company,
            doc_type=FISCAL_DOC_TYPE_FB,
            point_of_sale=self.point_of_sale,
            issue_mode="manual",
            require_invoice_ready=False,
        )
        original_snapshot = dict(document.request_payload.get("snapshot", {}))
        original_emitter_name = original_snapshot.get("emitter", {}).get("legal_name")

        self.company.legal_name = "Empresa Renombrada"
        self.company.save(update_fields=["legal_name", "updated_at"])
        self.client_profile.company_name = "Cliente Renombrado"
        self.client_profile.save(update_fields=["company_name", "updated_at"])

        document_again, created_again = create_local_fiscal_document_from_order(
            order=self.order,
            company=self.company,
            doc_type=FISCAL_DOC_TYPE_FB,
            point_of_sale=self.point_of_sale,
            issue_mode="manual",
            require_invoice_ready=False,
        )

        self.assertFalse(created_again)
        self.assertEqual(document_again.pk, document.pk)
        current_snapshot = document_again.request_payload.get("snapshot", {})
        self.assertEqual(current_snapshot.get("emitter", {}).get("legal_name"), original_emitter_name)
        self.assertEqual(current_snapshot, original_snapshot)


class FiscalTypeCompatibilityTests(TestCase):
    def setUp(self):
        self.company = get_default_company()

    def test_resolver_supports_factura_c_types(self):
        configured = SalesDocumentType.objects.create(
            company=self.company,
            code="factura-c-compat-test",
            name="Factura C compat test",
            letter="C",
            document_behavior=SALES_BEHAVIOR_FACTURA,
            billing_mode=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
            internal_doc_type="PED",
            fiscal_doc_type=FISCAL_DOC_TYPE_FC,
            enabled=True,
            is_default=False,
            display_order=9991,
        )

        resolved = resolve_sales_document_type(
            company=self.company,
            behavior=SALES_BEHAVIOR_FACTURA,
            fiscal_doc_type=FISCAL_DOC_TYPE_FC,
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.pk, configured.pk)

    def test_credit_note_c_requires_and_accepts_factura_c_base(self):
        point = FiscalPointOfSale.objects.create(
            company=self.company,
            number="88",
            is_active=True,
            is_default=False,
        )
        invoice_c = FiscalDocument.objects.create(
            source_key="test:fiscal:compat:fc",
            company=self.company,
            point_of_sale=point,
            doc_type=FISCAL_DOC_TYPE_FC,
            issue_mode="manual",
            status="authorized",
            number=10,
            subtotal_net=Decimal("100.00"),
            total=Decimal("100.00"),
        )
        credit_note_c = FiscalDocument.objects.create(
            source_key="test:fiscal:compat:ncc",
            company=self.company,
            point_of_sale=point,
            doc_type=FISCAL_DOC_TYPE_NCC,
            issue_mode="manual",
            status="ready_to_issue",
            number=1,
            related_document=invoice_c,
            subtotal_net=Decimal("10.00"),
            total=Decimal("10.00"),
        )

        is_valid, errors = validate_credit_note_relationship(credit_note_c)

        self.assertTrue(is_valid)
        self.assertEqual(errors, [])
