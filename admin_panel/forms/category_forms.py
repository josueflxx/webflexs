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

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        instance = self.instance

        if not instance or not getattr(instance, "pk", None) or parent is None:
            return parent

        if parent.pk == instance.pk:
            raise forms.ValidationError("Una categoria no puede ser su propio padre.")

        descendant_ids = instance.get_descendant_ids(include_self=False)
        if parent.pk in descendant_ids:
            raise forms.ValidationError(
                "No puedes mover esta categoria dentro de una de sus subcategorias."
            )

        return parent
