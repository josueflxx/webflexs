from django import forms
from django.core.validators import FileExtensionValidator


class ImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo Excel (.xlsx)",
        validators=[FileExtensionValidator(allowed_extensions=["xlsx"])],
    )

    dry_run = forms.BooleanField(
        required=False,
        initial=True,
        label="Modo de prueba (Dry Run)",
        help_text="Si esta marcado, solo valida el archivo y no guarda cambios.",
    )

    confirm_apply = forms.BooleanField(
        required=False,
        initial=False,
        label="Confirmo aplicacion real",
        help_text="Obligatorio si desactivas Dry Run. Revisa preview antes de ejecutar.",
    )


class ProductImportForm(ImportForm):
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label="Actualizar existentes",
        help_text="Si se encuentra un producto con el mismo SKU, actualiza sus datos.",
    )


class ClientImportForm(ImportForm):
    pass


class CategoryImportForm(ImportForm):
    pass
