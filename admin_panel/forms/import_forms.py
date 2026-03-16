from django import forms
from django.conf import settings
from django.core.validators import FileExtensionValidator
import zipfile


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

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        max_size = int(getattr(settings, "IMPORT_MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024))
        if uploaded_file.size and uploaded_file.size > max_size:
            max_mb = round(max_size / (1024 * 1024), 1)
            raise forms.ValidationError(
                f"El archivo supera el tamaño máximo permitido ({max_mb} MB)."
            )

        allowed_types = set(getattr(settings, "IMPORT_ALLOWED_CONTENT_TYPES", ()) or ())
        content_type = str(getattr(uploaded_file, "content_type", "") or "").strip().lower()
        if allowed_types and content_type and content_type not in allowed_types:
            raise forms.ValidationError("El tipo de archivo no es válido para una importación Excel.")

        try:
            current_pos = uploaded_file.tell()
        except Exception:
            current_pos = None
        try:
            if not zipfile.is_zipfile(uploaded_file):
                raise forms.ValidationError("El archivo no tiene un formato XLSX válido.")
        finally:
            try:
                if current_pos is not None:
                    uploaded_file.seek(current_pos)
                else:
                    uploaded_file.seek(0)
            except Exception:
                pass

        return uploaded_file


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
