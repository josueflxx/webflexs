import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientPayment, ClientProfile
from catalog.models import Category, ClampMeasureRequest, Product
from catalog.services.clamp_quoter import calculate_clamp_quote
from orders.models import ClampQuotation, Order, OrderItem


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

    def test_staff_can_modify_quote_criteria_and_recalculate_price(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_clamp_request_detail', args=[self.clamp_request.pk]),
            data={
                'status': ClampMeasureRequest.STATUS_COMPLETED,
                'dollar_rate': '1200.00',
                'steel_price_usd': '1550.00',
                'supplier_discount_pct': '5.00',
                'general_increase_pct': '35.00',
                'selected_price_list': 'lista_3',
                'confirmed_price_list': 'lista_3',
                'client_response_note': 'Precio actualizado con nuevos criterios.',
                'admin_note': 'Ajuste de criterios economicos.',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.clamp_request.refresh_from_db()
        self.assertEqual(self.clamp_request.status, ClampMeasureRequest.STATUS_COMPLETED)
        self.assertEqual(self.clamp_request.dollar_rate, Decimal('1200.00'))
        self.assertEqual(self.clamp_request.steel_price_usd, Decimal('1550.00'))
        self.assertEqual(self.clamp_request.supplier_discount_pct, Decimal('5.00'))
        self.assertEqual(self.clamp_request.general_increase_pct, Decimal('35.00'))
        self.assertEqual(self.clamp_request.selected_price_list, 'lista_3')
        self.assertEqual(self.clamp_request.confirmed_price_list, 'lista_3')

        expected = calculate_clamp_quote({
            'client_name': self.clamp_request.client_name,
            'dollar_rate': '1200.00',
            'steel_price_usd': '1550.00',
            'supplier_discount_pct': '5.00',
            'general_increase_pct': '35.00',
            'clamp_type': self.clamp_request.clamp_type,
            'is_zincated': self.clamp_request.is_zincated,
            'diameter': self.clamp_request.diameter,
            'width_mm': str(self.clamp_request.width_mm),
            'length_mm': str(self.clamp_request.length_mm),
            'profile_type': self.clamp_request.profile_type,
        })
        price_map = {row['key']: row['final_price'] for row in expected['price_rows']}
        self.assertEqual(self.clamp_request.estimated_final_price, price_map['lista_3'])
        self.assertEqual(self.clamp_request.confirmed_price, price_map['lista_3'])

    def test_staff_can_modify_technical_data_and_refresh_all_outputs(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_clamp_request_detail', args=[self.clamp_request.pk]),
            data={
                'status': ClampMeasureRequest.STATUS_PENDING,
                'clamp_type': 'laminada',
                'is_zincated': '1',
                'diameter': '3/4',
                'width_mm': '95',
                'length_mm': '260',
                'profile_type': 'CURVA',
                'quantity': '5',
                'dollar_rate': '1300.00',
                'steel_price_usd': '1450.00',
                'supplier_discount_pct': '0.00',
                'general_increase_pct': '40.00',
                'selected_price_list': 'lista_2',
                'confirmed_price_list': '',
                'confirmed_price': '',
                'admin_note': 'Ajuste tecnico por nueva medida.',
                'client_response_note': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.clamp_request.refresh_from_db()
        self.assertEqual(self.clamp_request.clamp_type, 'laminada')
        self.assertTrue(self.clamp_request.is_zincated)
        self.assertEqual(self.clamp_request.diameter, '3/4')
        self.assertEqual(self.clamp_request.width_mm, 95)
        self.assertEqual(self.clamp_request.length_mm, 260)
        self.assertEqual(self.clamp_request.profile_type, 'CURVA')
        self.assertEqual(self.clamp_request.quantity, 5)
        self.assertIn('LAMINADA', self.clamp_request.description)
        self.assertIn('95 X 260 CURVA', self.clamp_request.description)
        self.assertTrue(self.clamp_request.generated_code.startswith('ABL'))

        expected = calculate_clamp_quote({
            'client_name': self.clamp_request.client_name,
            'dollar_rate': '1300.00',
            'steel_price_usd': '1450.00',
            'supplier_discount_pct': '0.00',
            'general_increase_pct': '40.00',
            'clamp_type': 'laminada',
            'is_zincated': '1',
            'diameter': '3/4',
            'width_mm': '95',
            'length_mm': '260',
            'profile_type': 'CURVA',
        })
        price_map = {row['key']: row['final_price'] for row in expected['price_rows']}
        self.assertEqual(self.clamp_request.base_cost, expected['base_cost'])
        self.assertEqual(self.clamp_request.estimated_final_price, price_map['lista_2'])


class OrderClampPublishTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff_publish_clamp',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_publish_clamp',
            password='secret123',
        )
        self.order = Order.objects.create(
            user=self.client_user,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('1500.00'),
            total=Decimal('1500.00'),
            client_company='Cliente Publish Clamp',
        )
        self.product = Product.objects.create(
            sku='ABT3481220S-R1',
            name='ABRAZADERA TREFILADA DE 3/4 X 81 X 220 SEMICURVA',
            price=Decimal('1500.00'),
            stock=2,
            is_active=False,
            attributes={
                'source': 'clamp_request',
                'clamp_request_id': 0,
            },
        )
        self.clamp_request = ClampMeasureRequest.objects.create(
            client_user=self.client_user,
            client_name='Cliente Publish Clamp',
            clamp_type='trefilada',
            is_zincated=False,
            diameter='3/4',
            width_mm=81,
            length_mm=220,
            profile_type='SEMICURVA',
            quantity=2,
            description='ABRAZADERA TREFILADA DE 3/4 X 81 X 220 SEMICURVA',
            generated_code='ABT3481220S',
            linked_product=self.product,
            dollar_rate=Decimal('1300.00'),
            steel_price_usd=Decimal('1450.00'),
            supplier_discount_pct=Decimal('0.00'),
            general_increase_pct=Decimal('40.00'),
            base_cost=Decimal('1000.00'),
            selected_price_list='lista_1',
            estimated_final_price=Decimal('1400.00'),
            confirmed_price_list='lista_2',
            confirmed_price=Decimal('1500.00'),
            status=ClampMeasureRequest.STATUS_COMPLETED,
            exists_in_catalog=False,
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            clamp_request=self.clamp_request,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=1,
            price_at_purchase=Decimal('1500.00'),
            subtotal=Decimal('1500.00'),
        )

    def test_staff_can_publish_clamp_order_item_to_catalog(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_order_item_publish_clamp', args=[self.order.pk, self.order_item.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.clamp_request.refresh_from_db()
        self.order_item.refresh_from_db()

        self.assertTrue(self.product.is_active)
        self.assertIsNotNone(self.clamp_request.published_to_catalog_at)
        self.assertTrue(
            self.product.categories.filter(name__icontains='ABRAZADERA').exists()
        )
        self.assertEqual(self.order_item.product_id, self.product.pk)
        self.assertTrue(
            Category.objects.filter(name__icontains='ABRAZADERA').exists()
        )
        expected = calculate_clamp_quote({
            'client_name': self.clamp_request.client_name,
            'dollar_rate': str(self.clamp_request.dollar_rate),
            'steel_price_usd': str(self.clamp_request.steel_price_usd),
            'supplier_discount_pct': str(self.clamp_request.supplier_discount_pct),
            'general_increase_pct': str(self.clamp_request.general_increase_pct),
            'clamp_type': self.clamp_request.clamp_type,
            'is_zincated': self.clamp_request.is_zincated,
            'diameter': self.clamp_request.diameter,
            'width_mm': str(self.clamp_request.width_mm),
            'length_mm': str(self.clamp_request.length_mm),
            'profile_type': self.clamp_request.profile_type,
        })
        fact_map = {row['key']: row['final_price'] for row in expected['price_rows']}
        self.assertEqual(self.product.price, fact_map['facturacion'])

    def test_publish_uses_confirmed_facturacion_price_when_present(self):
        self.clamp_request.confirmed_price_list = 'facturacion'
        self.clamp_request.confirmed_price = Decimal('2278.54')
        self.clamp_request.save(update_fields=['confirmed_price_list', 'confirmed_price'])

        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin_order_item_publish_clamp', args=[self.order.pk, self.order_item.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('2278.54'))
