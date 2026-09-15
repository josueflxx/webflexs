from django import forms
from catalog.models import Brand, BrandStructureBatch
from catalog.services.brand_structure import clean_name, normalize_name


class BrandStructureForm(forms.Form):
    draft = forms.IntegerField(required=False, widget=forms.HiddenInput)
    scope = forms.ChoiceField(choices=[("selected", "Marcas seleccionadas"), ("all", "Todas las marcas")], initial="selected", widget=forms.RadioSelect)
    include_inactive = forms.BooleanField(required=False, label="Incluir marcas inactivas")
    brand_ids = forms.ModelMultipleChoiceField(queryset=Brand.objects.none(), required=False)
    operation = forms.ChoiceField(choices=BrandStructureBatch.OPERATION_CHOICES, label="Operación")
    names = forms.CharField(required=False, label="Nombres nuevos (uno por línea)", widget=forms.Textarea(attrs={"rows": 4, "placeholder": "BUJES ARMADOS\nBUJES DE GOMA"}))
    rubro_name = forms.CharField(required=False, max_length=100, label="Rubro", widget=forms.TextInput(attrs={"list": "structure-rubros", "autocomplete": "off"}))
    subrubro_name = forms.CharField(required=False, max_length=100, label="Subrubro a editar", widget=forms.TextInput(attrs={"list": "structure-subrubros", "autocomplete": "off"}))
    missing_parent = forms.ChoiceField(choices=[("skip", "Omitir marcas donde no exista el rubro"), ("create", "Crear también el rubro donde falte")], initial="skip", required=False, label="Si falta el rubro padre")
    change_name = forms.BooleanField(required=False, label="Cambiar nombre")
    new_name = forms.CharField(required=False, max_length=100, label="Nuevo nombre")
    change_order = forms.BooleanField(required=False, label="Cambiar orden")
    order = forms.IntegerField(required=False, min_value=0, max_value=2147483647, initial=0, label="Orden")
    change_is_active = forms.BooleanField(required=False, label="Cambiar estado")
    is_active = forms.BooleanField(required=False, initial=True, label="Activo")
    change_image = forms.BooleanField(required=False, label="Cambiar imagen")
    image = forms.ImageField(required=False, label="Imagen compartida", widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}))
    remove_image = forms.BooleanField(required=False, label="Quitar la imagen existente")
    observation = forms.CharField(required=False, max_length=300, label="Motivo del cambio (opcional)", widget=forms.TextInput(attrs={"placeholder": "Ej. Unificar la estructura comercial"}))

    def __init__(self, *args, previous=None, **kwargs):
        self.previous = previous
        super().__init__(*args, **kwargs)
        self.fields["brand_ids"].queryset = Brand.objects.order_by("order", "name")
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect, forms.HiddenInput)):
                field.widget.attrs["class"] = "structure-input"

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and (image.size > 5 * 1024 * 1024 or image.image.format not in {"JPEG", "PNG", "WEBP"}):
            raise forms.ValidationError("Usá PNG, JPG o WebP de hasta 5 MB.")
        return image

    def clean(self):
        data = super().clean()
        brands = data.get("brand_ids", Brand.objects.none())
        if data.get("scope") == "all":
            brands = Brand.objects.all()
            if not data.get("include_inactive"):
                brands = brands.filter(is_active=True)
        elif not data.get("include_inactive") and brands.filter(is_active=False).exists():
            self.add_error("brand_ids", "Activá «Incluir marcas inactivas» o quitá esas marcas.")
        data["resolved_brand_ids"] = sorted(brands.values_list("pk", flat=True))
        if not data["resolved_brand_ids"]:
            self.add_error("brand_ids", "Seleccioná al menos una marca.")
        if len(data["resolved_brand_ids"]) > 250:
            self.add_error("brand_ids", "Seleccioná hasta 250 marcas por lote.")
        op = data.get("operation", "")
        if op.startswith("create_"):
            lines = (data.get("names") or "").splitlines()
            try:
                names = [clean_name(line) for line in lines if line.strip()]
                if not names or len(names) > 30:
                    raise forms.ValidationError("Ingresá entre 1 y 30 nombres, uno por línea.")
                if len({normalize_name(name) for name in names}) != len(names):
                    raise forms.ValidationError("Hay nombres repetidos o equivalentes en la lista.")
                data["names_list"] = names
            except forms.ValidationError as exc:
                self.add_error("names", exc)
            data["order"] = data.get("order") or 0
        elif op.startswith("update_"):
            if not any(data.get(f"change_{field}") for field in ("name", "order", "is_active", "image")):
                raise forms.ValidationError("Marcá al menos un campo para modificar.")
            if data.get("change_order") and data.get("order") is None:
                self.add_error("order", "Ingresá el orden que querés aplicar.")
        required_names = []
        if op != "create_rubro":
            required_names.append("rubro_name")
        if op == "update_subrubro":
            required_names.append("subrubro_name")
        if op.startswith("update_") and data.get("change_name"):
            required_names.append("new_name")
        for field in required_names:
            try:
                data[field] = clean_name(data.get(field))
            except forms.ValidationError as exc:
                self.add_error(field, exc)
        if data.get("remove_image") and data.get("image"):
            self.add_error("image", "Elegí una nueva imagen o quitarla, no ambas opciones.")
        if op.startswith("update_") and data.get("change_image") and not (data.get("image") or data.get("remove_image") or self.previous and self.previous.image):
            self.add_error("image", "Seleccioná una imagen o marcá «Quitar la imagen existente».")
        return data
