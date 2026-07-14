import zipfile

from django import forms
from django.conf import settings
from django.core.validators import FileExtensionValidator

from catalog.models import ProductSupplier, Supplier, SupplierImportProfile
from catalog.services.supplier_price_lists import IDENTITY_FIELDS, MAPPING_FIELDS


class SupplierPriceListUploadForm(forms.Form):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.filter(is_active=True).order_by("name"),
        label="Proveedor",
    )
    source_file = forms.FileField(
        label="Lista XLSX o CSV",
        validators=[FileExtensionValidator(allowed_extensions=["xlsx", "csv"])],
    )
    profile = forms.ModelChoiceField(
        queryset=SupplierImportProfile.objects.filter(is_active=True).select_related("supplier"),
        required=False,
        label="Perfil de mapeo existente",
        help_text="Opcional. El perfil debe pertenecer al proveedor elegido.",
    )

    def clean_source_file(self):
        uploaded_file = self.cleaned_data["source_file"]
        max_size = int(getattr(settings, "IMPORT_MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024))
        if uploaded_file.size and uploaded_file.size > max_size:
            raise forms.ValidationError(
                f"El archivo supera el limite de {round(max_size / 1024 / 1024, 1)} MB."
            )
        extension = str(uploaded_file.name or "").lower().rsplit(".", 1)[-1]
        if extension == "xlsx":
            try:
                position = uploaded_file.tell()
            except Exception:
                position = 0
            try:
                if not zipfile.is_zipfile(uploaded_file):
                    raise forms.ValidationError("El archivo no tiene un formato XLSX valido.")
                uploaded_file.seek(0)
                with zipfile.ZipFile(uploaded_file) as archive:
                    entries = archive.infolist()
                    max_uncompressed = int(
                        getattr(settings, "SUPPLIER_PRICE_LIST_MAX_UNCOMPRESSED_BYTES", 100 * 1024 * 1024)
                    )
                    if len(entries) > 10000 or sum(item.file_size for item in entries) > max_uncompressed:
                        raise forms.ValidationError("El XLSX expandido supera el limite de seguridad.")
            finally:
                uploaded_file.seek(position)
        else:
            sample = uploaded_file.read(4096)
            uploaded_file.seek(0)
            if b"\x00" in sample:
                raise forms.ValidationError("El CSV contiene datos binarios y no es valido.")
        return uploaded_file

    def clean(self):
        cleaned = super().clean()
        supplier = cleaned.get("supplier")
        profile = cleaned.get("profile")
        if supplier and profile and profile.supplier_id != supplier.id:
            self.add_error("profile", "El perfil no pertenece al proveedor seleccionado.")
        return cleaned


class SupplierPriceListMappingForm(forms.Form):
    sheet_name = forms.ChoiceField(label="Hoja")
    header_row = forms.IntegerField(min_value=1, max_value=500, initial=1, label="Fila de encabezado")
    default_currency = forms.ChoiceField(
        choices=ProductSupplier.CURRENCY_CHOICES,
        initial=ProductSupplier.CURRENCY_ARS,
        label="Moneda predeterminada",
    )
    save_profile = forms.BooleanField(required=False, label="Guardar este mapeo como perfil")
    profile_name = forms.CharField(required=False, max_length=120, label="Nombre del perfil")

    def __init__(self, *args, headers=(), sheets=(), initial_mapping=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sheet_name"].choices = [(name, name) for name in sheets]
        column_choices = [("", "-- No usar --")] + [(header, header) for header in headers]
        initial_mapping = initial_mapping or {}
        for field_name, label in MAPPING_FIELDS:
            required = field_name == "cost"
            self.fields[field_name] = forms.ChoiceField(
                choices=column_choices,
                required=required,
                label=label,
            )
            self.order_fields(
                ["sheet_name", "header_row", "default_currency"]
                + [name for name, _label in MAPPING_FIELDS]
                + ["save_profile", "profile_name"]
            )
            if field_name in initial_mapping:
                self.fields[field_name].initial = initial_mapping[field_name]

    def clean(self):
        cleaned = super().clean()
        mapping = {
            field_name: cleaned.get(field_name, "")
            for field_name, _label in MAPPING_FIELDS
            if cleaned.get(field_name)
        }
        if not any(mapping.get(field) for field in IDENTITY_FIELDS):
            raise forms.ValidationError(
                "Mapea al menos codigo de proveedor, SKU interno o descripcion."
            )
        selected_columns = list(mapping.values())
        if len(selected_columns) != len(set(selected_columns)):
            raise forms.ValidationError("Cada columna del archivo solo puede usarse una vez.")
        if cleaned.get("save_profile") and not str(cleaned.get("profile_name") or "").strip():
            self.add_error("profile_name", "Indica un nombre para guardar el perfil.")
        cleaned["column_mapping"] = mapping
        return cleaned


class SupplierPriceListApplyForm(forms.Form):
    confirmation = forms.CharField(label="Confirmacion")

    def __init__(self, *args, batch, **kwargs):
        self.batch = batch
        super().__init__(*args, **kwargs)

    def clean_confirmation(self):
        value = str(self.cleaned_data["confirmation"] or "").strip().upper()
        expected = f"APLICAR LISTA {self.batch.pk}"
        if value != expected:
            raise forms.ValidationError(f'Escribe exactamente "{expected}".')
        return value
