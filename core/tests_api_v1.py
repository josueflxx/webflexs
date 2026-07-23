from decimal import Decimal
import tempfile

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.authtoken.models import Token

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Category, Product, ProductSupplier, Supplier
from orders.models import Order
from core.models import (
    AdminCapabilityProfile,
    AdminCompanyAccess,
    Company,
    ExternalEditorDraft,
    ExternalEditorJob,
    ExternalEditorSavedView,
)
from core.services.company_context import get_default_company


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
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
        )
        self.other_client_company = ClientCompany.objects.create(
            client_profile=self.other_profile,
            company=self.company,
            is_active=True,
        )
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

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
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("1000.00"),
            total=Decimal("1000.00"),
            client_company_ref=self.client_company,
        )
        self.order_other = Order.objects.create(
            user=self.other_user,
            company=self.company,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal("700.00"),
            total=Decimal("700.00"),
            client_company_ref=self.other_client_company,
        )

    def _results(self, response):
        payload = response.json()
        return payload.get("results", payload)

    def _select_company(self):
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

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
        self._select_company()
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

    def test_orders_queue_staff_only(self):
        self.client.force_login(self.client_user)
        forbidden = self.client.get(reverse("api_v1:orders_queue"))
        self.assertIn(forbidden.status_code, (401, 403))

        self.client.force_login(self.staff_user)
        self._select_company()
        response = self.client.get(reverse("api_v1:orders_queue"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("role"), "admin")
        self.assertIn("results", payload)

    def test_order_workflow_endpoint_visibility(self):
        self.client.force_login(self.client_user)
        own = self.client.get(
            reverse("api_v1:orders_workflow", kwargs={"order_id": self.order_client.id})
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json().get("order_id"), self.order_client.id)

        other = self.client.get(
            reverse("api_v1:orders_workflow", kwargs={"order_id": self.order_other.id})
        )
        self.assertEqual(other.status_code, 404)


class ApiV1CompanyIsolationTests(TestCase):
    def setUp(self):
        self.company_a = get_default_company()
        self.company_b = Company.objects.create(name="API Company B", slug="api-company-b")
        self.company_c = Company.objects.create(name="API Company C", slug="api-company-c")

        self.client_a_user = User.objects.create_user("company_client_a", password="secret123")
        self.client_b_user = User.objects.create_user("company_client_b", password="secret123")
        self.client_a_profile = ClientProfile.objects.create(
            user=self.client_a_user,
            company_name="Cliente Empresa A",
            is_approved=True,
        )
        self.client_b_profile = ClientProfile.objects.create(
            user=self.client_b_user,
            company_name="Cliente Empresa B",
            is_approved=True,
        )
        self.client_a_link = ClientCompany.objects.create(
            client_profile=self.client_a_profile,
            company=self.company_a,
            is_active=True,
        )
        self.client_b_link = ClientCompany.objects.create(
            client_profile=self.client_b_profile,
            company=self.company_b,
            is_active=True,
        )
        self.order_a = Order.objects.create(
            user=self.client_a_user,
            company=self.company_a,
            client_company_ref=self.client_a_link,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
        )
        self.order_b = Order.objects.create(
            user=self.client_b_user,
            company=self.company_b,
            client_company_ref=self.client_b_link,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal("200.00"),
            total=Decimal("200.00"),
        )

        role, _created = Group.objects.get_or_create(name="ventas")
        self.staff_without_company = User.objects.create_user(
            "staff_without_company",
            password="secret123",
            is_staff=True,
        )
        self.staff_without_company.groups.add(role)
        self.staff_one_company = User.objects.create_user(
            "staff_one_company",
            password="secret123",
            is_staff=True,
        )
        self.staff_one_company.groups.add(role)
        AdminCompanyAccess.objects.create(user=self.staff_one_company, company=self.company_a)

        self.staff_multiple_companies = User.objects.create_user(
            "staff_multiple_companies",
            password="secret123",
            is_staff=True,
        )
        self.staff_multiple_companies.groups.add(role)
        AdminCompanyAccess.objects.create(user=self.staff_multiple_companies, company=self.company_a)
        AdminCompanyAccess.objects.create(user=self.staff_multiple_companies, company=self.company_b)

        self.staff_without_capabilities = User.objects.create_user(
            "staff_without_capabilities",
            password="secret123",
            is_staff=True,
        )
        AdminCompanyAccess.objects.create(user=self.staff_without_capabilities, company=self.company_a)

    @staticmethod
    def _results(response):
        payload = response.json()
        return payload.get("results", payload)

    def _select_company(self, company):
        session = self.client.session
        session["active_company_id"] = company.pk
        session.save()

    def test_staff_without_company_access_gets_empty_company_scoped_endpoints(self):
        self.client.force_login(self.staff_without_company)

        clients_response = self.client.get(reverse("api_v1:clients"))
        orders_response = self.client.get(reverse("api_v1:orders"))
        queue_response = self.client.get(reverse("api_v1:orders_queue"))
        workflow_response = self.client.get(
            reverse("api_v1:orders_workflow", kwargs={"order_id": self.order_a.pk})
        )

        self.assertEqual(clients_response.status_code, 200)
        self.assertEqual(self._results(clients_response), [])
        self.assertEqual(orders_response.status_code, 200)
        self.assertEqual(self._results(orders_response), [])
        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(self._results(queue_response), [])
        self.assertEqual(workflow_response.status_code, 404)

    def test_single_company_staff_is_automatically_scoped(self):
        self.client.force_login(self.staff_one_company)

        clients_response = self.client.get(reverse("api_v1:clients"))
        orders_response = self.client.get(reverse("api_v1:orders"))
        queue_response = self.client.get(reverse("api_v1:orders_queue"))

        self.assertEqual(
            {row["username"] for row in self._results(clients_response)},
            {self.client_a_user.username},
        )
        self.assertEqual(
            {row["id"] for row in self._results(orders_response)},
            {self.order_a.pk},
        )
        self.assertEqual(
            {row["id"] for row in self._results(queue_response)},
            {self.order_a.pk},
        )

    def test_multiple_company_staff_must_select_an_authorized_company(self):
        self.client.force_login(self.staff_multiple_companies)

        no_company = self.client.get(reverse("api_v1:orders"))
        company_a = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": self.company_a.pk},
        )
        company_b = self.client.get(
            reverse("api_v1:orders"),
            {"company": self.company_b.pk},
        )

        self.assertEqual(self._results(no_company), [])
        self.assertEqual({row["id"] for row in self._results(company_a)}, {self.order_a.pk})
        self.assertEqual({row["id"] for row in self._results(company_b)}, {self.order_b.pk})

    def test_unauthorized_or_invalid_explicit_company_does_not_fall_back_to_session(self):
        self.client.force_login(self.staff_one_company)
        self._select_company(self.company_a)

        unauthorized = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": self.company_c.pk},
        )
        invalid = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": "invalid"},
        )

        self.assertEqual(self._results(unauthorized), [])
        self.assertEqual(self._results(invalid), [])

    def test_staff_without_required_capability_is_denied(self):
        self.client.force_login(self.staff_without_capabilities)

        clients_response = self.client.get(reverse("api_v1:clients"))
        orders_response = self.client.get(reverse("api_v1:orders"))
        queue_response = self.client.get(reverse("api_v1:orders_queue"))

        self.assertEqual(clients_response.status_code, 403)
        self.assertEqual(orders_response.status_code, 403)
        self.assertEqual(queue_response.status_code, 403)

    def test_staff_without_price_capability_does_not_receive_product_cost(self):
        category = Category.objects.create(name="Categoria costo API", is_active=True)
        product = Product.objects.create(
            sku="API-COST-SCOPE",
            name="Producto costo protegido",
            category=category,
            cost=Decimal("90.00"),
            price=Decimal("120.00"),
            stock=1,
            is_active=True,
        )
        self.client.force_login(self.staff_one_company)

        response = self.client.get(
            reverse("api_v1:catalog_products"),
            {"q": product.sku},
        )

        self.assertEqual(response.status_code, 200)
        rows = self._results(response)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("cost", rows[0])

    def test_client_with_multiple_companies_must_select_scope(self):
        shared_user = User.objects.create_user("shared_company_client", password="secret123")
        shared_profile = ClientProfile.objects.create(
            user=shared_user,
            company_name="Cliente Compartido",
            is_approved=True,
        )
        shared_a = ClientCompany.objects.create(
            client_profile=shared_profile,
            company=self.company_a,
            is_active=True,
        )
        shared_b = ClientCompany.objects.create(
            client_profile=shared_profile,
            company=self.company_b,
            is_active=True,
        )
        shared_order_a = Order.objects.create(
            user=shared_user,
            company=self.company_a,
            client_company_ref=shared_a,
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        shared_order_b = Order.objects.create(
            user=shared_user,
            company=self.company_b,
            client_company_ref=shared_b,
            subtotal=Decimal("20.00"),
            total=Decimal("20.00"),
        )
        self.client.force_login(shared_user)

        without_scope = self.client.get(reverse("api_v1:orders"))
        company_a = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": self.company_a.pk},
        )
        unauthorized = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": self.company_c.pk},
        )

        active_company_id = self.client.session.get("active_company_id")
        expected_orders = {shared_order_a.pk} if active_company_id == self.company_a.pk else {shared_order_b.pk}
        self.assertEqual({row["id"] for row in self._results(without_scope)}, expected_orders)
        self.assertEqual({row["id"] for row in self._results(company_a)}, {shared_order_a.pk})
        self.assertEqual(self._results(unauthorized), [])

    def test_token_authenticated_staff_is_scoped_by_explicit_company(self):
        token = Token.objects.create(user=self.staff_multiple_companies)
        auth_header = f"Token {token.key}"

        authorized = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": self.company_b.pk},
            HTTP_AUTHORIZATION=auth_header,
        )
        unauthorized = self.client.get(
            reverse("api_v1:orders"),
            {"company_id": self.company_c.pk},
            HTTP_AUTHORIZATION=auth_header,
        )

        self.assertEqual(authorized.status_code, 200)
        self.assertEqual({row["id"] for row in self._results(authorized)}, {self.order_b.pk})
        self.assertEqual(unauthorized.status_code, 200)
        self.assertEqual(self._results(unauthorized), [])


@override_settings(
    FEATURE_EXTERNAL_EDITOR_ENABLED=True,
    FEATURE_EXTERNAL_EDITOR_WRITES=True,
)
class ExternalEditorApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="external_editor_admin",
            password="secret123",
            is_staff=True,
            is_superuser=True,
        )
        self.client_user = User.objects.create_user(
            username="external_editor_client",
            password="secret123",
        )
        self.root_category = Category.objects.create(name="Editor Rubro", is_active=True)
        self.child_category = Category.objects.create(
            name="Editor Subrubro",
            parent=self.root_category,
            is_active=True,
        )
        self.supplier = Supplier.objects.create(name="Proveedor Editor")
        self.product = Product.objects.create(
            sku="EDITOR-001",
            name="Producto Editor",
            description="Inicial",
            cost=Decimal("100.00"),
            price=Decimal("150.00"),
            stock=5,
            category=self.child_category,
            supplier=self.supplier.name,
            supplier_ref=self.supplier,
            is_active=True,
        )
        self.product.categories.add(self.child_category)
        self.client.force_login(self.admin)

    def test_list_uses_editor_contract_and_server_filters(self):
        response = self.client.get(
            reverse("api_v1:editor_products"),
            {"code": "EDITOR-001", "categoryId": self.root_category.pk},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        row = payload["items"][0]
        self.assertEqual(row["internalCode"], self.product.sku)
        self.assertEqual(row["categoryId"], self.root_category.pk)
        self.assertEqual(row["subcategoryId"], self.child_category.pk)
        self.assertEqual(Decimal(row["margin"]), Decimal("50.00"))

    def test_selection_ids_resolves_all_filtered_products(self):
        Product.objects.create(
            sku="OTHER-001",
            name="Otro producto",
            cost=Decimal("10.00"),
            price=Decimal("15.00"),
            category=self.root_category,
        )

        response = self.client.get(
            reverse("api_v1:editor_product_selection_ids"),
            {"code": "EDITOR-"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ids": [self.product.pk], "count": 1})

    def test_patch_updates_official_product_and_hierarchy(self):
        response = self.client.patch(
            reverse("api_v1:editor_product_detail", kwargs={"product_id": self.product.pk}),
            {
                "name": "Producto actualizado",
                "cost": "110.00",
                "margin": "50",
                "stock": 9,
                "categoryId": self.root_category.pk,
                "subcategoryId": self.child_category.pk,
                "expectedUpdatedAt": self.product.updated_at.isoformat(),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Producto actualizado")
        self.assertEqual(self.product.cost, Decimal("110.00"))
        self.assertEqual(self.product.price, Decimal("165.00"))
        self.assertEqual(self.product.stock, 9)
        self.assertEqual(self.product.category_id, self.child_category.pk)

    def test_patch_supplier_keeps_preferred_offer_in_sync(self):
        other_supplier = Supplier.objects.create(name="Proveedor Editor Nuevo")

        response = self.client.patch(
            reverse("api_v1:editor_product_detail", kwargs={"product_id": self.product.pk}),
            {"supplierId": other_supplier.pk},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.supplier_ref_id, other_supplier.pk)
        self.assertTrue(
            ProductSupplier.objects.filter(
                product=self.product,
                supplier=other_supplier,
                is_preferred=True,
            ).exists()
        )

    def test_patch_rejects_stale_version(self):
        stale_version = self.product.updated_at.isoformat()
        self.product.name = "Cambio concurrente"
        self.product.save(update_fields=["name", "updated_at"])

        response = self.client.patch(
            reverse("api_v1:editor_product_detail", kwargs={"product_id": self.product.pk}),
            {"name": "Sobrescritura", "expectedUpdatedAt": stale_version},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Cambio concurrente")

    @override_settings(FEATURE_EXTERNAL_EDITOR_WRITES=False)
    def test_patch_is_locked_behind_write_feature_flag(self):
        response = self.client.patch(
            reverse("api_v1:editor_product_detail", kwargs={"product_id": self.product.pk}),
            {"name": "No debe cambiar"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Producto Editor")

    def test_non_staff_user_cannot_access_editor(self):
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("api_v1:editor_products"))
        self.assertEqual(response.status_code, 403)

    def test_price_fields_require_price_capability(self):
        limited_staff = User.objects.create_user(
            username="external_editor_limited",
            password="secret123",
            is_staff=True,
        )
        AdminCapabilityProfile.objects.create(
            user=limited_staff,
            capabilities=["manage_products"],
            is_configured=True,
        )
        self.client.force_login(limited_staff)

        response = self.client.patch(
            reverse("api_v1:editor_product_detail", kwargs={"product_id": self.product.pk}),
            {"price": "999.00"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal("150.00"))

    def test_bulk_preview_and_idempotent_execution(self):
        payload = {
            "productIds": [self.product.pk],
            "rules": [
                {"field": "cost", "action": "pct_inc", "value": "10"},
                {"field": "margin", "action": "set", "value": "50"},
            ],
        }
        preview = self.client.post(
            reverse("api_v1:editor_bulk_preview"),
            payload,
            content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["total"], 1)

        first = self.client.post(
            reverse("api_v1:editor_bulk"),
            payload,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-cost-job-1",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], ExternalEditorJob.STATUS_COMPLETED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal("110.00"))
        self.assertEqual(self.product.price, Decimal("165.00"))

        repeated = self.client.post(
            reverse("api_v1:editor_bulk"),
            payload,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-cost-job-1",
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["id"], first.json()["id"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal("110.00"))

    def test_bulk_job_can_be_rolled_back(self):
        response = self.client.post(
            reverse("api_v1:editor_bulk"),
            {
                "productIds": [self.product.pk],
                "changes": {"name": "Nombre masivo", "stock": 33},
            },
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-rollback-job-1",
        )
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["id"]
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Nombre masivo")
        self.assertEqual(self.product.stock, 33)

        rollback = self.client.post(
            reverse("api_v1:editor_job_rollback", kwargs={"job_id": job_id}),
            {},
            content_type="application/json",
        )
        self.assertEqual(rollback.status_code, 200)
        self.assertEqual(rollback.json()["status"], ExternalEditorJob.STATUS_ROLLED_BACK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Producto Editor")
        self.assertEqual(self.product.stock, 5)

    def test_bulk_rollback_refuses_to_overwrite_a_later_change(self):
        response = self.client.post(
            reverse("api_v1:editor_bulk"),
            {"productIds": [self.product.pk], "changes": {"name": "Nombre del trabajo"}},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-rollback-conflict-1",
        )
        job_id = response.json()["id"]
        self.product.name = "Cambio posterior"
        self.product.save(update_fields=["name", "updated_at"])

        rollback = self.client.post(
            reverse("api_v1:editor_job_rollback", kwargs={"job_id": job_id}),
            {},
            content_type="application/json",
        )

        self.assertEqual(rollback.status_code, 200)
        self.assertEqual(rollback.json()["status"], ExternalEditorJob.STATUS_ROLLBACK_PARTIAL)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Cambio posterior")

    def test_idempotency_key_cannot_be_reused_for_another_payload(self):
        first = self.client.post(
            reverse("api_v1:editor_bulk"),
            {"productIds": [self.product.pk], "changes": {"stock": 6}},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-conflicting-key-1",
        )
        self.assertEqual(first.status_code, 200)

        conflicting = self.client.post(
            reverse("api_v1:editor_bulk"),
            {"productIds": [self.product.pk], "changes": {"stock": 7}},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-conflicting-key-1",
        )
        self.assertEqual(conflicting.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 6)

    def test_react_compatibility_login_accepts_bearer_token(self):
        self.client.logout()
        login = self.client.post(
            reverse("api_v1:editor_login_compat"),
            {"username": self.admin.username, "password": "secret123"},
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["token"]

        profile = self.client.get(
            reverse("api_v1:editor_profile_compat"),
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        products = self.client.get(
            reverse("api_v1:editor_products_compat"),
            {"code": self.product.sku},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["username"], self.admin.username)
        self.assertEqual(products.status_code, 200)
        self.assertEqual(products.json()["totalCount"], 1)

    def test_react_compatibility_put_can_clear_category_and_supplier(self):
        response = self.client.put(
            reverse("api_v1:editor_product_detail_compat", kwargs={"product_id": self.product.pk}),
            {"clearCategory": True, "clearSupplier": True},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.category_id)
        self.assertIsNone(self.product.supplier_ref_id)
        self.assertEqual(self.product.supplier, "")
        self.assertFalse(self.product.categories.exists())

    def test_saved_views_are_persistent_and_user_scoped(self):
        created = self.client.post(
            reverse("api_v1:editor_saved_views"),
            {"name": "Sin stock", "filters": {"stock": "out"}},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(ExternalEditorSavedView.objects.filter(created_by=self.admin, name="Sin stock").exists())

        listed = self.client.get(reverse("api_v1:editor_saved_views"))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["filters"], {"stock": "out"})

    def test_draft_preview_publish_and_job_history(self):
        draft_response = self.client.post(
            reverse("api_v1:editor_drafts"),
            {
                "name": "Revision del catalogo",
                "changes": [
                    {
                        "productId": self.product.pk,
                        "changes": {"name": "Producto publicado", "tags": ["Revisado", "Oferta"]},
                    }
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(draft_response.status_code, 201)
        draft_id = draft_response.json()["id"]

        published = self.client.post(
            reverse("api_v1:editor_draft_publish", kwargs={"draft_id": draft_id}),
            {},
            content_type="application/json",
        )
        self.assertEqual(published.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Producto publicado")
        self.assertEqual(self.product.attributes["editor_tags"], ["Revisado", "Oferta"])
        self.assertEqual(ExternalEditorDraft.objects.get(pk=draft_id).status, ExternalEditorDraft.STATUS_PUBLISHED)

        jobs = self.client.get(reverse("api_v1:editor_jobs"))
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()["items"][0]["operation"], "draft")

    def test_workspace_validation_duplicates_clone_and_trash(self):
        Product.objects.create(
            sku="EDITOR-002",
            name=self.product.name,
            cost=Decimal("0"),
            price=Decimal("0"),
            stock=0,
        )
        workspace = self.client.get(reverse("api_v1:editor_workspace"))
        validation = self.client.get(reverse("api_v1:editor_validation"))
        duplicates = self.client.get(reverse("api_v1:editor_duplicates"))
        self.assertEqual(workspace.status_code, 200)
        self.assertGreaterEqual(workspace.json()["duplicateGroups"], 1)
        self.assertGreaterEqual(validation.json()["invalid"], 1)
        self.assertGreaterEqual(duplicates.json()["totalGroups"], 1)

        cloned = self.client.post(
            reverse("api_v1:editor_product_clone", kwargs={"product_id": self.product.pk}),
            {},
            content_type="application/json",
        )
        self.assertEqual(cloned.status_code, 201)
        clone_id = cloned.json()["id"]

        trashed = self.client.post(
            reverse("api_v1:editor_product_trash", kwargs={"product_id": clone_id}),
            {"action": "trash"},
            content_type="application/json",
        )
        self.assertTrue(trashed.json()["isDeleted"])
        regular_list = self.client.get(reverse("api_v1:editor_products"), {"code": cloned.json()["internalCode"]})
        trash_list = self.client.get(reverse("api_v1:editor_products"), {"trash": "true"})
        self.assertEqual(regular_list.json()["total"], 0)
        self.assertTrue(any(item["id"] == clone_id for item in trash_list.json()["items"]))

        restored = self.client.post(
            reverse("api_v1:editor_product_trash", kwargs={"product_id": clone_id}),
            {"action": "restore"},
            content_type="application/json",
        )
        self.assertFalse(restored.json()["isDeleted"])

    def test_image_upload_and_removal(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            uploaded = self.client.post(
                reverse("api_v1:editor_product_image", kwargs={"product_id": self.product.pk}),
                {"image": SimpleUploadedFile("producto.png", b"fake-png-content", content_type="image/png")},
            )
            self.assertEqual(uploaded.status_code, 200)
            self.assertIn("producto", uploaded.json()["imageUrl"])
            removed = self.client.delete(
                reverse("api_v1:editor_product_image", kwargs={"product_id": self.product.pk})
            )
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(removed.json()["imageUrl"], "")

    def test_redo_repeats_a_rolled_back_job(self):
        created = self.client.post(
            reverse("api_v1:editor_bulk"),
            {"productIds": [self.product.pk], "changes": {"stock": 42}},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="redo-source-job",
        )
        job_id = created.json()["id"]
        self.client.post(
            reverse("api_v1:editor_job_rollback", kwargs={"job_id": job_id}),
            {},
            content_type="application/json",
        )
        redone = self.client.post(
            reverse("api_v1:editor_job_redo", kwargs={"job_id": job_id}),
            {},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="redo-source-job-2",
        )
        self.assertEqual(redone.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 42)

    def test_create_and_bulk_trash_are_undoable(self):
        created = self.client.post(
            reverse("api_v1:editor_product_create"),
            {"internalCode": "EDITOR-NEW", "name": "Producto nuevo"},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        product_id = created.json()["id"]
        trashed = self.client.post(
            reverse("api_v1:editor_bulk"),
            {"productIds": [product_id], "changes": {"isDeleted": True}},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="bulk-trash-test",
        )
        self.assertEqual(trashed.status_code, 200)
        product = Product.objects.get(pk=product_id)
        self.assertIn("editor_deleted_at", product.attributes)
        rollback = self.client.post(
            reverse("api_v1:editor_job_rollback", kwargs={"job_id": trashed.json()["id"]}),
            {},
            content_type="application/json",
        )
        self.assertEqual(rollback.status_code, 200)
        product.refresh_from_db()
        self.assertNotIn("editor_deleted_at", product.attributes)

    def test_supplier_list_preview_matches_skus(self):
        csv_file = SimpleUploadedFile(
            "proveedor.csv",
            b"SKU;Costo;Proveedor\nEDITOR-001;123.45;Proveedor Lista\nNO-EXISTE;50;Otro\n",
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("api_v1:editor_import_preview"),
            {"file": csv_file},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matchedRows"], 1)
        self.assertEqual(response.json()["unmatchedRows"], 1)
        self.assertEqual(response.json()["items"][0]["changes"]["supplier"], "Proveedor Lista")
