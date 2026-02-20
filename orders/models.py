"""
Orders app models - Cart, Orders, and client portal helpers.
"""
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product


class Cart(models.Model):
    """
    Shopping cart for logged-in users.
    Persists across sessions.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="cart",
        verbose_name="Usuario",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Carrito"
        verbose_name_plural = "Carritos"

    def __str__(self):
        return f"Carrito de {self.user.username}"

    def get_total(self):
        """Calculate cart total without discounts."""
        return sum(item.get_subtotal() for item in self.items.all())

    def get_total_with_discount(self, discount_percentage=0):
        """Calculate cart total with client discount."""
        total = self.get_total()
        if discount_percentage:
            discount = total * discount_percentage
            return total - discount
        return total

    def get_item_count(self):
        """Total number of items in cart."""
        return sum(item.quantity for item in self.items.all())

    def clear(self):
        """Remove all items from cart."""
        self.items.all().delete()


class CartItem(models.Model):
    """Individual item in a shopping cart."""

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Carrito",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name="Producto",
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="Cantidad")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item del Carrito"
        verbose_name_plural = "Items del Carrito"
        unique_together = ["cart", "product"]

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"

    def get_subtotal(self):
        """Calculate subtotal for this item."""
        return self.product.price * self.quantity


class Order(models.Model):
    """
    Customer order with explicit workflow states.
    """

    STATUS_DRAFT = "draft"
    STATUS_CONFIRMED = "confirmed"
    STATUS_PREPARING = "preparing"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_CONFIRMED, "Confirmado"),
        (STATUS_PREPARING, "En preparacion"),
        (STATUS_SHIPPED, "Enviado"),
        (STATUS_DELIVERED, "Entregado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    LEGACY_STATUS_MAP = {
        "pending": STATUS_DRAFT,
        "processing": STATUS_PREPARING,
        "ready": STATUS_PREPARING,
    }

    WORKFLOW_TRANSITIONS = {
        STATUS_DRAFT: {STATUS_CONFIRMED, STATUS_CANCELLED},
        STATUS_CONFIRMED: {STATUS_PREPARING, STATUS_CANCELLED},
        STATUS_PREPARING: {STATUS_SHIPPED, STATUS_CANCELLED},
        STATUS_SHIPPED: {STATUS_DELIVERED, STATUS_CANCELLED},
        STATUS_DELIVERED: set(),
        STATUS_CANCELLED: set(),
    }

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="orders",
        verbose_name="Cliente",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name="Estado",
        db_index=True,
    )
    notes = models.TextField(blank=True, verbose_name="Notas del cliente")
    admin_notes = models.TextField(blank=True, verbose_name="Notas internas")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Subtotal")
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)",
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Monto de descuento")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Total")
    client_company = models.CharField(max_length=200, blank=True)
    client_cuit = models.CharField(max_length=20, blank=True)
    client_address = models.TextField(blank=True)
    client_phone = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    status_updated_at = models.DateTimeField(default=timezone.now, verbose_name="Ultimo cambio de estado")

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["user"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["status", "updated_at"]),
        ]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.user.username if self.user else 'N/A'}"

    def get_item_count(self):
        """Total number of items in order."""
        return sum(item.quantity for item in self.items.all())

    def normalized_status(self):
        return self.LEGACY_STATUS_MAP.get(self.status, self.status)

    def get_paid_amount(self):
        """Total non-cancelled payments allocated to this order."""
        ClientPayment = apps.get_model('accounts', 'ClientPayment')
        total = ClientPayment.objects.filter(
            order_id=self.pk,
            is_cancelled=False,
        ).aggregate(total=Sum('amount'))['total']
        return total or Decimal('0.00')

    def get_pending_amount(self):
        pending = (self.total or Decimal('0.00')) - self.get_paid_amount()
        return pending if pending > 0 else Decimal('0.00')

    def is_paid(self):
        return self.get_pending_amount() <= Decimal('0.00')

    def can_transition_to(self, new_status):
        normalized_current = self.normalized_status()
        normalized_target = self.LEGACY_STATUS_MAP.get(new_status, new_status)
        if normalized_current == normalized_target:
            return True
        return normalized_target in self.WORKFLOW_TRANSITIONS.get(normalized_current, set())

    def change_status(self, new_status, changed_by=None, note=""):
        normalized_target = self.LEGACY_STATUS_MAP.get(new_status, new_status)
        if normalized_target not in dict(self.STATUS_CHOICES):
            raise ValueError("Estado de pedido invalido.")
        if not self.can_transition_to(normalized_target):
            raise ValueError("Transicion de estado no permitida.")

        previous_status = self.normalized_status()
        if previous_status == normalized_target:
            return False

        self.status = normalized_target
        self.status_updated_at = timezone.now()
        self.save(update_fields=["status", "status_updated_at", "updated_at"])
        OrderStatusHistory.objects.create(
            order=self,
            from_status=previous_status,
            to_status=normalized_target,
            changed_by=changed_by if getattr(changed_by, "is_authenticated", False) else None,
            note=(note or "").strip(),
        )
        return True


class OrderStatusHistory(models.Model):
    """Traceability for each order status transition."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name="Pedido",
    )
    from_status = models.CharField(max_length=20, verbose_name="Estado anterior")
    to_status = models.CharField(max_length=20, verbose_name="Estado nuevo")
    note = models.CharField(max_length=255, blank=True, verbose_name="Nota")
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_status_changes",
        verbose_name="Actualizado por",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Historial de Pedido"
        verbose_name_plural = "Historial de Pedidos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order", "created_at"]),
            models.Index(fields=["to_status"]),
        ]

    def __str__(self):
        return f"Pedido #{self.order_id}: {self.from_status} -> {self.to_status}"


class OrderItem(models.Model):
    """
    Individual item in an order.
    Stores price at time of purchase for historical accuracy.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Pedido",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Producto",
    )
    product_sku = models.CharField(max_length=50, verbose_name="SKU")
    product_name = models.CharField(max_length=255, verbose_name="Nombre")
    quantity = models.PositiveIntegerField(verbose_name="Cantidad")
    price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio unitario")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Subtotal")

    class Meta:
        verbose_name = "Item del Pedido"
        verbose_name_plural = "Items del Pedido"

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

    def save(self, *args, **kwargs):
        self.subtotal = self.price_at_purchase * self.quantity
        super().save(*args, **kwargs)


class ClientFavoriteProduct(models.Model):
    """Favorite products for client portal quick reorders."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="favorite_products",
        verbose_name="Cliente",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="favorited_by",
        verbose_name="Producto",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Favorito de Cliente"
        verbose_name_plural = "Favoritos de Clientes"
        unique_together = ["user", "product"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.product.sku}"
