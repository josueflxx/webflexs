import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product
from core.models import (
    AdminCapabilityProfile,
    AdminCompanyAccess,
    Company,
    WebhookDelivery,
    WebhookEndpoint,
)
from core.services.authorization import (
    CAP_CHANGE_PRICES,
    CAP_GLOBAL_SEARCH,
    CAP_MANAGE_USERS,
    CAP_VIEW_DASHBOARD,
    get_user_capabilities,
)
from core.services.client_ip import get_client_ip
from core.services.company_context import get_user_companies
from core.services.webhooks import deliver_webhook, enqueue_webhook_event


class TrustedClientIPTests(TestCase):
    @override_settings(TRUSTED_PROXY_IPS=("10.0.0.0/8",))
    def test_ignores_forwarded_header_from_untrusted_peer(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="203.0.113.20",
            HTTP_X_FORWARDED_FOR="1.2.3.4",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.20")

    @override_settings(TRUSTED_PROXY_IPS=("10.0.0.0/8",))
    def test_walks_trusted_proxy_chain_from_the_right(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="10.0.0.2",
            HTTP_X_FORWARDED_FOR="198.51.100.9, 10.0.0.1",
        )
        self.assertEqual(get_client_ip(request), "198.51.100.9")


class StrictCompanyAndCapabilityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa seguridad features", slug="features-security")
        self.user = User.objects.create_user("operator_features", is_staff=True)

    def test_staff_company_access_is_fail_closed(self):
        self.assertFalse(get_user_companies(self.user).exists())
        AdminCompanyAccess.objects.create(user=self.user, company=self.company)
        self.assertEqual(list(get_user_companies(self.user)), [self.company])

    def test_explicit_capabilities_override_role_defaults(self):
        admin_group, _created = Group.objects.get_or_create(name="admin")
        self.user.groups.add(admin_group)
        self.assertIn(CAP_VIEW_DASHBOARD, get_user_capabilities(self.user))
        AdminCapabilityProfile.objects.create(
            user=self.user,
            capabilities=[CAP_GLOBAL_SEARCH],
            is_configured=True,
        )
        self.assertEqual(get_user_capabilities(self.user), {CAP_GLOBAL_SEARCH})


class WebhookFeatureTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa webhook", slug="empresa-webhook")
        self.endpoint = WebhookEndpoint.objects.create(
            company=self.company,
            name="ERP receptor",
            target_url="https://example.com/flexs-hook",
            events=[WebhookEndpoint.EVENT_ORDER_CREATED],
        )

    @patch("core.tasks.deliver_webhook_task.delay")
    def test_event_is_persisted_before_dispatch(self, mocked_delay):
        with self.captureOnCommitCallbacks(execute=True):
            enqueue_webhook_event(
                company=self.company,
                event_type=WebhookEndpoint.EVENT_ORDER_CREATED,
                data={"order_id": 123},
            )
        delivery = WebhookDelivery.objects.get(endpoint=self.endpoint)
        self.assertEqual(delivery.payload["data"]["order_id"], 123)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_PENDING)
        mocked_delay.assert_called_once_with(delivery.pk)

    def test_webhook_api_is_company_scoped_and_hides_existing_secret(self):
        user = User.objects.create_user("webhook_admin", password="secret123", is_staff=True)
        group, _created = Group.objects.get_or_create(name="admin")
        user.groups.add(group)
        AdminCompanyAccess.objects.create(user=user, company=self.company)
        self.client.force_login(user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

        response = self.client.get(reverse("api_v1:webhooks"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()["results"]
        self.assertEqual(payload[0]["id"], self.endpoint.pk)
        self.assertNotIn("secret", payload[0])

    @override_settings(WEBHOOK_ALLOW_PRIVATE_TARGETS=False)
    @patch("core.services.webhooks._webhook_opener.open")
    @patch("core.services.webhooks.socket.getaddrinfo")
    def test_private_network_target_is_rejected_before_http_request(self, mocked_dns, mocked_open):
        mocked_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        delivery = WebhookDelivery.objects.create(
            endpoint=self.endpoint,
            event_type=WebhookEndpoint.EVENT_ORDER_CREATED,
            payload={"id": "evt-private", "data": {}},
        )

        result = deliver_webhook(delivery.pk)

        delivery.refresh_from_db()
        self.assertEqual(result["status"], WebhookDelivery.STATUS_PENDING)
        self.assertEqual(delivery.attempts_count, 1)
        self.assertIn("privada", delivery.last_error)
        mocked_open.assert_not_called()


class BackupFeatureTests(TestCase):
    def test_database_only_backup_has_manifest_and_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(BACKUP_ROOT=Path(temp_dir), BACKUP_INCLUDE_MEDIA=False):
                call_command("backup_system", database_only=True, verbosity=0)
                manifests = list(Path(temp_dir).glob("*_manifest.json"))
                self.assertEqual(len(manifests), 1)
                payload = json.loads(manifests[0].read_text(encoding="utf-8"))
                self.assertFalse(payload["include_media"])
                self.assertEqual(len(payload["artifacts"][0]["sha256"]), 64)


class GlobalSearchFeatureTests(TestCase):
    def test_search_page_uses_active_company_context(self):
        company = Company.objects.create(name="Empresa search", slug="empresa-search")
        user = User.objects.create_user("search_admin", password="secret123", is_staff=True)
        group, _created = Group.objects.get_or_create(name="admin")
        user.groups.add(group)
        AdminCompanyAccess.objects.create(user=user, company=company)
        Product.objects.create(sku="GLOBAL-123", name="Resultado global", price=10, stock=2)
        self.client.force_login(user)
        session = self.client.session
        session["active_company_id"] = company.pk
        session.save()

        response = self.client.get(reverse("admin_global_search"), {"q": "GLOBAL-123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resultado global")


class GranularPermissionFeatureTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa permisos", slug="empresa-permisos")
        self.user = User.objects.create_user("permission_operator", is_staff=True)
        AdminCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def test_price_capability_allows_only_price_fields_in_grid(self):
        AdminCapabilityProfile.objects.create(
            user=self.user,
            capabilities=[CAP_CHANGE_PRICES],
            is_configured=True,
        )
        product = Product.objects.create(sku="PRICE-CAP-1", name="Precio granular", price=10, stock=1)

        allowed = self.client.post(
            reverse("admin_product_grid_update_cell"),
            data=json.dumps({"product_id": product.pk, "field": "price", "value": "25.50"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        denied = self.client.post(
            reverse("admin_product_grid_update_cell"),
            data=json.dumps({"product_id": product.pk, "field": "name", "value": "Otro nombre"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )

        product.refresh_from_db()
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(str(product.price), "25.50")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(product.name, "Precio granular")

    def test_user_management_capability_opens_managed_user_editor(self):
        AdminCapabilityProfile.objects.create(
            user=self.user,
            capabilities=[CAP_MANAGE_USERS],
            is_configured=True,
        )
        target = User.objects.create_user("managed_operator", is_staff=True)
        group, _created = Group.objects.get_or_create(name="ventas")
        target.groups.add(group)
        AdminCompanyAccess.objects.create(user=target, company=self.company)

        response = self.client.get(reverse("admin_user_edit", args=[target.pk]))

        self.assertEqual(response.status_code, 200)
