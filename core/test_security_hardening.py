from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import ClientCompany, ClientProfile
from core.models import AdminCompanyAccess, Company
from core.services.access_scope import (
    client_links_fully_manageable_by,
    clients_visible_to,
    orders_visible_to,
)
from core.services.sensitive_data import (
    OMITTED,
    REDACTED,
    sanitize_sensitive_payload,
    sanitize_sensitive_text,
)
from orders.models import Order


class CompanyAccessScopeTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name="Scope Company A")
        self.company_b = Company.objects.create(name="Scope Company B")
        self.staff = User.objects.create_user(
            username="scope_staff",
            password="test-password",
            is_staff=True,
        )
        AdminCompanyAccess.objects.create(user=self.staff, company=self.company_a)

        self.client_user_a = User.objects.create_user(username="scope_client_a")
        self.client_a = ClientProfile.objects.create(
            user=self.client_user_a,
            company_name="Scope Client A",
        )
        self.client_link_a = ClientCompany.objects.create(
            client_profile=self.client_a,
            company=self.company_a,
        )

        self.client_user_b = User.objects.create_user(username="scope_client_b")
        self.client_b = ClientProfile.objects.create(
            user=self.client_user_b,
            company_name="Scope Client B",
        )
        self.client_link_b = ClientCompany.objects.create(
            client_profile=self.client_b,
            company=self.company_b,
        )

        self.order_a = Order.objects.create(
            user=self.client_user_a,
            company=self.company_a,
            client_company_ref=self.client_link_a,
        )
        self.order_b = Order.objects.create(
            user=self.client_user_b,
            company=self.company_b,
            client_company_ref=self.client_link_b,
        )

    def test_scopes_return_only_authorized_company_records(self):
        self.assertQuerySetEqual(
            orders_visible_to(self.staff).order_by("pk"),
            [self.order_a],
        )
        self.assertQuerySetEqual(
            clients_visible_to(self.staff).order_by("pk"),
            [self.client_a],
        )
        self.assertFalse(orders_visible_to(self.staff, company=self.company_b).exists())

    def test_shared_global_client_requires_full_company_scope_to_edit(self):
        self.assertTrue(client_links_fully_manageable_by(self.staff, self.client_a))
        ClientCompany.objects.create(
            client_profile=self.client_a,
            company=self.company_b,
        )
        self.assertFalse(client_links_fully_manageable_by(self.staff, self.client_a))


class SensitiveDataSanitizationTests(TestCase):
    def test_nested_payload_removes_wsaa_secrets_and_raw_xml(self):
        payload = {
            "auth": {
                "token": "super-secret-token",
                "sign_preview": "secret-sign-prefix",
                "cuit": "30123456789",
            },
            "raw": "<Token>super-secret-token</Token>",
            "detail": "safe business detail",
        }

        sanitized = sanitize_sensitive_payload(payload)

        self.assertEqual(sanitized["auth"]["token"], REDACTED)
        self.assertEqual(sanitized["auth"]["sign_preview"], REDACTED)
        self.assertEqual(sanitized["raw"], OMITTED)
        self.assertEqual(sanitized["auth"]["cuit"], "30123456789")
        self.assertEqual(sanitized["detail"], "safe business detail")

    def test_text_redacts_xml_auth_headers_and_private_keys(self):
        value = (
            "<Token>token-value</Token><Sign>sign-value</Sign> "
            "Bearer header-value "
            "token=query-value "
            "-----BEGIN PRIVATE KEY-----private-value-----END PRIVATE KEY-----"
        )

        sanitized = sanitize_sensitive_text(value)

        for secret in (
            "token-value",
            "sign-value",
            "header-value",
            "query-value",
            "private-value",
        ):
            self.assertNotIn(secret, sanitized)
        self.assertIn(REDACTED, sanitized)
