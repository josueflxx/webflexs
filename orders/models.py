"""
Orders app models - Cart, Orders, and client portal helpers.
"""
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils import timezone

from catalog.models import Product
from core.models import Company


class Cart(models.Model):
    """
    Shopping cart for logged-in users.
    Persists across sessions.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="carts",
        verbose_name="Usuario",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="carts",
        verbose_name="Empresa",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Carrito"
        verbose_name_plural = "Carritos"
        unique_together = [("user", "company")]
        indexes = [
            models.Index(fields=["user", "company"]),
        ]

    def __str__(self):
        company_label = self.company.name if self.company_id else "-"
        return f"Carrito de {self.user.username} ({company_label})"

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
    clamp_request = models.ForeignKey(
        "catalog.ClampMeasureRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cart_items",
        verbose_name="Solicitud de medida",
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

    ORIGIN_CATALOG = "catalog"
    ORIGIN_ADMIN = "admin"
    ORIGIN_WHATSAPP = "whatsapp"
    ORIGIN_PHONE = "phone"
    ORIGIN_OTHER = "other"
    ORIGIN_CHOICES = [
        (ORIGIN_CATALOG, "Catalogo"),
        (ORIGIN_ADMIN, "Admin"),
        (ORIGIN_WHATSAPP, "WhatsApp"),
        (ORIGIN_PHONE, "Telefono"),
        (ORIGIN_OTHER, "Otro"),
    ]

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

    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Baja"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "Alta"),
        (PRIORITY_URGENT, "Urgente"),
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
    SYNC_STATUS_PENDING = "pending"
    SYNC_STATUS_SYNCED = "synced"
    SYNC_STATUS_FAILED = "failed"
    SYNC_STATUS_CHOICES = [
        (SYNC_STATUS_PENDING, "Pendiente"),
        (SYNC_STATUS_SYNCED, "Sincronizado"),
        (SYNC_STATUS_FAILED, "Fallido"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="orders",
        verbose_name="Cliente",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Empresa",
    )
    origin_channel = models.CharField(
        max_length=20,
        choices=ORIGIN_CHOICES,
        default=ORIGIN_ADMIN,
        verbose_name="Canal origen",
        db_index=True,
    )
    source_request = models.ForeignKey(
        "OrderRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_orders",
        verbose_name="Solicitud origen",
    )
    source_proposal = models.ForeignKey(
        "OrderProposal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_orders",
        verbose_name="Propuesta origen",
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
    client_company_ref = models.ForeignKey(
        "accounts.ClientCompany",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Cliente empresa",
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_orders",
        verbose_name="Asignado a",
    )
    priority = models.CharField(
        max_length=16,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_NORMAL,
        verbose_name="Prioridad",
    )
    saas_document_type = models.CharField(
        max_length=24,
        blank=True,
        default="",
        verbose_name="Tipo comprobante SaaS",
    )
    saas_document_number = models.CharField(
        max_length=60,
        blank=True,
        default="",
        verbose_name="Numero comprobante SaaS",
    )
    saas_document_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha comprobante SaaS",
    )
    saas_document_cae = models.CharField(
        max_length=40,
        blank=True,
        default="",
        verbose_name="CAE SaaS",
    )
    saas_document_pdf = models.FileField(
        upload_to="orders/saas/",
        null=True,
        blank=True,
        verbose_name="PDF comprobante SaaS",
    )
    saas_document_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Total comprobante SaaS",
    )
    external_system = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Sistema externo",
    )
    external_id = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name="ID externo",
    )
    external_number = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name="Numero externo",
    )
    sync_status = models.CharField(
        max_length=12,
        choices=SYNC_STATUS_CHOICES,
        default=SYNC_STATUS_PENDING,
        verbose_name="Estado sync",
    )
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha sync",
    )
    billing_company = models.CharField(
        max_length=20,
        default="flexs",
        verbose_name="Empresa facturacion",
    )
    billing_mode = models.CharField(
        max_length=20,
        default="official",
        verbose_name="Modo facturacion",
    )
    follow_up_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Seguimiento para",
    )
    follow_up_done_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Seguimiento resuelto",
    )
    follow_up_note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Nota seguimiento",
    )
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
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["origin_channel", "created_at"]),
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["sync_status", "created_at"]),
            models.Index(fields=["external_system", "external_id"]),
        ]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.user.username if self.user else 'N/A'}"

    def save(self, *args, **kwargs):
        """
        Keep ledger charge rows in sync when order key financial fields change.
        """
        if not kwargs.get("raw"):
            if self.company_id and getattr(self.company, "slug", None):
                self.billing_company = self.company.slug
            elif not self.billing_company:
                self.billing_company = "flexs"
            if not self.billing_mode:
                self.billing_mode = "official"
            if self._state.adding:
                if not self.company_id:
                    raise ValidationError("La empresa es obligatoria para nuevos pedidos.")
                if not self.client_company_ref_id:
                    raise ValidationError("El cliente empresa es obligatorio para nuevos pedidos.")
                if self.client_company_ref_id and not self.client_company_ref.is_active:
                    raise ValidationError("El cliente empresa no esta activo para operar.")
            if self.company_id and self.client_company_ref_id:
                if self.client_company_ref.company_id != self.company_id:
                    raise ValidationError("La empresa del pedido no coincide con el cliente empresa.")
                if self.user_id and self.client_company_ref.client_profile.user_id != self.user_id:
                    raise ValidationError("El cliente empresa no corresponde al usuario del pedido.")
            if not self.company_id and not self._state.adding:
                try:
                    from core.services.company_context import get_default_company

                    self.company = get_default_company()
                except Exception:
                    pass
        is_new = self._state.adding
        update_fields = kwargs.get("update_fields")
        tracked_fields = {"status", "total", "user", "status_updated_at"}
        should_sync_ledger = (
            is_new
            or update_fields is None
            or bool(tracked_fields.intersection(set(update_fields)))
        )

        super().save(*args, **kwargs)

        if kwargs.get("raw"):
            return
        if not should_sync_ledger:
            return

        try:
            from accounts.services.ledger import sync_order_charge_transaction

            sync_order_charge_transaction(order=self, actor=None)
        except Exception:
            # Ledger sync should never block persisting the order.
            pass

        try:
            from core.models import DocumentSeries
            from core.services.documents import ensure_document_for_order

            if self.status == self.STATUS_DRAFT:
                ensure_document_for_order(self, doc_type=DocumentSeries.DOC_COT)
            if self.status in {
                self.STATUS_CONFIRMED,
                self.STATUS_PREPARING,
                self.STATUS_SHIPPED,
                self.STATUS_DELIVERED,
            }:
                ensure_document_for_order(self, doc_type=DocumentSeries.DOC_PED)
            if self.status in {self.STATUS_SHIPPED, self.STATUS_DELIVERED}:
                ensure_document_for_order(self, doc_type=DocumentSeries.DOC_REM)
        except Exception:
            # Document creation should not block order persistence.
            pass

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

    def is_mutable_for_items(self):
        return self.normalized_status() == self.STATUS_DRAFT

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
        try:
            from accounts.services.ledger import sync_order_charge_transaction

            sync_order_charge_transaction(
                order=self,
                actor=changed_by if getattr(changed_by, "is_authenticated", False) else None,
            )
        except Exception:
            # Ledger sync should never block the core status workflow.
            pass
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
    clamp_request = models.ForeignKey(
        "catalog.ClampMeasureRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="Solicitud de medida",
    )
    product_sku = models.CharField(max_length=50, verbose_name="SKU")
    product_name = models.CharField(max_length=255, verbose_name="Nombre")
    quantity = models.PositiveIntegerField(verbose_name="Cantidad")
    unit_price_base = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Precio base unitario",
    )
    discount_percentage_used = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)",
    )
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="Lista de precio",
    )
    price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio unitario")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Subtotal")

    class Meta:
        verbose_name = "Item del Pedido"
        verbose_name_plural = "Items del Pedido"

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

    def save(self, *args, **kwargs):
        if (
            self.order_id
            and not self._state.adding
            and not getattr(self, "_force_item_write", False)
            and not self.order.is_mutable_for_items()
        ):
            raise ValidationError(
                "No se pueden editar items en pedidos confirmados o en estados posteriores."
            )
        self.subtotal = self.price_at_purchase * self.quantity
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if (
            self.order_id
            and not getattr(self, "_force_item_write", False)
            and not self.order.is_mutable_for_items()
        ):
            raise ValidationError(
                "No se pueden eliminar items en pedidos confirmados o en estados posteriores."
            )
        return super().delete(*args, **kwargs)


class OrderRequest(models.Model):
    """Pre-order request created before the operational order exists."""

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_IN_REVIEW = "in_review"
    STATUS_PROPOSAL_SENT = "proposal_sent"
    STATUS_WAITING_CLIENT = "waiting_client"
    STATUS_CONFIRMED = "confirmed"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_CONVERTED = "converted"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_SUBMITTED, "Enviado"),
        (STATUS_IN_REVIEW, "En revision"),
        (STATUS_PROPOSAL_SENT, "Propuesta enviada"),
        (STATUS_WAITING_CLIENT, "Esperando respuesta del cliente"),
        (STATUS_CONFIRMED, "Confirmado"),
        (STATUS_REJECTED, "Rechazado"),
        (STATUS_CANCELLED, "Cancelado"),
        (STATUS_CONVERTED, "Convertido a documento"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_requests",
        verbose_name="Cliente",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="order_requests",
        verbose_name="Empresa",
    )
    client_company_ref = models.ForeignKey(
        "accounts.ClientCompany",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_requests",
        verbose_name="Cliente empresa",
    )
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name="Estado",
        db_index=True,
    )
    origin_channel = models.CharField(
        max_length=20,
        choices=Order.ORIGIN_CHOICES,
        default=Order.ORIGIN_CATALOG,
        verbose_name="Canal origen",
        db_index=True,
    )
    client_note = models.TextField(blank=True, verbose_name="Nota del cliente")
    admin_note = models.TextField(blank=True, verbose_name="Nota interna")
    rejection_reason = models.CharField(max_length=255, blank=True, verbose_name="Motivo rechazo")
    requested_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Subtotal")
    requested_discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)",
    )
    requested_discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Monto descuento",
    )
    requested_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Total")
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="Enviado el")
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="Tomado en revision el")
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Resuelto el")
    converted_at = models.DateTimeField(null=True, blank=True, verbose_name="Convertido el")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Solicitud comercial"
        verbose_name_plural = "Solicitudes comerciales"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["origin_channel", "created_at"]),
        ]

    def __str__(self):
        company_label = self.company.name if self.company_id else "-"
        return f"Solicitud #{self.pk} - {company_label}"

    def clean(self):
        if self.client_company_ref_id and self.client_company_ref.company_id != self.company_id:
            raise ValidationError("La empresa de la solicitud no coincide con el cliente empresa.")
        if (
            self.client_company_ref_id
            and self.user_id
            and self.client_company_ref.client_profile.user_id != self.user_id
        ):
            raise ValidationError("El cliente empresa no corresponde al usuario de la solicitud.")

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)

    def get_item_count(self):
        """Total number of items in the request snapshot."""
        return sum(item.quantity for item in self.items.all())

    @property
    def current_proposal(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        proposals = prefetched.get("proposals")
        if proposals is not None:
            current = [proposal for proposal in proposals if proposal.is_current]
            if current:
                return sorted(current, key=lambda row: (row.version_number, row.id), reverse=True)[0]
            return None
        return self.proposals.filter(is_current=True).order_by("-version_number", "-id").first()

    @property
    def converted_order(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        generated_orders = prefetched.get("generated_orders")
        if generated_orders is not None:
            if not generated_orders:
                return None
            return sorted(generated_orders, key=lambda row: (row.created_at, row.id), reverse=True)[0]
        return self.generated_orders.order_by("-created_at", "-id").first()


class OrderRequestEvent(models.Model):
    """Minimal timeline/audit trail for the commercial request lifecycle."""

    EVENT_CREATED = "created"
    EVENT_REVIEW_STARTED = "review_started"
    EVENT_PROPOSAL_SENT = "proposal_sent"
    EVENT_PROPOSAL_ACCEPTED = "proposal_accepted"
    EVENT_PROPOSAL_REJECTED = "proposal_rejected"
    EVENT_CONFIRMED = "confirmed"
    EVENT_REJECTED = "rejected"
    EVENT_CONVERTED = "converted"
    EVENT_QUOTE_GENERATED = "quote_generated"
    EVENT_INVOICE_GENERATED = "invoice_generated"

    EVENT_CHOICES = [
        (EVENT_CREATED, "Solicitud creada"),
        (EVENT_REVIEW_STARTED, "Revision iniciada"),
        (EVENT_PROPOSAL_SENT, "Propuesta enviada"),
        (EVENT_PROPOSAL_ACCEPTED, "Propuesta aceptada"),
        (EVENT_PROPOSAL_REJECTED, "Propuesta rechazada"),
        (EVENT_CONFIRMED, "Solicitud confirmada"),
        (EVENT_REJECTED, "Solicitud rechazada"),
        (EVENT_CONVERTED, "Solicitud convertida"),
        (EVENT_QUOTE_GENERATED, "Cotizacion generada"),
        (EVENT_INVOICE_GENERATED, "Factura generada"),
    ]

    order_request = models.ForeignKey(
        OrderRequest,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name="Solicitud",
    )
    event_type = models.CharField(
        max_length=32,
        choices=EVENT_CHOICES,
        verbose_name="Evento",
        db_index=True,
    )
    from_status = models.CharField(max_length=24, blank=True, verbose_name="Estado anterior")
    to_status = models.CharField(max_length=24, blank=True, verbose_name="Estado nuevo")
    message = models.CharField(max_length=255, blank=True, verbose_name="Mensaje")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_request_events",
        verbose_name="Actor",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Evento de solicitud comercial"
        verbose_name_plural = "Eventos de solicitudes comerciales"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["order_request", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"Solicitud #{self.order_request_id} - {self.get_event_type_display()}"


class OrderRequestItem(models.Model):
    """Snapshot of one requested item before commercial approval."""

    order_request = models.ForeignKey(
        OrderRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Solicitud",
    )
    line_number = models.PositiveIntegerField(verbose_name="Linea")
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_request_items",
        verbose_name="Producto",
    )
    clamp_request = models.ForeignKey(
        "catalog.ClampMeasureRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_request_items",
        verbose_name="Solicitud de medida",
    )
    product_sku = models.CharField(max_length=50, verbose_name="SKU")
    product_name = models.CharField(max_length=255, verbose_name="Nombre")
    quantity = models.PositiveIntegerField(verbose_name="Cantidad")
    unit_price_base = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Precio base unitario",
    )
    discount_percentage_used = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)",
    )
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_request_items",
        verbose_name="Lista de precio",
    )
    price_at_snapshot = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio unitario")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Subtotal")

    class Meta:
        verbose_name = "Item de solicitud comercial"
        verbose_name_plural = "Items de solicitudes comerciales"
        ordering = ["order_request_id", "line_number"]
        unique_together = [("order_request", "line_number")]
        indexes = [
            models.Index(fields=["order_request", "line_number"]),
        ]

    def __str__(self):
        return f"Solicitud #{self.order_request_id} - linea {self.line_number}"

    def save(self, *args, **kwargs):
        self.subtotal = self.price_at_snapshot * self.quantity
        super().save(*args, **kwargs)


class OrderProposal(models.Model):
    """Commercial response prepared by admin for one request."""

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_ACCEPTED, "Aceptada"),
        (STATUS_REJECTED, "Rechazada"),
        (STATUS_EXPIRED, "Vencida"),
    ]

    order_request = models.ForeignKey(
        OrderRequest,
        on_delete=models.CASCADE,
        related_name="proposals",
        verbose_name="Solicitud",
    )
    version_number = models.PositiveIntegerField(verbose_name="Version")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name="Estado",
        db_index=True,
    )
    is_current = models.BooleanField(default=True, verbose_name="Version vigente")
    message_to_client = models.TextField(blank=True, verbose_name="Mensaje al cliente")
    internal_note = models.TextField(blank=True, verbose_name="Nota interna")
    proposed_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Subtotal")
    proposed_discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)",
    )
    proposed_discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Monto descuento",
    )
    proposed_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Total")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_proposals_created",
        verbose_name="Creado por",
    )
    responded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_proposals_responded",
        verbose_name="Respondido por",
    )
    sent_at = models.DateTimeField(default=timezone.now, verbose_name="Enviado el")
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name="Respondido el")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Vence el")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Propuesta comercial"
        verbose_name_plural = "Propuestas comerciales"
        ordering = ["-created_at", "-id"]
        unique_together = [("order_request", "version_number")]
        indexes = [
            models.Index(fields=["order_request", "status", "created_at"]),
            models.Index(fields=["order_request", "is_current"]),
        ]

    def __str__(self):
        return f"Propuesta #{self.pk} - solicitud #{self.order_request_id}"


class OrderProposalItem(models.Model):
    """Snapshot of the admin proposed item mix for one request."""

    order_proposal = models.ForeignKey(
        OrderProposal,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Propuesta",
    )
    line_number = models.PositiveIntegerField(verbose_name="Linea")
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_proposal_items",
        verbose_name="Producto",
    )
    clamp_request = models.ForeignKey(
        "catalog.ClampMeasureRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_proposal_items",
        verbose_name="Solicitud de medida",
    )
    product_sku = models.CharField(max_length=50, verbose_name="SKU")
    product_name = models.CharField(max_length=255, verbose_name="Nombre")
    quantity = models.PositiveIntegerField(verbose_name="Cantidad")
    unit_price_base = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Precio base unitario",
    )
    discount_percentage_used = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento aplicado (%)",
    )
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_proposal_items",
        verbose_name="Lista de precio",
    )
    price_at_snapshot = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio unitario")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Subtotal")

    class Meta:
        verbose_name = "Item de propuesta comercial"
        verbose_name_plural = "Items de propuestas comerciales"
        ordering = ["order_proposal_id", "line_number"]
        unique_together = [("order_proposal", "line_number")]
        indexes = [
            models.Index(fields=["order_proposal", "line_number"]),
        ]

    def __str__(self):
        return f"Propuesta #{self.order_proposal_id} - linea {self.line_number}"

    def save(self, *args, **kwargs):
        self.subtotal = self.price_at_snapshot * self.quantity
        super().save(*args, **kwargs)


class ClampQuotation(models.Model):
    """Stored result for clamp calculator price lists."""

    CLAMP_TREFILADA = "trefilada"
    CLAMP_LAMINADA = "laminada"
    CLAMP_TYPE_CHOICES = [
        (CLAMP_TREFILADA, "Trefilada"),
        (CLAMP_LAMINADA, "Laminada"),
    ]

    PROFILE_PLANA = "PLANA"
    PROFILE_SEMICURVA = "SEMICURVA"
    PROFILE_CURVA = "CURVA"
    PROFILE_TYPE_CHOICES = [
        (PROFILE_PLANA, "PLANA"),
        (PROFILE_SEMICURVA, "SEMICURVA"),
        (PROFILE_CURVA, "CURVA"),
    ]

    PRICE_LIST_1 = "lista_1"
    PRICE_LIST_2 = "lista_2"
    PRICE_LIST_3 = "lista_3"
    PRICE_LIST_4 = "lista_4"
    PRICE_LIST_FACT = "facturacion"
    PRICE_LIST_CHOICES = [
        (PRICE_LIST_1, "Lista 1"),
        (PRICE_LIST_2, "Lista 2"),
        (PRICE_LIST_3, "Lista 3"),
        (PRICE_LIST_4, "Lista 4"),
        (PRICE_LIST_FACT, "Facturacion"),
    ]

    client_name = models.CharField(max_length=200, blank=True, verbose_name="Cliente")
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clamp_quotations",
        verbose_name="Empresa",
    )
    dollar_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Dolar")
    steel_price_usd = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Precio acero USD")
    supplier_discount_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        verbose_name="Desc. proveedor (%)",
    )
    general_increase_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=23,
        verbose_name="Aumento general (%)",
    )
    clamp_type = models.CharField(max_length=20, choices=CLAMP_TYPE_CHOICES, verbose_name="Tipo abrazadera")
    is_zincated = models.BooleanField(default=False, verbose_name="Zincado")
    diameter = models.CharField(max_length=20, verbose_name="Diametro")
    width_mm = models.PositiveIntegerField(verbose_name="Ancho (mm)")
    length_mm = models.PositiveIntegerField(verbose_name="Largo (mm)")
    profile_type = models.CharField(max_length=20, choices=PROFILE_TYPE_CHOICES, verbose_name="Tipo")
    description = models.CharField(max_length=300, verbose_name="Descripcion")
    base_cost = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Costo base")
    price_list = models.CharField(max_length=20, choices=PRICE_LIST_CHOICES, verbose_name="Lista")
    final_price = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Precio final")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clamp_quotations_created",
        verbose_name="Creado por",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Cotizacion de Abrazadera"
        verbose_name_plural = "Cotizaciones de Abrazadera"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["client_name"]),
            models.Index(fields=["price_list"]),
        ]

    def __str__(self):
        list_display = dict(self.PRICE_LIST_CHOICES).get(self.price_list, self.price_list)
        return f"{self.description} [{list_display}]"

    def save(self, *args, **kwargs):
        if not kwargs.get("raw") and not self.company_id:
            try:
                from core.services.company_context import get_default_company

                self.company = get_default_company()
            except Exception:
                pass
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
