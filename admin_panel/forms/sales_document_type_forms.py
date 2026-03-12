from django import forms
from django.contrib.auth.models import User

from core.models import FiscalPointOfSale, SalesDocumentType, Warehouse


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
            "default_sales_user",
            "billing_mode",
            "internal_doc_type",
            "fiscal_doc_type",
            "is_default",
            "display_order",
        ]

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

        self.fields["point_of_sale"].required = False
        self.fields["default_warehouse"].required = False
        self.fields["default_sales_user"].required = False
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

        self.fields["default_sales_user"].queryset = User.objects.filter(
            is_staff=True,
            is_active=True,
        ).order_by("username")

        for name in [
            "code",
            "name",
            "letter",
            "last_number",
            "display_order",
        ]:
            self.fields[name].widget.attrs.update({"class": "form-input"})
        for name in [
            "point_of_sale",
            "document_behavior",
            "default_warehouse",
            "default_sales_user",
            "billing_mode",
            "internal_doc_type",
            "fiscal_doc_type",
        ]:
            self.fields[name].widget.attrs.update({"class": "form-select"})
        for name in [
            "enabled",
            "generate_stock_movement",
            "generate_account_movement",
            "group_equal_products",
            "prioritize_default_warehouse",
            "is_default",
        ]:
            self.fields[name].widget.attrs.update({"style": "width:18px; height:18px;"})

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.company:
            instance.company = self.company
        if commit:
            instance.save()
        return instance
