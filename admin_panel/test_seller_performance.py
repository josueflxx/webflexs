from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientCompany, ClientProfile
from core.models import (
    Company,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalPointOfSale,
)
from core.services.company_context import SESSION_COMPANY_KEY
from orders.models import Order


class SellerPerformanceReportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="seller_report_admin",
            email="seller-report@example.com",
            password="test-password",
        )
        self.seller = User.objects.create_user(
            username="seller_one",
            first_name="Vendedor",
            password="test-password",
            is_staff=True,
        )
        self.other_seller = User.objects.create_user(
            username="seller_two",
            password="test-password",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Empresa vendedores")
        self.other_company = Company.objects.create(name="Empresa vendedores B")
        self.point = FiscalPointOfSale.objects.create(
            company=self.company,
            number="1",
            is_default=True,
        )
        self.client_user = User.objects.create_user(username="seller_report_client")
        self.profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name="Cliente estadisticas",
        )
        self.client_company = ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.company,
        )
        self.order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            client_company_ref=self.client_company,
            assigned_to=self.seller,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("100.00"),
            total=Decimal("121.00"),
        )
        self.invoice = FiscalDocument.objects.create(
            source_key="seller-report-invoice",
            company=self.company,
            client_company_ref=self.client_company,
            client_profile=self.profile,
            order=self.order,
            point_of_sale=self.point,
            doc_type="FA",
            issue_mode="external_saas",
            status="draft",
            issued_at=timezone.now(),
            subtotal_net=Decimal("100.00"),
            tax_total=Decimal("21.00"),
            total=Decimal("121.00"),
        )
        FiscalDocumentItem.objects.create(
            fiscal_document=self.invoice,
            line_number=1,
            sku="SELLER-1",
            description="Producto del vendedor",
            quantity=Decimal("2.000"),
            unit_price_net=Decimal("50.00"),
            net_amount=Decimal("100.00"),
            iva_rate=Decimal("21.00"),
            iva_amount=Decimal("21.00"),
            total_amount=Decimal("121.00"),
        )
        self.invoice.transition_to("ready_to_issue")
        self.invoice.transition_to("external_recorded")
        self.credit_note = FiscalDocument.objects.create(
            source_key="seller-report-credit",
            company=self.company,
            client_company_ref=self.client_company,
            client_profile=self.profile,
            order=self.order,
            related_document=self.invoice,
            point_of_sale=self.point,
            doc_type="NCA",
            issue_mode="external_saas",
            status="external_recorded",
            issued_at=timezone.now(),
            subtotal_net=Decimal("10.00"),
            tax_total=Decimal("2.10"),
            total=Decimal("12.10"),
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session[SESSION_COMPANY_KEY] = self.company.pk
        session.save()

    def test_report_aggregates_invoices_credits_and_orders_by_seller(self):
        response = self.client.get(reverse("admin_seller_performance"))

        self.assertEqual(response.status_code, 200)
        row = next(
            item
            for item in response.context["seller_rows"]
            if item["seller_id"] == self.seller.pk
        )
        self.assertEqual(row["orders_count"], 1)
        self.assertEqual(row["invoices_count"], 1)
        self.assertEqual(row["credit_notes_count"], 1)
        self.assertEqual(row["billed_total"], Decimal("121.00"))
        self.assertEqual(row["credit_total"], Decimal("12.10"))
        self.assertEqual(row["net_billed"], Decimal("108.90"))
        self.assertContains(response, "Vendedor")
        self.assertContains(response, "asignado")

    def test_selected_seller_shows_products_and_recent_documents(self):
        response = self.client.get(
            reverse("admin_seller_performance"),
            {"seller_id": self.seller.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_seller"], self.seller)
        self.assertEqual(len(response.context["top_products"]), 1)
        self.assertEqual(
            response.context["top_products"][0]["quantity_total"],
            Decimal("2"),
        )
        self.assertEqual(response.context["recent_documents"], [self.invoice])
        self.assertContains(response, "Producto del vendedor")

    def test_report_does_not_mix_other_company(self):
        other_client_user = User.objects.create_user(username="other_seller_client")
        other_profile = ClientProfile.objects.create(
            user=other_client_user,
            company_name="Cliente otra empresa",
        )
        other_link = ClientCompany.objects.create(
            client_profile=other_profile,
            company=self.other_company,
        )
        Order.objects.create(
            user=other_client_user,
            company=self.other_company,
            client_company_ref=other_link,
            assigned_to=self.other_seller,
            status=Order.STATUS_CONFIRMED,
            total=Decimal("999.00"),
        )

        response = self.client.get(reverse("admin_seller_performance"))

        seller_ids = {
            item["seller_id"] for item in response.context["seller_rows"]
        }
        self.assertIn(self.seller.pk, seller_ids)
        self.assertNotIn(self.other_seller.pk, seller_ids)
