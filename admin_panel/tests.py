import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientPayment, ClientProfile
from catalog.models import ClampMeasureRequest
from orders.models import ClampQuotation, Order
from admin_panel.views import calculate_clamp_quote


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


class ClampQuoterTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_clamp_quoter',
            password='secret123',
            is_staff=True,
        )

    def test_clamp_calculation_matches_expected_formula(self):
        result = calculate_clamp_quote({
            'client_name': 'Cliente Demo',
            'dollar_rate': '1000',
            'steel_price_usd': '1',
            'supplier_discount_pct': '0',
            'general_increase_pct': '23',
            'clamp_type': 'trefilada',
            'is_zincated': '0',
            'diameter': '1/2',
            'width_mm': '100',
            'length_mm': '200',
            'profile_type': 'SEMICURVA',
        })

        self.assertEqual(result['base_cost'], Decimal('622.91'))
        row_map = {row['key']: row for row in result['price_rows']}
        self.assertEqual(row_map['lista_1']['final_price'], Decimal('872.07'))
        self.assertEqual(row_map['facturacion']['final_price'], Decimal('1245.82'))

    def test_laminada_allows_only_configured_diameters(self):
        with self.assertRaises(ValueError):
            calculate_clamp_quote({
                'client_name': 'Cliente Demo',
                'dollar_rate': '1000',
                'steel_price_usd': '1',
                'supplier_discount_pct': '0',
                'general_increase_pct': '23',
                'clamp_type': 'laminada',
                'is_zincated': '0',
                'diameter': '1/2',
                'width_mm': '100',
                'length_mm': '200',
                'profile_type': 'PLANA',
            })

    def test_staff_can_save_clamp_quote(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_clamp_quoter'),
            data={
                'action': 'save_quote',
                'price_list_key': 'lista_2',
                'client_name': 'Cliente Guardado',
                'dollar_rate': '1000',
                'steel_price_usd': '1.2',
                'supplier_discount_pct': '5',
                'general_increase_pct': '23',
                'clamp_type': 'laminada',
                'is_zincated': '1',
                'diameter': '3/4',
                'width_mm': '80',
                'length_mm': '150',
                'profile_type': 'PLANA',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        saved = ClampQuotation.objects.filter(client_name='Cliente Guardado').first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.price_list, ClampQuotation.PRICE_LIST_2)
        self.assertGreater(saved.final_price, Decimal('0.00'))

    def test_clamp_code_parse_api(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('api_clamp_code_parse'),
            data=json.dumps({'code': 'ABT91685270P'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['result']['diametro'], '9/16')
        self.assertEqual(payload['result']['ancho'], 85)
        self.assertEqual(payload['result']['largo'], 270)

    def test_clamp_code_generate_api(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('api_clamp_code_generate'),
            data=json.dumps({
                'tipo': 'ABT',
                'diametro': '3/4',
                'ancho': 80,
                'largo': 220,
                'forma': 'SEMICURVA',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['result']['codigo'], 'ABT3480220S')


class ClampMeasureRequestAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_clamp_requests',
            password='secret123',
            is_staff=True,
        )
        self.clamp_request = ClampMeasureRequest.objects.create(
            client_name='Cliente Clamp',
            client_email='cliente@clamp.com',
            clamp_type='trefilada',
            is_zincated=False,
            diameter='3/4',
            width_mm=80,
            length_mm=220,
            profile_type='SEMICURVA',
            quantity=2,
            description='ABRAZADERA TREFILADA DE 3/4 X 80 X 220 SEMICURVA',
            generated_code='ABT3480220S',
            dollar_rate=Decimal('1000.00'),
            steel_price_usd=Decimal('1450.00'),
            supplier_discount_pct=Decimal('0.00'),
            general_increase_pct=Decimal('40.00'),
            base_cost=Decimal('1000.00'),
            selected_price_list='lista_1',
            estimated_final_price=Decimal('1400.00'),
            exists_in_catalog=False,
        )

    def test_staff_can_open_clamp_request_queue(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_clamp_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Solicitud')

    def test_staff_can_update_clamp_request_status(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_clamp_request_detail', args=[self.clamp_request.pk]),
            data={
                'status': ClampMeasureRequest.STATUS_QUOTED,
                'admin_note': 'Cotizacion enviada por WhatsApp.',
                'confirmed_price_list': 'lista_2',
                'client_response_note': 'Precio confirmado para entrega inmediata.',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.clamp_request.refresh_from_db()
        self.assertEqual(self.clamp_request.status, ClampMeasureRequest.STATUS_QUOTED)
        self.assertEqual(self.clamp_request.processed_by, self.staff)
        self.assertEqual(self.clamp_request.confirmed_price_list, 'lista_2')
        self.assertIsNotNone(self.clamp_request.confirmed_price)
        self.assertEqual(self.clamp_request.client_response_note, 'Precio confirmado para entrega inmediata.')
