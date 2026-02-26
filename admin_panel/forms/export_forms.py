from django import forms

from catalog.models import Category, Supplier
from core.models import (
    CatalogExcelTemplate,
    CatalogExcelTemplateSheet,
    CatalogExcelTemplateColumn,
)


class CatalogExcelTemplateForm(forms.ModelForm):
    class Meta:
        model = CatalogExcelTemplate
        fields = [
            "name",
            "description",
            "is_active",
            "is_client_download_enabled",
            "client_download_label",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Ej: Catalogo clientes mayoristas"}),
            "description": forms.TextInput(attrs={"class": "form-input", "placeholder": "Opcional"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "is_client_download_enabled": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "client_download_label": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Ej: Descargar catalogo actualizado"}
            ),
        }
        labels = {
            "name": "Nombre",
            "description": "Descripcion",
            "is_active": "Activa",
            "is_client_download_enabled": "Disponible para clientes",
            "client_download_label": "Texto boton cliente",
        }


class CatalogExcelTemplateSheetForm(forms.ModelForm):
    class Meta:
        model = CatalogExcelTemplateSheet
        fields = [
            "name",
            "order",
            "include_header",
            "only_active_products",
            "only_catalog_visible",
            "include_descendant_categories",
            "sort_by",
            "search_query",
            "max_rows",
            "categories",
            "suppliers",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Ej: Activos - Abrazaderas"}),
            "order": forms.NumberInput(attrs={"class": "form-input", "min": "0"}),
            "include_header": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "only_active_products": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "only_catalog_visible": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "include_descendant_categories": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "sort_by": forms.Select(attrs={"class": "form-select"}),
            "search_query": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Filtro texto opcional (SKU, nombre, descripcion...)"},
            ),
            "max_rows": forms.NumberInput(attrs={"class": "form-input", "min": "1", "placeholder": "Vacio = sin limite"}),
            "categories": forms.SelectMultiple(attrs={"class": "form-select", "size": "10"}),
            "suppliers": forms.SelectMultiple(attrs={"class": "form-select", "size": "8"}),
        }
        labels = {
            "name": "Nombre hoja",
            "order": "Orden",
            "include_header": "Incluir encabezado",
            "only_active_products": "Solo productos activos",
            "only_catalog_visible": "Solo visibles en catalogo",
            "include_descendant_categories": "Incluir subcategorias",
            "sort_by": "Orden resultados",
            "search_query": "Busqueda interna",
            "max_rows": "Limite de filas",
            "categories": "Categorias",
            "suppliers": "Proveedores",
        }
        help_texts = {
            "categories": "Si seleccionas categorias, solo exporta productos que pertenezcan a esas categorias.",
            "suppliers": "Opcional. Filtra solo productos de los proveedores elegidos.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].queryset = Category.objects.order_by("order", "name")
        self.fields["suppliers"].queryset = Supplier.objects.order_by("name")


class CatalogExcelTemplateColumnForm(forms.ModelForm):
    class Meta:
        model = CatalogExcelTemplateColumn
        fields = ["key", "header", "order", "is_active"]
        widgets = {
            "key": forms.Select(attrs={"class": "form-select"}),
            "header": forms.TextInput(attrs={"class": "form-input", "placeholder": "Opcional"}),
            "order": forms.NumberInput(attrs={"class": "form-input", "min": "0"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }
        labels = {
            "key": "Campo",
            "header": "Encabezado",
            "order": "Orden",
            "is_active": "Activa",
        }
