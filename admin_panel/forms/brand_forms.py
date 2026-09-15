from django import forms
from catalog.models import (
    Category,
    Brand,
    BrandAlias,
    BrandCatalogRule,
    BrandRubro,
    BrandSubrubro,
    CategoryBrandMapping,
)


class BrandForm(forms.ModelForm):
    """Form to create/edit Brands."""

    class Meta:
        model = Brand
        fields = ["name", "logo", "banner", "order", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Nombre de la marca"}),
            "logo": forms.ClearableFileInput(attrs={"class": "form-input"}),
            "banner": forms.ClearableFileInput(attrs={"class": "form-input"}),
            "order": forms.NumberInput(attrs={"class": "form-input", "min": 0}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }
        labels = {
            "name": "Nombre de Marca",
            "logo": "Logo (Imagen)",
            "banner": "Banner Comercial",
            "order": "Orden Manual",
            "is_active": "Activa",
        }


class BrandRubroForm(forms.ModelForm):
    """Form to create/edit BrandRubros."""

    class Meta:
        model = BrandRubro
        fields = ["brand", "name", "image", "order", "is_active"]
        widgets = {
            "brand": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Nombre del rubro"}),
            "image": forms.ClearableFileInput(attrs={"class": "form-input"}),
            "order": forms.NumberInput(attrs={"class": "form-input", "min": 0}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }
        labels = {
            "brand": "Marca",
            "name": "Nombre del Rubro",
            "image": "Imagen Representativa",
            "order": "Orden Manual",
            "is_active": "Activo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.all().order_by("order", "name")


class BrandSubrubroForm(forms.ModelForm):
    """Form to create/edit BrandSubrubros."""

    class Meta:
        model = BrandSubrubro
        fields = ["brand_rubro", "name", "image", "order", "is_active", "helper_categories"]
        widgets = {
            "brand_rubro": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Nombre del subrubro"}),
            "image": forms.ClearableFileInput(attrs={"class": "form-input"}),
            "order": forms.NumberInput(attrs={"class": "form-input", "min": 0}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "helper_categories": forms.SelectMultiple(attrs={"class": "form-select", "style": "height: 150px;"}),
        }
        labels = {
            "brand_rubro": "Rubro de Marca",
            "name": "Nombre del Subrubro",
            "image": "Imagen Representativa",
            "order": "Orden Manual",
            "is_active": "Activo",
            "helper_categories": "Categorías Ayudantes",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand_rubro"].queryset = BrandRubro.objects.select_related("brand").all().order_by("brand__order", "brand__name", "order", "name")
        self.fields["helper_categories"].queryset = Category.objects.all().order_by("order", "name")
        self.fields["helper_categories"].required = False


class BrandAliasForm(forms.ModelForm):
    class Meta:
        model = BrandAlias
        fields = ["brand", "value", "is_active"]
        widgets = {
            "brand": forms.Select(attrs={"class": "form-select"}),
            "value": forms.TextInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "Ej. Mercedes-Benz, M. Benz, VW",
                    "autocomplete": "off",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.order_by("order", "name")


class BrandCatalogRuleForm(forms.ModelForm):
    class Meta:
        model = BrandCatalogRule
        fields = [
            "brand",
            "brand_rubro",
            "brand_subrubro",
            "source_field",
            "match_mode",
            "pattern",
            "priority",
            "confidence",
            "is_active",
        ]
        widgets = {
            "brand": forms.Select(attrs={"class": "form-select", "data-catalog-brand": "true"}),
            "brand_rubro": forms.Select(attrs={"class": "form-select", "data-catalog-rubro": "true"}),
            "brand_subrubro": forms.Select(attrs={"class": "form-select", "data-catalog-subrubro": "true"}),
            "source_field": forms.Select(attrs={"class": "form-select"}),
            "match_mode": forms.Select(attrs={"class": "form-select"}),
            "pattern": forms.TextInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "Texto que identifica el producto",
                    "autocomplete": "off",
                }
            ),
            "priority": forms.NumberInput(attrs={"class": "form-input", "min": 0, "max": 999}),
            "confidence": forms.NumberInput(attrs={"class": "form-input", "min": 1, "max": 100}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].queryset = Brand.objects.order_by("order", "name")
        self.fields["brand_rubro"].queryset = BrandRubro.objects.select_related("brand").order_by(
            "brand__order", "brand__name", "order", "name"
        )
        self.fields["brand_subrubro"].queryset = BrandSubrubro.objects.select_related(
            "brand_rubro__brand"
        ).order_by(
            "brand_rubro__brand__order",
            "brand_rubro__brand__name",
            "brand_rubro__order",
            "brand_rubro__name",
            "order",
            "name",
        )
        self.fields["brand_rubro"].required = False
        self.fields["brand_subrubro"].required = False


class CategoryBrandMappingForm(forms.ModelForm):
    class Meta:
        model = CategoryBrandMapping
        fields = [
            "source_category",
            "canonical_category",
            "brand",
            "brand_rubro",
            "brand_subrubro",
            "include_descendants",
            "observation",
        ]
        widgets = {
            "source_category": forms.Select(attrs={"class": "form-select"}),
            "canonical_category": forms.Select(attrs={"class": "form-select"}),
            "brand": forms.Select(attrs={"class": "form-select", "data-mapping-brand": "true"}),
            "brand_rubro": forms.Select(attrs={"class": "form-select", "data-mapping-rubro": "true"}),
            "brand_subrubro": forms.Select(attrs={"class": "form-select", "data-mapping-subrubro": "true"}),
            "include_descendants": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "observation": forms.TextInput(
                attrs={
                    "class": "form-input",
                    "maxlength": 300,
                    "placeholder": "Motivo del vinculo (opcional)",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_category"].queryset = Category.objects.select_related("parent").order_by("name")
        self.fields["canonical_category"].queryset = Category.objects.filter(is_active=True).order_by("name")
        self.fields["canonical_category"].required = False
        self.fields["brand"].queryset = Brand.objects.filter(is_active=True).order_by("order", "name")
        self.fields["brand_rubro"].queryset = BrandRubro.objects.filter(is_active=True).select_related("brand").order_by(
            "brand__order", "brand__name", "order", "name"
        )
        self.fields["brand_subrubro"].queryset = BrandSubrubro.objects.filter(is_active=True).select_related(
            "brand_rubro__brand"
        ).order_by(
            "brand_rubro__brand__order",
            "brand_rubro__brand__name",
            "brand_rubro__order",
            "brand_rubro__name",
            "order",
            "name",
        )
        self.fields["brand_subrubro"].required = False
        self.fields["observation"].required = False
        self.fields["observation"].label = "Observacion (opcional)"
