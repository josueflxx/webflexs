"""
Core app models - site-wide settings, analytics, and operation logs.
"""
from django.db import models
from django.conf import settings


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

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get_settings(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

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
