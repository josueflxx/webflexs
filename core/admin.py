from django.contrib import admin

from core.models import (
    Company,
    DocumentSeries,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalEmissionAttempt,
    FiscalPointOfSale,
    FiscalDocumentSeries,
    FiscalSeriesReconciliation,
    FiscalMutationAudit,
    InternalDocument,
    SiteSettings,
    UserActivity,
    CatalogAnalyticsEvent,
    AdminAuditLog,
    ExternalEditorJob,
    ExternalEditorJobItem,
    ExternalEditorDraft,
    ExternalEditorSavedView,
    ImportExecution,
    ProductWarehouseStock,
)


def _concrete_field_names(model):
    """Return every persisted field so newly added fiscal fields stay read-only."""
    return tuple(field.name for field in model._meta.concrete_fields)


class ReadOnlyFiscalAdminMixin:
    """Expose fiscal evidence for inspection without any mutation path."""

    actions = ()

    def get_readonly_fields(self, request, obj=None):
        return _concrete_field_names(self.model)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyFiscalInlineMixin:
    """Read-only counterpart for evidence embedded in a fiscal document."""

    extra = 0
    max_num = 0
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        return _concrete_field_names(self.model)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    SYSTEM_MANAGED_FIELDS = (
        "remote_last_authorized",
        "last_reconciled_at",
        "blocked_at",
        "blocked_reason",
        "blocked_by_document",
        "version",
        "created_at",
        "updated_at",
    )
    IDENTITY_AND_NUMBER_FIELDS = (
        "company",
        "point_of_sale_ref",
        "point_of_sale",
        "doc_type",
        "issuer_cuit",
        "environment",
        "next_number",
    )

    list_display = ("company", "point_of_sale_ref", "point_of_sale", "doc_type", "next_number", "updated_at")
    list_filter = ("company", "doc_type", "point_of_sale_ref")
    search_fields = ("company__name", "point_of_sale", "point_of_sale_ref__number")

    def _has_fiscal_usage(self, obj):
        if not obj or not obj.pk:
            return False
        if obj.blocked_by_document_id or obj.reconciliations.exists():
            return True

        documents = FiscalDocument.objects.filter(
            company_id=obj.company_id,
            doc_type=obj.doc_type,
        )
        if obj.point_of_sale_ref_id:
            documents = documents.filter(point_of_sale_id=obj.point_of_sale_ref_id)
        else:
            documents = documents.filter(
                issuer_cuit_snapshot=obj.issuer_cuit,
                environment_snapshot=obj.environment,
                point_of_sale_number_snapshot=obj.point_of_sale,
            )
        return documents.exists()

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.SYSTEM_MANAGED_FIELDS)
        if self._has_fiscal_usage(obj):
            readonly.extend(self.IDENTITY_AND_NUMBER_FIELDS)
        return tuple(dict.fromkeys(readonly))


@admin.register(FiscalPointOfSale)
class FiscalPointOfSaleAdmin(admin.ModelAdmin):
    IDENTITY_FIELDS = ("company", "number", "environment")

    list_display = ("company", "number", "name", "environment", "is_default", "is_active")
    list_filter = ("company", "environment", "is_default", "is_active")
    search_fields = ("company__name", "number", "name")

    def _has_fiscal_usage(self, obj):
        if not obj or not obj.pk:
            return False
        return obj.fiscal_documents.exists() or obj.fiscal_series.exists()

    def get_readonly_fields(self, request, obj=None):
        readonly = ["created_at", "updated_at"]
        if self._has_fiscal_usage(obj):
            readonly.extend(self.IDENTITY_FIELDS)
        return tuple(readonly)


class FiscalDocumentItemInline(ReadOnlyFiscalInlineMixin, admin.TabularInline):
    model = FiscalDocumentItem


class FiscalEmissionAttemptInline(ReadOnlyFiscalInlineMixin, admin.TabularInline):
    model = FiscalEmissionAttempt


@admin.register(FiscalDocument)
class FiscalDocumentAdmin(ReadOnlyFiscalAdminMixin, admin.ModelAdmin):
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
class FiscalDocumentItemAdmin(ReadOnlyFiscalAdminMixin, admin.ModelAdmin):
    list_display = ("fiscal_document", "line_number", "sku", "quantity", "net_amount", "iva_amount", "total_amount")
    list_filter = ("fiscal_document__company", "iva_rate")
    search_fields = ("fiscal_document__source_key", "sku", "description")


@admin.register(FiscalEmissionAttempt)
class FiscalEmissionAttemptAdmin(ReadOnlyFiscalAdminMixin, admin.ModelAdmin):
    list_display = ("fiscal_document", "result_status", "triggered_by", "error_code", "created_at")
    list_filter = ("result_status", "created_at")
    search_fields = ("fiscal_document__source_key", "error_code", "error_message")


@admin.register(FiscalSeriesReconciliation)
class FiscalSeriesReconciliationAdmin(ReadOnlyFiscalAdminMixin, admin.ModelAdmin):
    list_display = (
        "created_at",
        "series",
        "fiscal_document",
        "environment",
        "point_of_sale",
        "doc_type",
        "remote_last_authorized",
        "outcome",
    )
    list_filter = ("environment", "doc_type", "outcome", "created_at")
    search_fields = (
        "issuer_cuit",
        "point_of_sale",
        "correlation_id",
        "reason",
        "fiscal_document__source_key",
    )


@admin.register(FiscalMutationAudit)
class FiscalMutationAuditAdmin(ReadOnlyFiscalAdminMixin, admin.ModelAdmin):
    list_display = (
        "created_at",
        "fiscal_document",
        "action",
        "reason",
        "source",
        "actor",
    )
    list_filter = ("action", "source", "created_at")
    search_fields = (
        "fiscal_document__source_key",
        "reason",
        "correlation_id",
        "actor__username",
    )


@admin.register(InternalDocument)
class InternalDocumentAdmin(admin.ModelAdmin):
    list_display = ("doc_type", "number", "company", "client_company_ref", "issued_at", "is_cancelled")
    list_filter = ("doc_type", "company", "is_cancelled")
    search_fields = ("source_key", "company__name", "client_company_ref__client_profile__company_name")


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "show_public_prices",
        "require_primary_category_for_multicategory",
        "warehouse_stock_enabled",
    )


@admin.register(ProductWarehouseStock)
class ProductWarehouseStockAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "warehouse",
        "on_hand",
        "reserved",
        "minimum",
        "ideal",
        "initialized_at",
        "updated_at",
    )
    list_filter = ("warehouse__company", "warehouse")
    search_fields = ("product__sku", "product__name", "warehouse__name")
    readonly_fields = ("initialized_at", "created_at", "updated_at")


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


class ExternalEditorJobItemInline(admin.TabularInline):
    model = ExternalEditorJobItem
    extra = 0
    fields = ("product_id_snapshot", "sku", "status", "error", "updated_at")
    readonly_fields = fields
    can_delete = False
    max_num = 0


@admin.register(ExternalEditorJob)
class ExternalEditorJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "created_by",
        "status",
        "total",
        "processed",
        "succeeded",
        "failed",
    )
    list_filter = ("status", "created_at")
    search_fields = ("idempotency_key", "created_by__username")
    readonly_fields = (
        "created_by",
        "rolled_back_by",
        "idempotency_key",
        "status",
        "request_payload",
        "total",
        "processed",
        "succeeded",
        "failed",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "rolled_back_at",
    )
    inlines = (ExternalEditorJobItemInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExternalEditorDraft)
class ExternalEditorDraftAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_by", "status", "updated_at", "published_at")
    list_filter = ("status", "updated_at")
    search_fields = ("name", "created_by__username")
    readonly_fields = ("published_job", "published_at", "created_at", "updated_at")


@admin.register(ExternalEditorSavedView)
class ExternalEditorSavedViewAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "updated_at")
    search_fields = ("name", "created_by__username")


@admin.register(ImportExecution)
class ImportExecutionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "import_type", "company", "status", "dry_run", "created_count", "updated_count", "error_count")
    list_filter = ("import_type", "status", "dry_run", "created_at")
    search_fields = ("file_name", "user__username")
