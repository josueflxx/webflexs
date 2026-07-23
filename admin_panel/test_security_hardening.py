import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientCompany, ClientFiscalReview, ClientProfile
from catalog.models import Product
from core.models import AdminCompanyAccess, Company
from core.decorators import PRIMARY_SUPERADMIN_USERNAME
from orders.models import Order, OrderItem


class TenantAndPriceGuardViewTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name="View Scope Company A")
        self.company_b = Company.objects.create(name="View Scope Company B")
        self.staff = User.objects.create_user(
            username="view_scope_staff",
            password="test-password",
            is_staff=True,
        )
        sales_group, _ = Group.objects.get_or_create(name="ventas")
        self.staff.groups.add(sales_group)
        AdminCompanyAccess.objects.create(user=self.staff, company=self.company_a)

        self.client_user_a = User.objects.create_user(username="view_client_a")
        self.client_a = ClientProfile.objects.create(
            user=self.client_user_a,
            company_name="View Client A",
        )
        self.client_link_a = ClientCompany.objects.create(
            client_profile=self.client_a,
            company=self.company_a,
        )

        self.client_user_b = User.objects.create_user(username="view_client_b")
        self.client_b = ClientProfile.objects.create(
            user=self.client_user_b,
            company_name="View Client B",
        )
        self.client_link_b = ClientCompany.objects.create(
            client_profile=self.client_b,
            company=self.company_b,
        )

        self.order_a = Order.objects.create(
            user=self.client_user_a,
            company=self.company_a,
            client_company_ref=self.client_link_a,
            client_company=self.client_a.company_name,
        )
        self.order_b = Order.objects.create(
            user=self.client_user_b,
            company=self.company_b,
            client_company_ref=self.client_link_b,
            client_company=self.client_b.company_name,
        )
        self.product = Product.objects.create(
            sku="SEC-PRICE-1",
            name="Security price product",
            price=Decimal("100.00"),
            cost=Decimal("50.00"),
            stock=10,
            is_active=True,
        )
        self.alt_product = Product.objects.create(
            sku="SEC-PRICE-2",
            name="Security alternate product",
            price=Decimal("240.00"),
            cost=Decimal("120.00"),
            stock=10,
            is_active=True,
        )

        self.client.force_login(self.staff)
        session = self.client.session
        session["active_company_id"] = self.company_a.pk
        session.save()

    def _add_item(self, order, *, price=None, observation=""):
        data = {
            "product_id": str(self.product.pk),
            "sku": self.product.sku,
            "quantity": "1",
        }
        if price is not None:
            data["price"] = str(price)
        if observation:
            data["price_override_note"] = observation
        return self.client.post(
            reverse("admin_order_item_add", args=[order.pk]),
            data=data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_order_and_client_from_another_company_are_not_resolvable(self):
        self.assertEqual(
            self.client.get(reverse("admin_order_detail", args=[self.order_b.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("admin_client_order_history", args=[self.client_b.pk])).status_code,
            404,
        )
        self.assertEqual(self._add_item(self.order_b).status_code, 404)

    def test_below_cost_price_requires_observation(self):
        response = self._add_item(self.order_a, price="1.00")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(OrderItem.objects.filter(order=self.order_a).exists())

    def test_calculated_price_remains_available_without_override_permission(self):
        response = self._add_item(self.order_a)

        self.assertEqual(response.status_code, 200)
        item = OrderItem.objects.get(order=self.order_a)
        self.assertEqual(item.price_at_purchase, Decimal("100.00"))

    def test_observation_allows_below_cost_price_for_sales_operator(self):
        response = self._add_item(
            self.order_a,
            price="1.00",
            observation="Precio autorizado por cierre comercial",
        )

        self.assertEqual(response.status_code, 200)
        item = OrderItem.objects.get(order=self.order_a)
        self.assertEqual(item.price_at_purchase, Decimal("1.00"))
        self.assertEqual(item.price_override_note, "Precio autorizado por cierre comercial")

    def test_all_sales_operators_can_override_price_above_cost(self):
        response = self._add_item(self.order_a, price="80.00")

        self.assertEqual(response.status_code, 200)
        item = OrderItem.objects.get(order=self.order_a)
        self.assertEqual(item.price_at_purchase, Decimal("80.00"))

    def test_existing_item_below_cost_change_requires_observation(self):
        item = OrderItem.objects.create(
            order=self.order_a,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=1,
            unit_price_base=Decimal("100.00"),
            price_at_purchase=Decimal("100.00"),
        )

        response = self.client.post(
            reverse("admin_order_item_edit", args=[self.order_a.pk, item.pk]),
            data={
                "product_id": str(self.product.pk),
                "sku": self.product.sku,
                "quantity": "1",
                "price": "1.00",
            },
        )

        self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.price_at_purchase, Decimal("100.00"))

    def test_product_replacement_below_cost_requires_observation(self):
        item = OrderItem.objects.create(
            order=self.order_a,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=1,
            unit_price_base=Decimal("100.00"),
            price_at_purchase=Decimal("100.00"),
        )

        response = self.client.post(
            reverse("admin_order_item_edit", args=[self.order_a.pk, item.pk]),
            data={
                "product_id": str(self.alt_product.pk),
                "sku": self.alt_product.sku,
                "quantity": "1",
                "price": "1.00",
            },
        )

        self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.product_id, self.product.pk)
        self.assertEqual(item.price_at_purchase, Decimal("100.00"))

    def test_sales_operator_can_change_seller(self):
        seller = User.objects.create_user(
            username="second_seller",
            is_staff=True,
        )

        response = self.client.post(
            reverse("admin_order_detail", args=[self.order_a.pk]),
            data={"action": "assign_seller", "assigned_to": str(seller.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.order_a.refresh_from_db()
        self.assertEqual(self.order_a.assigned_to_id, seller.pk)

    def test_partial_scope_operator_cannot_edit_shared_client_profile(self):
        ClientCompany.objects.create(
            client_profile=self.client_a,
            company=self.company_b,
        )

        response = self.client.post(
            reverse("admin_client_edit", args=[self.client_a.pk]),
            data={"company_id": str(self.company_a.pk)},
        )

        self.assertEqual(response.status_code, 403)


class ProductCommercialRuleFormTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username=PRIMARY_SUPERADMIN_USERNAME,
            password="test-password",
            email="admin@example.com",
        )
        self.company = Company.objects.create(name="Product form company")
        self.client.force_login(self.superuser)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def test_product_form_saves_net_price_iva_and_optional_stock(self):
        response = self.client.post(
            reverse("admin_product_create"),
            data={
                "sku": "COMMERCIAL-RULE-1",
                "name": "Producto reglas comerciales",
                "supplier": "",
                "supplier_code": "",
                "price": "100.00",
                "cost": "60.00",
                "iva_rate": "21.00",
                "stock": "12",
                "tracks_stock": "on",
                "category": "",
                "description": "",
                "attributes_json": "{}",
                "product_blocks_json": "{}",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_product_list"))
        product = Product.objects.get(sku="COMMERCIAL-RULE-1")
        self.assertEqual(product.price, Decimal("100.00"))
        self.assertEqual(product.iva_rate, Decimal("21.00"))
        self.assertTrue(product.tracks_stock)

    def test_grid_bulk_assigns_iva_and_optional_stock_to_selected_products(self):
        first = Product.objects.create(
            sku="GRID-FISCAL-1", name="Grid fiscal 1", price=Decimal("100.00"), cost=Decimal("50.00")
        )
        second = Product.objects.create(
            sku="GRID-FISCAL-2", name="Grid fiscal 2", price=Decimal("100.00"), cost=Decimal("50.00")
        )
        url = reverse("admin_product_grid_bulk_update")

        iva_response = self.client.post(
            url,
            data=json.dumps(
                {
                    "product_ids": [first.pk, second.pk],
                    "action": "iva_rate",
                    "iva_rate": "10.50",
                }
            ),
            content_type="application/json",
        )
        stock_response = self.client.post(
            url,
            data=json.dumps(
                {
                    "product_ids": [first.pk, second.pk],
                    "action": "tracks_stock",
                    "tracks_stock": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(iva_response.status_code, 200)
        self.assertEqual(stock_response.status_code, 200)
        self.assertEqual(
            Product.objects.filter(pk__in=[first.pk, second.pk], iva_rate=Decimal("10.50")).count(),
            2,
        )
        self.assertEqual(
            Product.objects.filter(pk__in=[first.pk, second.pk], tracks_stock=True).count(),
            2,
        )

    def test_grid_bulk_select_all_respects_missing_iva_filter(self):
        missing = Product.objects.create(
            sku="GRID-MISSING-IVA", name="Missing IVA", price=Decimal("100.00"), cost=Decimal("50.00")
        )
        configured = Product.objects.create(
            sku="GRID-CONFIGURED-IVA",
            name="Configured IVA",
            price=Decimal("100.00"),
            cost=Decimal("50.00"),
            iva_rate=Decimal("21.00"),
        )

        response = self.client.post(
            reverse("admin_product_grid_bulk_update"),
            data=json.dumps(
                {
                    "product_ids": "all_filtered",
                    "action": "iva_rate",
                    "iva_rate": "2.50",
                    "filters": {"f_iva": "missing"},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        missing.refresh_from_db()
        configured.refresh_from_db()
        self.assertEqual(missing.iva_rate, Decimal("2.50"))
        self.assertEqual(configured.iva_rate, Decimal("21.00"))


class ClientFiscalReviewTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Fiscal review company")
        self.staff = User.objects.create_user(
            username="fiscal_review_staff",
            password="test-password",
            is_staff=True,
        )
        AdminCompanyAccess.objects.create(user=self.staff, company=self.company)
        self.existing_user = User.objects.create_user(username="existing_fiscal_client")
        self.existing = ClientProfile.objects.create(
            user=self.existing_user,
            company_name="Cliente fiscal existente",
            cuit_dni="20-12345678-6",
            document_number="20123456786",
        )
        ClientCompany.objects.create(client_profile=self.existing, company=self.company)
        self.client.force_login(self.staff)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def test_duplicate_cuit_is_queued_once_for_manual_review(self):
        url = reverse("admin_client_cuit_lookup")
        first = self.client.get(url, {"cuit": "20-12345678-6"})
        second = self.client.get(url, {"cuit": "20123456786"})

        self.assertEqual(first.status_code, 409)
        self.assertEqual(second.status_code, 409)
        self.assertTrue(first.json()["duplicate"])
        self.assertEqual(ClientFiscalReview.objects.count(), 1)
        review = ClientFiscalReview.objects.get()
        self.assertEqual(review.status, ClientFiscalReview.STATUS_PENDING)
        self.assertEqual(list(review.candidate_profiles.all()), [self.existing])

    def test_client_create_does_not_persist_a_duplicate_cuit(self):
        response = self.client.post(
            reverse("admin_client_create"),
            data={
                "username": "duplicate_fiscal_client",
                "company_id": str(self.company.pk),
                "company_name": "Cliente fiscal duplicado",
                "cuit_dni": "20-12345678-6",
                "document_number": "20123456786",
                "password": "ClaveSegura123!",
                "password_confirm": "ClaveSegura123!",
                "company_is_active": "on",
                "client_is_approved": "on",
                "user_is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="duplicate_fiscal_client").exists())
        self.assertEqual(ClientFiscalReview.objects.count(), 1)
        self.assertContains(response, "no se creo un duplicado")

    def test_review_requires_observation_and_can_be_resolved_by_staff(self):
        review = ClientFiscalReview.objects.create(
            company=self.company,
            normalized_document="20123456786",
            reason=ClientFiscalReview.REASON_DUPLICATE,
            requested_by=self.staff,
        )
        url = reverse("admin_client_fiscal_review_resolve", args=[review.pk])

        missing_note_response = self.client.post(url, {"action": "resolved"})
        review.refresh_from_db()
        self.assertEqual(missing_note_response.status_code, 302)
        self.assertEqual(review.status, ClientFiscalReview.STATUS_PENDING)

        response = self.client.post(
            url,
            {"action": "resolved", "resolution_note": "Se verifico y se unificaran los datos."},
        )
        review.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(review.status, ClientFiscalReview.STATUS_RESOLVED)
        self.assertEqual(review.resolved_by, self.staff)
        self.assertIsNotNone(review.resolved_at)

    def test_invalid_cuit_checksum_is_rejected(self):
        response = self.client.get(
            reverse("admin_client_cuit_lookup"),
            {"cuit": "20-12345678-9"},
        )

        self.assertEqual(response.status_code, 400)
