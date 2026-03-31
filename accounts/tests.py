import re

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AccountRequest, ClientCategory, ClientCompany, ClientPayment, ClientProfile, ClientTransaction
from accounts.services.client_importer import ClientImporter
from core.models import Company, FiscalDocument, FiscalPointOfSale, SalesDocumentType
from orders.models import Order
from core.services.company_context import get_default_company, get_default_client_origin_company


class LoginSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "secret123"
        self.user = User.objects.create_user(username="cliente_seguridad", password=self.password)

    @override_settings(
        LOGIN_MAX_FAILED_ATTEMPTS=3,
        LOGIN_LOCKOUT_SECONDS=120,
        LOGIN_ATTEMPT_WINDOW_SECONDS=300,
    )
    def test_login_lockout_after_repeated_failures(self):
        login_url = reverse("login")

        for _ in range(3):
            self.client.post(login_url, {"username": self.user.username, "password": "bad-pass"}, follow=True)

        response = self.client.post(
            login_url,
            {"username": self.user.username, "password": self.password},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demasiados intentos fallidos")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(
        LOGIN_MAX_FAILED_ATTEMPTS=4,
        LOGIN_LOCKOUT_SECONDS=120,
        LOGIN_ATTEMPT_WINDOW_SECONDS=300,
    )
    def test_successful_login_still_works_before_limit(self):
        login_url = reverse("login")

        self.client.post(login_url, {"username": self.user.username, "password": "wrong-1"}, follow=True)
        self.client.post(login_url, {"username": self.user.username, "password": "wrong-2"}, follow=True)

        response = self.client.post(
            login_url,
            {"username": self.user.username, "password": self.password},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(SESSION_COOKIE_AGE=60 * 60 * 8, SESSION_IDLE_TIMEOUT_SECONDS=60 * 45)
    def test_login_without_remember_me_keeps_default_timeout(self):
        login_url = reverse("login")
        response = self.client.post(
            login_url,
            {"username": self.user.username, "password": self.password},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session.get("_idle_timeout_seconds"), 60 * 45)

    @override_settings(REMEMBER_ME_SESSION_AGE=60 * 60 * 24 * 30)
    def test_login_with_remember_me_uses_longer_timeout(self):
        login_url = reverse("login")
        response = self.client.post(
            login_url,
            {"username": self.user.username, "password": self.password, "remember_me": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session.get("_idle_timeout_seconds"), 60 * 60 * 24 * 30)


class LoginRedirectCompanySelectionTests(TestCase):
    def setUp(self):
        self.default_company = get_default_company()
        self.default_client_company = get_default_client_origin_company()
        self.second_company = Company.objects.create(
            name="Cliente Multi Empresa",
            slug="cliente-multi-empresa",
            is_active=True,
        )

    def test_client_with_multiple_companies_goes_to_catalog_without_selector(self):
        user = User.objects.create_user(username="cliente_multi_redirect", password="secret123")
        profile = ClientProfile.objects.create(user=user, company_name="Cliente Multi Redirect")
        primary_link_company = self.default_client_company or self.default_company
        secondary_link_company = (
            self.default_company
            if primary_link_company.pk != self.default_company.pk
            else self.second_company
        )
        ClientCompany.objects.create(
            client_profile=profile,
            company=primary_link_company,
            is_active=True,
        )
        ClientCompany.objects.create(
            client_profile=profile,
            company=secondary_link_company,
            is_active=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("login_redirect"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("catalog"))
        self.assertEqual(
            self.client.session.get("active_company_id"),
            primary_link_company.pk,
        )

    def test_staff_with_multiple_companies_still_goes_to_company_selector(self):
        staff = User.objects.create_user(
            username="staff_multi_redirect",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("login_redirect"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("select_company"), response.url)
        self.assertNotIn("active_company_id", self.client.session)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetFlowTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def _extract_confirm_path(self, email_body):
        match = re.search(
            r"/accounts/password-reset-confirm/[0-9A-Za-z_\-]+/[0-9A-Za-z\-]+/",
            email_body,
        )
        self.assertIsNotNone(match)
        return match.group(0)

    def test_password_reset_sends_email_and_allows_new_password(self):
        user = User.objects.create_user(
            username="cliente_recovery",
            email="cliente_recovery@example.com",
            password="oldpass123",
        )

        response = self.client.post(
            reverse("password_reset"),
            {"email": user.email},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])

        confirm_path = self._extract_confirm_path(mail.outbox[0].body)
        confirm_get = self.client.get(confirm_path, follow=True)
        self.assertEqual(confirm_get.status_code, 200)
        confirm_submit_path = confirm_get.request.get("PATH_INFO", confirm_path)

        confirm_post = self.client.post(
            confirm_submit_path,
            {
                "new_password1": "NuevaClaveSegura123!",
                "new_password2": "NuevaClaveSegura123!",
            },
            follow=True,
        )
        self.assertContains(confirm_post, "Contrasena actualizada")

        login_response = self.client.post(
            reverse("login"),
            {"username": user.username, "password": "NuevaClaveSegura123!"},
            follow=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    def test_password_reset_for_unknown_email_does_not_reveal_user_existence(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "noexiste@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_staff_user_can_use_password_reset_flow(self):
        staff = User.objects.create_user(
            username="staff_recovery",
            email="staff_recovery@example.com",
            password="oldpass123",
            is_staff=True,
        )

        response = self.client.post(
            reverse("password_reset"),
            {"email": staff.email},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [staff.email])


class ClientLedgerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ledger_client", password="secret123")
        self.profile = ClientProfile.objects.create(
            user=self.user,
            company_name="Cliente Ledger",
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.company,
            is_active=True,
        )
        self.second_company = Company.objects.create(name="Ledger Company 2")

    def test_current_balance_prefers_ledger_when_available(self):
        ClientTransaction.objects.create(
            client_profile=self.profile,
            company=self.company,
            transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
            amount="100.00",
            description="Cargo pedido #1",
            source_key="test:order:1",
            movement_state=ClientTransaction.STATE_CLOSED,
        )
        ClientTransaction.objects.create(
            client_profile=self.profile,
            company=self.company,
            transaction_type=ClientTransaction.TYPE_PAYMENT,
            amount="-35.00",
            description="Pago #1",
            source_key="test:payment:1",
            movement_state=ClientTransaction.STATE_CLOSED,
        )

        self.assertEqual(self.profile.get_current_balance(), 65)

    def test_current_balance_excludes_voided_ledger_movements(self):
        ClientTransaction.objects.create(
            client_profile=self.profile,
            company=self.company,
            transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
            amount="100.00",
            description="Cargo activo",
            source_key="test:order:active",
            movement_state=ClientTransaction.STATE_CLOSED,
        )
        ClientTransaction.objects.create(
            client_profile=self.profile,
            company=self.company,
            transaction_type=ClientTransaction.TYPE_ADJUSTMENT,
            amount="40.00",
            description="Ajuste anulado",
            source_key="test:adjustment:voided",
            movement_state=ClientTransaction.STATE_VOIDED,
        )

        self.assertEqual(self.profile.get_current_balance(), 100)

    def test_current_balance_ignores_open_ledger_movements(self):
        ClientTransaction.objects.create(
            client_profile=self.profile,
            company=self.company,
            transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
            amount="100.00",
            description="Cargo pendiente",
            source_key="test:order:open",
            movement_state=ClientTransaction.STATE_OPEN,
        )
        ClientTransaction.objects.create(
            client_profile=self.profile,
            company=self.company,
            transaction_type=ClientTransaction.TYPE_PAYMENT,
            amount="-30.00",
            description="Pago pendiente",
            source_key="test:payment:open",
            movement_state=ClientTransaction.STATE_OPEN,
        )

        self.assertEqual(self.profile.get_current_balance(), 0)

    def test_client_payment_save_creates_or_updates_ledger_transaction(self):
        order = Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal="100.00",
            total="100.00",
            client_company="Cliente Ledger",
            client_company_ref=self.client_company,
        )
        payment = ClientPayment.objects.create(
            client_profile=self.profile,
            order=order,
            company=self.company,
            amount="40.00",
            method=ClientPayment.METHOD_TRANSFER,
        )

        tx = ClientTransaction.objects.get(source_key=f"payment:{payment.pk}:applied")
        self.assertEqual(tx.amount, -40)

        payment.is_cancelled = True
        payment.save(update_fields=["is_cancelled", "updated_at"])
        tx.refresh_from_db()
        self.assertEqual(tx.amount, 0)

    def test_balance_fallback_ignores_confirmed_order_without_invoice(self):
        Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal="100.00",
            total="100.00",
            client_company="Cliente Ledger",
            client_company_ref=self.client_company,
        )

        self.assertEqual(self.profile.get_total_orders_for_balance(), 0)
        self.assertEqual(self.profile.get_current_balance(), 0)

    def test_balance_fallback_uses_billed_invoice_order(self):
        order = Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal="100.00",
            total="100.00",
            client_company="Cliente Ledger",
            client_company_ref=self.client_company,
        )
        point = FiscalPointOfSale.objects.create(
            company=self.company,
            number="81",
            is_active=True,
            is_default=True,
        )
        sales_type = SalesDocumentType.objects.filter(
            company=self.company,
            document_behavior="Factura",
        ).first()
        FiscalDocument.objects.create(
            source_key="ledger-fallback-invoice",
            company=self.company,
            client_company_ref=self.client_company,
            client_profile=self.profile,
            order=order,
            point_of_sale=point,
            sales_document_type=sales_type,
            doc_type="FA",
            issue_mode="manual",
            status="external_recorded",
            subtotal_net="100.00",
            total="100.00",
        )

        self.assertEqual(self.profile.get_total_orders_for_balance(), 100)

    def test_discount_decimal_uses_client_category_when_assigned(self):
        category = ClientCategory.objects.create(
            name="Distribuidor",
            discount_percentage="25.00",
            default_sale_condition=ClientCategory.SALE_CONDITION_ACCOUNT,
            allows_account_current=True,
            account_current_limit="1000000.00",
            price_list_name="Principal",
        )
        self.profile.client_category = category
        self.profile.discount = 5
        self.profile.save(update_fields=["client_category", "discount"])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.get_discount_decimal(), 0.25)

    def test_can_operate_in_any_active_company_link_without_extra_rules(self):
        second_link = ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.second_company,
            is_active=True,
        )

        self.assertTrue(self.profile.can_operate_in_company(self.company))
        self.assertTrue(self.profile.can_operate_in_company(self.second_company))
        self.assertTrue(second_link.is_active)


class ClientImporterCategoryTests(TestCase):
    def setUp(self):
        self.import_company_a = get_default_company()
        self.import_company_b = Company.objects.filter(slug="ubolt").first()
        if not self.import_company_b:
            self.import_company_b = Company.objects.create(name="Ubolt Import", slug="ubolt")
        self.category_n2, _ = ClientCategory.objects.get_or_create(
            name="N°2",
            defaults={
                "discount_percentage": "25.00",
                "default_sale_condition": ClientCategory.SALE_CONDITION_ACCOUNT,
                "allows_account_current": True,
                "account_current_limit": "8000000.00",
                "price_list_name": "Principal",
            },
        )

    def test_import_assigns_client_category_and_uses_category_discount(self):
        importer = ClientImporter(file=None)
        row = {
            "Usuario": "cliente_import_n2",
            "Nombre": "Cliente N2",
            "Tipo de cliente": "N°2",
            "Descuento": "5",
            "Cond. IVA": "consumidor final",
        }

        result = importer.process_row(row, dry_run=False)
        self.assertTrue(result.success)

        user = User.objects.get(username="cliente_import_n2")
        profile = user.client_profile
        self.assertEqual(profile.client_category_id, self.category_n2.pk)
        self.assertEqual(profile.discount, self.category_n2.discount_percentage)

    def test_import_rejects_unknown_client_category(self):
        importer = ClientImporter(file=None)
        row = {
            "Usuario": "cliente_import_x",
            "Nombre": "Cliente X",
            "Tipo de cliente": "N°99",
            "Descuento": "5",
        }

        result = importer.process_row(row, dry_run=True)
        self.assertFalse(result.success)
        self.assertTrue(any("no coincide" in err.lower() for err in result.errors))

    @override_settings(DEFAULT_CLIENT_IMPORT_COMPANY_SLUGS=["flexs", "ubolt"])
    def test_import_creates_links_for_both_default_import_companies(self):
        importer = ClientImporter(file=None)
        row = {
            "Usuario": "cliente_import_multi",
            "Nombre": "Cliente Import Multi",
            "Tipo de cliente": self.category_n2.name,
            "Cond. IVA": "consumidor final",
        }

        result = importer.process_row(row, dry_run=False)

        self.assertTrue(result.success)
        profile = User.objects.get(username="cliente_import_multi").client_profile
        self.assertEqual(
            ClientCompany.objects.filter(client_profile=profile, is_active=True).count(),
            2,
        )
        self.assertTrue(profile.can_operate_in_company(self.import_company_a))
        self.assertTrue(profile.can_operate_in_company(self.import_company_b))

    def test_import_defaults_to_origin_company_only(self):
        importer = ClientImporter(file=None)
        row = {
            "Usuario": "cliente_import_default_ubolt",
            "Nombre": "Cliente Import Default",
            "Tipo de cliente": self.category_n2.name,
            "Cond. IVA": "consumidor final",
        }

        result = importer.process_row(row, dry_run=False)

        self.assertTrue(result.success)
        profile = User.objects.get(username="cliente_import_default_ubolt").client_profile
        active_links = list(
            ClientCompany.objects.filter(client_profile=profile, is_active=True).values_list("company__slug", flat=True)
        )
        self.assertEqual(len(active_links), 1)
        self.assertIn("ubolt", [str(slug).lower() for slug in active_links])


class AccountRequestSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("account_request")
        self.payload = {
            "company_name": "Empresa Seguridad",
            "contact_name": "Brian",
            "email": "seguridad@example.com",
            "phone": "11111111",
            "cuit_dni": "20333444556",
        }

    @override_settings(
        ACCOUNT_REQUEST_HONEYPOT_FIELD="website",
        ACCOUNT_REQUEST_MIN_INTERVAL_SECONDS=0,
    )
    def test_honeypot_submission_is_ignored_but_returns_success_feedback(self):
        response = self.client.post(
            self.url,
            {**self.payload, "website": "https://spam.invalid"},
            follow=True,
        )

        self.assertEqual(AccountRequest.objects.count(), 0)
        self.assertContains(response, "Solicitud enviada")

    @override_settings(
        ACCOUNT_REQUEST_MAX_SUBMISSIONS=2,
        ACCOUNT_REQUEST_WINDOW_SECONDS=3600,
        ACCOUNT_REQUEST_MIN_INTERVAL_SECONDS=0,
    )
    def test_rate_limit_blocks_excessive_submissions(self):
        self.client.post(self.url, self.payload, follow=True)
        self.client.post(self.url, self.payload, follow=True)
        response = self.client.post(self.url, self.payload, follow=True)

        self.assertEqual(AccountRequest.objects.count(), 2)
        self.assertContains(response, "solicitudes", status_code=200)
