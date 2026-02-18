from django.contrib import admin

from core.models import (
    SiteSettings,
    UserActivity,
    CatalogAnalyticsEvent,
    AdminAuditLog,
    ImportExecution,
)


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
    list_display = ("created_at", "import_type", "status", "dry_run", "created_count", "updated_count", "error_count")
    list_filter = ("import_type", "status", "dry_run", "created_at")
    search_fields = ("file_name", "user__username")
