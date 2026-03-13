"""
Accounts app models - Client profiles and account requests.
"""
from decimal import Decimal

from django.apps import apps
from django.db import models
from django.db.models import Sum
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from core.models import Company

class ClientCategory(models.Model):
    """
    Operational category for clients (pricing/discount/account-current rules).
    """

    SALE_CONDITION_CASH = "cash"
    SALE_CONDITION_ACCOUNT = "account"
    SALE_CONDITION_CHOICES = [
        (SALE_CONDITION_CASH, "Contado"),
        (SALE_CONDITION_ACCOUNT, "Cuenta corriente"),
    ]

    name = models.CharField(max_length=120, unique=True, verbose_name="Nombre")
    slug = models.SlugField(max_length=150, unique=True, blank=True)
    default_sale_condition = models.CharField(
        max_length=20,
        choices=SALE_CONDITION_CHOICES,
        default=SALE_CONDITION_CASH,
        verbose_name="Condicion de venta predeterminada",
    )
    allows_account_current = models.BooleanField(
        default=False,
        verbose_name="Habilita cuenta corriente",
    )
    account_current_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Limite de cuenta corriente",
    )
    expose_cost = models.BooleanField(
        default=False,
        verbose_name="Costo",
        help_text="Reservado para reglas internas/visuales futuras.",
    )
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento (%)",
    )
    price_list_name = models.CharField(
        max_length=80,
        default="Principal",
        verbose_name="Lista de precio",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria de cliente"
        verbose_name_plural = "Categorias de clientes"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["is_active", "sort_order"]),
            models.Index(fields=["name"]),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "categoria-cliente"
            candidate = base_slug
            counter = 1
            while ClientCategory.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{counter}"
                counter += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ClientProfile(models.Model):
    """
    Extended profile for B2B clients.
    Linked to Django's built-in User model.
    """
    DOCUMENT_TYPE_CHOICES = [
        ('cuit', 'CUIT'),
        ('cuil', 'CUIL'),
        ('dni', 'DNI'),
        ('cdi', 'CDI'),
        ('passport', 'Pasaporte'),
        ('otro', 'Otro'),
    ]
    IVA_CHOICES = [
        ('responsable_inscripto', 'Responsable Inscripto'),
        ('monotributista', 'Monotributista'),
        ('exento', 'Exento'),
        ('consumidor_final', 'Consumidor Final'),
    ]
    CLIENT_TYPE_CHOICES = [
        ('taller', 'Taller'),
        ('distribuidora', 'Distribuidora'),
        ('flota', 'Flota'),
        ('otro', 'Otro'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='client_profile',
        verbose_name="Usuario"
    )
    client_category = models.ForeignKey(
        ClientCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clients',
        verbose_name="Categoria de cliente",
    )
    company_name = models.CharField(
        max_length=200,
        verbose_name="Empresa/Razón Social"
    )
    document_type = models.CharField(
        max_length=20,
        choices=DOCUMENT_TYPE_CHOICES,
        blank=True,
        verbose_name="Tipo de documento",
    )
    document_number = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Numero de documento",
    )
    cuit_dni = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="CUIT/DNI"
    )
    province = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Provincia"
    )
    fiscal_province = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Provincia fiscal",
    )
    fiscal_city = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Localidad fiscal",
    )
    address = models.TextField(
        blank=True,
        verbose_name="Domicilio"
    )
    fiscal_address = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Domicilio fiscal",
    )
    postal_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Codigo postal",
    )
    phone = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Teléfonos"
    )
    discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento (%)",
        help_text="Porcentaje de descuento (ej: 10.00 para 10%)"
    )
    iva_condition = models.CharField(
        max_length=50,
        choices=IVA_CHOICES,
        blank=True,
        verbose_name="Condición IVA"
    )
    client_type = models.CharField(
        max_length=50,
        choices=CLIENT_TYPE_CHOICES,
        blank=True,
        verbose_name="Tipo de Cliente"
    )
    is_approved = models.BooleanField(
        default=True,
        verbose_name="Cuenta aprobada"
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Notas internas"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil de Cliente"
        verbose_name_plural = "Perfiles de Clientes"
        ordering = ['company_name']

    def __str__(self):
        return f"{self.company_name} ({self.user.username})"

    def get_company_link(self, company):
        if not company:
            return None
        return (
            self.company_links.select_related("company", "client_category")
            .filter(company=company, is_active=True, company__is_active=True)
            .first()
        )

    def get_effective_client_category(self, company=None):
        link = self.get_company_link(company) if company else None
        if link and link.client_category_id:
            return link.client_category
        if self.client_category_id:
            return self.client_category
        return None

    def get_effective_discount_percentage(self, company=None):
        """
        Discount in percentage points.
        Priority:
        1) ClientCompany.discount_percentage
        2) ClientCategoryCompanyRule.discount_percentage
        3) ClientCategory.discount_percentage
        4) ClientProfile.discount (legacy)
        """
        link = self.get_company_link(company) if company else None
        if link and link.discount_percentage and link.discount_percentage != 0:
            return link.discount_percentage or Decimal("0")
        category = self.get_effective_client_category(company=company)
        if company and category:
            rule = ClientCategoryCompanyRule.objects.filter(
                company=company,
                client_category=category,
            ).first()
            if rule and rule.discount_percentage is not None and rule.discount_percentage != 0:
                return rule.discount_percentage
        if category and getattr(category, "is_active", False):
            if category.discount_percentage and category.discount_percentage != 0:
                return category.discount_percentage
        if self.discount and self.discount != 0:
            return self.discount
        return Decimal("0")

    def get_discount_decimal(self, company=None):
        """Return discount as decimal (e.g., 0.10 for 10%)."""
        return self.get_effective_discount_percentage(company=company) / 100

    def uses_legacy_commercial_rules(self, company=None):
        if not company:
            return True
        link = self.company_links.filter(company=company).first()
        if not link:
            return True
        if link.client_category_id:
            return False
        if link.discount_percentage and link.discount_percentage != 0:
            return False
        if link.price_list_id:
            return False
        category = self.get_effective_client_category(company=company)
        if category:
            rule = ClientCategoryCompanyRule.objects.filter(
                company=company,
                client_category=category,
            ).first()
            if rule and (rule.price_list_id or (rule.discount_percentage and rule.discount_percentage != 0)):
                return False
        return True

    def can_operate_in_company(self, company=None):
        if not self.is_approved:
            return False
        link = self.get_company_link(company)
        if not link or not link.is_active:
            return False
        return True

    def get_total_paid(self, company=None):
        payments = self.payments.filter(is_cancelled=False)
        if company:
            payments = payments.filter(company=company)
        total = payments.aggregate(total=Sum('amount'))['total']
        return total or Decimal('0.00')

    def get_ledger_queryset(self, company=None):
        queryset = self.transactions.select_related('order', 'payment', 'created_by')
        if company:
            queryset = queryset.filter(company=company)
        return queryset.order_by('occurred_at', 'id')

    def get_ledger_balance(self, company=None):
        queryset = self.transactions
        if company:
            queryset = queryset.filter(company=company)
        total = queryset.aggregate(total=Sum('amount'))['total']
        return total or Decimal('0.00')

    def get_orders_queryset_for_balance(self, company=None):
        """
        Orders that impact client debt/saldo:
        confirmed and subsequent operational states (except draft/cancelled).
        """
        Order = apps.get_model('orders', 'Order')
        queryset = Order.objects.filter(
            user_id=self.user_id,
            status__in=[
                Order.STATUS_CONFIRMED,
                Order.STATUS_PREPARING,
                Order.STATUS_SHIPPED,
                Order.STATUS_DELIVERED,
            ],
        )
        if company:
            queryset = queryset.filter(company=company)
        return queryset

    def get_total_orders_for_balance(self, company=None):
        total = self.get_orders_queryset_for_balance(company=company).aggregate(total=Sum('total'))['total']
        return total or Decimal('0.00')

    def get_current_balance(self, company=None):
        # Prefer ledger when available (auditable + robust to adjustments/reversals).
        transactions = self.transactions
        if company:
            transactions = transactions.filter(company=company)
        if transactions.exists():
            return self.get_ledger_balance(company=company)
        return self.get_total_orders_for_balance(company=company) - self.get_total_paid(company=company)


class ClientCompany(models.Model):
    """Client-specific settings per company."""

    client_profile = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name="company_links",
        verbose_name="Cliente",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="client_links",
        verbose_name="Empresa",
    )
    client_category = models.ForeignKey(
        ClientCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company_clients",
        verbose_name="Categoria de cliente",
    )
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_companies",
        verbose_name="Lista de precio",
    )
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Descuento (%)",
    )
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cliente por Empresa"
        verbose_name_plural = "Clientes por Empresa"
        unique_together = [("client_profile", "company")]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["client_profile", "company"]),
            models.Index(fields=["company", "price_list"]),
        ]

    def __str__(self):
        return f"{self.client_profile.company_name} - {self.company.name}"

    def has_commercial_rules(self):
        if self.client_category_id:
            return True
        if self.discount_percentage and self.discount_percentage != 0:
            return True
        return False


class ClientCategoryCompanyRule(models.Model):
    """Commercial rule for a category scoped to a company."""

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="category_rules",
        verbose_name="Empresa",
    )
    client_category = models.ForeignKey(
        ClientCategory,
        on_delete=models.CASCADE,
        related_name="company_rules",
        verbose_name="Categoria de cliente",
    )
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="category_company_rules",
        verbose_name="Lista de precio",
    )
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Descuento (%)",
    )
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Regla categoria por empresa"
        verbose_name_plural = "Reglas categoria por empresa"
        unique_together = [("company", "client_category")]
        indexes = [
            models.Index(fields=["company", "client_category"]),
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.client_category.name}"


class AccountRequest(models.Model):
    """
    Account requests from potential clients.
    Admin reviews and approves these to create actual user accounts.
    """
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('approved', 'Aprobada'),
        ('rejected', 'Rechazada'),
    ]

    company_name = models.CharField(
        max_length=200,
        verbose_name="Empresa/Razón Social"
    )
    contact_name = models.CharField(
        max_length=100,
        verbose_name="Nombre de Contacto"
    )
    cuit_dni = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="CUIT/DNI"
    )
    email = models.EmailField(verbose_name="Email")
    phone = models.CharField(
        max_length=50,
        verbose_name="Teléfono"
    )
    province = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Provincia"
    )
    address = models.TextField(
        blank=True,
        verbose_name="Domicilio"
    )
    message = models.TextField(
        blank=True,
        verbose_name="Mensaje"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name="Estado"
    )
    admin_notes = models.TextField(
        blank=True,
        verbose_name="Notas del Admin"
    )
    created_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_from_request',
        verbose_name="Usuario creado"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de procesamiento"
    )

    class Meta:
        verbose_name = "Solicitud de Cuenta"
        verbose_name_plural = "Solicitudes de Cuenta"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.company_name} - {self.email} ({self.get_status_display()})"


class ClientPayment(models.Model):
    """Client payment record with optional order allocation."""

    ORIGIN_LOCAL = "local"
    ORIGIN_EXTERNAL = "external"
    ORIGIN_CHOICES = [
        (ORIGIN_LOCAL, "Local"),
        (ORIGIN_EXTERNAL, "Externo"),
    ]
    SYNC_STATUS_PENDING = "pending"
    SYNC_STATUS_SYNCED = "synced"
    SYNC_STATUS_FAILED = "failed"
    SYNC_STATUS_CHOICES = [
        (SYNC_STATUS_PENDING, "Pendiente"),
        (SYNC_STATUS_SYNCED, "Sincronizado"),
        (SYNC_STATUS_FAILED, "Fallido"),
    ]
    METHOD_TRANSFER = 'transfer'
    METHOD_CASH = 'cash'
    METHOD_CARD = 'card'
    METHOD_CHECK = 'check'
    METHOD_OTHER = 'other'

    METHOD_CHOICES = [
        (METHOD_TRANSFER, 'Transferencia'),
        (METHOD_CASH, 'Efectivo'),
        (METHOD_CARD, 'Tarjeta'),
        (METHOD_CHECK, 'Cheque'),
        (METHOD_OTHER, 'Otro'),
    ]

    client_profile = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name='payments',
        verbose_name='Cliente',
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="client_payments",
        verbose_name="Empresa",
    )
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments',
        verbose_name='Pedido asociado',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Monto')
    method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES,
        default=METHOD_TRANSFER,
        verbose_name='Medio de pago',
    )
    origin = models.CharField(
        max_length=12,
        choices=ORIGIN_CHOICES,
        default=ORIGIN_LOCAL,
        verbose_name="Origen",
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
    paid_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha de pago')
    reference = models.CharField(max_length=120, blank=True, verbose_name='Referencia')
    notes = models.TextField(blank=True, verbose_name='Notas')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_client_payments',
        verbose_name='Registrado por',
    )
    is_cancelled = models.BooleanField(default=False, verbose_name='Anulado')
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de anulacion')
    cancel_reason = models.CharField(max_length=255, blank=True, verbose_name='Motivo anulacion')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pago de Cliente'
        verbose_name_plural = 'Pagos de Clientes'
        ordering = ['-paid_at', '-id']
        indexes = [
            models.Index(fields=['client_profile', 'paid_at']),
            models.Index(fields=['client_profile', 'is_cancelled', 'paid_at']),
            models.Index(fields=['order', 'paid_at']),
            models.Index(fields=['is_cancelled']),
            models.Index(fields=['company', 'paid_at']),
            models.Index(fields=['sync_status', 'paid_at']),
            models.Index(fields=['external_system', 'external_id']),
        ]

    def __str__(self):
        company = self.client_profile.company_name if self.client_profile_id else '-'
        return f"{company} - ${self.amount}"

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            if self.order_id:
                if self.company_id and getattr(self.order, "company_id", None):
                    if self.company_id != self.order.company_id:
                        raise ValidationError("La empresa del pago no coincide con el pedido.")
                if getattr(self.order, "company_id", None):
                    self.company_id = self.order.company_id
                else:
                    if self._state.adding and not self.company_id:
                        try:
                            from core.services.company_context import get_default_company

                            self.company = get_default_company()
                        except Exception:
                            pass
            else:
                if self._state.adding and not self.company_id:
                    raise ValidationError("La empresa es obligatoria para pagos sin pedido.")
        super().save(*args, **kwargs)

        # Keep client ledger in sync on create/update/cancel.
        from accounts.services.ledger import sync_payment_transaction
        sync_payment_transaction(
            payment=self,
            actor=self.created_by if self.created_by_id else None,
        )

        try:
            from core.services.documents import ensure_document_for_payment

            ensure_document_for_payment(self)
        except Exception:
            # Document creation should not block payment persistence.
            pass


class ClientTransaction(models.Model):
    """Auditable ledger row for client current-account balance."""

    TYPE_ORDER_CHARGE = 'order_charge'
    TYPE_PAYMENT = 'payment'
    TYPE_ADJUSTMENT = 'adjustment'

    TYPE_CHOICES = [
        (TYPE_ORDER_CHARGE, 'Cargo por pedido'),
        (TYPE_PAYMENT, 'Pago'),
        (TYPE_ADJUSTMENT, 'Ajuste manual'),
    ]

    client_profile = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name='transactions',
        verbose_name='Cliente',
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="client_transactions",
        verbose_name="Empresa",
    )
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='client_transactions',
        verbose_name='Pedido asociado',
    )
    payment = models.ForeignKey(
        ClientPayment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_transactions',
        verbose_name='Pago asociado',
    )
    billing_company = models.CharField(
        max_length=20,
        default="flexs",
        verbose_name="Empresa facturacion",
    )
    transaction_type = models.CharField(
        max_length=24,
        choices=TYPE_CHOICES,
        verbose_name='Tipo',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Monto (+/-)',
        help_text='Positivo suma deuda; negativo reduce deuda.',
    )
    description = models.CharField(max_length=255, blank=True, verbose_name='Descripcion')
    source_key = models.CharField(
        max_length=120,
        unique=True,
        db_index=True,
        verbose_name='Clave de origen',
        help_text='Clave idempotente para evitar duplicados.',
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name='Fecha operativa')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='client_transactions_created',
        verbose_name='Generado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Movimiento de Cuenta Corriente'
        verbose_name_plural = 'Movimientos de Cuenta Corriente'
        ordering = ['occurred_at', 'id']
        indexes = [
            models.Index(fields=['client_profile', 'occurred_at']),
            models.Index(fields=['transaction_type', 'occurred_at']),
            models.Index(fields=['order']),
            models.Index(fields=['payment']),
            models.Index(fields=['company', 'occurred_at']),
        ]

    def __str__(self):
        return f'{self.client_profile.company_name} | {self.transaction_type} | {self.amount}'
