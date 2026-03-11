from django.contrib import admin

from core.models import (
    Company,
    DocumentSeries,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalEmissionAttempt,
    FiscalPointOfSale,
    FiscalDocumentSeries,
    InternalDocument,
    SiteSettings,
    UserActivity,
    CatalogAnalyticsEvent,
    AdminAuditLog,
    ImportExecution,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "legal_name",
        "cuit",
        "tax_condition",
        "point_of_sale_default",
        "email",
        "default_price_list",
        "is_active",
    )
    search_fields = ("name", "legal_name", "cuit", "email")
    list_filter = ("is_active", "default_price_list", "tax_condition")


@admin.register(DocumentSeries)
class DocumentSeriesAdmin(admin.ModelAdmin):
    list_display = ("company", "doc_type", "next_number", "updated_at")
    list_filter = ("company", "doc_type")
    search_fields = ("company__name",)


@admin.register(FiscalDocumentSeries)
class FiscalDocumentSeriesAdmin(admin.ModelAdmin):
    list_display = ("company", "point_of_sale_ref", "point_of_sale", "doc_type", "next_number", "updated_at")
    list_filter = ("company", "doc_type", "point_of_sale_ref")
    search_fields = ("company__name", "point_of_sale", "point_of_sale_ref__number")


@admin.register(FiscalPointOfSale)
class FiscalPointOfSaleAdmin(admin.ModelAdmin):
    list_display = ("company", "number", "name", "environment", "is_default", "is_active")
    list_filter = ("company", "environment", "is_default", "is_active")
    search_fields = ("company__name", "number", "name")


class FiscalDocumentItemInline(admin.TabularInline):
    model = FiscalDocumentItem
    extra = 0


class FiscalEmissionAttemptInline(admin.TabularInline):
    model = FiscalEmissionAttempt
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(FiscalDocument)
class FiscalDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "point_of_sale",
        "doc_type",
        "number",
        "status",
        "issue_mode",
        "total",
        "cae",
        "created_at",
    )
    list_filter = ("company", "doc_type", "status", "issue_mode", "point_of_sale")
    search_fields = ("source_key", "cae", "external_id", "external_number")
    inlines = (FiscalDocumentItemInline, FiscalEmissionAttemptInline)


@admin.register(FiscalDocumentItem)
class FiscalDocumentItemAdmin(admin.ModelAdmin):
    list_display = ("fiscal_document", "line_number", "sku", "quantity", "net_amount", "iva_amount", "total_amount")
    list_filter = ("fiscal_document__company", "iva_rate")
    search_fields = ("fiscal_document__source_key", "sku", "description")


@admin.register(FiscalEmissionAttempt)
class FiscalEmissionAttemptAdmin(admin.ModelAdmin):
    list_display = ("fiscal_document", "result_status", "triggered_by", "error_code", "created_at")
    list_filter = ("result_status", "created_at")
    search_fields = ("fiscal_document__source_key", "error_code", "error_message")


@admin.register(InternalDocument)
class InternalDocumentAdmin(admin.ModelAdmin):
    list_display = ("doc_type", "number", "company", "client_company_ref", "issued_at", "is_cancelled")
    list_filter = ("doc_type", "company", "is_cancelled")
    search_fields = ("source_key", "company__name", "client_company_ref__client_profile__company_name")


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("company_name", "show_public_prices", "require_primary_category_for_multicategory")


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "is_online", "last_activity")
    search_fields = ("user__username",)


@admin.register(CatalogAnalyticsEvent)
class CatalogAnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "query", "category_slug", "results_count", "user")
    list_filter = ("event_type", "created_at")
    search_fields = ("query", "category_slug")


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target_type", "target_id", "user")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("action", "target_type", "target_id", "user__username")


@admin.register(ImportExecution)
class ImportExecutionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "import_type", "company", "status", "dry_run", "created_count", "updated_count", "error_count")
    list_filter = ("import_type", "status", "dry_run", "created_at")
    search_fields = ("file_name", "user__username")
