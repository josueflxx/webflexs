"""
Core app models - Site-wide configuration.
"""
from django.db import models


class SiteSettings(models.Model):
    """
    Singleton model for site-wide settings.
    Only one instance should exist.
    """
    show_public_prices = models.BooleanField(
        default=False,
        verbose_name="Mostrar precios en catálogo público",
        help_text="Si está activado, los visitantes pueden ver precios sin iniciar sesión"
    )
    public_prices_message = models.CharField(
        max_length=255,
        default="Iniciá sesión o solicitá una cuenta para ver precios",
        verbose_name="Mensaje cuando precios están ocultos"
    )
    company_name = models.CharField(
        max_length=100,
        default="FLEXS",
        verbose_name="Nombre de la empresa"
    )
    company_email = models.EmailField(
        default="ventas@flexs.com.ar",
        verbose_name="Email de contacto"
    )
    company_phone = models.CharField(
        max_length=50,
        default="+54 011 5177-9690",
        verbose_name="Teléfono principal"
    )
    company_phone_2 = models.CharField(
        max_length=50,
        default="+54 011 4755-2404",
        verbose_name="Teléfono secundario",
        blank=True
    )
    company_address = models.TextField(
        default="Indalecio Gomez 4215 (Villa Lynch) San Martin, Buenos Aires. Argentina",
        verbose_name="Dirección"
    )

    class Meta:
        verbose_name = "Configuración del Sitio"
        verbose_name_plural = "Configuración del Sitio"

    def save(self, *args, **kwargs):
        """Ensure only one instance exists."""
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion."""
        pass

    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance."""
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

    def __str__(self):
        return "Configuración del Sitio"


class UserActivity(models.Model):
    """Track user online/offline status."""
    user = models.OneToOneField(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='activity'
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
        """User is active if last activity was within 5 minutes."""
        from django.utils import timezone
        time_diff = timezone.now() - self.last_activity
        return time_diff.total_seconds() < 300

