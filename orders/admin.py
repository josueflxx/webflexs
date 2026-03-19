from django.contrib import admin

from orders.models import (
    Order,
    OrderItem,
    OrderProposal,
    OrderProposalItem,
    OrderRequest,
    OrderRequestEvent,
    OrderRequestItem,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_sku", "product_name", "quantity", "price_at_purchase", "subtotal")


class OrderRequestItemInline(admin.TabularInline):
    model = OrderRequestItem
    extra = 0
    readonly_fields = ("line_number", "product_sku", "product_name", "quantity", "price_at_snapshot", "subtotal")


class OrderProposalItemInline(admin.TabularInline):
    model = OrderProposalItem
    extra = 0
    readonly_fields = ("line_number", "product_sku", "product_name", "quantity", "price_at_snapshot", "subtotal")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "user",
        "origin_channel",
        "status",
        "total",
        "sync_status",
        "external_number",
        "created_at",
    )
    list_filter = ("company", "origin_channel", "status", "sync_status")
    search_fields = ("id", "user__username", "client_company", "external_number", "external_id")
    inlines = [OrderItemInline]


@admin.register(OrderRequest)
class OrderRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "user",
        "origin_channel",
        "status",
        "requested_total",
        "created_at",
    )
    list_filter = ("company", "origin_channel", "status")
    search_fields = ("id", "user__username", "client_company_ref__client_profile__company_name")
    inlines = [OrderRequestItemInline]


@admin.register(OrderProposal)
class OrderProposalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order_request",
        "version_number",
        "status",
        "proposed_total",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "is_current", "order_request__company")
    search_fields = ("id", "order_request__id", "order_request__user__username")
    inlines = [OrderProposalItemInline]


@admin.register(OrderRequestEvent)
class OrderRequestEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order_request",
        "event_type",
        "actor",
        "created_at",
    )
    list_filter = ("event_type", "order_request__company")
    search_fields = ("id", "order_request__id", "message", "actor__username")
