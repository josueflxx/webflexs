from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Category, Product
from orders.models import Order
from core.models import AdminCompanyAccess, Company
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
