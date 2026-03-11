from django.contrib import admin

from accounts.models import (
    AccountRequest,
    ClientCategory,
    ClientCategoryCompanyRule,
    ClientCompany,
    ClientPayment,
    ClientProfile,
)


class ClientCompanyInline(admin.TabularInline):
    model = ClientCompany
    extra = 0
    fields = ("company", "client_category", "price_list", "discount_percentage", "is_active")
    readonly_fields = ()


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'user', 'cuit_dni', 'province', 'discount', 'is_approved')
    search_fields = ('company_name', 'user__username', 'cuit_dni')
    inlines = (ClientCompanyInline,)


@admin.register(AccountRequest)
class AccountRequestAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'email', 'status', 'created_at', 'processed_at')
    search_fields = ('company_name', 'email', 'cuit_dni')
    list_filter = ('status',)


@admin.register(ClientPayment)
class ClientPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'client_profile',
        'company',
        'order',
        'amount',
        'method',
        'origin',
        'sync_status',
        'external_number',
        'paid_at',
        'is_cancelled',
    )
    search_fields = (
        'client_profile__company_name',
        'client_profile__user__username',
        'reference',
        'external_number',
        'external_id',
    )
    list_filter = ('method', 'origin', 'sync_status', 'is_cancelled', 'company')


@admin.register(ClientCompany)
class ClientCompanyAdmin(admin.ModelAdmin):
    list_display = ("client_profile", "company", "client_category", "price_list", "discount_percentage", "is_active")
    search_fields = ("client_profile__company_name", "client_profile__user__username", "company__name")
    list_filter = ("company", "price_list", "is_active")


@admin.register(ClientCategory)
class ClientCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "discount_percentage", "price_list_name", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)


@admin.register(ClientCategoryCompanyRule)
class ClientCategoryCompanyRuleAdmin(admin.ModelAdmin):
    list_display = ("company", "client_category", "price_list", "discount_percentage", "is_active")
    search_fields = ("company__name", "client_category__name")
    list_filter = ("company", "is_active", "price_list")
