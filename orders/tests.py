from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientCompany, ClientPayment, ClientProfile
from catalog.models import ClampMeasureRequest, Product
from orders.models import Cart, CartItem, Order, OrderItem
from orders.services.workflow import can_user_transition_order, get_order_queue_queryset_for_user
from core.services.company_context import get_default_company


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
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
            discount_percentage=Decimal('5.00'),
        )

    def test_can_confirm_unpaid_order(self):
        order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Pago Workflow',
            client_company_ref=self.client_company,
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
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Pago Workflow',
            client_company_ref=self.client_company,
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


class CheckoutClampRequestFlowTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            username='cliente_checkout_clamp',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Checkout Clamp',
            discount=Decimal('0.00'),
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
        )
        self.product = Product.objects.create(
            sku='TEST-CLAMP-CHK-01',
            name='Producto prueba abrazadera',
            price=Decimal('150.00'),
            cost=Decimal('90.00'),
            stock=3,
            is_active=True,
        )
        self.clamp_request = ClampMeasureRequest.objects.create(
            client_user=self.client_user,
            client_name='Cliente Checkout Clamp',
            client_email='checkoutclamp@example.com',
            clamp_type='TREFILADA',
            is_zincated=False,
            diameter='7/16',
            width_mm=60,
            length_mm=120,
            profile_type='PLANA',
            quantity=1,
            description='ABRAZADERA TREFILADA DE 7/16 X 60 X 120 PLANA',
            generated_code='ABT71660120P',
            dollar_rate=Decimal('1450'),
            steel_price_usd=Decimal('1.45'),
            supplier_discount_pct=Decimal('0'),
            general_increase_pct=Decimal('40'),
            base_cost=Decimal('100.00'),
            selected_price_list='lista_1',
            estimated_final_price=Decimal('140.00'),
            status=ClampMeasureRequest.STATUS_COMPLETED,
            confirmed_price=Decimal('140.00'),
        )
        self.cart = Cart.objects.create(user=self.client_user, company=self.company)
        CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            clamp_request=self.clamp_request,
            quantity=1,
        )

    def test_checkout_with_clamp_request_sets_ordered_at(self):
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse('checkout'),
            data={'notes': 'Pedido con abrazadera a medida'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        order = Order.objects.filter(user=self.client_user).order_by('-id').first()
        self.assertIsNotNone(order)
        self.clamp_request.refresh_from_db()
        self.assertIsNotNone(self.clamp_request.ordered_at)


class OrderItemMutationGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='order_item_guard_user',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.user,
            company_name='Guard Co',
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
        )
        self.product = Product.objects.create(
            sku='GUARD-ITEM-01',
            name='Producto Guard',
            price=Decimal('100.00'),
            cost=Decimal('50.00'),
            stock=3,
            is_active=True,
        )

    def test_edit_item_blocked_when_order_confirmed(self):
        order = Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Guard Co',
            client_company_ref=self.client_company,
        )
        item = OrderItem.objects.create(
            order=order,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=1,
            price_at_purchase=Decimal('100.00'),
            subtotal=Decimal('100.00'),
        )
        item.quantity = 2
        with self.assertRaises(ValidationError):
            item.save()

    def test_edit_item_allowed_when_order_draft(self):
        order = Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Guard Co',
            client_company_ref=self.client_company,
        )
        item = OrderItem.objects.create(
            order=order,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=1,
            price_at_purchase=Decimal('100.00'),
            subtotal=Decimal('100.00'),
        )
        item.quantity = 2
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)


class OrderWorkflowRolesTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_multi_role_workflow',
            password='secret123',
            is_staff=True,
        )
        ventas_group, _ = Group.objects.get_or_create(name='ventas')
        deposito_group, _ = Group.objects.get_or_create(name='deposito')
        self.staff_user.groups.add(ventas_group, deposito_group)
        self.client_user = User.objects.create_user(
            username='cliente_multi_role_workflow',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Multi Rol Workflow',
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
        )

    def test_combined_sales_and_deposit_roles_expand_allowed_transitions(self):
        draft_order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Multi Rol Workflow',
            client_company_ref=self.client_company,
        )
        confirmed_order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Multi Rol Workflow',
            client_company_ref=self.client_company,
        )
        preparing_order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_PREPARING,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Multi Rol Workflow',
            client_company_ref=self.client_company,
        )

        allowed_confirm, _ = can_user_transition_order(
            self.staff_user,
            draft_order,
            Order.STATUS_CONFIRMED,
        )
        allowed_prepare, _ = can_user_transition_order(
            self.staff_user,
            confirmed_order,
            Order.STATUS_PREPARING,
        )
        allowed_ship, _ = can_user_transition_order(
            self.staff_user,
            preparing_order,
            Order.STATUS_SHIPPED,
        )

        self.assertTrue(allowed_confirm)
        self.assertTrue(allowed_prepare)
        self.assertTrue(allowed_ship)

    def test_combined_roles_expand_order_queue_statuses(self):
        draft_order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_DRAFT,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Multi Rol Workflow',
            client_company_ref=self.client_company,
        )
        confirmed_order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Multi Rol Workflow',
            client_company_ref=self.client_company,
        )
        preparing_order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            status=Order.STATUS_PREPARING,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            client_company='Cliente Multi Rol Workflow',
            client_company_ref=self.client_company,
        )

        queryset, primary_role = get_order_queue_queryset_for_user(Order.objects.all(), self.staff_user)

        self.assertEqual(primary_role, 'deposito')
        self.assertEqual(
            set(queryset.values_list('id', flat=True)),
            {draft_order.id, confirmed_order.id, preparing_order.id},
        )
