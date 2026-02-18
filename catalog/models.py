"""
Catalog app models - products, categories, and clamp specs.
"""
from django.db import models
from django.db.models import Q
import re


class Category(models.Model):
    """Product category with optional parent for hierarchy."""

    name = models.CharField(max_length=100, verbose_name="Nombre")
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Categoria padre",
    )
    order = models.IntegerField(default=0, verbose_name="Orden")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    seo_title = models.CharField(
        max_length=160,
        blank=True,
        verbose_name="SEO title",
        help_text="Opcional. Título para buscadores de esta categoría.",
    )
    seo_description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="SEO description",
        help_text="Opcional. Descripción para buscadores de esta categoría.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["order"]),
            models.Index(fields=["parent", "is_active"]),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            self.slug = slugify(self.name)
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
        path = [self.name]
        parent = self.parent
        while parent:
            path.insert(0, parent.name)
            parent = parent.parent
        return " > ".join(path)

    def get_descendant_ids(self, include_self=True, only_active=False):
        """Return all descendant category IDs using iterative traversal."""
        if not self.pk:
            return []

        include_root = include_self and (not only_active or self.is_active)
        ids = [self.pk] if include_root else []
        pending = [self.pk]

        base_qs = Category.objects.all()
        if only_active:
            base_qs = base_qs.filter(is_active=True)

        while pending:
            children = list(
                base_qs.filter(parent_id__in=pending).values_list("id", flat=True)
            )
            if not children:
                break
            ids.extend(children)
            pending = children

        return ids

    def get_ancestor_ids(self, include_self=True):
        """Return ancestor IDs up to root."""
        ids = [self.pk] if include_self and self.pk else []
        parent = self.parent
        while parent:
            ids.append(parent.pk)
            parent = parent.parent
        return ids


class CategoryAttribute(models.Model):
    """
    Dynamic attribute definition for a category.
    Includes regex pattern for auto-extraction from description.
    """

    TYPE_CHOICES = [
        ("text", "Texto"),
        ("number", "Numero"),
        ("select", "Seleccion"),
    ]

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="attributes",
        verbose_name="Categoria",
    )
    name = models.CharField(max_length=100, verbose_name="Nombre")
    slug = models.SlugField(max_length=100)
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="text",
        verbose_name="Tipo de dato",
    )
    options = models.TextField(
        blank=True,
        help_text="Opciones separadas por coma (solo para tipo Seleccion)",
        verbose_name="Opciones",
    )
    required = models.BooleanField(default=False, verbose_name="Requerido")
    regex_pattern = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Patron Regex",
        help_text=r"Regex para extraer valor. Ej: 'Diametro: (\d+mm)'",
    )

    class Meta:
        verbose_name = "Atributo de categoria"
        verbose_name_plural = "Atributos de categoria"
        unique_together = ("category", "slug")

    def __str__(self):
        return f"{self.category.name} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def options_list(self):
        if self.type == "select" and self.options:
            return [opt.strip() for opt in self.options.split(",") if opt.strip()]
        return []


class Supplier(models.Model):
    """Normalized supplier entity."""

    name = models.CharField(max_length=120, verbose_name="Nombre")
    normalized_name = models.CharField(max_length=120, unique=True, db_index=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active"]),
        ]

    @staticmethod
    def normalize_name(value):
        cleaned = re.sub(r"\s+", " ", str(value or "").strip())
        return cleaned.upper()

    def save(self, *args, **kwargs):
        from django.utils.text import slugify

        self.name = re.sub(r"\s+", " ", str(self.name or "").strip())
        self.normalized_name = self.normalize_name(self.name)
        if not self.slug:
            base_slug = slugify(self.name) or "proveedor"
            slug = base_slug
            counter = 1
            while Supplier.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Product(models.Model):
    """Product model with all required fields."""

    sku = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="SKU")
    name = models.CharField(max_length=255, db_index=True, verbose_name="Nombre")
    supplier = models.CharField(max_length=120, blank=True, db_index=True, verbose_name="Proveedor")
    supplier_ref = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="Proveedor normalizado",
    )
    description = models.TextField(blank=True, verbose_name="Descripcion")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Precio")
    stock = models.IntegerField(default=0, verbose_name="Stock")
    # Legacy single category (kept for backward compatibility and as primary category)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="Categoria principal",
    )
    # Flexible assignment: one product can belong to many categories
    categories = models.ManyToManyField(
        Category,
        blank=True,
        related_name="products_m2m",
        verbose_name="Categorias vinculadas",
    )
    image = models.ImageField(upload_to="products/", blank=True, null=True, verbose_name="Imagen")
    is_active = models.BooleanField(default=True, verbose_name="Activo")

    filter_1 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 1")
    filter_2 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 2")
    filter_3 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 3")
    filter_4 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 4")
    filter_5 = models.CharField(max_length=100, blank=True, verbose_name="Filtro 5")

    attributes = models.JSONField(default=dict, blank=True, verbose_name="Atributos")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["sku"]),
            models.Index(fields=["name"]),
            models.Index(fields=["supplier"]),
            models.Index(fields=["supplier_ref"]),
            models.Index(fields=["category"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["updated_at"]),
            models.Index(fields=["filter_1"]),
            models.Index(fields=["filter_2"]),
            models.Index(fields=["filter_3"]),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def get_discounted_price(self, discount_percentage):
        if discount_percentage:
            discount = self.price * discount_percentage
            return self.price - discount
        return self.price

    def get_primary_category(self):
        """Fallback-safe primary category for legacy views."""
        if self.category_id:
            return self.category
        return self.categories.order_by("name").first()

    def get_linked_categories(self):
        """
        Return direct categories (many-to-many) with a safe fallback to legacy primary.
        """
        linked_categories = list(self.categories.all())
        if self.category_id and all(cat.id != self.category_id for cat in linked_categories):
            linked_categories.append(self.category)
        return linked_categories

    @classmethod
    def catalog_visibility_q(cls, include_uncategorized=True):
        visibility_q = Q(category__is_active=True) | Q(categories__is_active=True)
        if include_uncategorized:
            visibility_q |= Q(category__isnull=True, categories__isnull=True)
        return visibility_q

    @classmethod
    def catalog_visible(cls, queryset=None, include_uncategorized=True):
        """
        Products visible in public catalog:
        - Product must be active
        - At least one assigned category must be active
          (or uncategorized if include_uncategorized=True).
        """
        qs = queryset if queryset is not None else cls.objects.all()
        return qs.filter(is_active=True).filter(
            cls.catalog_visibility_q(include_uncategorized=include_uncategorized)
        ).distinct()

    def is_visible_in_catalog(self, include_uncategorized=True):
        if not self.is_active:
            return False
        linked_categories = self.get_linked_categories()
        if not linked_categories:
            return include_uncategorized
        return any(cat.is_active for cat in linked_categories)

    def extract_attributes_from_description(self):
        """
        Extract attributes from description based on category regex patterns.
        """
        category = self.get_primary_category()
        if not category or not self.description:
            return {}

        extracted = {}
        attrs = category.attributes.exclude(regex_pattern__isnull=True).exclude(regex_pattern="")

        for attr in attrs:
            try:
                match = re.search(attr.regex_pattern, self.description, re.IGNORECASE)
                if match:
                    val = match.group(1) if match.groups() else match.group(0)
                    extracted[attr.slug] = val.strip()
            except re.error:
                continue

        if extracted:
            if self.attributes is None:
                self.attributes = {}
            self.attributes.update(extracted)

        return extracted


class ClampSpecs(models.Model):
    """
    Structured technical specs for clamp products.
    OneToOne with Product.
    """

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="clamp_specs",
        verbose_name="Producto",
    )

    FABRICACION_CHOICES = [
        ("TREFILADA", "TREFILADA"),
        ("LAMINADA", "LAMINADA"),
    ]
    fabrication = models.CharField(
        max_length=20,
        choices=FABRICACION_CHOICES,
        null=True,
        blank=True,
        verbose_name="Fabricacion",
    )

    diameter = models.CharField(max_length=20, null=True, blank=True, verbose_name="Diametro")

    width = models.IntegerField(null=True, blank=True, verbose_name="Ancho (mm)")
    length = models.IntegerField(null=True, blank=True, verbose_name="Largo (mm)")

    FORMA_CHOICES = [
        ("PLANA", "PLANA"),
        ("CURVA", "CURVA"),
        ("SEMICURVA", "SEMICURVA"),
    ]
    shape = models.CharField(
        max_length=20,
        choices=FORMA_CHOICES,
        null=True,
        blank=True,
        verbose_name="Forma",
    )

    parse_confidence = models.IntegerField(default=0, verbose_name="Confianza parser (%)")
    parse_warnings = models.JSONField(default=list, blank=True, verbose_name="Warnings")

    manual_override = models.BooleanField(
        default=False,
        verbose_name="Manual override",
        help_text="Si es True, el parser no sobrescribira estos datos.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Especificacion abrazadera"
        verbose_name_plural = "Especificaciones abrazaderas"
        indexes = [
            models.Index(fields=["fabrication"]),
            models.Index(fields=["diameter"]),
            models.Index(fields=["width"]),
            models.Index(fields=["length"]),
            models.Index(fields=["shape"]),
        ]

    def __str__(self):
        return f"Spec for {self.product.sku}"
