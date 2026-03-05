from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import ClientCategory, ClientPayment, ClientProfile, ClientTransaction
from accounts.services.client_importer import ClientImporter
from orders.models import Order


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


class ClientLedgerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ledger_client", password="secret123")
        self.profile = ClientProfile.objects.create(
            user=self.user,
            company_name="Cliente Ledger",
        )

    def test_current_balance_prefers_ledger_when_available(self):
        ClientTransaction.objects.create(
            client_profile=self.profile,
            transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
            amount="100.00",
            description="Cargo pedido #1",
            source_key="test:order:1",
        )
        ClientTransaction.objects.create(
            client_profile=self.profile,
            transaction_type=ClientTransaction.TYPE_PAYMENT,
            amount="-35.00",
            description="Pago #1",
            source_key="test:payment:1",
        )

        self.assertEqual(self.profile.get_current_balance(), 65)

    def test_client_payment_save_creates_or_updates_ledger_transaction(self):
        order = Order.objects.create(
            user=self.user,
            status=Order.STATUS_CONFIRMED,
            subtotal="100.00",
            total="100.00",
            client_company="Cliente Ledger",
        )
        payment = ClientPayment.objects.create(
            client_profile=self.profile,
            order=order,
            amount="40.00",
            method=ClientPayment.METHOD_TRANSFER,
        )

        tx = ClientTransaction.objects.get(source_key=f"payment:{payment.pk}:applied")
        self.assertEqual(tx.amount, -40)

        payment.is_cancelled = True
        payment.save(update_fields=["is_cancelled", "updated_at"])
        tx.refresh_from_db()
        self.assertEqual(tx.amount, 0)

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


class ClientImporterCategoryTests(TestCase):
    def setUp(self):
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
