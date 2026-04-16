from django import forms
from django.contrib.auth.models import User

from core.models import (
    FiscalPointOfSale,
    SALES_BEHAVIOR_COTIZACION,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BEHAVIOR_NOTA_DEBITO,
    SALES_BEHAVIOR_PRESUPUESTO,
    SALES_BILLING_MODE_INTERNAL_DOCUMENT,
    SALES_DEFAULT_USER_CURRENT,
    SALES_DEFAULT_USER_NONE,
    SALES_DEFAULT_USER_SPECIFIC,
    SalesDocumentType,
    Warehouse,
)


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["code", "name", "is_active", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs.update({"class": "form-input"})
        self.fields["name"].widget.attrs.update({"class": "form-input"})
        self.fields["notes"].widget.attrs.update({"class": "form-textarea"})
        self.fields["is_active"].widget.attrs.update({"style": "width:18px; height:18px;"})


class SalesDocumentTypeForm(forms.ModelForm):
    default_sales_user_selector = forms.ChoiceField(required=False)
    print_locality = forms.ChoiceField(required=False)

    class Meta:
        model = SalesDocumentType
        fields = [
            "code",
            "name",
            "letter",
            "point_of_sale",
            "last_number",
            "enabled",
            "document_behavior",
            "generate_stock_movement",
            "generate_account_movement",
            "group_equal_products",
            "default_warehouse",
            "prioritize_default_warehouse",
            "billing_mode",
            "use_document_situation",
            "internal_doc_type",
            "fiscal_doc_type",
            "default_origin_channel",
            "print_address",
            "print_email",
            "print_phones",
            "print_locality",
            "print_signature",
            "base_design",
            "notes",
            "is_default",
            "display_order",
        ]

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        if company:
            # Ensure model-level clean() validates against the active company
            # during form.is_valid(), before save() assigns company.
            self.instance.company = company

        self.fields["point_of_sale"].required = False
        self.fields["default_warehouse"].required = False
        self.fields["internal_doc_type"].required = False
        self.fields["fiscal_doc_type"].required = False

        if company:
            self.fields["point_of_sale"].queryset = FiscalPointOfSale.objects.filter(
                company=company
            ).order_by("number")
            self.fields["default_warehouse"].queryset = Warehouse.objects.filter(
                company=company
            ).order_by("name")
        else:
            self.fields["point_of_sale"].queryset = FiscalPointOfSale.objects.none()
            self.fields["default_warehouse"].queryset = Warehouse.objects.none()

        sales_users = list(
            User.objects.filter(
                is_staff=True,
                is_active=True,
            ).order_by("username")
        )
        self.fields["default_sales_user_selector"].choices = [
            (SALES_DEFAULT_USER_CURRENT, "El usuario que agrega la venta"),
            (SALES_DEFAULT_USER_NONE, "Sin especificar"),
            *[(f"{SALES_DEFAULT_USER_SPECIFIC}:{user.pk}", user.get_username()) for user in sales_users],
        ]
        self.fields["default_sales_user_selector"].widget.attrs.update({"class": "form-select"})

        if self.instance and self.instance.pk:
            if self.instance.default_sales_user_mode == SALES_DEFAULT_USER_SPECIFIC and self.instance.default_sales_user_id:
                self.fields["default_sales_user_selector"].initial = (
                    f"{SALES_DEFAULT_USER_SPECIFIC}:{self.instance.default_sales_user_id}"
                )
            else:
                self.fields["default_sales_user_selector"].initial = self.instance.default_sales_user_mode
        else:
            self.fields["default_sales_user_selector"].initial = SALES_DEFAULT_USER_CURRENT

        locality_values = []
        if company:
            if company.fiscal_city:
                locality_values.append(company.fiscal_city.strip())
            locality_values.extend(
                value.strip()
                for value in SalesDocumentType.objects.filter(company=company)
                .exclude(print_locality="")
                .values_list("print_locality", flat=True)
            )
        seen = set()
        localities = []
        for value in locality_values:
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            localities.append(value)
        self.fields["print_locality"].choices = [("", "Sin especificar"), *[(value, value) for value in localities]]
        self.fields["print_locality"].widget.attrs.update({"class": "form-select"})

        for name in [
            "code",
            "name",
            "letter",
            "last_number",
            "display_order",
            "print_address",
            "print_email",
            "print_phones",
        ]:
            self.fields[name].widget.attrs.update({"class": "form-input"})
        for name in [
            "point_of_sale",
            "document_behavior",
            "default_warehouse",
            "billing_mode",
            "internal_doc_type",
            "fiscal_doc_type",
            "default_origin_channel",
            "base_design",
        ]:
            self.fields[name].widget.attrs.update({"class": "form-select"})
        for name in [
            "enabled",
            "generate_stock_movement",
            "generate_account_movement",
            "group_equal_products",
            "prioritize_default_warehouse",
            "use_document_situation",
            "is_default",
        ]:
            self.fields[name].widget.attrs.update({"style": "width:18px; height:18px;"})

        self.fields["print_signature"].widget.attrs.update({"class": "form-textarea", "rows": 4})
        self.fields["notes"].widget.attrs.update({"class": "form-textarea", "rows": 4})

    def clean_default_sales_user_selector(self):
        raw = str(self.cleaned_data.get("default_sales_user_selector") or "").strip()
        if not raw:
            return SALES_DEFAULT_USER_CURRENT
        if raw.startswith(f"{SALES_DEFAULT_USER_SPECIFIC}:"):
            user_id = raw.split(":", 1)[1].strip()
            if not user_id.isdigit():
                raise forms.ValidationError("Selecciona un usuario valido para el vendedor predeterminado.")
            user_exists = User.objects.filter(
                pk=int(user_id),
                is_staff=True,
                is_active=True,
            ).exists()
            if not user_exists:
                raise forms.ValidationError("El usuario vendedor seleccionado no esta disponible.")
        return raw

    def clean(self):
        cleaned_data = super().clean()
        behavior = cleaned_data.get("document_behavior")
        billing_mode = cleaned_data.get("billing_mode")
        point_of_sale = cleaned_data.get("point_of_sale")
        is_default = bool(cleaned_data.get("is_default"))
        default_origin_channel = str(cleaned_data.get("default_origin_channel") or "").strip().lower()
        generate_account_movement = bool(cleaned_data.get("generate_account_movement"))
        generate_stock_movement = bool(cleaned_data.get("generate_stock_movement"))

        fiscal_behaviors = {
            SALES_BEHAVIOR_FACTURA,
            SALES_BEHAVIOR_NOTA_CREDITO,
            SALES_BEHAVIOR_NOTA_DEBITO,
        }
        if billing_mode != SALES_BILLING_MODE_INTERNAL_DOCUMENT and behavior not in fiscal_behaviors:
            self.add_error(
                "billing_mode",
                "Solo Factura / Nota de credito / Nota de debito permiten modo fiscal.",
            )
        if billing_mode != SALES_BILLING_MODE_INTERNAL_DOCUMENT and not point_of_sale:
            self.add_error(
                "point_of_sale",
                "Debes elegir un punto de venta para los modos fiscales.",
            )

        no_stock_behaviors = {
            SALES_BEHAVIOR_COTIZACION,
            SALES_BEHAVIOR_PRESUPUESTO,
        }
        if behavior in no_stock_behaviors and generate_stock_movement:
            self.add_error(
                "generate_stock_movement",
                "Cotizacion y Presupuesto no deben generar stock.",
            )
        if behavior in no_stock_behaviors and generate_account_movement:
            self.add_error(
                "generate_account_movement",
                "Cotizacion y Presupuesto no deben impactar cuenta corriente.",
            )

        if self.company and behavior and is_default:
            existing_default_qs = SalesDocumentType.objects.filter(
                company=self.company,
                document_behavior=behavior,
                default_origin_channel=default_origin_channel,
                is_default=True,
            )
            if self.instance and self.instance.pk:
                existing_default_qs = existing_default_qs.exclude(pk=self.instance.pk)
            if existing_default_qs.exists():
                self.add_error(
                    "is_default",
                    "Ya existe un tipo de venta predeterminado para este comportamiento y canal.",
                )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        selector = str(self.cleaned_data.get("default_sales_user_selector") or SALES_DEFAULT_USER_CURRENT).strip()
        instance.default_sales_user = None

        if selector == SALES_DEFAULT_USER_NONE:
            instance.default_sales_user_mode = SALES_DEFAULT_USER_NONE
        elif selector.startswith(f"{SALES_DEFAULT_USER_SPECIFIC}:"):
            user_id = selector.split(":", 1)[1].strip()
            user_obj = User.objects.filter(
                pk=int(user_id) if user_id.isdigit() else None,
                is_staff=True,
                is_active=True,
            ).first()
            if user_obj:
                instance.default_sales_user_mode = SALES_DEFAULT_USER_SPECIFIC
                instance.default_sales_user = user_obj
            else:
                instance.default_sales_user_mode = SALES_DEFAULT_USER_NONE
        else:
            instance.default_sales_user_mode = SALES_DEFAULT_USER_CURRENT

        if self.company:
            instance.company = self.company
        if commit:
            instance.save()
        return instance
