"""
Core app models - site-wide settings, analytics, and operation logs.
"""
from django.db import models, transaction
from django.conf import settings
from django.core.cache import cache
from django.utils.text import slugify


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
