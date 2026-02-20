from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import ClientPayment, ClientProfile
from orders.models import Order


class OrderPaymentWorkflowTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_payment_workflow',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_pago_workflow',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Pago Workflow',
        )

    def test_can_confirm_unpaid_order(self):
        order = Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Pago Workflow',
        )

        changed = order.change_status(
            Order.STATUS_CONFIRMED,
            changed_by=self.staff_user,
            note='Confirmar sin pago',
        )

        self.assertTrue(changed)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CONFIRMED)
        self.assertEqual(order.get_pending_amount(), Decimal('100.00'))

    def test_pending_amount_decreases_with_payments(self):
        order = Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Pago Workflow',
        )
        ClientPayment.objects.create(
            client_profile=self.client_profile,
            order=order,
            amount=Decimal('100.00'),
            method=ClientPayment.METHOD_TRANSFER,
            created_by=self.staff_user,
        )

        self.assertEqual(order.get_paid_amount(), Decimal('100.00'))
        self.assertEqual(order.get_pending_amount(), Decimal('0.00'))
        self.assertTrue(order.is_paid())
