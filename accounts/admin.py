from django.contrib import admin

from accounts.models import AccountRequest, ClientPayment, ClientProfile


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'user', 'cuit_dni', 'province', 'discount', 'is_approved')
    search_fields = ('company_name', 'user__username', 'cuit_dni')


@admin.register(AccountRequest)
class AccountRequestAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'email', 'status', 'created_at', 'processed_at')
    search_fields = ('company_name', 'email', 'cuit_dni')
    list_filter = ('status',)


@admin.register(ClientPayment)
class ClientPaymentAdmin(admin.ModelAdmin):
    list_display = ('client_profile', 'order', 'amount', 'method', 'paid_at', 'is_cancelled')
    search_fields = ('client_profile__company_name', 'client_profile__user__username', 'reference')
    list_filter = ('method', 'is_cancelled')
