from django.contrib import admin

from orders.models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_sku", "product_name", "quantity", "price_at_purchase", "subtotal")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "user",
        "status",
        "total",
        "sync_status",
        "external_number",
        "created_at",
    )
    list_filter = ("company", "status", "sync_status")
    search_fields = ("id", "user__username", "client_company", "external_number", "external_id")
    inlines = [OrderItemInline]
