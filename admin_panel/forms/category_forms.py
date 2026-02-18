from django import forms

from catalog.models import Category


class CategoryForm(forms.ModelForm):
    """Form to create/edit categories."""

    class Meta:
        model = Category
        fields = ["name", "parent", "is_active", "seo_title", "seo_description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Nombre de la categoria"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "seo_title": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Titulo SEO (opcional)"}
            ),
            "seo_description": forms.Textarea(
                attrs={"class": "form-textarea", "rows": 3, "placeholder": "Descripcion SEO (opcional)"}
            ),
        }
        labels = {
            "name": "Nombre",
            "parent": "Categoria padre",
            "is_active": "Activa",
            "seo_title": "SEO title",
            "seo_description": "SEO description",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Category.objects.all().order_by("order", "name")
