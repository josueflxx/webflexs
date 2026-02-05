
from django import forms
from django.core.validators import FileExtensionValidator

class ImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo Excel (.xlsx)",
        validators=[FileExtensionValidator(allowed_extensions=['xlsx'])]
    )
    
    dry_run = forms.BooleanField(
        required=False, 
        initial=True, 
        label="Modo de Prueba (Dry Run)",
        help_text="Si está marcado, solo se validará el archivo sin guardar cambios en la base de datos."
    )
    
class ProductImportForm(ImportForm):
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label="Actualizar existentes",
        help_text="Si se encuentra un producto con el mismo SKU, actualizar sus datos."
    )

class ClientImportForm(ImportForm):
    # Same as generic for now, but explicit class allows for future extension
    pass

class CategoryImportForm(ImportForm):
    # Same as generic
    pass
