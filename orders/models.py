"""
Orders app models - Cart and Orders.
"""
from django.db import models
from django.contrib.auth.models import User
from catalog.models import Product


class Cart(models.Model):
    """
    Shopping cart for logged-in users.
    Persists across sessions.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='cart',
        verbose_name="Usuario"
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
        related_name='items',
        verbose_name="Carrito"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name="Producto"
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="Cantidad"
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item del Carrito"
        verbose_name_plural = "Items del Carrito"
        unique_together = ['cart', 'product']

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"

    def get_subtotal(self):
        """Calculate subtotal for this item."""
        return self.product.price * self.quantity


class Order(models.Model):
    """
    Customer order.
    No payment processing - orders are confirmed and processed offline.
    """
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('confirmed', 'Confirmado'),
        ('processing', 'En Proceso'),
        ('ready', 'Listo'),
        ('shipped', 'Enviado'),
        ('cancelled', 'Cancelado'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='orders',
        verbose_name="Cliente"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name="Estado",
        db_index=True
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Notas del cliente"
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name="Notas internas"
    )
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Subtotal"
    )
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)"
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Monto de descuento"
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Total"
    )
    
    # Client info at time of order (denormalized for history)
    client_company = models.CharField(max_length=200, blank=True)
    client_cuit = models.CharField(max_length=20, blank=True)
    client_address = models.TextField(blank=True)
    client_phone = models.CharField(max_length=100, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['user']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.user.username if self.user else 'N/A'}"

    def get_item_count(self):
        """Total number of items in order."""
        return sum(item.quantity for item in self.items.all())


class OrderItem(models.Model):
    """
    Individual item in an order.
    Stores price at time of purchase for historical accuracy.
    """
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name="Pedido"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Producto"
    )
    # Denormalized product info at time of order
    product_sku = models.CharField(max_length=50, verbose_name="SKU")
    product_name = models.CharField(max_length=255, verbose_name="Nombre")
    quantity = models.PositiveIntegerField(verbose_name="Cantidad")
    price_at_purchase = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Precio unitario"
    )
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Subtotal"
    )

    class Meta:
        verbose_name = "Item del Pedido"
        verbose_name_plural = "Items del Pedido"

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

    def save(self, *args, **kwargs):
        """Calculate subtotal before saving."""
        self.subtotal = self.price_at_purchase * self.quantity
        super().save(*args, **kwargs)
