"""
Core app models - site-wide settings, analytics, and operation logs.
"""
from django.db import models, transaction
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify


FISCAL_DOC_TYPE_FA = "FA"
FISCAL_DOC_TYPE_FB = "FB"
FISCAL_DOC_TYPE_NCA = "NCA"
FISCAL_DOC_TYPE_NCB = "NCB"
FISCAL_DOC_TYPE_CHOICES = [
    (FISCAL_DOC_TYPE_FA, "Factura A"),
    (FISCAL_DOC_TYPE_FB, "Factura B"),
    (FISCAL_DOC_TYPE_NCA, "Nota de Credito A"),
    (FISCAL_DOC_TYPE_NCB, "Nota de Credito B"),
]

FISCAL_ISSUE_MODE_ARCA_WSFE = "arca_wsfe"
FISCAL_ISSUE_MODE_MANUAL = "manual"
FISCAL_ISSUE_MODE_EXTERNAL_SAAS = "external_saas"
FISCAL_ISSUE_MODE_CHOICES = [
    (FISCAL_ISSUE_MODE_ARCA_WSFE, "ARCA WSFE"),
    (FISCAL_ISSUE_MODE_MANUAL, "Manual"),
    (FISCAL_ISSUE_MODE_EXTERNAL_SAAS, "Externo SaaS"),
]

FISCAL_STATUS_READY_TO_ISSUE = "ready_to_issue"
FISCAL_STATUS_SUBMITTING = "submitting"
FISCAL_STATUS_AUTHORIZED = "authorized"
FISCAL_STATUS_PENDING_RETRY = "pending_retry"
FISCAL_STATUS_REJECTED = "rejected"
FISCAL_STATUS_VOIDED = "voided"
FISCAL_STATUS_EXTERNAL_RECORDED = "external_recorded"
FISCAL_STATUS_CHOICES = [
    (FISCAL_STATUS_READY_TO_ISSUE, "Listo para emitir"),
    (FISCAL_STATUS_SUBMITTING, "Enviando"),
    (FISCAL_STATUS_AUTHORIZED, "Autorizado"),
    (FISCAL_STATUS_PENDING_RETRY, "Pendiente reintento"),
    (FISCAL_STATUS_REJECTED, "Rechazado"),
    (FISCAL_STATUS_VOIDED, "Anulado"),
    (FISCAL_STATUS_EXTERNAL_RECORDED, "Registrado externo"),
]

FISCAL_ATTEMPT_RESULT_PENDING = "pending"
FISCAL_ATTEMPT_RESULT_SUCCESS = "success"
FISCAL_ATTEMPT_RESULT_ERROR = "error"
FISCAL_ATTEMPT_RESULT_CHOICES = [
    (FISCAL_ATTEMPT_RESULT_PENDING, "Pendiente"),
    (FISCAL_ATTEMPT_RESULT_SUCCESS, "Exitoso"),
    (FISCAL_ATTEMPT_RESULT_ERROR, "Con error"),
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


class FiscalPointOfSale(models.Model):
    """Fiscal point of sale configuration per company."""

    ENV_HOMOLOGATION = "homologation"
    ENV_PRODUCTION = "production"
    ENV_CHOICES = [
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
        default=ENV_HOMOLOGATION,
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


class FiscalDocumentSeries(models.Model):
    """Fiscal numbering series per company and point of sale."""

    DOC_FA = FISCAL_DOC_TYPE_FA
    DOC_FB = FISCAL_DOC_TYPE_FB
    DOC_NCA = FISCAL_DOC_TYPE_NCA
    DOC_NCB = FISCAL_DOC_TYPE_NCB
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
    next_number = models.PositiveIntegerField(default=1, verbose_name="Siguiente numero")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Serie Fiscal"
        verbose_name_plural = "Series Fiscales"
        unique_together = [("point_of_sale_ref", "doc_type")]
        indexes = [
            models.Index(fields=["point_of_sale_ref", "doc_type"]),
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
        elif not (self.point_of_sale or "").strip():
            raise ValidationError("Debe definirse un punto de venta fiscal.")

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)


class FiscalDocument(models.Model):
    """Fiscal document record, separated from internal documents."""

    DOC_TYPE_CHOICES = FISCAL_DOC_TYPE_CHOICES
    ISSUE_MODE_CHOICES = FISCAL_ISSUE_MODE_CHOICES
    STATUS_CHOICES = FISCAL_STATUS_CHOICES

    source_key = models.CharField(
        max_length=160,
        unique=True,
        db_index=True,
        verbose_name="Clave de origen",
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
        on_delete=models.SET_NULL,
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
        max_length=24,
        choices=STATUS_CHOICES,
        default=FISCAL_STATUS_READY_TO_ISSUE,
        verbose_name="Estado fiscal",
        db_index=True,
    )
    issued_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de emision",
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
    request_payload = models.JSONField(default=dict, blank=True, verbose_name="Request payload")
    response_payload = models.JSONField(default=dict, blank=True, verbose_name="Response payload")
    error_code = models.CharField(max_length=80, blank=True, default="", verbose_name="Codigo error")
    error_message = models.TextField(blank=True, default="", verbose_name="Mensaje error")
    attempts_count = models.PositiveIntegerField(default=0, verbose_name="Intentos")
    last_attempt_at = models.DateTimeField(null=True, blank=True, verbose_name="Ultimo intento")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Documento Fiscal"
        verbose_name_plural = "Documentos Fiscales"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
            models.Index(fields=["company", "doc_type", "created_at"]),
            models.Index(fields=["point_of_sale", "doc_type", "number"]),
            models.Index(fields=["external_system", "external_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "point_of_sale", "doc_type", "number"],
                condition=models.Q(number__isnull=False),
                name="uniq_fiscal_doc_company_pos_type_number",
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

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        number_text = self.number if self.number is not None else "-"
        return f"{self.doc_type} {self.point_of_sale.number}-{number_text} ({self.company.name})"


class FiscalDocumentItem(models.Model):
    """Fiscal document item snapshot for audit/reporting."""

    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.CASCADE,
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
    iva_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="IVA")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Total")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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


class FiscalEmissionAttempt(models.Model):
    """One request/response attempt against fiscal backend."""

    RESULT_STATUS_CHOICES = FISCAL_ATTEMPT_RESULT_CHOICES

    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.CASCADE,
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
    result_status = models.CharField(
        max_length=20,
        choices=RESULT_STATUS_CHOICES,
        default=FISCAL_ATTEMPT_RESULT_PENDING,
        verbose_name="Resultado",
    )
    error_code = models.CharField(max_length=80, blank=True, default="", verbose_name="Codigo error")
    error_message = models.TextField(blank=True, default="", verbose_name="Mensaje error")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Intento de Emision Fiscal"
        verbose_name_plural = "Intentos de Emision Fiscal"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["fiscal_document", "created_at"]),
            models.Index(fields=["result_status", "created_at"]),
        ]

    def __str__(self):
        return f"Intento {self.fiscal_document_id} - {self.result_status}"


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
    result_summary = models.JSONField(default=dict, blank=True)
    created_refs = models.JSONField(default=list, blank=True)
    rollback_summary = models.JSONField(default=dict, blank=True)
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
