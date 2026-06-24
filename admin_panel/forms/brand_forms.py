from django import forms
from catalog.models import Category, Brand, BrandRubro, BrandSubrubro


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
