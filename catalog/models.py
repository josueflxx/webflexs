"""
Catalog app models - Products and Categories.
"""
from django.db import models
import re


class Category(models.Model):
    """Product category with optional parent for hierarchy."""
    name = models.CharField(max_length=100, verbose_name="Nombre")
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
        verbose_name="Categoría padre"
    )
    order = models.IntegerField(default=0, verbose_name="Orden")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"
        ordering = ['order', 'name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['slug']),
            models.Index(fields=['parent']),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
            # Ensure unique slug
            original_slug = self.slug
            counter = 1
            while Category.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name

    def get_full_path(self):
        """Return full category path like 'Parent > Child > Grandchild'."""
        path = [self.name]
        parent = self.parent
        while parent:
            path.insert(0, parent.name)
            parent = parent.parent
        return " > ".join(path)


class CategoryAttribute(models.Model):
    """
    Dynamic attribute definition for a category.
    Includes regex pattern for auto-extraction from description.
    """
    TYPE_CHOICES = [
        ('text', 'Texto'),
        ('number', 'Número'),
        ('select', 'Selección'),
    ]

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='attributes',
        verbose_name="Categoría"
    )
    name = models.CharField(max_length=100, verbose_name="Nombre")
    slug = models.SlugField(max_length=100)
    type = models.CharField(
        max_length=20, 
        choices=TYPE_CHOICES, 
        default='text', 
        verbose_name="Tipo de dato"
    )
    options = models.TextField(
        blank=True, 
        help_text="Opciones separadas por coma (solo para tipo Selección)", 
        verbose_name="Opciones"
    )
    required = models.BooleanField(default=False, verbose_name="Requerido")
    regex_pattern = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        verbose_name="Patrón Regex", 
        help_text="Regex para extraer valor. Ej: 'Diámetro: (\d+mm)'"
    )

    class Meta:
        verbose_name = "Atributo de Categoría"
        verbose_name_plural = "Atributos de Categoría"
        unique_together = ('category', 'slug')

    def __str__(self):
        return f"{self.category.name} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def options_list(self):
        """Return list of options if type is select."""
        if self.type == 'select' and self.options:
            return [opt.strip() for opt in self.options.split(',') if opt.strip()]
        return []


class Product(models.Model):
    """Product model with all required fields."""
    sku = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name="SKU"
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Nombre"
    )
    description = models.TextField(
        blank=True,
        verbose_name="Descripción"
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Precio"
    )
    stock = models.IntegerField(
        default=0,
        verbose_name="Stock"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        verbose_name="Categoría"
    )
    image = models.ImageField(
        upload_to='products/',
        blank=True,
        null=True,
        verbose_name="Imagen"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Activo"
    )
    
    # Generic filters (from Excel import)
    filter_1 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 1")
    filter_2 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 2")
    filter_3 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 3")
    filter_4 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 4")
    filter_5 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 5")
    
    # Dynamic attributes
    attributes = models.JSONField(default=dict, blank=True, verbose_name="Atributos")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        ordering = ['name']
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['name']),
            models.Index(fields=['category']),
            models.Index(fields=['is_active']),
            models.Index(fields=['filter_1']),
            models.Index(fields=['filter_2']),
            models.Index(fields=['filter_3']),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def get_discounted_price(self, discount_percentage):
        """Calculate price after discount."""
        if discount_percentage:
            discount = self.price * discount_percentage
            return self.price - discount
        return self.price

    def extract_attributes_from_description(self):
        """
        Extracts attributes from description based on CategoryAttribute regex patterns.
        Updates self.attributes with found values.
        """
        if not self.category or not self.description:
            return {}
        
        extracted = {}
        # Get all regex patterns for this category
        attrs = self.category.attributes.exclude(regex_pattern__isnull=True).exclude(regex_pattern='')
        
        for attr in attrs:
            try:
                match = re.search(attr.regex_pattern, self.description, re.IGNORECASE)
                if match:
                    # If capture group exists, use it. Otherwise use whole match.
                    val = match.group(1) if match.groups() else match.group(0)
                    extracted[attr.slug] = val.strip()
            except re.error:
                continue
        
        if extracted:
            # Initialize attributes if it's None (though default is dict)
            if self.attributes is None:
                self.attributes = {}
            self.attributes.update(extracted)
            
        return extracted



class ClampSpecs(models.Model):
    """
    Especificaciones técnicas estructuradas para Abrazaderas.
    OneToOne con Product.
    """
    product = models.OneToOneField(
        Product, 
        on_delete=models.CASCADE, 
        related_name='clamp_specs',
        verbose_name="Producto"
    )
    
    # 1. Fabricación
    FABRICACION_CHOICES = [
        ('TREFILADA', 'TREFILADA'),
        ('LAMINADA', 'LAMINADA'),
    ]
    fabrication = models.CharField(
        max_length=20, 
        choices=FABRICACION_CHOICES, 
        null=True, blank=True,
        verbose_name="Fabricación"
    )
    
    # 2. Diámetro (Texto para soportar '1/2', '7/16')
    diameter = models.CharField(max_length=20, null=True, blank=True, verbose_name="Diámetro")
    
    # 3. Medidas (Enteros en mm)
    width = models.IntegerField(null=True, blank=True, verbose_name="Ancho (mm)")
    length = models.IntegerField(null=True, blank=True, verbose_name="Largo (mm)")
    
    # 4. Forma
    FORMA_CHOICES = [
        ('PLANA', 'PLANA'),
        ('CURVA', 'CURVA'),
        ('SEMICURVA', 'SEMICURVA'),
    ]
    shape = models.CharField(
        max_length=20, 
        choices=FORMA_CHOICES, 
        null=True, blank=True,
        verbose_name="Forma"
    )
    
    # Metadata del parser
    parse_confidence = models.IntegerField(default=0, verbose_name="Confianza Parser (%)")
    parse_warnings = models.JSONField(default=list, blank=True, verbose_name="Warnings")
    
    manual_override = models.BooleanField(
        default=False, 
        verbose_name="Manual Override",
        help_text="Si es True, el parser no sobrescribirá estos datos."
    )
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Especificación Abrazadera"
        verbose_name_plural = "Especificaciones Abrazaderas"

    def __str__(self):
        return f"Spec for {self.product.sku}"

