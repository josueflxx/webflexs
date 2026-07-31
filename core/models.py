"""
Core app models - site-wide settings, analytics, and operation logs.
"""
import secrets
import uuid
import logging
from urllib.parse import urlsplit

from django.db import models, transaction
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.text import slugify


FISCAL_DOC_TYPE_FA = "FA"
FISCAL_DOC_TYPE_FB = "FB"
FISCAL_DOC_TYPE_FC = "FC"
FISCAL_DOC_TYPE_NCA = "NCA"
FISCAL_DOC_TYPE_NCB = "NCB"
FISCAL_DOC_TYPE_NCC = "NCC"
FISCAL_DOC_TYPE_NDA = "NDA"
FISCAL_DOC_TYPE_NDB = "NDB"
FISCAL_DOC_TYPE_NDC = "NDC"
FISCAL_DOC_TYPE_CHOICES = [
    (FISCAL_DOC_TYPE_FA, "Factura A"),
    (FISCAL_DOC_TYPE_FB, "Factura B"),
    (FISCAL_DOC_TYPE_FC, "Factura C"),
    (FISCAL_DOC_TYPE_NCA, "Nota de Credito A"),
    (FISCAL_DOC_TYPE_NCB, "Nota de Credito B"),
    (FISCAL_DOC_TYPE_NCC, "Nota de Credito C"),
    (FISCAL_DOC_TYPE_NDA, "Nota de Debito A"),
    (FISCAL_DOC_TYPE_NDB, "Nota de Debito B"),
    (FISCAL_DOC_TYPE_NDC, "Nota de Debito C"),
]
FISCAL_INVOICE_DOC_TYPES = {
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_FC,
}
FISCAL_CREDIT_NOTE_DOC_TYPES = {
    FISCAL_DOC_TYPE_NCA,
    FISCAL_DOC_TYPE_NCB,
    FISCAL_DOC_TYPE_NCC,
}
FISCAL_DEBIT_NOTE_DOC_TYPES = {
    FISCAL_DOC_TYPE_NDA,
    FISCAL_DOC_TYPE_NDB,
    FISCAL_DOC_TYPE_NDC,
}
FISCAL_BILLABLE_DOC_TYPES = set(FISCAL_INVOICE_DOC_TYPES)

FISCAL_ISSUE_MODE_ARCA_WSFE = "arca_wsfe"
FISCAL_ISSUE_MODE_MANUAL = "manual"
FISCAL_ISSUE_MODE_EXTERNAL_SAAS = "external_saas"
FISCAL_ISSUE_MODE_CHOICES = [
    (FISCAL_ISSUE_MODE_ARCA_WSFE, "ARCA WSFE"),
    (FISCAL_ISSUE_MODE_MANUAL, "Manual"),
    (FISCAL_ISSUE_MODE_EXTERNAL_SAAS, "Externo SaaS"),
]

FISCAL_STATUS_DRAFT = "draft"
FISCAL_STATUS_READY_TO_ISSUE = "ready_to_issue"
FISCAL_STATUS_SUBMITTING = "submitting"
FISCAL_STATUS_AUTHORIZED = "authorized"
FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS = "authorized_with_observations"
FISCAL_STATUS_UNCERTAIN = "uncertain"
FISCAL_STATUS_RECOVERY_PENDING = "recovery_pending"
FISCAL_STATUS_RECOVERED_AUTHORIZED = "recovered_authorized"
FISCAL_STATUS_RECOVERED_NOT_FOUND = "recovered_not_found"
FISCAL_STATUS_MANUAL_REVIEW = "manual_review"
FISCAL_STATUS_PENDING_RETRY = "pending_retry"
FISCAL_STATUS_REJECTED = "rejected"
FISCAL_STATUS_VOIDED = "voided"
FISCAL_STATUS_EXTERNAL_RECORDED = "external_recorded"
FISCAL_STATUS_CHOICES = [
    (FISCAL_STATUS_DRAFT, "Borrador"),
    (FISCAL_STATUS_READY_TO_ISSUE, "Listo para emitir"),
    (FISCAL_STATUS_SUBMITTING, "Enviando"),
    (FISCAL_STATUS_AUTHORIZED, "Autorizado"),
    (FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS, "Autorizado con observaciones"),
    (FISCAL_STATUS_UNCERTAIN, "Resultado incierto"),
    (FISCAL_STATUS_RECOVERY_PENDING, "Consulta de recuperacion pendiente"),
    (FISCAL_STATUS_RECOVERED_AUTHORIZED, "Autorizado recuperado"),
    (FISCAL_STATUS_RECOVERED_NOT_FOUND, "No encontrado durante recuperacion"),
    (FISCAL_STATUS_MANUAL_REVIEW, "Revision manual"),
    (FISCAL_STATUS_PENDING_RETRY, "Pendiente reintento (legado)"),
    (FISCAL_STATUS_REJECTED, "Rechazado"),
    (FISCAL_STATUS_VOIDED, "Anulado"),
    (FISCAL_STATUS_EXTERNAL_RECORDED, "Registrado externo"),
]

FISCAL_AUTHORIZED_STATUSES = {
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
    FISCAL_STATUS_RECOVERED_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
}
FISCAL_UNCERTAIN_STATUSES = {
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_UNCERTAIN,
    FISCAL_STATUS_RECOVERY_PENDING,
    FISCAL_STATUS_RECOVERED_NOT_FOUND,
    FISCAL_STATUS_MANUAL_REVIEW,
}
FISCAL_ACTIVE_OPERATION_STATUSES = {
    FISCAL_STATUS_DRAFT,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_UNCERTAIN,
    FISCAL_STATUS_RECOVERY_PENDING,
    FISCAL_STATUS_RECOVERED_NOT_FOUND,
    FISCAL_STATUS_MANUAL_REVIEW,
}

FISCAL_ATTEMPT_RESULT_PENDING = "pending"
FISCAL_ATTEMPT_RESULT_SUCCESS = "success"
FISCAL_ATTEMPT_RESULT_ERROR = "error"
FISCAL_ATTEMPT_RESULT_UNCERTAIN = "uncertain"
FISCAL_ATTEMPT_RESULT_NOT_FOUND = "not_found"
FISCAL_ATTEMPT_RESULT_RECOVERED = "recovered"
FISCAL_ATTEMPT_RESULT_CHOICES = [
    (FISCAL_ATTEMPT_RESULT_PENDING, "Pendiente"),
    (FISCAL_ATTEMPT_RESULT_SUCCESS, "Exitoso"),
    (FISCAL_ATTEMPT_RESULT_ERROR, "Con error"),
    (FISCAL_ATTEMPT_RESULT_UNCERTAIN, "Resultado incierto"),
    (FISCAL_ATTEMPT_RESULT_NOT_FOUND, "No encontrado"),
    (FISCAL_ATTEMPT_RESULT_RECOVERED, "Recuperado"),
]

FISCAL_ATTEMPT_OPERATION_AUTHORIZE = "authorize"
FISCAL_ATTEMPT_OPERATION_RECOVER = "recover"
FISCAL_ATTEMPT_OPERATION_RECONCILE = "reconcile"
FISCAL_ATTEMPT_OPERATION_CHOICES = [
    (FISCAL_ATTEMPT_OPERATION_AUTHORIZE, "Autorizar"),
    (FISCAL_ATTEMPT_OPERATION_RECOVER, "Recuperar por consulta"),
    (FISCAL_ATTEMPT_OPERATION_RECONCILE, "Reconciliar serie"),
]

logger = logging.getLogger(__name__)

SALES_BEHAVIOR_FACTURA = "Factura"
SALES_BEHAVIOR_NOTA_CREDITO = "NotaCredito"
SALES_BEHAVIOR_NOTA_DEBITO = "NotaDebito"
SALES_BEHAVIOR_RECIBO = "Recibo"
SALES_BEHAVIOR_REMITO = "Remito"
SALES_BEHAVIOR_PEDIDO = "Pedido"
SALES_BEHAVIOR_PRESUPUESTO = "Presupuesto"
SALES_BEHAVIOR_COTIZACION = "Cotizacion"
SALES_DOCUMENT_BEHAVIOR_CHOICES = [
    (SALES_BEHAVIOR_FACTURA, "Factura"),
    (SALES_BEHAVIOR_NOTA_CREDITO, "Nota de Credito"),
    (SALES_BEHAVIOR_NOTA_DEBITO, "Nota de Debito"),
    (SALES_BEHAVIOR_RECIBO, "Recibo"),
    (SALES_BEHAVIOR_REMITO, "Remito"),
    (SALES_BEHAVIOR_PEDIDO, "Pedido"),
    (SALES_BEHAVIOR_PRESUPUESTO, "Presupuesto"),
    (SALES_BEHAVIOR_COTIZACION, "Cotizacion"),
]

SALES_BILLING_MODE_INTERNAL_DOCUMENT = "INTERNAL_DOCUMENT"
SALES_BILLING_MODE_AFIP_WSFE = "ELECTRONIC_AFIP_WSFE"
SALES_BILLING_MODE_MANUAL_FISCAL = "MANUAL_FISCAL_RECEIPT"
SALES_BILLING_MODE_AFIP_ONLINE = "AFIP_ONLINE_INVOICE"
SALES_BILLING_MODE_CHOICES = [
    (SALES_BILLING_MODE_INTERNAL_DOCUMENT, "Documento interno"),
    (SALES_BILLING_MODE_AFIP_WSFE, "ARCA WSFE"),
    (SALES_BILLING_MODE_MANUAL_FISCAL, "Comprobante fiscal manual"),
    (SALES_BILLING_MODE_AFIP_ONLINE, "Factura online AFIP"),
]

SALES_DEFAULT_USER_CURRENT = "CURRENT_USER"
SALES_DEFAULT_USER_SPECIFIC = "SPECIFIC_USER"
SALES_DEFAULT_USER_NONE = "UNSPECIFIED"
SALES_DEFAULT_USER_CHOICES = [
    (SALES_DEFAULT_USER_CURRENT, "El usuario que agrega la venta"),
    (SALES_DEFAULT_USER_SPECIFIC, "Usuario/Vendedor especifico"),
    (SALES_DEFAULT_USER_NONE, "Sin especificar"),
]

SALES_PRINT_BASE_DEFAULT = "default"
SALES_PRINT_BASE_COMPACT = "compact"
SALES_PRINT_BASE_EXTENDED = "extended"
SALES_PRINT_BASE_CHOICES = [
    (SALES_PRINT_BASE_DEFAULT, "Predeterminado"),
    (SALES_PRINT_BASE_COMPACT, "Compacto"),
    (SALES_PRINT_BASE_EXTENDED, "Extendido"),
]

SALES_ORIGIN_CATALOG = "catalog"
SALES_ORIGIN_ADMIN = "admin"
SALES_ORIGIN_WHATSAPP = "whatsapp"
SALES_ORIGIN_PHONE = "phone"
SALES_ORIGIN_OTHER = "other"
SALES_DOCUMENT_ORIGIN_CHANNEL_CHOICES = [
    ("", "Todos los canales"),
    (SALES_ORIGIN_CATALOG, "Catalogo"),
    (SALES_ORIGIN_ADMIN, "Admin"),
    (SALES_ORIGIN_WHATSAPP, "WhatsApp"),
    (SALES_ORIGIN_PHONE, "Telefono"),
    (SALES_ORIGIN_OTHER, "Otro"),
]

STOCK_MOVEMENT_IN = "in"
STOCK_MOVEMENT_OUT = "out"
STOCK_MOVEMENT_RESERVE = "reserve"
STOCK_MOVEMENT_RELEASE = "release"
STOCK_MOVEMENT_ADJUSTMENT = "adjustment"
STOCK_MOVEMENT_CHOICES = [
    (STOCK_MOVEMENT_IN, "Ingreso"),
    (STOCK_MOVEMENT_OUT, "Salida"),
    (STOCK_MOVEMENT_RESERVE, "Reserva"),
    (STOCK_MOVEMENT_RELEASE, "Liberacion"),
    (STOCK_MOVEMENT_ADJUSTMENT, "Ajuste"),
]


class Company(models.Model):
    """Legal entity / business unit."""

    TAX_CONDITION_CHOICES = [
        ("responsable_inscripto", "Responsable Inscripto"),
        ("monotributista", "Monotributista"),
        ("exento", "Exento"),
        ("consumidor_final", "Consumidor Final"),
    ]

    name = models.CharField(max_length=80, unique=True, verbose_name="Nombre")
    legal_name = models.CharField(max_length=150, blank=True, verbose_name="Razon social")
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    cuit = models.CharField(max_length=20, blank=True, verbose_name="CUIT")
    email = models.EmailField(blank=True, verbose_name="Email")
    tax_condition = models.CharField(
        max_length=50,
        choices=TAX_CONDITION_CHOICES,
        blank=True,
        verbose_name="Condicion fiscal",
    )
    fiscal_address = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Domicilio fiscal",
    )
    fiscal_city = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Localidad fiscal",
    )
    fiscal_province = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Provincia fiscal",
    )
    postal_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Codigo postal",
    )
    activity_start_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Inicio de actividades",
        help_text="Dato fiscal oficial. No se completa automaticamente.",
    )
    point_of_sale_default = models.CharField(
        max_length=10,
        blank=True,
        verbose_name="Punto de venta (default)",
    )
    default_price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="default_for_companies",
        verbose_name="Lista base",
    )
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.get("raw"):
            previous = type(self).objects.filter(pk=self.pk).only("cuit").first()
            if (
                previous
                and previous.cuit != self.cuit
                and (
                    self.fiscal_documents.exists()
                    or self.fiscal_points_of_sale.filter(fiscal_series__isnull=False).exists()
                )
            ):
                raise ValidationError(
                    "El CUIT de una empresa con operaciones fiscales no puede modificarse."
                )
        if not self.slug:
            base_slug = slugify(self.name) or "empresa"
            candidate = base_slug
            counter = 1
            while Company.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{counter}"
                counter += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class AdminCompanyAccess(models.Model):
    """Optional company scoping for staff users inside the internal admin."""

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="company_access_links",
        verbose_name="Admin",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="admin_access_links",
        verbose_name="Empresa",
    )
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Acceso admin por empresa"
        verbose_name_plural = "Accesos admin por empresa"
        unique_together = [("user", "company")]
        ordering = ["user__username", "company__name"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.company.name}"


class AdminCapabilityProfile(models.Model):
    """Explicit action-level permissions for one internal operator."""

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="admin_capability_profile",
        verbose_name="Operador",
    )
    capabilities = models.JSONField(default=list, blank=True, verbose_name="Capacidades")
    is_configured = models.BooleanField(default=False, verbose_name="Configurado")
    updated_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_capability_profiles_updated",
        verbose_name="Actualizado por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Permisos granulares de operador"
        verbose_name_plural = "Permisos granulares de operadores"

    def __str__(self):
        return f"Permisos de {self.user.username}"


class Warehouse(models.Model):
    """Logical warehouse/deposit per company for document configuration."""

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="warehouses",
        verbose_name="Empresa",
    )
    code = models.SlugField(max_length=40, verbose_name="Codigo")
    name = models.CharField(max_length=80, verbose_name="Nombre")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    stock_balance_enabled = models.BooleanField(
        default=False,
        verbose_name="Usar saldos por deposito",
        help_text="Solo debe activarse despues de inicializar y verificar los saldos de este deposito.",
    )
    notes = models.TextField(blank=True, verbose_name="Notas")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Depositos"
        verbose_name_plural = "Depositos"
        ordering = ["company_id", "name"]
        unique_together = [("company", "code")]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "code"]),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class ProductWarehouseStock(models.Model):
    """Materialized stock balance and thresholds for one product and warehouse."""

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.PROTECT,
        related_name="warehouse_balances",
        verbose_name="Producto",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="product_balances",
        verbose_name="Deposito",
    )
    on_hand = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Stock actual",
    )
    reserved = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Stock reservado",
    )
    minimum = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Stock minimo",
    )
    ideal = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Stock ideal",
    )
    initialized_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Inicializado",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock de producto por deposito"
        verbose_name_plural = "Stocks de productos por deposito"
        ordering = ["warehouse__company_id", "warehouse__name", "product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "warehouse"],
                name="uniq_product_warehouse_stock",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved__gte=0),
                name="product_warehouse_reserved_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum__gte=0),
                name="product_warehouse_minimum_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(ideal__gte=0),
                name="product_warehouse_ideal_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(ideal__gte=models.F("minimum")),
                name="product_warehouse_ideal_gte_minimum",
            ),
        ]
        indexes = [
            models.Index(fields=["warehouse", "product"]),
            models.Index(fields=["product", "updated_at"]),
            models.Index(fields=["warehouse", "on_hand"]),
        ]

    @property
    def available(self):
        return self.on_hand - self.reserved

    @property
    def is_below_minimum(self):
        return self.on_hand < self.minimum

    def clean(self):
        if self.reserved < 0:
            raise ValidationError("El stock reservado no puede ser negativo.")
        if self.minimum < 0 or self.ideal < 0:
            raise ValidationError("Los niveles minimo e ideal no pueden ser negativos.")
        if self.ideal < self.minimum:
            raise ValidationError("El stock ideal no puede ser menor que el minimo.")

    def __str__(self):
        return f"{self.product} - {self.warehouse}: {self.on_hand}"


class FiscalPointOfSale(models.Model):
    """Fiscal point of sale configuration per company."""

    ENV_DISABLED = "disabled"
    ENV_HOMOLOGATION = "homologation"
    ENV_PRODUCTION = "production"
    ENV_CHOICES = [
        (ENV_DISABLED, "Deshabilitado"),
        (ENV_HOMOLOGATION, "Homologacion"),
        (ENV_PRODUCTION, "Produccion"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="fiscal_points_of_sale",
        verbose_name="Empresa",
    )
    number = models.CharField(max_length=6, verbose_name="Punto de venta")
    name = models.CharField(max_length=80, blank=True, verbose_name="Nombre")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    environment = models.CharField(
        max_length=20,
        choices=ENV_CHOICES,
        default=ENV_DISABLED,
        verbose_name="Entorno",
    )
    is_default = models.BooleanField(default=False, verbose_name="Default")
    notes = models.TextField(blank=True, verbose_name="Notas")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Punto de venta fiscal"
        verbose_name_plural = "Puntos de venta fiscales"
        ordering = ["company_id", "number"]
        unique_together = [("company", "number")]
        indexes = [
            models.Index(fields=["company", "is_active"]),
            models.Index(fields=["company", "is_default"]),
        ]

    def clean(self):
        if not self.company_id:
            raise ValidationError("La empresa es obligatoria en el punto de venta fiscal.")
        if not (self.number or "").strip():
            raise ValidationError("El numero de punto de venta es obligatorio.")

    def save(self, *args, **kwargs):
        self.number = (self.number or "").strip()
        self.name = (self.name or "").strip()
        if not kwargs.get("raw"):
            self.clean()
            if self.pk:
                previous = type(self).objects.filter(pk=self.pk).only(
                    "number",
                    "environment",
                ).first()
                if (
                    previous
                    and (
                        previous.number != self.number
                        or previous.environment != self.environment
                    )
                    and (
                        self.fiscal_series.exists()
                        or self.fiscal_documents.exists()
                    )
                ):
                    raise ValidationError(
                        "El numero y el entorno de un punto de venta con historial fiscal son inmutables."
                    )
        super().save(*args, **kwargs)
        if self.is_default:
            FiscalPointOfSale.objects.filter(
                company_id=self.company_id,
                is_default=True,
            ).exclude(pk=self.pk).update(is_default=False)

    def __str__(self):
        return f"{self.company.name} - PV {self.number}"


class DocumentSeries(models.Model):
    """Numbering series per company and document type."""

    DOC_COT = "COT"
    DOC_PED = "PED"
    DOC_REM = "REM"
    DOC_REC = "REC"
    DOC_AJU = "AJU"
    DOC_TYPE_CHOICES = [
        (DOC_COT, "Cotizacion"),
        (DOC_PED, "Pedido"),
        (DOC_REM, "Remito"),
        (DOC_REC, "Recibo"),
        (DOC_AJU, "Ajuste"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="document_series",
        verbose_name="Empresa",
    )
    doc_type = models.CharField(
        max_length=3,
        choices=DOC_TYPE_CHOICES,
        verbose_name="Tipo",
    )
    next_number = models.PositiveIntegerField(default=1, verbose_name="Siguiente numero")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Serie de Documento"
        verbose_name_plural = "Series de Documentos"
        unique_together = [("company", "doc_type")]
        indexes = [
            models.Index(fields=["company", "doc_type"]),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.doc_type} ({self.next_number})"


class SalesDocumentType(models.Model):
    """Configurable commercial document type that drives generation rules."""

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="sales_document_types",
        verbose_name="Empresa",
    )
    code = models.SlugField(max_length=40, verbose_name="Codigo")
    name = models.CharField(max_length=80, verbose_name="Nombre")
    letter = models.CharField(max_length=4, blank=True, verbose_name="Letra")
    point_of_sale = models.ForeignKey(
        "core.FiscalPointOfSale",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_document_types",
        verbose_name="Punto de venta",
    )
    last_number = models.PositiveIntegerField(default=0, verbose_name="Ultimo numero")
    enabled = models.BooleanField(default=True, verbose_name="Habilitado")
    document_behavior = models.CharField(
        max_length=24,
        choices=SALES_DOCUMENT_BEHAVIOR_CHOICES,
        verbose_name="Tipo de comprobante",
    )
    generate_stock_movement = models.BooleanField(default=False, verbose_name="Genera movimiento de stock")
    generate_account_movement = models.BooleanField(default=False, verbose_name="Genera movimiento de cuenta")
    group_equal_products = models.BooleanField(default=True, verbose_name="Agrupa productos iguales")
    default_warehouse = models.ForeignKey(
        "core.Warehouse",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales_document_types",
        verbose_name="Deposito predeterminado",
    )
    prioritize_default_warehouse = models.BooleanField(default=True, verbose_name="Priorizar deposito predeterminado")
    default_sales_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_sales_document_types",
        verbose_name="Vendedor predeterminado",
    )
    default_sales_user_mode = models.CharField(
        max_length=20,
        choices=SALES_DEFAULT_USER_CHOICES,
        default=SALES_DEFAULT_USER_CURRENT,
        verbose_name="Modo vendedor predeterminado",
    )
    billing_mode = models.CharField(
        max_length=32,
        choices=SALES_BILLING_MODE_CHOICES,
        default=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
        verbose_name="Modo de facturacion",
    )
    use_document_situation = models.BooleanField(default=False, verbose_name="Usa funcionalidad de situacion")
    internal_doc_type = models.CharField(
        max_length=3,
        blank=True,
        choices=DocumentSeries.DOC_TYPE_CHOICES,
        verbose_name="Tipo interno asociado",
    )
    fiscal_doc_type = models.CharField(
        max_length=3,
        blank=True,
        choices=FISCAL_DOC_TYPE_CHOICES,
        verbose_name="Tipo fiscal asociado",
    )
    print_address = models.CharField(max_length=180, blank=True, verbose_name="Domicilio personalizado")
    print_email = models.EmailField(blank=True, verbose_name="Email personalizado")
    print_phones = models.CharField(max_length=120, blank=True, verbose_name="Telefonos personalizados")
    print_locality = models.CharField(max_length=120, blank=True, verbose_name="Localidad personalizada")
    print_signature = models.TextField(blank=True, verbose_name="Firma personalizada")
    base_design = models.CharField(
        max_length=20,
        choices=SALES_PRINT_BASE_CHOICES,
        default=SALES_PRINT_BASE_DEFAULT,
        verbose_name="Diseno base",
    )
    notes = models.TextField(blank=True, verbose_name="Observaciones")
    is_default = models.BooleanField(default=False, verbose_name="Predeterminado")
    display_order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    default_origin_channel = models.CharField(
        max_length=20,
        choices=SALES_DOCUMENT_ORIGIN_CHANNEL_CHOICES,
        blank=True,
        default="",
        verbose_name="Canal default",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tipo de documento comercial"
        verbose_name_plural = "Tipos de documentos comerciales"
        ordering = ["company_id", "display_order", "name"]
        indexes = [
            models.Index(fields=["company", "enabled"]),
            models.Index(fields=["company", "document_behavior"]),
            models.Index(fields=["company", "billing_mode"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_sales_document_type_company_code",
            ),
            models.UniqueConstraint(
                fields=["company", "document_behavior", "default_origin_channel"],
                condition=models.Q(is_default=True),
                name="uniq_default_sales_document_type_per_behavior",
            ),
        ]

    def clean(self):
        if self.point_of_sale_id and self.point_of_sale.company_id != self.company_id:
            raise ValidationError("El punto de venta no pertenece a la empresa del tipo de documento.")
        if self.default_warehouse_id and self.default_warehouse.company_id != self.company_id:
            raise ValidationError("El deposito no pertenece a la empresa del tipo de documento.")
        if self.default_sales_user_mode == SALES_DEFAULT_USER_SPECIFIC and not self.default_sales_user_id:
            raise ValidationError("Debes seleccionar un usuario vendedor cuando el modo es especifico.")
        if self.default_sales_user_mode != SALES_DEFAULT_USER_SPECIFIC:
            self.default_sales_user = None
        if self.document_behavior in {
            SALES_BEHAVIOR_FACTURA,
            SALES_BEHAVIOR_NOTA_CREDITO,
            SALES_BEHAVIOR_NOTA_DEBITO,
        }:
            if self.billing_mode == SALES_BILLING_MODE_INTERNAL_DOCUMENT and not self.internal_doc_type:
                raise ValidationError("Debes definir un tipo interno si el modo de facturacion es interno.")
            if self.billing_mode != SALES_BILLING_MODE_INTERNAL_DOCUMENT and not self.fiscal_doc_type:
                raise ValidationError("Debes definir un tipo fiscal para documentos con facturacion fiscal.")
        if self.internal_doc_type and self.billing_mode != SALES_BILLING_MODE_INTERNAL_DOCUMENT:
            # Allow storing the mapping for print/compatibility without blocking.
            pass

    def save(self, *args, **kwargs):
        if not self.code:
            base_code = slugify(self.name) or "tipo-documento"
            candidate = base_code
            counter = 1
            while SalesDocumentType.objects.filter(company=self.company, code=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_code}-{counter}"
                counter += 1
            self.code = candidate
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)

    @property
    def point_of_sale_number(self):
        return getattr(self.point_of_sale, "number", "") or ""

    def format_number(self, number=None):
        seq = int(number if number is not None else (self.last_number or 0))
        sequence = str(seq).zfill(8)
        point = str(self.point_of_sale_number or "").strip()
        letter = str(self.letter or "").strip()
        if point:
            prefix = f"{letter}{point.zfill(5)}"
            return f"{prefix}-{sequence}"
        if letter:
            return f"{letter}-{sequence}"
        return sequence

    def __str__(self):
        return f"{self.company.name} - {self.name}"

    @property
    def default_sales_user_label(self):
        if self.default_sales_user_mode == SALES_DEFAULT_USER_CURRENT:
            return "El usuario que agrega la venta"
        if self.default_sales_user_mode == SALES_DEFAULT_USER_NONE:
            return "Sin especificar"
        if self.default_sales_user_id:
            return self.default_sales_user.get_username()
        return "Sin especificar"

    @property
    def default_origin_channel_label(self):
        return dict(SALES_DOCUMENT_ORIGIN_CHANNEL_CHOICES).get(
            self.default_origin_channel or "",
            "Todos los canales",
        )


class FiscalDocumentSeries(models.Model):
    """Fiscal numbering series per company and point of sale."""

    DOC_FA = FISCAL_DOC_TYPE_FA
    DOC_FB = FISCAL_DOC_TYPE_FB
    DOC_FC = FISCAL_DOC_TYPE_FC
    DOC_NCA = FISCAL_DOC_TYPE_NCA
    DOC_NCB = FISCAL_DOC_TYPE_NCB
    DOC_NCC = FISCAL_DOC_TYPE_NCC
    DOC_NDA = FISCAL_DOC_TYPE_NDA
    DOC_NDB = FISCAL_DOC_TYPE_NDB
    DOC_NDC = FISCAL_DOC_TYPE_NDC
    DOC_TYPE_CHOICES = FISCAL_DOC_TYPE_CHOICES

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="fiscal_series",
        verbose_name="Empresa",
    )
    point_of_sale_ref = models.ForeignKey(
        "core.FiscalPointOfSale",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fiscal_series",
        verbose_name="Punto de venta",
    )
    point_of_sale = models.CharField(
        max_length=6,
        blank=True,
        verbose_name="Punto de venta",
        help_text="Campo legacy temporal. Usar punto_de_venta (FK).",
    )
    doc_type = models.CharField(
        max_length=3,
        choices=DOC_TYPE_CHOICES,
        verbose_name="Tipo",
    )
    issuer_cuit = models.CharField(
        max_length=11,
        blank=True,
        db_index=True,
        verbose_name="CUIT emisor congelado",
    )
    environment = models.CharField(
        max_length=20,
        choices=FiscalPointOfSale.ENV_CHOICES,
        default=FiscalPointOfSale.ENV_DISABLED,
        db_index=True,
        verbose_name="Entorno fiscal",
    )
    next_number = models.PositiveIntegerField(default=1, verbose_name="Siguiente numero")
    remote_last_authorized = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Ultimo autorizado remoto",
    )
    last_reconciled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Ultima reconciliacion",
    )
    blocked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Serie bloqueada desde",
    )
    blocked_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Motivo de bloqueo",
    )
    blocked_by_document = models.ForeignKey(
        "core.FiscalDocument",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="blocked_fiscal_series",
        verbose_name="Documento que bloquea la serie",
    )
    version = models.PositiveIntegerField(default=1, verbose_name="Version de concurrencia")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Serie Fiscal"
        verbose_name_plural = "Series Fiscales"
        unique_together = [("point_of_sale_ref", "doc_type")]
        indexes = [
            models.Index(fields=["point_of_sale_ref", "doc_type"]),
            models.Index(fields=["issuer_cuit", "environment", "point_of_sale_ref", "doc_type"]),
            models.Index(fields=["blocked_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["issuer_cuit", "environment", "point_of_sale", "doc_type"],
                condition=~models.Q(issuer_cuit="") & ~models.Q(point_of_sale=""),
                name="uniq_fiscal_series_identity",
            ),
        ]

    def __str__(self):
        pv = self.point_of_sale_ref.number if self.point_of_sale_ref_id else (self.point_of_sale or "-")
        return f"{self.company.name} - {pv} - {self.doc_type} ({self.next_number})"

    def clean(self):
        if not self.company_id:
            raise ValidationError("La empresa es obligatoria en la serie fiscal.")
        if self.point_of_sale_ref_id:
            if self.point_of_sale_ref.company_id != self.company_id:
                raise ValidationError("La empresa de la serie no coincide con el punto de venta fiscal.")
            self.point_of_sale = self.point_of_sale_ref.number
            if self.environment != self.point_of_sale_ref.environment:
                raise ValidationError("El entorno de la serie no coincide con el punto de venta fiscal.")
        elif not (self.point_of_sale or "").strip():
            raise ValidationError("Debe definirse un punto de venta fiscal.")
        normalized_cuit = "".join(char for char in str(self.issuer_cuit or "") if char.isdigit())
        if normalized_cuit and len(normalized_cuit) != 11:
            raise ValidationError("El CUIT emisor de la serie debe tener 11 digitos.")
        self.issuer_cuit = normalized_cuit

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)


FISCAL_DOCUMENT_PROTECTED_FIELDS = {
    "source_key",
    "idempotency_key",
    "correlation_id",
    "company",
    "company_id",
    "client_company_ref",
    "client_company_ref_id",
    "client_profile",
    "client_profile_id",
    "order",
    "order_id",
    "internal_document",
    "internal_document_id",
    "related_document",
    "related_document_id",
    "point_of_sale",
    "point_of_sale_id",
    "doc_type",
    "issue_mode",
    "number",
    "issued_at",
    "payment_due_date",
    "cae",
    "cae_due_date",
    "subtotal_net",
    "discount_total",
    "tax_total",
    "total",
    "currency",
    "exchange_rate",
    "sales_document_type",
    "sales_document_type_id",
    "series",
    "series_id",
    "fiscal_snapshot",
    "snapshot_hash",
    "snapshot_schema_version",
    "prepared_at",
    "resolved_at",
    "payload_hash",
    "request_payload",
    "response_payload",
    "issuer_cuit_snapshot",
    "environment_snapshot",
    "point_of_sale_number_snapshot",
    "receiver_iva_condition_id_snapshot",
    "receiver_iva_condition_label_snapshot",
    "receiver_iva_condition_source_snapshot",
    "receiver_iva_condition_validated_at_snapshot",
}


class FiscalDocumentQuerySet(models.QuerySet):
    """Prevent bulk operations from bypassing fiscal immutability."""

    def update(self, **kwargs):
        protected = FISCAL_DOCUMENT_PROTECTED_FIELDS.intersection(kwargs)
        if protected and self.exclude(status=FISCAL_STATUS_DRAFT).exists():
            logger.warning(
                "Rejected bulk mutation of protected fiscal fields: %s",
                sorted(protected),
            )
            raise ValidationError("No se pueden modificar campos fiscales protegidos en bloque.")
        if "status" in kwargs:
            raise ValidationError("Los estados fiscales deben cambiar mediante una transicion de dominio.")
        return super().update(**kwargs)

    def delete(self):
        protected = self.exclude(status=FISCAL_STATUS_DRAFT)
        if protected.exists():
            logger.warning(
                "Rejected bulk deletion of authorized fiscal documents; count=%s",
                protected.count(),
            )
            raise ProtectedError(
                "Los comprobantes fiscales autorizados no pueden eliminarse.",
                list(protected[:20]),
            )
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        if "status" in fields or FISCAL_DOCUMENT_PROTECTED_FIELDS.intersection(fields):
            raise ValidationError(
                "Los campos fiscales protegidos no pueden modificarse mediante bulk_update."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)


class FiscalDocument(models.Model):
    """Fiscal document record, separated from internal documents."""

    DOC_TYPE_CHOICES = FISCAL_DOC_TYPE_CHOICES
    ISSUE_MODE_CHOICES = FISCAL_ISSUE_MODE_CHOICES
    STATUS_CHOICES = FISCAL_STATUS_CHOICES
    AUTHORIZED_STATUSES = FISCAL_AUTHORIZED_STATUSES
    ALLOWED_STATUS_TRANSITIONS = {
        FISCAL_STATUS_DRAFT: {
            FISCAL_STATUS_READY_TO_ISSUE,
            FISCAL_STATUS_VOIDED,
        },
        FISCAL_STATUS_READY_TO_ISSUE: {
            FISCAL_STATUS_SUBMITTING,
            FISCAL_STATUS_RECOVERY_PENDING,
            FISCAL_STATUS_MANUAL_REVIEW,
            FISCAL_STATUS_EXTERNAL_RECORDED,
            FISCAL_STATUS_VOIDED,
        },
        FISCAL_STATUS_SUBMITTING: {
            FISCAL_STATUS_READY_TO_ISSUE,
            FISCAL_STATUS_AUTHORIZED,
            FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
            FISCAL_STATUS_REJECTED,
            FISCAL_STATUS_UNCERTAIN,
            FISCAL_STATUS_MANUAL_REVIEW,
        },
        FISCAL_STATUS_PENDING_RETRY: {
            FISCAL_STATUS_UNCERTAIN,
            FISCAL_STATUS_MANUAL_REVIEW,
        },
        FISCAL_STATUS_UNCERTAIN: {
            FISCAL_STATUS_RECOVERY_PENDING,
            FISCAL_STATUS_MANUAL_REVIEW,
        },
        FISCAL_STATUS_RECOVERY_PENDING: {
            FISCAL_STATUS_RECOVERED_AUTHORIZED,
            FISCAL_STATUS_RECOVERED_NOT_FOUND,
            FISCAL_STATUS_MANUAL_REVIEW,
        },
        FISCAL_STATUS_RECOVERED_NOT_FOUND: {
            FISCAL_STATUS_RECOVERY_PENDING,
            FISCAL_STATUS_MANUAL_REVIEW,
        },
        FISCAL_STATUS_REJECTED: {
            FISCAL_STATUS_MANUAL_REVIEW,
            FISCAL_STATUS_VOIDED,
        },
        FISCAL_STATUS_MANUAL_REVIEW: {
            FISCAL_STATUS_READY_TO_ISSUE,
            FISCAL_STATUS_RECOVERY_PENDING,
            FISCAL_STATUS_VOIDED,
        },
    }

    source_key = models.CharField(
        max_length=160,
        unique=True,
        db_index=True,
        verbose_name="Clave de origen",
    )
    idempotency_key = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="Clave de idempotencia",
    )
    correlation_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="ID de correlacion",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="fiscal_documents",
        verbose_name="Empresa",
    )
    client_company_ref = models.ForeignKey(
        "accounts.ClientCompany",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_documents",
        verbose_name="Cliente empresa",
    )
    client_profile = models.ForeignKey(
        "accounts.ClientProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_documents",
        verbose_name="Cliente",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_documents",
        verbose_name="Pedido",
    )
    internal_document = models.ForeignKey(
        "core.InternalDocument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_documents",
        verbose_name="Documento interno",
    )
    related_document = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_notes",
        verbose_name="Documento relacionado",
    )
    point_of_sale = models.ForeignKey(
        "core.FiscalPointOfSale",
        on_delete=models.PROTECT,
        related_name="fiscal_documents",
        verbose_name="Punto de venta fiscal",
    )
    series = models.ForeignKey(
        "core.FiscalDocumentSeries",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fiscal_documents",
        verbose_name="Serie fiscal congelada",
    )
    doc_type = models.CharField(
        max_length=3,
        choices=DOC_TYPE_CHOICES,
        verbose_name="Tipo fiscal",
    )
    issue_mode = models.CharField(
        max_length=20,
        choices=ISSUE_MODE_CHOICES,
        default=FISCAL_ISSUE_MODE_ARCA_WSFE,
        verbose_name="Modo de emision",
    )
    number = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Numero fiscal",
    )
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=FISCAL_STATUS_DRAFT,
        verbose_name="Estado fiscal",
        db_index=True,
    )
    dispatch_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Solicitud de despacho encolada",
    )
    authorization_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Inicio de autorizacion remota",
    )
    recovery_attempts_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Consultas de recuperacion",
    )
    last_recovery_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Ultima consulta de recuperacion",
    )
    next_recovery_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Proxima consulta de recuperacion",
    )
    issued_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de emision",
    )
    payment_due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Vencimiento de cobro",
    )
    cae = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="CAE",
    )
    cae_due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Vencimiento CAE",
    )
    subtotal_net = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Subtotal neto")
    discount_total = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Descuento")
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="IVA")
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Total")
    currency = models.CharField(max_length=3, default="ARS", verbose_name="Moneda")
    exchange_rate = models.DecimalField(max_digits=14, decimal_places=6, default=1, verbose_name="Tipo de cambio")
    external_system = models.CharField(max_length=20, blank=True, default="", verbose_name="Sistema externo")
    external_id = models.CharField(max_length=80, blank=True, default="", verbose_name="ID externo")
    external_number = models.CharField(max_length=80, blank=True, default="", verbose_name="Numero externo")
    sales_document_type = models.ForeignKey(
        "core.SalesDocumentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_documents",
        verbose_name="Tipo de documento comercial",
    )
    fiscal_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Snapshot fiscal inmutable",
    )
    snapshot_schema_version = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="Version del snapshot fiscal",
    )
    snapshot_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Hash SHA-256 del snapshot fiscal",
    )
    prepared_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Snapshot fiscal preparado",
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Operacion fiscal resuelta",
    )
    payload_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Hash SHA-256 del payload fiscal",
    )
    issuer_cuit_snapshot = models.CharField(
        max_length=11,
        blank=True,
        default="",
        verbose_name="CUIT emisor usado",
    )
    environment_snapshot = models.CharField(
        max_length=20,
        choices=FiscalPointOfSale.ENV_CHOICES,
        default=FiscalPointOfSale.ENV_DISABLED,
        verbose_name="Entorno usado",
    )
    point_of_sale_number_snapshot = models.CharField(
        max_length=6,
        blank=True,
        default="",
        verbose_name="Punto de venta usado",
    )
    receiver_iva_condition_id_snapshot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Condicion IVA receptor usada (ARCA ID)",
    )
    receiver_iva_condition_label_snapshot = models.CharField(
        max_length=120,
        blank=True,
        default="",
        verbose_name="Condicion IVA receptor usada",
    )
    receiver_iva_condition_source_snapshot = models.CharField(
        max_length=40,
        blank=True,
        default="",
        verbose_name="Fuente condicion IVA receptor",
    )
    receiver_iva_condition_validated_at_snapshot = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Validacion condicion IVA receptor usada",
    )
    request_payload = models.JSONField(default=dict, blank=True, verbose_name="Request payload")
    response_payload = models.JSONField(default=dict, blank=True, verbose_name="Response payload")
    error_code = models.CharField(max_length=80, blank=True, default="", verbose_name="Codigo error")
    error_message = models.TextField(blank=True, default="", verbose_name="Mensaje error")
    attempts_count = models.PositiveIntegerField(default=0, verbose_name="Intentos")
    last_attempt_at = models.DateTimeField(null=True, blank=True, verbose_name="Ultimo intento")
    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Proximo reintento sugerido",
    )
    email_last_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Ultimo envio por email",
    )
    email_last_recipient = models.EmailField(
        blank=True,
        default="",
        verbose_name="Ultimo destinatario email",
    )
    email_last_error = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Ultimo error de email",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FiscalDocumentQuerySet.as_manager()

    class Meta:
        verbose_name = "Documento Fiscal"
        verbose_name_plural = "Documentos Fiscales"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
            models.Index(fields=["company", "doc_type", "created_at"]),
            models.Index(fields=["point_of_sale", "doc_type", "number"]),
            models.Index(fields=["external_system", "external_id"]),
            models.Index(fields=["environment_snapshot", "issuer_cuit_snapshot", "point_of_sale_number_snapshot", "doc_type"]),
            models.Index(fields=["status", "next_recovery_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "point_of_sale", "doc_type", "number"],
                condition=models.Q(number__isnull=False),
                name="uniq_fiscal_doc_company_pos_type_number",
            ),
            models.UniqueConstraint(
                fields=[
                    "environment_snapshot",
                    "issuer_cuit_snapshot",
                    "point_of_sale_number_snapshot",
                    "doc_type",
                    "number",
                ],
                condition=(
                    models.Q(number__isnull=False)
                    & ~models.Q(issuer_cuit_snapshot="")
                    & ~models.Q(point_of_sale_number_snapshot="")
                ),
                name="uniq_fiscal_doc_identity_number",
            ),
            models.UniqueConstraint(
                fields=["company", "order"],
                condition=(
                    models.Q(order__isnull=False)
                    & models.Q(issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE)
                    & models.Q(
                        status__in=tuple(
                            sorted(FISCAL_ACTIVE_OPERATION_STATUSES)
                        )
                    )
                ),
                name="uniq_active_arca_operation_per_order",
            ),
        ]

    def clean(self):
        if not self.company_id:
            raise ValidationError("La empresa es obligatoria en el documento fiscal.")
        if not self.point_of_sale_id:
            raise ValidationError("El punto de venta fiscal es obligatorio.")
        if self.point_of_sale_id and self.point_of_sale.company_id != self.company_id:
            raise ValidationError("La empresa del documento fiscal no coincide con el punto de venta.")
        if self.client_company_ref_id and self.client_company_ref.company_id != self.company_id:
            raise ValidationError("La empresa del documento fiscal no coincide con el cliente empresa.")
        if (
            self.environment_snapshot
            and self.environment_snapshot != FiscalPointOfSale.ENV_DISABLED
            and self.point_of_sale_id
        ):
            if self.environment_snapshot != self.point_of_sale.environment:
                raise ValidationError("El entorno congelado no coincide con el punto de venta fiscal.")
        if self.point_of_sale_number_snapshot and self.point_of_sale_id:
            if self.point_of_sale_number_snapshot != self.point_of_sale.number:
                raise ValidationError("El punto de venta congelado no coincide con la configuracion.")
        normalized_cuit = "".join(
            char for char in str(self.issuer_cuit_snapshot or "") if char.isdigit()
        )
        if normalized_cuit and len(normalized_cuit) != 11:
            raise ValidationError("El CUIT emisor congelado debe tener 11 digitos.")
        self.issuer_cuit_snapshot = normalized_cuit

    def save(self, *args, **kwargs):
        allow_transition = bool(kwargs.pop("allow_fiscal_transition", False))
        if not kwargs.get("raw"):
            self.clean()
        if self.pk and not kwargs.get("raw"):
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous:
                changed_protected = []
                for field_name in FISCAL_DOCUMENT_PROTECTED_FIELDS:
                    attr_name = field_name
                    if field_name.endswith("_id"):
                        attr_name = field_name
                    if not hasattr(previous, attr_name) or not hasattr(self, attr_name):
                        continue
                    if getattr(previous, attr_name) != getattr(self, attr_name):
                        changed_protected.append(field_name)

                if previous.status in FISCAL_AUTHORIZED_STATUSES and changed_protected:
                    self._record_rejected_mutation(
                        action="update",
                        fields=changed_protected,
                        reason="authorized_document_immutable",
                    )
                    raise ValidationError(
                        "Un comprobante fiscal autorizado es inmutable; emita un documento relacionado."
                    )

                if previous.status != self.status:
                    allowed = self.ALLOWED_STATUS_TRANSITIONS.get(previous.status, set())
                    if not allow_transition or self.status not in allowed:
                        self._record_rejected_mutation(
                            action="status_transition",
                            fields=["status"],
                            reason=f"invalid_transition:{previous.status}->{self.status}",
                        )
                        raise ValidationError(
                            f"Transicion fiscal no permitida: {previous.status} -> {self.status}."
                        )

                payload_locked = previous.status != FISCAL_STATUS_DRAFT
                if payload_locked and changed_protected and not allow_transition:
                    self._record_rejected_mutation(
                        action="update",
                        fields=changed_protected,
                        reason="fiscal_payload_locked",
                    )
                    raise ValidationError(
                        "El payload fiscal no puede modificarse despues de iniciar su procesamiento."
                    )
        super().save(*args, **kwargs)

    def transition_to(self, status, *, update_fields=None, **changes):
        """Apply one explicit, auditable fiscal state transition."""
        for field_name, value in changes.items():
            setattr(self, field_name, value)
        self.status = status
        fields = set(update_fields or changes.keys())
        fields.update({"status", "updated_at"})
        self.save(
            update_fields=sorted(fields),
            allow_fiscal_transition=True,
        )
        return self

    def _record_rejected_mutation(self, *, action, fields, reason):
        logger.warning(
            "Rejected fiscal mutation document=%s action=%s fields=%s reason=%s",
            self.pk,
            action,
            sorted(set(fields or [])),
            reason,
        )
        try:
            FiscalMutationAudit.objects.create(
                fiscal_document=self,
                action=action,
                attempted_fields=sorted(set(fields or [])),
                reason=reason,
                correlation_id=self.correlation_id,
            )
        except Exception:
            logger.debug("Could not persist rejected fiscal mutation audit", exc_info=True)

    def delete(self, *args, **kwargs):
        if self.status != FISCAL_STATUS_DRAFT:
            self._record_rejected_mutation(
                action="delete",
                fields=[],
                reason="authorized_document_delete_blocked",
            )
            raise ProtectedError(
                "Las operaciones fiscales que salieron de borrador no pueden eliminarse.",
                [self],
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        number_text = self.number if self.number is not None else "-"
        return f"{self.doc_type} {self.point_of_sale.number}-{number_text} ({self.company.name})"

    @property
    def commercial_type_label(self):
        if self.sales_document_type_id:
            return self.sales_document_type.name
        return self.get_doc_type_display()

    @property
    def display_number(self):
        if self.number is None:
            return self.external_number or "-"
        if self.sales_document_type_id:
            return self.sales_document_type.format_number(number=self.number)
        point = getattr(self.point_of_sale, "number", "") or ""
        if point:
            return f"{str(point).zfill(5)}-{str(self.number).zfill(8)}"
        return str(self.number).zfill(8)

    @property
    def can_retry_now(self):
        return False

    @property
    def can_recover_now(self):
        if self.status not in {
            FISCAL_STATUS_UNCERTAIN,
            FISCAL_STATUS_RECOVERY_PENDING,
            FISCAL_STATUS_RECOVERED_NOT_FOUND,
            FISCAL_STATUS_MANUAL_REVIEW,
        }:
            return False
        if not self.next_recovery_at:
            return True
        return self.next_recovery_at <= timezone.now()


class FiscalDocumentItemQuerySet(models.QuerySet):
    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        document_ids = {
            item.fiscal_document_id
            for item in objs
            if getattr(item, "fiscal_document_id", None)
        }
        if document_ids and FiscalDocument.objects.filter(
            pk__in=document_ids
        ).exclude(status=FISCAL_STATUS_DRAFT).exists():
            raise ValidationError(
                "Los items sólo pueden agregarse mientras el comprobante está en borrador."
            )
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def update(self, **kwargs):
        if self.exclude(
            fiscal_document__status=FISCAL_STATUS_DRAFT
        ).exists():
            raise ValidationError("Los items fiscales ya procesados son inmutables.")
        return super().update(**kwargs)

    def delete(self):
        if self.exclude(
            fiscal_document__status=FISCAL_STATUS_DRAFT
        ).exists():
            raise ProtectedError("Los items fiscales ya procesados no pueden eliminarse.", list(self[:20]))
        return super().delete()

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Los items fiscales no pueden modificarse mediante bulk_update.")


class FiscalDocumentItem(models.Model):
    """Fiscal document item snapshot for audit/reporting."""

    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name="Documento fiscal",
    )
    line_number = models.PositiveIntegerField(verbose_name="Linea")
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_document_items",
        verbose_name="Producto",
    )
    sku = models.CharField(max_length=80, blank=True, verbose_name="SKU")
    description = models.CharField(max_length=255, verbose_name="Descripcion")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name="Cantidad")
    unit_price_net = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Unitario neto")
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Descuento (%)")
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Monto descuento")
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Neto")
    iva_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Alicuota IVA (%)")
    arca_iva_id = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Identificador de alicuota ARCA",
    )
    tax_treatment = models.CharField(
        max_length=20,
        choices=[
            ("taxed", "Gravado"),
            ("exempt", "Exento"),
            ("non_taxed", "No gravado"),
        ],
        default="taxed",
        verbose_name="Tratamiento fiscal",
    )
    iva_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="IVA")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Total")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FiscalDocumentItemQuerySet.as_manager()

    class Meta:
        verbose_name = "Item de Documento Fiscal"
        verbose_name_plural = "Items de Documentos Fiscales"
        ordering = ["fiscal_document_id", "line_number"]
        unique_together = [("fiscal_document", "line_number")]
        indexes = [
            models.Index(fields=["fiscal_document", "line_number"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.fiscal_document_id} - linea {self.line_number}"

    def _assert_mutable(self):
        if self.fiscal_document.status != FISCAL_STATUS_DRAFT:
            raise ValidationError("El item pertenece a un comprobante fiscal inmutable.")

    def save(self, *args, **kwargs):
        if self.fiscal_document_id:
            self._assert_mutable()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._assert_mutable()
        return super().delete(*args, **kwargs)


class FiscalEmissionAttemptQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Los intentos fiscales son append-only.")

    def delete(self):
        raise ProtectedError("Los intentos fiscales no pueden eliminarse.", list(self[:20]))


class FiscalEmissionAttempt(models.Model):
    """One request/response attempt against fiscal backend."""

    RESULT_STATUS_CHOICES = FISCAL_ATTEMPT_RESULT_CHOICES

    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="emission_attempts",
        verbose_name="Documento fiscal",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_emission_attempts",
        verbose_name="Ejecutado por",
    )
    request_payload = models.JSONField(default=dict, blank=True, verbose_name="Request payload")
    response_payload = models.JSONField(default=dict, blank=True, verbose_name="Response payload")
    operation = models.CharField(
        max_length=20,
        choices=FISCAL_ATTEMPT_OPERATION_CHOICES,
        default=FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
        verbose_name="Operacion",
    )
    correlation_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        verbose_name="ID de correlacion",
    )
    payload_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Hash del payload",
    )
    request_may_have_been_sent = models.BooleanField(
        default=False,
        verbose_name="La solicitud pudo haber sido enviada",
    )
    issuer_cuit = models.CharField(
        max_length=11,
        blank=True,
        default="",
        verbose_name="CUIT emisor intentado",
    )
    environment = models.CharField(
        max_length=20,
        choices=FiscalPointOfSale.ENV_CHOICES,
        default=FiscalPointOfSale.ENV_DISABLED,
        verbose_name="Entorno intentado",
    )
    point_of_sale = models.CharField(
        max_length=6,
        blank=True,
        default="",
        verbose_name="Punto de venta intentado",
    )
    doc_type = models.CharField(
        max_length=3,
        choices=FISCAL_DOC_TYPE_CHOICES,
        blank=True,
        default="",
        verbose_name="Tipo de comprobante intentado",
    )
    attempted_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Numero intentado",
    )
    attempt_number = models.PositiveIntegerField(default=1, verbose_name="Numero de intento")
    duration_ms = models.PositiveIntegerField(null=True, blank=True, verbose_name="Duracion (ms)")
    will_retry = models.BooleanField(default=False, verbose_name="Permite reintento")
    result_status = models.CharField(
        max_length=20,
        choices=RESULT_STATUS_CHOICES,
        default=FISCAL_ATTEMPT_RESULT_PENDING,
        verbose_name="Resultado",
    )
    error_code = models.CharField(max_length=80, blank=True, default="", verbose_name="Codigo error")
    error_message = models.TextField(blank=True, default="", verbose_name="Mensaje error")
    dispatched_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Despacho remoto iniciado",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Intento finalizado",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FiscalEmissionAttemptQuerySet.as_manager()

    class Meta:
        verbose_name = "Intento de Emision Fiscal"
        verbose_name_plural = "Intentos de Emision Fiscal"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["fiscal_document", "created_at"]),
            models.Index(fields=["result_status", "created_at"]),
            models.Index(fields=["fiscal_document", "attempt_number"]),
            models.Index(fields=["correlation_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["fiscal_document", "operation", "attempt_number"],
                name="uniq_fiscal_attempt_operation_number",
            ),
        ]

    def __str__(self):
        return f"Intento {self.fiscal_document_id} #{self.attempt_number} - {self.result_status}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = {
                "fiscal_document_id",
                "triggered_by_id",
                "request_payload",
                "operation",
                "correlation_id",
                "payload_hash",
                "attempt_number",
                "issuer_cuit",
                "environment",
                "point_of_sale",
                "doc_type",
                "attempted_number",
                "created_at",
            }
            changed_immutable = [
                field_name
                for field_name in immutable_fields
                if getattr(previous, field_name) != getattr(self, field_name)
            ]
            if changed_immutable:
                raise ValidationError(
                    "La identidad y el request de un intento fiscal son inmutables."
                )
            if previous.result_status != FISCAL_ATTEMPT_RESULT_PENDING:
                raise ValidationError("Un intento fiscal finalizado es inmutable.")
            if (
                previous.request_may_have_been_sent
                and not self.request_may_have_been_sent
            ):
                raise ValidationError("No puede revertirse la marca de despacho fiscal.")
            if (
                self.result_status != FISCAL_ATTEMPT_RESULT_PENDING
                and not self.completed_at
            ):
                raise ValidationError("Debe registrarse la fecha de finalizacion del intento.")
        super().save(*args, **kwargs)

    def mark_dispatched(self):
        if self.result_status != FISCAL_ATTEMPT_RESULT_PENDING:
            raise ValidationError("El intento fiscal ya fue finalizado.")
        self.request_may_have_been_sent = True
        self.dispatched_at = timezone.now()
        self.save(update_fields=["request_may_have_been_sent", "dispatched_at"])
        return self

    def finalize(
        self,
        *,
        result_status,
        response_payload=None,
        duration_ms=None,
        error_code="",
        error_message="",
    ):
        if result_status == FISCAL_ATTEMPT_RESULT_PENDING:
            raise ValidationError("La finalizacion requiere un resultado terminal.")
        self.result_status = result_status
        self.response_payload = response_payload or {}
        self.duration_ms = duration_ms
        self.will_retry = False
        self.error_code = error_code or ""
        self.error_message = error_message or ""
        self.completed_at = timezone.now()
        self.save(
            update_fields=[
                "result_status",
                "response_payload",
                "duration_ms",
                "will_retry",
                "error_code",
                "error_message",
                "completed_at",
            ]
        )
        return self

    def delete(self, *args, **kwargs):
        raise ProtectedError("Los intentos fiscales no pueden eliminarse.", [self])


class FiscalSeriesReconciliation(models.Model):
    """Append-only evidence of every local/remote series comparison."""

    OUTCOME_MATCHED = "matched"
    OUTCOME_ADVANCED = "advanced"
    OUTCOME_BLOCKED = "blocked"
    OUTCOME_FAILED = "failed"
    OUTCOME_CHOICES = [
        (OUTCOME_MATCHED, "Coincide"),
        (OUTCOME_ADVANCED, "Serie local adelantada al remoto"),
        (OUTCOME_BLOCKED, "Serie bloqueada"),
        (OUTCOME_FAILED, "Consulta fallida"),
    ]

    series = models.ForeignKey(
        FiscalDocumentSeries,
        on_delete=models.PROTECT,
        related_name="reconciliations",
    )
    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="series_reconciliations",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fiscal_series_reconciliations",
    )
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    issuer_cuit = models.CharField(max_length=11)
    environment = models.CharField(max_length=20, choices=FiscalPointOfSale.ENV_CHOICES)
    point_of_sale = models.CharField(max_length=6)
    doc_type = models.CharField(max_length=3, choices=FISCAL_DOC_TYPE_CHOICES)
    local_next_before = models.PositiveIntegerField()
    local_next_after = models.PositiveIntegerField()
    remote_last_authorized = models.PositiveIntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES)
    reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["series", "created_at"]),
            models.Index(fields=["issuer_cuit", "environment", "point_of_sale", "doc_type"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("La reconciliacion fiscal es append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("La reconciliacion fiscal no puede eliminarse.", [self])


class FiscalMutationAudit(models.Model):
    """Rejected attempts to alter an immutable fiscal record."""

    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="rejected_mutations",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejected_fiscal_mutations",
    )
    action = models.CharField(max_length=40)
    attempted_fields = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=255)
    source = models.CharField(max_length=80, blank=True, default="model")
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["fiscal_document", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("La auditoria de mutaciones fiscales es append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("La auditoria fiscal no puede eliminarse.", [self])


class ArcaReceiverIvaConditionParameter(models.Model):
    """Versioned receiver VAT condition obtained from an ARCA parameter source."""

    arca_id = models.PositiveSmallIntegerField(unique=True)
    description = models.CharField(max_length=160)
    voucher_classes = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=40)
    fetched_at = models.DateTimeField()
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["arca_id"]

    def __str__(self):
        return f"{self.arca_id} - {self.description}"


class ArcaVatRateParameter(models.Model):
    """Versioned VAT rate mapping; local code is not the definitive catalog."""

    arca_id = models.PositiveSmallIntegerField(unique=True)
    rate = models.DecimalField(max_digits=5, decimal_places=2, unique=True)
    description = models.CharField(max_length=120)
    source = models.CharField(max_length=40)
    fetched_at = models.DateTimeField()
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rate"]

    def __str__(self):
        return f"{self.rate}% (ARCA {self.arca_id})"


class InternalDocument(models.Model):
    """Internal operational document with per-company numbering."""

    DOC_TYPE_CHOICES = DocumentSeries.DOC_TYPE_CHOICES

    source_key = models.CharField(
        max_length=120,
        unique=True,
        verbose_name="Clave de origen",
        help_text="Clave idempotente para evitar duplicados.",
    )
    doc_type = models.CharField(
        max_length=3,
        choices=DOC_TYPE_CHOICES,
        verbose_name="Tipo",
    )
    number = models.PositiveIntegerField(verbose_name="Numero")
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="documents",
        verbose_name="Empresa",
    )
    client_company_ref = models.ForeignKey(
        "accounts.ClientCompany",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="Cliente empresa",
    )
    client_profile = models.ForeignKey(
        "accounts.ClientProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="Cliente",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="Pedido",
    )
    payment = models.ForeignKey(
        "accounts.ClientPayment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="Pago",
    )
    transaction = models.ForeignKey(
        "accounts.ClientTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="Movimiento",
    )
    issued_at = models.DateTimeField(default=timezone.now, verbose_name="Fecha emision")
    sales_document_type = models.ForeignKey(
        "core.SalesDocumentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="internal_documents",
        verbose_name="Tipo de documento comercial",
    )
    is_cancelled = models.BooleanField(default=False, verbose_name="Anulado")
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="Fecha anulacion")
    cancel_reason = models.CharField(max_length=255, blank=True, verbose_name="Motivo anulacion")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Documento Interno"
        verbose_name_plural = "Documentos Internos"
        ordering = ["-issued_at", "-id"]
        unique_together = [("company", "doc_type", "number")]
        indexes = [
            models.Index(fields=["company", "doc_type", "number"]),
            models.Index(fields=["company", "issued_at"]),
            models.Index(fields=["doc_type", "issued_at"]),
        ]

    def __str__(self):
        return f"{self.doc_type}-{self.number:07d} ({self.company.name})"

    @property
    def commercial_type_label(self):
        if self.sales_document_type_id:
            return self.sales_document_type.name
        return self.get_doc_type_display()

    @property
    def display_number(self):
        if self.number is None:
            return "-"
        if self.sales_document_type_id:
            return self.sales_document_type.format_number(number=self.number)
        return f"{self.number:07d}"

    def clean(self):
        if not self.company_id:
            raise ValidationError("La empresa es obligatoria para documentos internos.")
        if self.doc_type in {
            DocumentSeries.DOC_COT,
            DocumentSeries.DOC_PED,
            DocumentSeries.DOC_REM,
            DocumentSeries.DOC_REC,
        } and not self.client_company_ref_id:
            raise ValidationError("El cliente empresa es obligatorio para este documento.")
        if self.client_company_ref_id and self.client_company_ref.company_id != self.company_id:
            raise ValidationError("La empresa del documento no coincide con el cliente empresa.")

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)


class StockMovement(models.Model):
    """Auditable stock movement generated from configurable sales documents."""

    source_key = models.CharField(
        max_length=160,
        unique=True,
        db_index=True,
        verbose_name="Clave de origen",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="stock_movements",
        verbose_name="Empresa",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name="Deposito",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        verbose_name="Producto",
    )
    sales_document_type = models.ForeignKey(
        SalesDocumentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name="Tipo de documento comercial",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name="Pedido",
    )
    internal_document = models.ForeignKey(
        InternalDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name="Documento interno",
    )
    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name="Documento fiscal",
    )
    movement_type = models.CharField(
        max_length=20,
        choices=STOCK_MOVEMENT_CHOICES,
        verbose_name="Tipo de movimiento",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3, verbose_name="Cantidad")
    notes = models.CharField(max_length=255, blank=True, verbose_name="Notas")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements_created",
        verbose_name="Generado por",
    )
    warehouse_balance_applied_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Aplicado al saldo por deposito",
    )
    warehouse_balance_error = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Error de saldo por deposito",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Movimiento de stock"
        verbose_name_plural = "Movimientos de stock"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["product", "created_at"]),
            models.Index(fields=["warehouse", "created_at"]),
            models.Index(fields=["movement_type", "created_at"]),
        ]

    def clean(self):
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            raise ValidationError("El deposito no coincide con la empresa del movimiento.")
        if self.sales_document_type_id and self.sales_document_type.company_id != self.company_id:
            raise ValidationError("El tipo de documento no coincide con la empresa del movimiento.")

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product_id} | {self.movement_type} | {self.quantity}"


class SiteSettings(models.Model):
    """
    Singleton model for site-wide settings.
    """

    show_public_prices = models.BooleanField(
        default=False,
        verbose_name="Mostrar precios en catalogo publico",
        help_text="Si esta activado, los visitantes pueden ver precios sin iniciar sesion",
    )
    public_prices_message = models.CharField(
        max_length=255,
        default="Inicia sesion o solicita una cuenta para ver precios",
        verbose_name="Mensaje cuando precios estan ocultos",
    )
    company_name = models.CharField(
        max_length=100,
        default="FLEXS",
        verbose_name="Nombre de la empresa",
    )
    company_email = models.EmailField(
        default="ventas@flexs.com.ar",
        verbose_name="Email de contacto",
    )
    company_phone = models.CharField(
        max_length=50,
        default="+54 011 5177-9690",
        verbose_name="Telefono principal",
    )
    company_phone_2 = models.CharField(
        max_length=50,
        default="+54 011 4755-2404",
        verbose_name="Telefono secundario",
        blank=True,
    )
    company_address = models.TextField(
        default="Indalecio Gomez 4215 (Villa Lynch) San Martin, Buenos Aires. Argentina",
        verbose_name="Direccion",
    )
    require_primary_category_for_multicategory = models.BooleanField(
        default=False,
        verbose_name="Exigir categoria principal en multi-categoria",
        help_text="Si esta activo, al vincular un producto a multiples categorias se exige definir categoria principal.",
    )
    warehouse_stock_enabled = models.BooleanField(
        default=False,
        verbose_name="Activar stock por deposito",
        help_text="Activa la escritura de saldos por deposito despues de inicializar el inventario.",
    )

    class Meta:
        verbose_name = "Configuracion del Sitio"
        verbose_name_plural = "Configuracion del Sitio"

    CACHE_KEY = "site_settings_singleton_v1"
    CACHE_TTL = 300

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.set(self.CACHE_KEY, self, self.CACHE_TTL)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get_settings(cls):
        cached = cache.get(cls.CACHE_KEY)
        if cached:
            return cached
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        cache.set(cls.CACHE_KEY, settings_obj, cls.CACHE_TTL)
        return settings_obj

    def __str__(self):
        return "Configuracion del Sitio"


class UserActivity(models.Model):
    """Track user online/offline status."""

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="activity",
    )
    last_activity = models.DateTimeField(auto_now=True)
    is_online = models.BooleanField(default=False)

    class Meta:
        verbose_name = "User Activity"
        verbose_name_plural = "User Activities"

    def __str__(self):
        return f"{self.user.username} - {'Online' if self.is_online else 'Offline'}"

    @property
    def is_active(self):
        from django.utils import timezone

        if not self.is_online:
            return False
        window_seconds = max(
            int(getattr(settings, "ADMIN_ONLINE_WINDOW_SECONDS", 300)),
            30,
        )
        time_diff = timezone.now() - self.last_activity
        return time_diff.total_seconds() < window_seconds


class CatalogAnalyticsEvent(models.Model):
    """Raw analytics events for catalog behavior."""

    EVENT_SEARCH = "search"
    EVENT_CATEGORY_VIEW = "category_view"
    EVENT_FILTER = "filter"

    EVENT_CHOICES = [
        (EVENT_SEARCH, "Search"),
        (EVENT_CATEGORY_VIEW, "Category View"),
        (EVENT_FILTER, "Filter"),
    ]

    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES)
    query = models.CharField(max_length=255, blank=True)
    category_slug = models.CharField(max_length=120, blank=True)
    results_count = models.IntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["category_slug"]),
            models.Index(fields=["query"]),
            models.Index(fields=["results_count"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} - {self.query or self.category_slug or '-'}"


class AdminAuditLog(models.Model):
    """Security and operations audit trail for admin actions."""

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_audit_logs",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_audit_logs",
        verbose_name="Empresa",
    )
    action = models.CharField(max_length=120)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=120, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["action"]),
            models.Index(fields=["target_type"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class ExternalEditorJob(models.Model):
    """Auditable, idempotent product mutation requested by the external editor."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_ROLLED_BACK = "rolled_back"
    STATUS_ROLLBACK_PARTIAL = "rollback_partial"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_RUNNING, "En proceso"),
        (STATUS_COMPLETED, "Completado"),
        (STATUS_PARTIAL, "Completado con errores"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_ROLLED_BACK, "Revertido"),
        (STATUS_ROLLBACK_PARTIAL, "Reversion parcial"),
    ]

    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="external_editor_jobs",
    )
    rolled_back_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_editor_jobs_rolled_back",
    )
    idempotency_key = models.CharField(max_length=120)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    request_payload = models.JSONField(default=dict)
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    succeeded = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["created_by", "idempotency_key"],
                name="core_editor_job_user_idempotency_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return f"Editor job {self.pk} ({self.status})"


class ExternalEditorJobItem(models.Model):
    """Per-product snapshot used for diagnostics and safe rollback."""

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_ROLLED_BACK = "rolled_back"
    STATUS_ROLLBACK_CONFLICT = "rollback_conflict"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_COMPLETED, "Completado"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_ROLLED_BACK, "Revertido"),
        (STATUS_ROLLBACK_CONFLICT, "Conflicto al revertir"),
    ]

    job = models.ForeignKey(
        ExternalEditorJob,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_editor_job_items",
    )
    product_id_snapshot = models.PositiveIntegerField()
    sku = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "product_id_snapshot"],
                name="core_editor_job_item_product_uniq",
            )
        ]
        indexes = [models.Index(fields=["job", "status"])]

    def __str__(self):
        return f"Editor job {self.job_id}: {self.sku or self.product_id_snapshot}"


class ExternalEditorSavedView(models.Model):
    """Reusable server-side filter preset owned by an editor user."""

    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="external_editor_saved_views",
    )
    name = models.CharField(max_length=120)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["created_by", "name"],
                name="core_editor_saved_view_user_name_uniq",
            )
        ]

    def __str__(self):
        return f"{self.created_by}: {self.name}"


class ExternalEditorDraft(models.Model):
    """A named set of per-product changes that can be reviewed before publication."""

    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_PUBLISHED, "Publicado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="external_editor_drafts",
    )
    name = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    changes = models.JSONField(default=list)
    published_job = models.ForeignKey(
        ExternalEditorJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_drafts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(
                fields=["created_by", "status", "updated_at"],
                name="core_editor_created_aa51a1_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"


def generate_webhook_secret():
    return secrets.token_urlsafe(36)


class WebhookEndpoint(models.Model):
    """Company-scoped destination subscribed to signed business events."""

    EVENT_ORDER_CREATED = "order.created"
    EVENT_ORDER_STATUS_CHANGED = "order.status_changed"
    EVENT_PAYMENT_RECORDED = "payment.recorded"
    EVENT_FISCAL_UPDATED = "fiscal.updated"
    EVENT_CHOICES = [
        (EVENT_ORDER_CREATED, "Pedido creado"),
        (EVENT_ORDER_STATUS_CHANGED, "Estado de pedido actualizado"),
        (EVENT_PAYMENT_RECORDED, "Pago registrado"),
        (EVENT_FISCAL_UPDATED, "Comprobante fiscal actualizado"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
        verbose_name="Empresa",
    )
    name = models.CharField(max_length=100, verbose_name="Nombre")
    target_url = models.URLField(max_length=500, verbose_name="URL destino")
    secret = models.CharField(max_length=128, default=generate_webhook_secret, verbose_name="Secreto")
    events = models.JSONField(default=list, blank=True, verbose_name="Eventos")
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_endpoints_created",
        verbose_name="Creado por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Webhook"
        verbose_name_plural = "Webhooks"
        ordering = ["company_id", "name", "id"]
        unique_together = [("company", "name")]
        indexes = [
            models.Index(fields=["company", "is_active"]),
        ]

    def clean(self):
        parsed = urlsplit(str(self.target_url or ""))
        allow_insecure = bool(getattr(settings, "WEBHOOK_ALLOW_INSECURE_URLS", settings.DEBUG))
        if parsed.scheme not in ({"http", "https"} if allow_insecure else {"https"}):
            raise ValidationError({"target_url": "La URL del webhook debe usar HTTPS."})
        if parsed.username or parsed.password:
            raise ValidationError({"target_url": "La URL no puede incluir credenciales."})
        if not isinstance(self.events, (list, tuple, set)):
            raise ValidationError({"events": "Los eventos deben enviarse como una lista."})
        valid_events = {value for value, _label in self.EVENT_CHOICES}
        invalid_events = set(self.events or []) - valid_events
        if invalid_events:
            raise ValidationError({"events": "Hay eventos de webhook no reconocidos."})

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.events = sorted(set(self.events or []))
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class WebhookDelivery(models.Model):
    """Durable delivery record with retry metadata."""

    STATUS_PENDING = "pending"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_DELIVERED, "Entregado"),
        (STATUS_FAILED, "Fallido"),
    ]

    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name="Webhook",
    )
    event_id = models.UUIDField(default=uuid.uuid4, editable=False, verbose_name="ID de evento")
    event_type = models.CharField(max_length=60, db_index=True, verbose_name="Evento")
    payload = models.JSONField(default=dict, verbose_name="Contenido")
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="Estado",
    )
    attempts_count = models.PositiveIntegerField(default=0, verbose_name="Intentos")
    response_status = models.PositiveIntegerField(null=True, blank=True, verbose_name="HTTP")
    response_excerpt = models.CharField(max_length=500, blank=True, verbose_name="Respuesta")
    last_error = models.CharField(max_length=500, blank=True, verbose_name="Ultimo error")
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Proximo intento")
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="Entregado el")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Entrega de webhook"
        verbose_name_plural = "Entregas de webhooks"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint", "event_id"],
                name="unique_webhook_event_per_endpoint",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["endpoint", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} -> {self.endpoint.name} ({self.status})"


class ImportExecution(models.Model):
    """History row for each import execution."""

    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_ROLLED_BACK = "rolled_back"

    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Procesando"),
        (STATUS_COMPLETED, "Completado"),
        (STATUS_FAILED, "Fallido"),
        (STATUS_ROLLED_BACK, "Rollback aplicado"),
    ]

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_executions",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="import_executions",
        verbose_name="Empresa",
    )
    import_type = models.CharField(max_length=50)
    file_name = models.CharField(max_length=255, blank=True)
    dry_run = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    created_count = models.IntegerField(default=0)
    updated_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    # Legacy columns kept in sync with historical databases used in active environments.
    metrics = models.JSONField(default=dict, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    created_refs = models.JSONField(default=list, blank=True)
    rollback_summary = models.JSONField(default=dict, blank=True)
    supplier = models.ForeignKey(
        "catalog.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_executions",
    )
    supplier_name = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rollback_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["import_type", "created_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.import_type} - {self.status} - {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if not kwargs.get("raw") and not self.company_id:
            try:
                from core.services.company_context import get_default_company

                self.company = get_default_company()
            except Exception:
                pass
        super().save(*args, **kwargs)


CATALOG_EXPORT_COLUMN_CHOICES = [
    ("sku", "SKU"),
    ("name", "Nombre"),
    ("description", "Descripcion"),
    ("supplier", "Proveedor"),
    ("supplier_normalized", "Proveedor normalizado"),
    ("price", "Precio"),
    ("cost", "Costo"),
    ("stock", "Stock"),
    ("is_active", "Producto activo"),
    ("is_visible_in_catalog", "Visible en catalogo"),
    ("primary_category", "Categoria principal"),
    ("categories", "Categorias vinculadas"),
    ("filter_1", "Filtro 1"),
    ("filter_2", "Filtro 2"),
    ("filter_3", "Filtro 3"),
    ("filter_4", "Filtro 4"),
    ("filter_5", "Filtro 5"),
    ("created_at", "Creado"),
    ("updated_at", "Actualizado"),
    ("attributes_json", "Atributos JSON"),
]

CATALOG_EXPORT_SORT_CHOICES = [
    ("name_asc", "Nombre A-Z"),
    ("name_desc", "Nombre Z-A"),
    ("sku_asc", "SKU A-Z"),
    ("sku_desc", "SKU Z-A"),
    ("updated_desc", "Mas recientes"),
    ("price_desc", "Precio mayor a menor"),
    ("price_asc", "Precio menor a mayor"),
]

CATALOG_EXPORT_SPECIAL_GROUPING_NONE = ""
CATALOG_EXPORT_SPECIAL_GROUPING_CLAMP_MEASURE = "clamp_measure"
CATALOG_EXPORT_SPECIAL_GROUPING_CHOICES = [
    (CATALOG_EXPORT_SPECIAL_GROUPING_NONE, "Sin agrupacion tecnica"),
    (CATALOG_EXPORT_SPECIAL_GROUPING_CLAMP_MEASURE, "Abrazaderas por medida"),
]


class CatalogExcelTemplate(models.Model):
    """Workbook template to export the product catalog."""

    name = models.CharField(max_length=120, unique=True, verbose_name="Nombre")
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.CharField(max_length=255, blank=True, verbose_name="Descripcion")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    is_client_download_enabled = models.BooleanField(
        default=False,
        verbose_name="Disponible para clientes",
        help_text="Si esta activo, esta plantilla se publica para descarga en cuentas de cliente.",
    )
    client_download_label = models.CharField(
        max_length=120,
        blank=True,
        default="Descargar catalogo Excel",
        verbose_name="Texto boton cliente",
        help_text="Texto del boton que vera el cliente para descargar esta plantilla.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_excel_templates_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_excel_templates_updated",
    )
    last_generated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Ultima generacion",
    )
    last_generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_excel_templates_generated",
        verbose_name="Ultimo generador",
    )
    last_generated_rows = models.PositiveIntegerField(
        default=0,
        verbose_name="Filas ultima generacion",
    )
    last_generated_stats = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Estadisticas ultima generacion",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plantilla Excel de Catalogo"
        verbose_name_plural = "Plantillas Excel de Catalogo"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "plantilla-catalogo"
            slug = base_slug
            counter = 1
            while CatalogExcelTemplate.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        if not (self.client_download_label or "").strip():
            self.client_download_label = "Descargar catalogo Excel"
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_client_download_enabled:
                CatalogExcelTemplate.objects.filter(
                    is_client_download_enabled=True
                ).exclude(pk=self.pk).update(is_client_download_enabled=False)

    def __str__(self):
        return self.name

    def mark_generated(self, stats=None, user=None):
        stats = stats or {}
        generated_at = timezone.now()
        row_count = int(stats.get("total_rows") or 0)
        user_id = getattr(user, "pk", None) if user and getattr(user, "is_authenticated", False) else None
        CatalogExcelTemplate.objects.filter(pk=self.pk).update(
            last_generated_at=generated_at,
            last_generated_by_id=user_id,
            last_generated_rows=row_count,
            last_generated_stats=stats,
        )
        self.last_generated_at = generated_at
        self.last_generated_by_id = user_id
        self.last_generated_rows = row_count
        self.last_generated_stats = stats

    @classmethod
    def get_client_download_template(cls):
        return (
            cls.objects.filter(is_active=True, is_client_download_enabled=True)
            .order_by("-updated_at", "id")
            .first()
        )


class CatalogExcelTemplateSheet(models.Model):
    """One worksheet configuration inside a catalog export template."""

    template = models.ForeignKey(
        CatalogExcelTemplate,
        on_delete=models.CASCADE,
        related_name="sheets",
        verbose_name="Plantilla",
    )
    name = models.CharField(max_length=80, verbose_name="Nombre hoja")
    order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    include_header = models.BooleanField(default=True, verbose_name="Incluir encabezado")
    only_active_products = models.BooleanField(default=True, verbose_name="Solo productos activos")
    only_catalog_visible = models.BooleanField(default=False, verbose_name="Solo visibles en catalogo")
    include_descendant_categories = models.BooleanField(
        default=True,
        verbose_name="Incluir subcategorias",
    )
    group_by_subcategories = models.BooleanField(
        default=False,
        verbose_name="Separar por subcategorias",
        help_text="Cuando la hoja filtra una categoria principal, agrupa los productos en tablas internas por subcategoria.",
    )
    special_grouping = models.CharField(
        max_length=30,
        choices=CATALOG_EXPORT_SPECIAL_GROUPING_CHOICES,
        blank=True,
        default=CATALOG_EXPORT_SPECIAL_GROUPING_NONE,
        verbose_name="Agrupacion tecnica",
        help_text="Opcional. Usa una salida especial para rubros tecnicos, sin modificar categorias reales.",
    )
    categories = models.ManyToManyField(
        "catalog.Category",
        blank=True,
        related_name="catalog_excel_template_sheets",
        verbose_name="Categorias",
    )
    suppliers = models.ManyToManyField(
        "catalog.Supplier",
        blank=True,
        related_name="catalog_excel_template_sheets",
        verbose_name="Proveedores",
    )
    search_query = models.CharField(max_length=120, blank=True, verbose_name="Busqueda interna")
    max_rows = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Limite de filas",
        help_text="Opcional. Dejar vacio para exportar todo.",
    )
    sort_by = models.CharField(
        max_length=30,
        choices=CATALOG_EXPORT_SORT_CHOICES,
        default="name_asc",
        verbose_name="Orden",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hoja de plantilla Excel"
        verbose_name_plural = "Hojas de plantilla Excel"
        ordering = ["template_id", "order", "id"]
        indexes = [
            models.Index(fields=["template", "order"]),
            models.Index(fields=["only_active_products"]),
            models.Index(fields=["only_catalog_visible"]),
        ]
        unique_together = [("template", "name")]

    def __str__(self):
        return f"{self.template.name} / {self.name}"


class CatalogExcelTemplateColumn(models.Model):
    """Column definition for one worksheet."""

    sheet = models.ForeignKey(
        CatalogExcelTemplateSheet,
        on_delete=models.CASCADE,
        related_name="columns",
        verbose_name="Hoja",
    )
    key = models.CharField(
        max_length=40,
        choices=CATALOG_EXPORT_COLUMN_CHOICES,
        verbose_name="Campo",
    )
    header = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Encabezado",
        help_text="Opcional. Si se deja vacio, se usa el nombre por defecto del campo.",
    )
    order = models.PositiveIntegerField(default=0, verbose_name="Orden")
    is_active = models.BooleanField(default=True, verbose_name="Activa")

    class Meta:
        verbose_name = "Columna de plantilla Excel"
        verbose_name_plural = "Columnas de plantilla Excel"
        ordering = ["sheet_id", "order", "id"]
        indexes = [
            models.Index(fields=["sheet", "order"]),
            models.Index(fields=["key"]),
            models.Index(fields=["is_active"]),
        ]
        unique_together = [("sheet", "key")]

    def __str__(self):
        return f"{self.sheet} / {self.key}"

    def get_effective_header(self):
        if self.header:
            return self.header
        return dict(CATALOG_EXPORT_COLUMN_CHOICES).get(self.key, self.key)

    def _touch_parent_sheet(self):
        if not self.sheet_id:
            return
        sheet = (
            CatalogExcelTemplateSheet.objects.filter(pk=self.sheet_id)
            .only("id", "template_id")
            .first()
        )
        if not sheet:
            return
        now = timezone.now()
        CatalogExcelTemplateSheet.objects.filter(pk=sheet.pk).update(updated_at=now)
        CatalogExcelTemplate.objects.filter(pk=sheet.template_id).update(updated_at=now)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._touch_parent_sheet()

    def delete(self, *args, **kwargs):
        sheet_id = self.sheet_id
        result = super().delete(*args, **kwargs)
        self.sheet_id = sheet_id
        self._touch_parent_sheet()
        return result
