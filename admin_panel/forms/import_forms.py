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
    UPDATE_MODE_CHOICES = [
        (
            "commercial",
            "Actualizar datos comerciales sin tocar categorias ni orden manual",
        ),
        (
            "prices",
            "Actualizar solo precios y costos",
        ),
        (
            "create_only",
            "Crear productos nuevos y omitir SKUs existentes",
        ),
    ]

    CATEGORY_MODE_CHOICES = [
        (
            "existing",
            "Usar solo categorias existentes para productos nuevos (recomendado)",
        ),
        (
            "ignore",
            "No tocar categorias de productos",
        ),
        (
            "hidden",
            "Crear categorias nuevas ocultas para revisar",
        ),
        (
            "create",
            "Crear categorias nuevas visibles",
        ),
    ]

    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label="Actualizar existentes",
        help_text="Si se encuentra un producto con el mismo SKU, actualiza sus datos.",
    )

    update_mode = forms.ChoiceField(
        choices=UPDATE_MODE_CHOICES,
        required=True,
        initial="commercial",
        label="Modo de actualizacion",
        help_text=(
            "El modo recomendado actualiza el catalogo comercial, conserva las categorias "
            "actuales de los articulos existentes y no toca el orden manual del catalogo/Excel."
        ),
    )

    category_mode = forms.ChoiceField(
        choices=CATEGORY_MODE_CHOICES,
        required=True,
        initial="existing",
        label="Categorias del Excel",
        help_text=(
            "Controla que hace el importador con Rubro/Categoria/Subrubro. "
            "Por defecto no crea categorias nuevas ni recategoriza productos existentes."
        ),
    )

    preserve_existing_categories = forms.BooleanField(
        required=False,
        initial=True,
        label="Conservar categorias actuales al actualizar productos existentes",
        help_text=(
            "Recomendado: si el SKU ya existe, actualiza precio, costo, stock y datos, "
            "pero deja el articulo en las categorias que ya tenia en el sistema."
        ),
    )

    allow_category_creation = forms.BooleanField(
        required=False,
        initial=False,
        label="Autorizar creacion de categorias nuevas desde el Excel",
        help_text=(
            "Usalo solo cuando quieras construir categorias desde cero. "
            "Si no esta marcado, los modos de creacion se tratan como 'solo existentes'."
        ),
    )

    is_global_base = forms.BooleanField(
        required=False,
        initial=False,
        label="Importar como Base Global (Bajas automáticas)",
        help_text=(
            "Si se activa, cualquier producto activo en la base de datos que NO "
            "esté presente en el archivo Excel de importación será desactivado y archivado "
            "bajo la categoría especial 'Bajas por Importación'."
        ),
    )


class ClientImportForm(ImportForm):
    pass


class CategoryImportForm(ImportForm):
    pass
