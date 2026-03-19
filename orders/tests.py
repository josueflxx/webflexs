from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientCompany, ClientPayment, ClientProfile, ClientTransaction
from catalog.models import ClampMeasureRequest, Product
from orders.models import Cart, CartItem, Order, OrderItem, OrderProposal, OrderRequest, OrderRequestEvent
from orders.services.request_workflow import (
    accept_order_proposal,
    build_order_request_from_cart,
    convert_request_to_order,
    create_order_proposal,
)
from orders.services.workflow import can_user_transition_order, get_order_queue_queryset_for_user
from core.models import (
    DocumentSeries,
    FISCAL_DOC_TYPE_FB,
    FiscalDocument,
    FiscalPointOfSale,
    InternalDocument,
    SALES_BEHAVIOR_COTIZACION,
    SALES_BEHAVIOR_FACTURA,
    SALES_BILLING_MODE_INTERNAL_DOCUMENT,
    SALES_BILLING_MODE_MANUAL_FISCAL,
    SalesDocumentType,
)
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
        order_request = OrderRequest.objects.filter(user=self.client_user).order_by('-id').first()
        self.assertIsNotNone(order_request)
        self.assertEqual(Order.objects.filter(user=self.client_user).count(), 0)
        self.assertEqual(self.cart.items.count(), 0)
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


class OrderRequestWorkflowTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_order_request_workflow',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_order_request_workflow',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Request Workflow',
            discount=Decimal('10.00'),
            is_approved=True,
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
            discount_percentage=Decimal('10.00'),
        )
        self.product_a = Product.objects.create(
            sku='REQ-001',
            name='Producto Request A',
            price=Decimal('100.00'),
            cost=Decimal('60.00'),
            stock=10,
            is_active=True,
        )
        self.product_b = Product.objects.create(
            sku='REQ-002',
            name='Producto Request B',
            price=Decimal('50.00'),
            cost=Decimal('25.00'),
            stock=8,
            is_active=True,
        )
        self.cart = Cart.objects.create(user=self.client_user, company=self.company)
        CartItem.objects.create(cart=self.cart, product=self.product_a, quantity=2)
        CartItem.objects.create(cart=self.cart, product=self.product_b, quantity=1)

    def test_build_order_request_from_cart_preserves_snapshot_without_ledger_impact(self):
        order_request = build_order_request_from_cart(
            cart=self.cart,
            user=self.client_user,
            company=self.company,
            client_note='Necesito revision comercial',
        )

        self.assertEqual(order_request.status, OrderRequest.STATUS_SUBMITTED)
        self.assertEqual(order_request.origin_channel, Order.ORIGIN_CATALOG)
        self.assertEqual(order_request.items.count(), 2)
        self.assertEqual(order_request.requested_subtotal, Decimal('250.00'))
        self.assertEqual(order_request.requested_discount_amount, Decimal('25.00'))
        self.assertEqual(order_request.requested_total, Decimal('225.00'))
        self.assertEqual(Order.objects.count(), 0)
        self.assertFalse(
            ClientTransaction.objects.filter(
                client_profile=self.client_profile,
                transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
            ).exists()
        )

    def test_accept_proposal_and_convert_to_order_uses_proposed_snapshot(self):
        order_request = build_order_request_from_cart(
            cart=self.cart,
            user=self.client_user,
            company=self.company,
            client_note='Pedido inicial',
        )
        proposal = create_order_proposal(
            order_request=order_request,
            created_by=self.staff_user,
            message_to_client='Ajustamos cantidades segun stock disponible',
            item_payloads=[
                {
                    'product': self.product_a,
                    'clamp_request': None,
                    'product_sku': self.product_a.sku,
                    'product_name': self.product_a.name,
                    'quantity': 1,
                    'unit_price_base': Decimal('100.00'),
                    'discount_percentage_used': Decimal('10.00'),
                    'price_list': None,
                    'price_at_snapshot': Decimal('90.00'),
                },
                {
                    'product': self.product_b,
                    'clamp_request': None,
                    'product_sku': self.product_b.sku,
                    'product_name': self.product_b.name,
                    'quantity': 3,
                    'unit_price_base': Decimal('50.00'),
                    'discount_percentage_used': Decimal('10.00'),
                    'price_list': None,
                    'price_at_snapshot': Decimal('45.00'),
                },
            ],
        )

        accept_order_proposal(order_proposal=proposal, actor=self.client_user)
        order_request.refresh_from_db()
        self.assertEqual(order_request.status, OrderRequest.STATUS_CONFIRMED)

        order, created = convert_request_to_order(
            order_request=order_request,
            actor=self.staff_user,
            source_proposal=proposal,
        )

        self.assertTrue(created)
        self.assertEqual(order.origin_channel, Order.ORIGIN_CATALOG)
        self.assertEqual(order.source_request_id, order_request.id)
        self.assertEqual(order.source_proposal_id, proposal.id)
        self.assertEqual(order.status, Order.STATUS_DRAFT)
        self.assertEqual(order.subtotal, Decimal('250.00'))
        self.assertEqual(order.discount_amount, Decimal('25.00'))
        self.assertEqual(order.total, Decimal('225.00'))
        self.assertEqual(order.items.count(), 2)

        item_a = order.items.get(product_sku='REQ-001')
        item_b = order.items.get(product_sku='REQ-002')
        self.assertEqual(item_a.quantity, 1)
        self.assertEqual(item_a.price_at_purchase, Decimal('90.00'))
        self.assertEqual(item_b.quantity, 3)
        self.assertEqual(item_b.price_at_purchase, Decimal('45.00'))

        order_request.refresh_from_db()
        self.assertEqual(order_request.status, OrderRequest.STATUS_CONVERTED)
        self.assertEqual(order_request.converted_order.id, order.id)
        self.assertTrue(
            OrderProposal.objects.filter(pk=proposal.pk, status=OrderProposal.STATUS_ACCEPTED).exists()
        )


class OrderRequestPortalViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_order_request_portal',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_order_request_portal',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Request Portal',
            is_approved=True,
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
        )
        self.product = Product.objects.create(
            sku='REQ-VIEW-001',
            name='Producto Request Vista',
            price=Decimal('80.00'),
            cost=Decimal('40.00'),
            stock=6,
            is_active=True,
        )
        self.cart = Cart.objects.create(user=self.client_user, company=self.company)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        self.order_request = build_order_request_from_cart(
            cart=self.cart,
            user=self.client_user,
            company=self.company,
            client_note='Necesito seguimiento desde portal',
        )

    def test_client_can_see_request_list_and_detail(self):
        self.client.force_login(self.client_user)

        list_response = self.client.get(reverse('order_request_list'))
        detail_response = self.client.get(reverse('order_request_detail', args=[self.order_request.pk]))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, f'Solicitud #{self.order_request.pk}')
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Necesito seguimiento desde portal')

    def test_staff_can_see_admin_order_request_inbox(self):
        self.client.force_login(self.staff_user)

        list_response = self.client.get(reverse('admin_order_request_list'))
        detail_response = self.client.get(reverse('admin_order_request_detail', args=[self.order_request.pk]))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, str(self.order_request.pk))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.product.name)


class OrderRequestReviewActionsTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_order_request_actions',
            password='secret123',
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            username='cliente_order_request_actions',
            password='secret123',
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name='Cliente Request Actions',
            is_approved=True,
        )
        self.company = get_default_company()
        self.client_company = ClientCompany.objects.create(
            client_profile=self.client_profile,
            company=self.company,
            is_active=True,
            discount_percentage=Decimal('10.00'),
        )
        self.point_of_sale = FiscalPointOfSale.objects.create(
            company=self.company,
            number='9001',
            name='PV Principal Test',
            is_active=True,
            is_default=True,
        )
        self.company.point_of_sale_default = self.point_of_sale.number
        self.company.save(update_fields=['point_of_sale_default', 'updated_at'])
        self.quote_type = SalesDocumentType.objects.create(
            company=self.company,
            code='cot-solicitud-test',
            name='Cotizacion Solicitud Test',
            document_behavior=SALES_BEHAVIOR_COTIZACION,
            billing_mode=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
            internal_doc_type=DocumentSeries.DOC_COT,
            is_default=False,
        )
        self.invoice_type = SalesDocumentType.objects.create(
            company=self.company,
            code='fac-solicitud-test',
            name='Factura Solicitud Test',
            document_behavior=SALES_BEHAVIOR_FACTURA,
            billing_mode=SALES_BILLING_MODE_MANUAL_FISCAL,
            fiscal_doc_type=FISCAL_DOC_TYPE_FB,
            point_of_sale=self.point_of_sale,
            is_default=False,
        )
        self.product = Product.objects.create(
            sku='REQ-ACT-001',
            name='Producto Request Acciones',
            price=Decimal('100.00'),
            cost=Decimal('60.00'),
            stock=5,
            is_active=True,
        )
        self.alt_product = Product.objects.create(
            sku='REQ-ACT-ALT-001',
            name='Producto Alternativo Request',
            price=Decimal('135.00'),
            cost=Decimal('85.00'),
            stock=7,
            is_active=True,
        )
        self.cart = Cart.objects.create(user=self.client_user, company=self.company)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        self.order_request = build_order_request_from_cart(
            cart=self.cart,
            user=self.client_user,
            company=self.company,
            client_note='Revisar condiciones',
        )

    def test_admin_can_confirm_and_convert_request(self):
        self.client.force_login(self.staff_user)

        confirm_response = self.client.post(
            reverse('admin_order_request_confirm', args=[self.order_request.pk]),
            data={'admin_note': 'Confirmado sin ajustes'},
        )
        self.assertEqual(confirm_response.status_code, 302)
        self.order_request.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_CONFIRMED)

        convert_response = self.client.post(
            reverse('admin_order_request_convert', args=[self.order_request.pk]),
        )
        self.assertEqual(convert_response.status_code, 302)
        self.order_request.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_CONVERTED)
        self.assertIsNotNone(self.order_request.converted_order)
        self.assertEqual(self.order_request.converted_order.status, Order.STATUS_DRAFT)
        self.assertEqual(
            list(self.order_request.events.order_by('created_at', 'id').values_list('event_type', flat=True)),
            [
                OrderRequestEvent.EVENT_CREATED,
                OrderRequestEvent.EVENT_REVIEW_STARTED,
                OrderRequestEvent.EVENT_CONFIRMED,
                OrderRequestEvent.EVENT_CONVERTED,
            ],
        )

    def test_admin_can_reject_request_with_reason(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse('admin_order_request_reject', args=[self.order_request.pk]),
            data={'rejection_reason': 'Sin stock disponible por ahora'},
        )

        self.assertEqual(response.status_code, 302)
        self.order_request.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_REJECTED)
        self.assertEqual(self.order_request.rejection_reason, 'Sin stock disponible por ahora')

    def test_admin_can_delete_unconverted_request(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse('admin_order_request_delete', args=[self.order_request.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrderRequest.objects.filter(pk=self.order_request.pk).exists())

    def test_admin_can_send_proposal_and_client_accepts(self):
        self.client.force_login(self.staff_user)

        proposal_response = self.client.post(
            reverse('admin_order_request_propose', args=[self.order_request.pk]),
            data={
                'message_to_client': 'Ajustamos cantidad por disponibilidad.',
                'internal_note': 'Primer ajuste',
                'row_enabled_1': 'on',
                'quantity_1': '1',
                'unit_price_base_1': '100.00',
                'price_at_snapshot_1': '90.00',
            },
        )
        self.assertEqual(proposal_response.status_code, 302)
        self.order_request.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_WAITING_CLIENT)
        proposal = self.order_request.current_proposal
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.proposed_total, Decimal('90.00'))

        self.client.force_login(self.client_user)
        accept_response = self.client.post(
            reverse('order_request_accept_proposal', args=[self.order_request.pk, proposal.pk]),
        )

        self.assertEqual(accept_response.status_code, 302)
        self.order_request.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_CONFIRMED)
        self.assertEqual(proposal.status, OrderProposal.STATUS_ACCEPTED)

    def test_client_request_detail_shows_timeline(self):
        self.client.force_login(self.staff_user)

        self.client.post(
            reverse('admin_order_request_propose', args=[self.order_request.pk]),
            data={
                'message_to_client': 'Te enviamos una propuesta comercial.',
                'internal_note': 'Revision inicial',
                'row_enabled_1': 'on',
                'quantity_1': '2',
                'unit_price_base_1': '100.00',
                'price_at_snapshot_1': '90.00',
            },
        )

        self.client.force_login(self.client_user)
        response = self.client.get(reverse('order_request_detail', args=[self.order_request.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seguimiento comercial')
        self.assertContains(response, 'Solicitud creada')
        self.assertContains(response, 'Propuesta enviada')

    def test_client_can_reject_proposal_and_request_returns_to_review(self):
        proposal = create_order_proposal(
            order_request=self.order_request,
            created_by=self.staff_user,
            item_payloads=[
                {
                    'product': self.product,
                    'clamp_request': None,
                    'product_sku': self.product.sku,
                    'product_name': self.product.name,
                    'quantity': 1,
                    'unit_price_base': Decimal('100.00'),
                    'discount_percentage_used': Decimal('10.00'),
                    'price_list': None,
                    'price_at_snapshot': Decimal('90.00'),
                }
            ],
            message_to_client='Nueva cantidad propuesta',
        )

        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse('order_request_reject_proposal', args=[self.order_request.pk, proposal.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.order_request.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_IN_REVIEW)
        self.assertEqual(proposal.status, OrderProposal.STATUS_REJECTED)

    def test_admin_can_replace_product_in_proposal(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse('admin_order_request_propose', args=[self.order_request.pk]),
            data={
                'message_to_client': 'Ofrecemos reemplazo por falta de stock.',
                'internal_note': 'Cambiar por alternativo',
                'row_enabled_1': 'on',
                'replacement_product_id_1': str(self.alt_product.pk),
                'quantity_1': '2',
                'unit_price_base_1': '135.00',
                'price_at_snapshot_1': '121.50',
            },
        )

        self.assertEqual(response.status_code, 302)
        proposal = self.order_request.proposals.order_by('-version_number').first()
        self.assertIsNotNone(proposal)
        proposal_item = proposal.items.get(line_number=1)
        self.assertEqual(proposal_item.product_id, self.alt_product.pk)
        self.assertEqual(proposal_item.product_sku, self.alt_product.sku)
        self.assertEqual(proposal_item.product_name, self.alt_product.name)
        self.assertEqual(proposal_item.price_at_snapshot, Decimal('121.50'))

    def test_admin_can_generate_quote_from_confirmed_request(self):
        self.client.force_login(self.staff_user)
        self.client.post(
            reverse('admin_order_request_confirm', args=[self.order_request.pk]),
            data={'admin_note': 'Lista para cotizar'},
        )

        response = self.client.post(
            reverse('admin_order_request_generate_quote', args=[self.order_request.pk]),
            data={'sales_document_type_id': str(self.quote_type.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.order_request.refresh_from_db()
        self.assertEqual(self.order_request.status, OrderRequest.STATUS_CONVERTED)
        order = self.order_request.converted_order
        self.assertIsNotNone(order)
        self.assertEqual(order.status, Order.STATUS_DRAFT)
        self.assertTrue(
            InternalDocument.objects.filter(
                order=order,
                doc_type=DocumentSeries.DOC_COT,
            ).exists()
        )

    def test_admin_can_generate_invoice_from_confirmed_request(self):
        self.client.force_login(self.staff_user)
        self.client.post(
            reverse('admin_order_request_confirm', args=[self.order_request.pk]),
            data={'admin_note': 'Lista para facturar'},
        )

        response = self.client.post(
            reverse('admin_order_request_generate_invoice', args=[self.order_request.pk]),
            data={'sales_document_type_id': str(self.invoice_type.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.order_request.refresh_from_db()
        order = self.order_request.converted_order
        self.assertIsNotNone(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CONFIRMED)
        self.assertTrue(
            FiscalDocument.objects.filter(
                order=order,
                sales_document_type=self.invoice_type,
                doc_type=FISCAL_DOC_TYPE_FB,
            ).exists()
        )

    def test_client_can_view_generated_documents_from_request_and_order(self):
        self.client.force_login(self.staff_user)
        self.client.post(
            reverse('admin_order_request_confirm', args=[self.order_request.pk]),
            data={'admin_note': 'Lista para documentos'},
        )
        self.client.post(
            reverse('admin_order_request_generate_quote', args=[self.order_request.pk]),
            data={'sales_document_type_id': str(self.quote_type.pk)},
        )
        self.client.post(
            reverse('admin_order_request_generate_invoice', args=[self.order_request.pk]),
            data={'sales_document_type_id': str(self.invoice_type.pk)},
        )

        self.order_request.refresh_from_db()
        order = self.order_request.converted_order
        internal_doc = InternalDocument.objects.filter(order=order, doc_type=DocumentSeries.DOC_COT).first()
        fiscal_doc = FiscalDocument.objects.filter(order=order, doc_type=FISCAL_DOC_TYPE_FB).first()

        self.client.force_login(self.client_user)
        request_response = self.client.get(reverse('order_request_detail', args=[self.order_request.pk]))
        order_response = self.client.get(reverse('order_detail', args=[order.pk]))
        internal_response = self.client.get(reverse('order_internal_document_print', args=[internal_doc.pk]))
        fiscal_response = self.client.get(reverse('order_fiscal_document_print', args=[fiscal_doc.pk]))

        self.assertEqual(request_response.status_code, 200)
        self.assertContains(request_response, internal_doc.display_number)
        self.assertEqual(order_response.status_code, 200)
        self.assertContains(order_response, fiscal_doc.commercial_type_label)
        self.assertEqual(internal_response.status_code, 200)
        self.assertContains(internal_response, self.product.name)
        self.assertEqual(fiscal_response.status_code, 200)
        self.assertContains(fiscal_response, fiscal_doc.get_doc_type_display())
