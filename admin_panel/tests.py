from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientPayment, ClientProfile
from orders.models import Order


class ClientOrderHistoryViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_history',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_historial',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Historial',
        )
        Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Historial',
        )
        Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('200.00'),
            total=Decimal('190.00'),
            discount_amount=Decimal('10.00'),
            discount_percentage=Decimal('5.00'),
            client_company='Cliente Historial',
        )

    def test_staff_can_open_client_order_history(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_client_order_history', args=[self.client_profile.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['client'].pk, self.client_profile.pk)
        self.assertEqual(response.context['summary']['orders_count'], 2)

    def test_status_filter_applies_in_client_order_history(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('admin_client_order_history', args=[self.client_profile.pk]),
            {'status': Order.STATUS_CONFIRMED},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['orders_count'], 1)


class PaymentPanelTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_payments_panel',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_payments_panel',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Panel Pagos',
        )
        self.order = Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('120.00'),
            total=Decimal('120.00'),
            client_company='Cliente Panel Pagos',
        )
        self.order_confirmed = Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('80.00'),
            total=Decimal('80.00'),
            client_company='Cliente Panel Pagos',
        )

    def test_staff_can_register_payment_from_panel(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_payment_list'),
            data={
                'action': 'create',
                'client_profile_id': self.client_profile.pk,
                'order_id': self.order.pk,
                'amount': '120.00',
                'method': ClientPayment.METHOD_TRANSFER,
                'paid_at': '2026-02-20T10:30',
                'reference': 'TRX-001',
                'notes': 'Pago completo',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        payment = ClientPayment.objects.filter(order=self.order, is_cancelled=False).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal('120.00'))

    def test_client_balance_uses_confirmed_orders_minus_payments(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_payment_list'),
            data={
                'action': 'create',
                'client_profile_id': self.client_profile.pk,
                'order_id': self.order_confirmed.pk,
                'amount': '30.00',
                'method': ClientPayment.METHOD_CASH,
                'paid_at': '2026-02-20T11:00',
                'reference': 'EFE-001',
                'notes': '',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.client_profile.refresh_from_db()
        self.assertEqual(self.client_profile.get_total_orders_for_balance(), Decimal('80.00'))
        self.assertEqual(self.client_profile.get_total_paid(), Decimal('30.00'))
        self.assertEqual(self.client_profile.get_current_balance(), Decimal('50.00'))
