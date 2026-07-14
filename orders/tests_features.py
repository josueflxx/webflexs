from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.services.company_context import get_default_company
from orders.models import Cart, CartItem, Order, OrderRequest
from orders.services.request_workflow import build_order_request_from_cart, convert_request_to_order


class OrderIdempotencyFeatureTests(TestCase):
    def setUp(self):
        self.company = get_default_company()
        self.user = User.objects.create_user("idempotent_client")
        self.profile = ClientProfile.objects.create(
            user=self.user,
            company_name="Cliente idempotencia",
            is_approved=True,
        )
        self.link = ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.company,
            is_active=True,
        )
        self.product = Product.objects.create(
            sku="IDEMPOTENT-1",
            name="Producto idempotente",
            price=Decimal("100.00"),
            stock=10,
        )
        self.cart = Cart.objects.create(user=self.user, company=self.company)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

    def test_same_checkout_key_returns_existing_request(self):
        first = build_order_request_from_cart(
            cart=self.cart,
            user=self.user,
            company=self.company,
            idempotency_key="same-checkout-key",
        )
        second = build_order_request_from_cart(
            cart=self.cart,
            user=self.user,
            company=self.company,
            idempotency_key="same-checkout-key",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OrderRequest.objects.filter(idempotency_key="same-checkout-key").count(), 1)

    def test_request_conversion_returns_same_order_on_retry(self):
        order_request = build_order_request_from_cart(
            cart=self.cart,
            user=self.user,
            company=self.company,
        )
        order_request.status = OrderRequest.STATUS_CONFIRMED
        order_request.save(update_fields=["status", "updated_at"])

        first, first_created = convert_request_to_order(order_request=order_request, actor=self.user)
        order_request.refresh_from_db()
        second, second_created = convert_request_to_order(order_request=order_request, actor=self.user)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.filter(source_request=order_request).count(), 1)
