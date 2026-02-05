"""
Accounts app models - Client profiles and account requests.
"""
from django.db import models
from django.contrib.auth.models import User


class ClientProfile(models.Model):
    """
    Extended profile for B2B clients.
    Linked to Django's built-in User model.
    """
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
    company_name = models.CharField(
        max_length=200,
        verbose_name="Empresa/Razón Social"
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
    address = models.TextField(
        blank=True,
        verbose_name="Domicilio"
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

    def get_discount_decimal(self):
        """Return discount as decimal (e.g., 0.10 for 10%)."""
        return self.discount / 100


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
