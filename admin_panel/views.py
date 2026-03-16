"""
Admin Panel views - Custom admin interface.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db import DatabaseError
from django.db.models import Q, Count, Sum, Max, Avg, F, DecimalField, ExpressionWrapper, Value, Prefetch
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
import json
import os
import re
from io import BytesIO, StringIO
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode, parse_qs
import csv
from openpyxl import Workbook

from catalog.models import Product, Category, CategoryAttribute, ClampMeasureRequest, Supplier, PriceList
from accounts.models import (
    AccountRequest,
    ClientCategory,
    ClientCompany,
    ClientPayment,
    ClientProfile,
    ClientTransaction,
)
from accounts.services.ledger import (
    create_adjustment_transaction,
    sync_order_charge_transaction,
)
from orders.models import ClampQuotation, Order, OrderItem, OrderStatusHistory
from orders.services.workflow import (
    ROLE_ADMIN,
    ROLE_DEPOSITO,
    ROLE_FACTURACION,
    ROLE_VENTAS,
    can_user_transition_order,
    resolve_user_order_role,
)
from core.models import (
    AdminCompanyAccess,
    Company,
    DocumentSeries,
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
    FISCAL_ISSUE_MODE_MANUAL,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_VOIDED,
    FiscalDocument,
    FiscalPointOfSale,
    InternalDocument,
    SALES_BEHAVIOR_COTIZACION,
    SALES_DOCUMENT_BEHAVIOR_CHOICES,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BEHAVIOR_NOTA_DEBITO,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_PRESUPUESTO,
    SALES_BEHAVIOR_RECIBO,
    SALES_BEHAVIOR_REMITO,
    SalesDocumentType,
    SiteSettings,
    CatalogAnalyticsEvent,
    AdminAuditLog,
    ImportExecution,
    CatalogExcelTemplate,
    CatalogExcelTemplateSheet,
    CatalogExcelTemplateColumn,
    Warehouse,
)
from core.services.company_context import (
    get_active_company,
    get_default_company,
    get_default_client_origin_company,
    get_user_companies,
    set_active_company,
    user_has_company_access,
)
from django.contrib.auth.models import Group, User
from admin_panel.forms.import_forms import ProductImportForm, ClientImportForm, CategoryImportForm
from admin_panel.forms.category_forms import CategoryForm
from admin_panel.forms.export_forms import (
    CatalogExcelTemplateForm,
    CatalogExcelTemplateSheetForm,
    CatalogExcelTemplateColumnForm,
)
from admin_panel.forms.sales_document_type_forms import SalesDocumentTypeForm, WarehouseForm
from catalog.services.product_importer import ProductImporter
from accounts.services.client_importer import ClientImporter
from catalog.services.category_importer import CategoryImporter
from catalog.services.abrazadera_importer import AbrazaderaImporter
from catalog.services.supplier_sync import ensure_supplier, clean_supplier_name
from catalog.services.clamp_code import (
    DIAMETER_HUMAN_TO_COMPACT_DEFAULT,
    generarCodigo,
    parsearCodigo,
)
from catalog.services.clamp_quoter import (
    CLAMP_LAMINATED_ALLOWED_DIAMETERS,
    CLAMP_PRICE_LISTS,
    CLAMP_WEIGHT_MAP,
    calculate_clamp_quote,
    get_allowed_diameter_options,
    parse_decimal_value,
    parse_int_value,
)
from catalog.services.clamp_request_products import (
    publish_clamp_request_product,
)
from catalog.services.category_assignment import (
    normalize_category_ids,
    assign_categories_to_product,
    add_category_to_products,
    replace_categories_for_products,
    remove_category_from_products,
)
from core.services.import_manager import ImportTaskManager
from core.services.background_jobs import dispatch_import_job
from core.services.fiscal import is_company_fiscal_ready, is_invoice_ready
from core.services.fiscal_documents import (
    close_fiscal_document,
    create_local_fiscal_document_from_order,
    reopen_fiscal_document,
    register_external_fiscal_document_for_order,
    void_fiscal_document,
)
from core.services.fiscal_emission import emit_fiscal_document_now
from core.services.documents import ensure_document_for_adjustment, ensure_document_for_payment
from core.services.sales_documents import (
    create_fiscal_document_from_sales_type,
    create_internal_document_from_sales_type,
    resolve_sales_document_type,
)
from core.services.advanced_search import (
    apply_compact_text_search,
    apply_text_search,
    apply_parsed_text_search,
    compact_search_token,
    parse_text_search_query,
    sanitize_search_token,
)
from core.services.catalog_excel_exporter import build_catalog_workbook, build_export_filename
from core.services.audit import log_admin_action, log_admin_change, model_snapshot
from core.services.pricing import resolve_effective_price_list
import traceback
import logging
from core.decorators import superuser_required_for_modifications

logger = logging.getLogger(__name__)
PRIMARY_SUPERADMIN_USERNAME = "josueflexs"
ADMIN_ROLE_CHOICES = [
    (ROLE_ADMIN, "Administracion"),
    (ROLE_VENTAS, "Ventas"),
    (ROLE_DEPOSITO, "Deposito"),
    (ROLE_FACTURACION, "Facturacion"),
]
ADMIN_ROLE_LABELS = dict(ADMIN_ROLE_CHOICES)
CLIENT_FACTURABLE_STATUSES = {
    Order.STATUS_CONFIRMED,
    Order.STATUS_PREPARING,
    Order.STATUS_SHIPPED,
    Order.STATUS_DELIVERED,
}
CLIENT_REMITO_READY_STATUSES = {
    Order.STATUS_SHIPPED,
    Order.STATUS_DELIVERED,
}
ORDER_INTERNAL_DOC_STATUS_RULES = {
    DocumentSeries.DOC_COT: {
        Order.STATUS_DRAFT,
        Order.STATUS_CONFIRMED,
        Order.STATUS_PREPARING,
        Order.STATUS_SHIPPED,
        Order.STATUS_DELIVERED,
    },
    DocumentSeries.DOC_PED: {
        Order.STATUS_CONFIRMED,
        Order.STATUS_PREPARING,
        Order.STATUS_SHIPPED,
        Order.STATUS_DELIVERED,
    },
    DocumentSeries.DOC_REM: CLIENT_REMITO_READY_STATUSES,
}


def ensure_admin_role_groups():
    for role_value, role_label in ADMIN_ROLE_CHOICES:
        Group.objects.get_or_create(name=role_value)


def get_admin_role_values(user):
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if getattr(user, "is_superuser", False):
        return [ROLE_ADMIN]
    active_roles = [
        role_value
        for role_value, _role_label in ADMIN_ROLE_CHOICES
        if user.groups.filter(name=role_value).exists()
    ]
    if active_roles:
        return active_roles
    resolved_role = resolve_user_order_role(user)
    return [resolved_role] if resolved_role else []


def get_admin_role_value(user):
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    if getattr(user, "is_superuser", False):
        return ROLE_ADMIN
    return resolve_user_order_role(user) or ""


def get_admin_role_label(user):
    if getattr(user, "is_superuser", False):
        return "Superadmin"
    role_labels = get_admin_role_labels(user)
    if not role_labels:
        return "Sin rol"
    return " + ".join(role_labels)


def get_admin_role_labels(user):
    if getattr(user, "is_superuser", False):
        return ["Superadmin"]
    return [ADMIN_ROLE_LABELS.get(role_value, role_value) for role_value in get_admin_role_values(user)]


def set_admin_roles_for_user(user, role_values):
    ensure_admin_role_groups()
    normalized_roles = {
        str(role_value or "").strip().lower()
        for role_value in (role_values or [])
        if str(role_value or "").strip()
    }
    valid_roles = {choice[0] for choice in ADMIN_ROLE_CHOICES}
    role_groups = Group.objects.filter(name__in=valid_roles)
    user.groups.remove(*role_groups)
    for role_value, _role_label in ADMIN_ROLE_CHOICES:
        if role_value not in normalized_roles or role_value not in valid_roles:
            continue
        target_group = role_groups.filter(name=role_value).first() or Group.objects.get(name=role_value)
        user.groups.add(target_group)


def set_admin_role_for_user(user, role_value):
    """
    Backward-compatible wrapper for places that still think in singular role.
    """
    roles = [role_value] if role_value else []
    set_admin_roles_for_user(user, roles)


def get_admin_company_scope_mode(user):
    if not user or not getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return "all"
    has_db_scope = AdminCompanyAccess.objects.filter(
        user=user,
        is_active=True,
        company__is_active=True,
    ).exists()
    return "limited" if has_db_scope else "all"


def get_admin_user_scope_ids(user):
    return set(
        AdminCompanyAccess.objects.filter(
            user=user,
            is_active=True,
            company__is_active=True,
        ).values_list("company_id", flat=True)
    )


def build_admin_user_snapshot(user):
    return {
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "role": get_admin_role_value(user),
        "roles": get_admin_role_values(user),
        "company_scope_mode": get_admin_company_scope_mode(user),
        "company_ids": sorted(get_admin_user_scope_ids(user)),
    }


def get_recent_admin_user_audit_logs(user, limit=8):
    return list(
        AdminAuditLog.objects.filter(target_type="auth_user", target_id=str(user.pk))
        .select_related("user")
        .order_by("-created_at")[:limit]
    )


def get_managed_admin_users_queryset():
    valid_roles = [choice[0] for choice in ADMIN_ROLE_CHOICES]
    return (
        User.objects.filter(
            Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(groups__name__in=valid_roles)
            | Q(company_access_links__is_active=True)
        )
        .distinct()
        .order_by("username")
    )

CLIENT_REPORT_TEXT_FIELD_CHOICES = [
    ("company_name", "Nombre"),
    ("client_id", "N° de cliente"),
    ("username", "Usuario"),
    ("email", "Mail"),
    ("phone", "Telefonos"),
    ("document", "CUIT/DNI"),
]
CLIENT_REPORT_OPTIONAL_COLUMNS = [
    ("category", "Categoria del cliente"),
    ("price_list", "Lista de precios"),
    ("locality", "Localidades"),
    ("province", "Provincias"),
    ("address", "Domicilio"),
    ("document_detail", "Tipo y numero de documento"),
    ("phones", "Telefonos"),
    ("email", "Mail"),
    ("extra", "Datos extra"),
    ("balance", "Saldo"),
]
CLIENT_REPORT_STATE_CHOICES = [
    ("all", "Todos los estados de cliente"),
    ("enabled", "Habilitado"),
    ("disabled", "No habilitado"),
]
CLIENT_REPORT_DATE_RANGE_CHOICES = [
    ("all", "Todas las fechas"),
    ("today", "Hoy"),
    ("yesterday", "Ayer"),
    ("this_week", "Esta semana"),
    ("last_week", "Semana anterior"),
    ("last_7_days", "Ultimos 7 dias"),
    ("this_month", "Este mes"),
    ("last_month", "Mes anterior"),
    ("last_30_days", "Ultimos 30 dias"),
    ("this_year", "Este ano"),
    ("last_year", "Ano anterior"),
    ("last_12_months", "Ultimos 12 meses"),
    ("custom", "Personalizado"),
]
CLIENT_REPORT_RANKING_CHOICES = [
    ("top_10", "Los 10 mas facturados"),
    ("top_100", "Los 100 mas facturados"),
    ("bottom_10", "Los 10 menos facturados"),
    ("bottom_100", "Los 100 menos facturados"),
]
CLIENT_REPORT_DEBTOR_CHOICES = [
    ("enabled_debtors", "Clientes habilitados con saldo deudor en sus cuentas corrientes"),
    ("enabled_creditors", "Clientes habilitados con saldo acreedor en sus cuentas corrientes"),
    ("disabled_non_zero", "Clientes deshabilitados con saldo diferente de cero"),
]
CLIENT_REPORT_CURRENCY_CHOICES = [
    ("all", "Todas las monedas"),
    ("ars", "Pesos"),
]
CLIENT_EXPORT_ENCODING_CHOICES = [
    ("utf8", "UTF-8"),
    ("latin1", "ISO-8859-1 (compatibilidad con Excel)"),
]
CLIENT_EXPORT_PRESET_CHOICES = [
    ("operational", "Base operativa de clientes"),
    ("import_compatible", "Compatible con importacion / actualizacion"),
]
CLIENT_REPORT_ORDER_STATUSES = (
    Order.STATUS_CONFIRMED,
    Order.STATUS_PREPARING,
    Order.STATUS_SHIPPED,
    Order.STATUS_DELIVERED,
)
CLIENT_REPORT_RESULTS_SORT_FIELDS = {
    "top_10": ("-total_sales", "user__client_profile__company_name"),
    "top_100": ("-total_sales", "user__client_profile__company_name"),
    "bottom_10": ("total_sales", "user__client_profile__company_name"),
    "bottom_100": ("total_sales", "user__client_profile__company_name"),
}


def _build_client_report_queryset(active_company=None, include_balance_prefetch=False):
    clients = ClientProfile.objects.select_related("user", "client_category")
    client_company_queryset = ClientCompany.objects.select_related(
        "company",
        "client_category",
        "price_list",
    )

    if active_company:
        clients = clients.filter(company_links__company=active_company).distinct()
        client_company_queryset = client_company_queryset.filter(company=active_company)

    prefetches = [
        Prefetch(
            "company_links",
            queryset=client_company_queryset,
            to_attr="report_company_links",
        )
    ]

    if include_balance_prefetch and active_company:
        prefetches.extend(
            [
                Prefetch(
                    "transactions",
                    queryset=ClientTransaction.objects.filter(company=active_company).order_by("-occurred_at", "-id"),
                    to_attr="report_transactions",
                ),
                Prefetch(
                    "payments",
                    queryset=ClientPayment.objects.filter(
                        company=active_company,
                        is_cancelled=False,
                    ).order_by("-paid_at", "-id"),
                    to_attr="report_payments",
                ),
                Prefetch(
                    "user__orders",
                    queryset=Order.objects.filter(
                        company=active_company,
                        status__in=CLIENT_REPORT_ORDER_STATUSES,
                    ).order_by("-created_at", "-id"),
                    to_attr="report_balance_orders",
                ),
            ]
        )

    return clients.prefetch_related(*prefetches)


def _get_report_company_link(client, active_company=None):
    prefetched_links = getattr(client, "report_company_links", None)
    if prefetched_links is not None:
        if active_company:
            for link in prefetched_links:
                if link.company_id == active_company.id:
                    return link
        return prefetched_links[0] if prefetched_links else None
    return client.get_company_link(active_company)


def _get_report_client_category(client, active_company=None, company_link=None):
    if company_link and company_link.client_category_id:
        return company_link.client_category
    return client.client_category


def _get_report_client_locality(client):
    value = (client.fiscal_city or "").strip() or (client.province or "").strip()
    return value or "Sin especificar"


def _get_report_client_province(client):
    value = (client.fiscal_province or "").strip() or (client.province or "").strip()
    return value or "Sin especificar"


def _get_report_client_address(client):
    value = (client.fiscal_address or "").strip() or (client.address or "").strip()
    return value or "-"


def _get_report_client_document_detail(client):
    if client.document_type and client.document_number:
        return f"{client.get_document_type_display()}: {client.document_number}"
    if client.cuit_dni:
        return client.cuit_dni
    return "-"


def _get_report_client_state(client, active_company=None, company_link=None):
    company_link = company_link or _get_report_company_link(client, active_company)
    enabled = bool(client.user and client.user.is_active and client.is_approved)
    if active_company:
        enabled = enabled and bool(company_link and company_link.is_active)
    return {
        "enabled": enabled,
        "label": "Habilitado" if enabled else "No habilitado",
    }


def _sum_decimal_values(values):
    total = Decimal("0.00")
    for value in values:
        if value is None:
            continue
        total += Decimal(str(value))
    return total


def _get_report_client_balance(client, active_company=None):
    transactions = getattr(client, "report_transactions", None)
    if transactions is not None:
        if transactions:
            return _sum_decimal_values(tx.amount for tx in transactions)
        payments = getattr(client, "report_payments", None)
        orders = getattr(getattr(client, "user", None), "report_balance_orders", None)
        if payments is not None and orders is not None:
            orders_total = _sum_decimal_values(order.total for order in orders)
            paid_total = _sum_decimal_values(payment.amount for payment in payments)
            return orders_total - paid_total
    return client.get_current_balance(company=active_company)


def _get_report_client_price_list_name(client, active_company=None, company_link=None):
    if not active_company:
        return "Sin lista"
    company_link = company_link or _get_report_company_link(client, active_company)
    category = _get_report_client_category(client, active_company=active_company, company_link=company_link)
    from core.services.pricing import resolve_effective_price_list

    price_list = resolve_effective_price_list(
        company=active_company,
        client_company=company_link,
        client_category=category,
    )
    return getattr(price_list, "name", "") or "Sin lista"


def _get_client_report_locality_choices(active_company=None):
    clients = list(_build_client_report_queryset(active_company).order_by("company_name", "user__username"))
    choices = []
    seen = set()
    for client in clients:
        value = _get_report_client_locality(client)
        if value not in seen:
            seen.add(value)
            choices.append(value)
    if "Sin especificar" in choices:
        choices.remove("Sin especificar")
        choices.insert(0, "Sin especificar")
    return choices


def _client_report_matches_text(client_row, text_query):
    if not text_query:
        return True
    haystack = str(client_row or "").strip().lower()
    return text_query.lower() in haystack


def _resolve_report_date_range(range_key, start_raw="", end_raw=""):
    today = timezone.localdate()
    start_date = None
    end_date = None
    normalized_key = range_key or "all"

    if normalized_key == "today":
        start_date = end_date = today
    elif normalized_key == "yesterday":
        start_date = end_date = today - timedelta(days=1)
    elif normalized_key == "this_week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif normalized_key == "last_week":
        this_week_start = today - timedelta(days=today.weekday())
        end_date = this_week_start - timedelta(days=1)
        start_date = end_date - timedelta(days=6)
    elif normalized_key == "last_7_days":
        start_date = today - timedelta(days=6)
        end_date = today
    elif normalized_key == "this_month":
        start_date = today.replace(day=1)
        end_date = today
    elif normalized_key == "last_month":
        this_month_start = today.replace(day=1)
        end_date = this_month_start - timedelta(days=1)
        start_date = end_date.replace(day=1)
    elif normalized_key == "last_30_days":
        start_date = today - timedelta(days=29)
        end_date = today
    elif normalized_key == "this_year":
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif normalized_key == "last_year":
        start_date = today.replace(year=today.year - 1, month=1, day=1)
        end_date = today.replace(year=today.year - 1, month=12, day=31)
    elif normalized_key == "last_12_months":
        start_date = today - timedelta(days=365)
        end_date = today
    elif normalized_key == "custom":
        start_date = parse_date(start_raw) if start_raw else None
        end_date = parse_date(end_raw) if end_raw else None
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date

    return start_date, end_date


def _client_report_date_label(range_key, start_date=None, end_date=None):
    labels = dict(CLIENT_REPORT_DATE_RANGE_CHOICES)
    if range_key == "custom":
        if start_date and end_date:
            return f"{start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"
        if start_date:
            return f"Desde {start_date.strftime('%d/%m/%Y')}"
        if end_date:
            return f"Hasta {end_date.strftime('%d/%m/%Y')}"
    return labels.get(range_key or "all", "Todas las fechas")


def _client_report_csv_response(filename, headers, rows):
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    payload = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    response = HttpResponse(payload, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _is_standalone_report_request(request):
    return str(request.GET.get("standalone", "")).strip().lower() in {"1", "true", "yes"}


def _build_client_report_row(client, active_company=None, company_link=None, include_balance=False):
    company_link = company_link or _get_report_company_link(client, active_company)
    category = _get_report_client_category(client, active_company=active_company, company_link=company_link)
    state = _get_report_client_state(client, active_company=active_company, company_link=company_link)
    balance = _get_report_client_balance(client, active_company=active_company) if include_balance else None
    price_list_name = _get_report_client_price_list_name(
        client,
        active_company=active_company,
        company_link=company_link,
    )
    extra_values = [client.get_client_type_display() if client.client_type else ""]
    if client.notes:
        extra_values.append(client.notes)

    return {
        "client": client,
        "client_id": client.pk,
        "company_name": client.company_name or "-",
        "username": getattr(client.user, "username", "-") or "-",
        "email": getattr(client.user, "email", "-") or "-",
        "cuit_dni": client.cuit_dni or client.document_number or "-",
        "state": state["label"],
        "is_enabled": state["enabled"],
        "iva_condition": client.get_iva_condition_display() if client.iva_condition else "-",
        "category": category.name if category else "Sin categoria",
        "category_id": getattr(category, "pk", None),
        "price_list": price_list_name,
        "locality": _get_report_client_locality(client),
        "province": _get_report_client_province(client),
        "address": _get_report_client_address(client),
        "document_detail": _get_report_client_document_detail(client),
        "phones": client.phone or "-",
        "extra": " | ".join([value for value in extra_values if value]) or "-",
        "balance": balance,
    }


def _client_reports_nav():
    return [
        {
            "label": "Centro de informes",
            "url": reverse("admin_client_reports_hub"),
            "key": "hub",
        },
        {
            "label": "Lista de clientes",
            "url": reverse("admin_client_report_list"),
            "key": "list",
        },
        {
            "label": "Ranking de clientes",
            "url": reverse("admin_client_report_ranking"),
            "key": "ranking",
        },
        {
            "label": "Clientes deudores",
            "url": reverse("admin_client_report_debtors"),
            "key": "debtors",
        },
    ]


def _client_tools_nav():
    return [
        {
            "label": "Centro de herramientas",
            "url": reverse("admin_client_tools_hub"),
            "key": "hub",
        },
        {
            "label": "Exportar clientes",
            "url": reverse("admin_client_export"),
            "key": "export",
        },
        {
            "label": "Importar o actualizar",
            "url": reverse("admin_import_process", args=["clients"]),
            "key": "import",
        },
        {
            "label": "Solicitudes",
            "url": reverse("admin_request_list"),
            "key": "requests",
        },
    ]


def _get_report_client_contact_name(client):
    user = getattr(client, "user", None)
    if not user:
        return "-"
    full_name = user.get_full_name().strip()
    if full_name:
        return full_name
    if user.first_name:
        return user.first_name.strip()
    return user.username or "-"


def _get_client_export_rows(active_company, preset="operational"):
    include_balance = preset == "operational"
    clients = list(
        _build_client_report_queryset(active_company, include_balance_prefetch=include_balance).order_by(
            "company_name",
            "user__username",
        )
    )
    rows = []

    if preset == "import_compatible":
        headers = [
            "Usuario",
            "Contrasena",
            "Nombre",
            "Email",
            "CUIT/DNI",
            "Tipo de cliente",
            "Rubro",
            "Cond. IVA",
            "Descuento",
            "Provincia",
            "Domicilio",
            "Telefonos",
            "Contacto",
        ]

        for client in clients:
            company_link = _get_report_company_link(client, active_company)
            category = _get_report_client_category(client, active_company=active_company, company_link=company_link)
            rows.append(
                [
                    getattr(client.user, "username", "") or "",
                    "",
                    client.company_name or "",
                    getattr(client.user, "email", "") or "",
                    client.cuit_dni or client.document_number or "",
                    getattr(category, "name", "") or "",
                    client.get_client_type_display() if client.client_type else "",
                    client.get_iva_condition_display() if client.iva_condition else "",
                    f"{client.get_effective_discount_percentage(company=active_company):.2f}",
                    _get_report_client_province(client) or "",
                    _get_report_client_address(client) or "",
                    client.phone or "",
                    _get_report_client_contact_name(client) or "",
                ]
            )
        return headers, rows

    headers = [
        "Nro de cliente",
        "Categoria de cliente",
        "Estado",
        "Nombre",
        "Usuario",
        "Contacto",
        "Telefonos",
        "Domicilio",
        "Localidad",
        "Provincia",
        "Mail",
        "Condicion de IVA",
        "Razon social",
        "CUIT",
        "Tipo de documento",
        "Nro de documento",
        "Lista de precios",
        "Descuento",
        "Saldo",
        "Moneda",
        "Observaciones",
    ]

    for client in clients:
        company_link = _get_report_company_link(client, active_company)
        row = _build_client_report_row(
            client,
            active_company=active_company,
            company_link=company_link,
            include_balance=True,
        )
        rows.append(
            [
                row["client_id"],
                row["category"] or "Sin categoria",
                row["state"],
                row["company_name"],
                row["username"],
                _get_report_client_contact_name(client),
                row["phones"],
                row["address"],
                row["locality"],
                row["province"],
                row["email"],
                row["iva_condition"],
                client.company_name or "",
                client.cuit_dni or "",
                client.get_document_type_display() if client.document_type else "",
                client.document_number or "",
                row["price_list"],
                f"{client.get_effective_discount_percentage(company=active_company):.2f}",
                f"{row['balance']:.2f}" if row["balance"] is not None else "0.00",
                "Pesos",
                client.notes or "",
            ]
        )
    return headers, rows


def _client_export_csv_response(filename, headers, rows, encoding_key="utf8"):
    delimiter = ";"
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)

    charset = "utf-8"
    payload = buffer.getvalue()
    if encoding_key == "latin1":
        charset = "iso-8859-1"
        content = payload.encode(charset, errors="replace")
    else:
        content = ("\ufeff" + payload).encode("utf-8")

    response = HttpResponse(content, content_type=f"text/csv; charset={charset}")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
FISCAL_PRINT_COPY_LABELS = {
    "original": "ORIGINAL",
    "duplicado": "DUPLICADO",
    "triplicado": "TRIPLICADO",
}


def _get_order_client_profile(order):
    client_company = getattr(order, "client_company_ref", None)
    if client_company and getattr(client_company, "client_profile", None):
        return client_company.client_profile
    if getattr(order, "user_id", None):
        return ClientProfile.objects.filter(user_id=order.user_id).first()
    return None


def _resolve_default_point_of_sale(company):
    if not company:
        return None

    company_default = str(getattr(company, "point_of_sale_default", "") or "").strip()
    points = FiscalPointOfSale.objects.filter(company=company, is_active=True)
    if company_default:
        matched = points.filter(number=company_default).first()
        if matched:
            return matched
    return points.order_by("-is_default", "number", "id").first()


def _resolve_preferred_invoice_doc_type(order):
    client_profile = _get_order_client_profile(order)
    if client_profile and client_profile.iva_condition == "responsable_inscripto":
        return FISCAL_DOC_TYPE_FA
    return FISCAL_DOC_TYPE_FB


def _resolve_invoice_sales_document_type_for_order(order):
    preferred_doc_type = _resolve_preferred_invoice_doc_type(order)
    configured = resolve_sales_document_type(
        company=order.company,
        behavior=SALES_BEHAVIOR_FACTURA,
        fiscal_doc_type=preferred_doc_type,
    )
    if configured:
        return configured
    return resolve_sales_document_type(
        company=order.company,
        behavior=SALES_BEHAVIOR_FACTURA,
    )


def _get_order_active_invoice(order):
    if not order:
        return None
    return (
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "sales_document_type",
            "client_company_ref__client_profile",
        )
        .filter(order=order, doc_type__in=[FISCAL_DOC_TYPE_FA, FISCAL_DOC_TYPE_FB])
        .exclude(status=FISCAL_STATUS_VOIDED)
        .order_by("-created_at", "-id")
        .first()
    )


def _get_fiscal_workflow_state(document):
    if not document:
        return {"label": "Sin factura", "badge_class": "", "is_open": False}
    if document.status == FISCAL_STATUS_VOIDED:
        return {"label": "Cancelada", "badge_class": "", "is_open": False}
    if document.issue_mode == FISCAL_ISSUE_MODE_MANUAL:
        if document.status == FISCAL_STATUS_EXTERNAL_RECORDED:
            return {"label": "Cerrada", "badge_class": "is-success", "is_open": False}
        return {"label": "Abierta", "badge_class": "is-info", "is_open": True}
    if document.issue_mode == FISCAL_ISSUE_MODE_EXTERNAL_SAAS:
        return {"label": "Cerrada", "badge_class": "is-success", "is_open": False}
    if document.status == FISCAL_STATUS_AUTHORIZED:
        return {"label": "Cerrada", "badge_class": "is-success", "is_open": False}
    if document.status == FISCAL_STATUS_SUBMITTING:
        return {"label": "En proceso", "badge_class": "is-info", "is_open": True}
    if document.status == FISCAL_STATUS_PENDING_RETRY:
        return {"label": "Pendiente", "badge_class": "", "is_open": True}
    if document.status == FISCAL_STATUS_REJECTED:
        return {"label": "Rechazada", "badge_class": "", "is_open": True}
    return {"label": "Abierta", "badge_class": "is-info", "is_open": True}
FISCAL_PRINT_DOC_META = {
    "FA": {"letter": "A", "code": "001"},
    "FB": {"letter": "B", "code": "006"},
    "NCA": {"letter": "A", "code": "003"},
    "NCB": {"letter": "B", "code": "008"},
}
ORDER_PRODUCT_SEARCH_FIELDS = [
    "sku",
    "name",
    "supplier",
    "supplier_ref__name",
    "description",
]


def _find_products_for_order_query(raw_value, *, limit=6):
    query = sanitize_search_token(raw_value)
    if not query:
        return []

    queryset = Product.objects.filter(is_active=True).select_related("supplier_ref")
    parsed_query = parse_text_search_query(
        query,
        max_include=6,
        max_exclude=2,
        max_phrases=3,
    )
    compact_query = compact_search_token(query)
    candidates = []
    seen_ids = set()

    def collect(rows):
        for product in rows:
            if product.pk in seen_ids:
                continue
            seen_ids.add(product.pk)
            candidates.append(product)
            if len(candidates) >= limit:
                return True
        return False

    if collect(queryset.filter(Q(sku__iexact=query) | Q(name__iexact=query)).order_by("name")[:3]):
        return candidates
    if collect(queryset.filter(Q(sku__istartswith=query) | Q(name__istartswith=query)).order_by("name")[:5]):
        return candidates

    if parsed_query.get("raw"):
        if collect(
            apply_parsed_text_search(
                queryset,
                parsed_query,
                ORDER_PRODUCT_SEARCH_FIELDS,
                order_by_similarity=False,
            )
            .order_by("name")[:limit]
        ):
            return candidates

    if compact_query:
        collect(
            apply_compact_text_search(queryset, compact_query, ["sku", "name"])
            .order_by("name")[:limit]
        )

    return candidates[:limit]


def is_primary_superadmin(user):
    """Allow only the designated primary superadmin account."""
    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_superuser
        and user.username.lower() == PRIMARY_SUPERADMIN_USERNAME
    )


def can_edit_client_profile(user):
    """
    Any staff admin can operate the client panel and update client profile data.
    """
    return bool(getattr(user, "is_authenticated", False) and user.is_staff)


def can_manage_client_credentials(user):
    """
    Credentials are sensitive and remain restricted to primary superadmin.
    """
    return is_primary_superadmin(user)


def can_delete_client_record(user):
    """
    Client deletion is restricted to primary superadmin.
    """
    return is_primary_superadmin(user)


def get_active_client_categories():
    return ClientCategory.objects.filter(is_active=True).order_by("sort_order", "name")


def get_client_categories_for_client(client=None, client_company=None):
    queryset = ClientCategory.objects.all()
    selected_category_id = None
    if client_company and getattr(client_company, "client_category_id", None):
        selected_category_id = client_company.client_category_id
    elif client and getattr(client, "client_category_id", None):
        selected_category_id = client.client_category_id
    if selected_category_id:
        queryset = queryset.filter(Q(is_active=True) | Q(pk=selected_category_id))
    else:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("sort_order", "name")


def get_admin_selected_company(request):
    company_id = request.POST.get("company_id") or request.GET.get("company_id") or request.GET.get("company")
    if company_id:
        if str(company_id).isdigit():
            company = Company.objects.filter(pk=company_id, is_active=True).first()
            if company and user_has_company_access(request.user, company):
                set_active_company(request, company)
                return company
    return get_active_company(request)


def get_admin_company_filter(request):
    raw_company = request.GET.get("company_id") or request.GET.get("company") or request.POST.get("company_id")
    if raw_company == "all":
        return get_active_company(request)
    if raw_company and str(raw_company).isdigit():
        company = Company.objects.filter(pk=int(raw_company), is_active=True).first()
        if company and user_has_company_access(request.user, company):
            set_active_company(request, company)
            return company
    return get_active_company(request)


def get_admin_company_required(request):
    """
    Return explicit company for export/import flows.
    """
    raw_company = request.GET.get("company_id") or request.GET.get("company") or request.POST.get("company_id")
    if raw_company == "all":
        return get_active_company(request)
    if raw_company and str(raw_company).isdigit():
        company = Company.objects.filter(pk=int(raw_company), is_active=True).first()
        if company and user_has_company_access(request.user, company):
            set_active_company(request, company)
            return company
        return None
    active_company = get_active_company(request)
    if active_company:
        return active_company
    return None


def _redirect_client_history(client, company=None):
    url = reverse("admin_client_order_history", args=[client.pk])
    if company:
        url = f"{url}?{urlencode({'company_id': company.pk})}"
    return redirect(url)


def _get_client_orders_queryset(client, company=None):
    if not getattr(client, "user_id", None):
        return Order.objects.none()
    queryset = Order.objects.select_related("company", "client_company_ref", "user").filter(
        user_id=client.user_id
    )
    if company:
        queryset = queryset.filter(company=company)
    return queryset


def _annotate_client_orders_with_documents(order_list, company=None):
    orders = list(order_list or [])
    if not orders:
        return orders

    order_ids = [order.pk for order in orders if getattr(order, "pk", None)]
    remito_docs_by_order = {}
    invoice_docs_by_order = {}
    latest_fiscal_docs_by_order = {}

    if company and order_ids:
        for document in (
            InternalDocument.objects.filter(
                company=company,
                doc_type=DocumentSeries.DOC_REM,
                order_id__in=order_ids,
            )
            .order_by("-issued_at", "-id")
        ):
            remito_docs_by_order.setdefault(document.order_id, document)

        for document in (
            FiscalDocument.objects.select_related("point_of_sale", "related_document")
            .filter(company=company, order_id__in=order_ids)
            .exclude(status="voided")
            .order_by("-created_at", "-id")
        ):
            latest_fiscal_docs_by_order.setdefault(document.order_id, document)
            if document.doc_type in {"FA", "FB"}:
                invoice_docs_by_order.setdefault(document.order_id, document)

    for order in orders:
        order.client_remito_document = remito_docs_by_order.get(order.pk)
        order.client_invoice_document = invoice_docs_by_order.get(order.pk)
        order.client_latest_fiscal_document = latest_fiscal_docs_by_order.get(order.pk)
        order.client_has_saas_invoice = bool(order.saas_document_type or order.saas_document_number)
        order.client_can_remito = bool(order.client_remito_document) or order.status in CLIENT_REMITO_READY_STATUSES
        order.client_can_invoice = order.status in CLIENT_FACTURABLE_STATUSES
        order.client_can_credit_note = bool(order.client_invoice_document)
    return orders


def parse_optional_client_category(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    try:
        return ClientCategory.objects.get(pk=int(raw))
    except (ValueError, TypeError, ClientCategory.DoesNotExist):
        raise ValueError("Categoria de cliente invalida.")


def _is_checked(form_data, field_name, default=False):
    if form_data is None:
        return bool(default)
    if field_name not in form_data:
        return bool(default)
    return str(form_data.get(field_name, "")).strip().lower() in {"1", "true", "on", "yes"}


def _extract_linked_company_ids(form_data):
    if form_data is None or not hasattr(form_data, "getlist"):
        return []
    selected = []
    seen = set()
    for raw_value in form_data.getlist("linked_company_ids"):
        normalized = str(raw_value or "").strip()
        if not normalized.isdigit() or normalized in seen:
            continue
        selected.append(normalized)
        seen.add(normalized)
    return selected


def _resolve_client_editor_company(request, client=None):
    companies = get_user_companies(getattr(request, "user", None)).filter(is_active=True).order_by("name")
    requested_company_id = str(request.GET.get("company_id", "")).strip()
    if requested_company_id.isdigit():
        requested_company = companies.filter(pk=int(requested_company_id)).first()
        if requested_company:
            return requested_company, companies
    active_company = get_admin_selected_company(request)
    if active_company:
        return active_company, companies
    if client:
        preferred_link = (
            client.company_links.select_related("company")
            .filter(company__is_active=True)
            .order_by("-is_active", "company__name", "company_id")
            .first()
        )
        if preferred_link:
            return preferred_link.company, companies
    fallback_company = get_default_client_origin_company() or companies.first()
    return fallback_company, companies


def _build_client_form_values(client=None, active_company=None, client_company=None, form_data=None):
    if form_data is not None:
        return {
            "username": str(form_data.get("username", "")).strip(),
            "email": str(form_data.get("email", "")).strip(),
            "first_name": str(form_data.get("first_name", "")).strip(),
            "last_name": str(form_data.get("last_name", "")).strip(),
            "user_is_active": _is_checked(form_data, "user_is_active", default=False),
            "client_is_approved": _is_checked(form_data, "client_is_approved", default=False),
            "company_id": str(form_data.get("company_id", "")).strip(),
            "company_name": str(form_data.get("company_name", "")).strip(),
            "cuit_dni": str(form_data.get("cuit_dni", "")).strip(),
            "document_type": str(form_data.get("document_type", "")).strip(),
            "document_number": str(form_data.get("document_number", "")).strip(),
            "discount": str(form_data.get("discount", "")).strip(),
            "client_category": str(form_data.get("client_category", "")).strip(),
            "company_is_active": _is_checked(form_data, "company_is_active", default=False),
            "province": str(form_data.get("province", "")).strip(),
            "fiscal_province": str(form_data.get("fiscal_province", "")).strip(),
            "fiscal_city": str(form_data.get("fiscal_city", "")).strip(),
            "address": str(form_data.get("address", "")).strip(),
            "fiscal_address": str(form_data.get("fiscal_address", "")).strip(),
            "postal_code": str(form_data.get("postal_code", "")).strip(),
            "phone": str(form_data.get("phone", "")).strip(),
            "client_type": str(form_data.get("client_type", "")).strip(),
            "iva_condition": str(form_data.get("iva_condition", "")).strip(),
            "notes": str(form_data.get("notes", "")).strip(),
            "linked_company_ids": _extract_linked_company_ids(form_data),
        }

    effective_category = None
    effective_discount = Decimal("0")
    linked_company_ids = []
    if client:
        effective_category = (
            client_company.client_category
            if client_company and client_company.client_category_id
            else client.client_category
        )
        effective_discount = client.get_effective_discount_percentage(company=active_company)
        linked_company_ids = [
            str(link.company_id)
            for link in client.company_links.filter(company__is_active=True, is_active=True).order_by("company__name", "company_id")
        ]
    elif active_company:
        linked_company_ids = [str(active_company.pk)]

    user = getattr(client, "user", None)
    return {
        "username": getattr(user, "username", "") or "",
        "email": getattr(user, "email", "") or "",
        "first_name": getattr(user, "first_name", "") or "",
        "last_name": getattr(user, "last_name", "") or "",
        "user_is_active": bool(getattr(user, "is_active", True)) if user else True,
        "client_is_approved": bool(getattr(client, "is_approved", True)) if client else True,
        "company_id": str(active_company.pk) if active_company else "",
        "company_name": getattr(client, "company_name", "") or "",
        "cuit_dni": getattr(client, "cuit_dni", "") or "",
        "document_type": getattr(client, "document_type", "") or "",
        "document_number": getattr(client, "document_number", "") or "",
        "discount": format(effective_discount, "f"),
        "client_category": str(effective_category.pk) if effective_category else "",
        "company_is_active": client_company.is_active if client_company else bool(getattr(client, "is_approved", True)) if client else True,
        "province": getattr(client, "province", "") or "",
        "fiscal_province": getattr(client, "fiscal_province", "") or "",
        "fiscal_city": getattr(client, "fiscal_city", "") or "",
        "address": getattr(client, "address", "") or "",
        "fiscal_address": getattr(client, "fiscal_address", "") or "",
        "postal_code": getattr(client, "postal_code", "") or "",
        "phone": getattr(client, "phone", "") or "",
        "client_type": getattr(client, "client_type", "") or "",
        "iva_condition": getattr(client, "iva_condition", "") or "",
        "notes": getattr(client, "notes", "") or "",
        "linked_company_ids": linked_company_ids,
    }


def _resolve_linked_companies(form_values, companies):
    company_map = {str(company.pk): company for company in companies}
    selected = []
    seen = set()
    for raw_company_id in form_values.get("linked_company_ids", []):
        normalized = str(raw_company_id or "").strip()
        if normalized in company_map and normalized not in seen:
            selected.append(company_map[normalized])
            seen.add(normalized)

    current_company_id = str(form_values.get("company_id", "")).strip()
    if (
        form_values.get("company_is_active", True)
        and current_company_id in company_map
        and current_company_id not in seen
    ):
        selected.append(company_map[current_company_id])
        seen.add(current_company_id)

    return selected


def _build_client_company_summary_rows(client, companies):
    if not client:
        return []

    company_list = list(companies or [])
    if not company_list:
        return []

    company_ids = [company.pk for company in company_list]
    links = {
        link.company_id: link
        for link in client.company_links.select_related("company", "client_category", "price_list").filter(
            company_id__in=company_ids
        )
    }
    order_stats = {
        row["company_id"]: row
        for row in (
            Order.objects.filter(
                user_id=client.user_id,
                company_id__in=company_ids,
            )
            .values("company_id")
            .annotate(
                total_orders=Count("id"),
                total_volume=Coalesce(Sum("total"), Decimal("0.00")),
                last_order_at=Max("created_at"),
            )
        )
    }

    rows = []
    for company in company_list:
        link = links.get(company.pk)
        category = client.get_effective_client_category(company=company)
        price_list = resolve_effective_price_list(
            company=company,
            client_company=link,
            client_category=category,
        )
        stats = order_stats.get(company.pk, {})
        rows.append(
            {
                "company": company,
                "link": link,
                "is_enabled": client.can_operate_in_company(company),
                "category_name": getattr(category, "name", "Sin categoria"),
                "discount_percentage": client.get_effective_discount_percentage(company=company),
                "price_list_name": getattr(price_list, "name", "Lista base"),
                "balance": client.get_current_balance(company=company),
                "total_orders": stats.get("total_orders", 0) or 0,
                "total_volume": stats.get("total_volume", Decimal("0.00")) or Decimal("0.00"),
                "last_order_at": stats.get("last_order_at"),
            }
        )
    return rows


def _render_client_form(
    request,
    *,
    client=None,
    active_company=None,
    companies=None,
    client_company=None,
    form_values=None,
):
    if companies is None:
        companies = Company.objects.filter(is_active=True).order_by("name")
    if form_values is None:
        form_values = _build_client_form_values(
            client=client,
            active_company=active_company,
            client_company=client_company,
        )

    effective_category = (
        client_company.client_category
        if client_company and client_company.client_category_id
        else getattr(client, "client_category", None)
    )
    effective_category_id = effective_category.pk if effective_category else None
    effective_discount = (
        client.get_effective_discount_percentage(company=active_company)
        if client
        else Decimal("0")
    )
    company_is_active = form_values.get("company_is_active", True)
    uses_legacy = client.uses_legacy_commercial_rules(active_company) if client else False
    selected_company_ids = {str(company_id) for company_id in form_values.get("linked_company_ids", [])}
    current_company_id = str(form_values.get("company_id", "")).strip()
    if company_is_active and current_company_id:
        selected_company_ids.add(current_company_id)
    links_by_company_id = {}
    if client:
        links_by_company_id = {
            link.company_id: link
            for link in client.company_links.select_related("company", "client_category").filter(company__is_active=True)
        }
    company_cards = []
    for company in companies:
        link = links_by_company_id.get(company.pk)
        company_cards.append(
            {
                "company": company,
                "link": link,
                "is_selected": str(company.pk) in selected_company_ids,
                "is_current": current_company_id == str(company.pk),
            }
        )

    recent_client_audit_logs = []
    if client and client.pk:
        recent_client_audit_logs = list(
            AdminAuditLog.objects.filter(
                Q(target_type="client_profile", target_id=str(client.pk))
                | Q(target_type="accounts.clientprofile", target_id=str(client.pk))
            )
            .select_related("user")
            .order_by("-created_at")[:6]
        )

    return render(
        request,
        "admin_panel/clients/form.html",
        {
            "client": client,
            "form_values": form_values,
            "is_create": client is None,
            "client_categories": get_client_categories_for_client(client, client_company),
            "companies": companies,
            "active_company": active_company,
            "client_company": client_company,
            "effective_category_id": effective_category_id,
            "effective_discount": effective_discount,
            "company_is_active": company_is_active,
            "uses_legacy_rules": uses_legacy,
            "company_cards": company_cards,
            "client_company_summary_rows": _build_client_company_summary_rows(client, companies),
            "recent_client_audit_logs": recent_client_audit_logs,
            "can_manage_client_credentials": can_manage_client_credentials(request.user),
            "document_type_choices": ClientProfile.DOCUMENT_TYPE_CHOICES,
            "client_type_choices": ClientProfile.CLIENT_TYPE_CHOICES,
            "iva_condition_choices": ClientProfile.IVA_CHOICES,
        },
    )


def normalize_admin_search_query(raw_query):
    """
    Shared admin search parser for all panel lists.
    """
    return parse_text_search_query(
        raw_query,
        max_include=10,
        max_exclude=10,
        max_phrases=5,
    )


def apply_admin_text_search(queryset, raw_query, fields):
    parsed = normalize_admin_search_query(raw_query)
    if not parsed.get("raw"):
        return queryset, ""
    filtered = apply_parsed_text_search(
        queryset,
        parsed,
        fields,
        order_by_similarity=False,
    )
    return filtered, parsed["raw"]


def parse_admin_decimal_input(raw_value, field_label, min_value=None, max_value=None):
    """
    Parse decimal input supporting both comma and dot separators.
    """
    raw = str(raw_value or "").strip().replace(",", ".")
    if raw == "":
        raise ValueError(f"{field_label} es obligatorio.")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_label} invalido.")

    if min_value is not None:
        min_decimal = Decimal(str(min_value))
        if value < min_decimal:
            raise ValueError(f"{field_label} no puede ser menor a {min_decimal}.")
    if max_value is not None:
        max_decimal = Decimal(str(max_value))
        if value > max_decimal:
            raise ValueError(f"{field_label} no puede ser mayor a {max_decimal}.")
    return value


def build_category_tree_rows(categories):
    """
    Build a stable flattened tree for an arbitrary category queryset/list.
    """
    category_list = list(categories)
    category_map = {category.id: category for category in category_list}
    children_map = {}

    for category in category_list:
        children_map.setdefault(category.parent_id, []).append(category)

    for siblings in children_map.values():
        siblings.sort(key=lambda cat: (cat.order, cat.name.lower(), cat.id))

    roots = [cat for cat in category_list if cat.parent_id not in category_map]
    roots.sort(key=lambda cat: (cat.order, cat.name.lower(), cat.id))

    rows = []
    visited = set()

    def walk(node, depth, path_names):
        if node.id in visited:
            return
        visited.add(node.id)

        next_path = [*path_names, node.name]
        children = children_map.get(node.id, [])
        rows.append({
            'category': node,
            'depth': depth,
            'full_path': " > ".join(next_path),
            'has_children': bool(children),
            'children_count': len(children),
        })

        for child in children:
            walk(child, depth + 1, next_path)

    for root in roots:
        walk(root, 0, [])

    # Safety pass for orphan/cyclic data that had no detected root.
    remaining = sorted(
        (cat for cat in category_list if cat.id not in visited),
        key=lambda cat: (cat.order, cat.name.lower(), cat.id),
    )
    for category in remaining:
        walk(category, 0, [])

    return rows


def build_category_options(categories, include_inactive_suffix=False):
    """
    Return flattened options with tree depth and readable labels.
    """
    options = []
    for row in build_category_tree_rows(categories):
        category = row['category']
        option_label = f"{'-- ' * row['depth']}{category.name}"
        if include_inactive_suffix and not category.is_active:
            option_label = f"{option_label} [inactiva]"

        options.append({
            'id': category.id,
            'name': category.name,
            'depth': row['depth'],
            'full_path': row['full_path'],
            'label': option_label,
            'is_active': category.is_active,
        })

    return options


def get_cached_category_options(only_active=True, include_inactive_suffix=False):
    """
    Cache category options to reduce repeated tree processing in admin forms/filters.
    """
    qs = Category.objects.all()
    if only_active:
        qs = qs.filter(is_active=True)

    aggregate = qs.aggregate(total=Count("id"), max_updated=Max("updated_at"))
    max_updated = aggregate.get("max_updated")
    stamp = int(max_updated.timestamp()) if max_updated else 0
    cache_key = f"admin_cat_opts:{only_active}:{include_inactive_suffix}:{aggregate.get('total') or 0}:{stamp}"

    options = cache.get(cache_key)
    if options is not None:
        return options

    options = build_category_options(
        categories=qs.select_related("parent").order_by("order", "name"),
        include_inactive_suffix=include_inactive_suffix,
    )
    cache.set(cache_key, options, 300)
    return options


def detect_category_integrity_issues(categories):
    """
    Return a list of hierarchy integrity warnings.
    """
    issues = []
    category_map = {category.id: category for category in categories}

    for category in categories:
        if category.parent_id and category.parent_id not in category_map:
            issues.append(f'Categoria "{category.name}" apunta a un padre inexistente.')

        if category.parent and not category.parent.is_active and category.is_active:
            issues.append(
                f'Categoria activa "{category.name}" tiene padre inactivo "{category.parent.name}".'
            )

        seen = set()
        parent = category.parent
        while parent:
            if parent.id in seen or parent.id == category.id:
                issues.append(f"Se detecto ciclo jerarquico en {category.name}.")
                break
            seen.add(parent.id)
            parent = parent.parent

    return list(dict.fromkeys(issues))


def calculate_category_deactivation_impact(category):
    """
    Simulate how many products would become hidden if this category tree is deactivated.
    """
    category_tree_ids = category.get_descendant_ids(include_self=True)

    linked_products = Product.objects.filter(
        Q(category_id__in=category_tree_ids) | Q(categories__id__in=category_tree_ids)
    ).distinct().prefetch_related("categories").select_related("category")

    would_hide = 0
    for product in linked_products:
        linked_categories = product.get_linked_categories()
        has_other_active = any(
            cat.is_active and cat.id not in category_tree_ids
            for cat in linked_categories
        )
        if product.is_active and not has_other_active:
            would_hide += 1

    linked_total = linked_products.count()
    return {
        "descendants_count": max(len(category_tree_ids) - 1, 0),
        "linked_products": linked_total,
        "would_hide": would_hide,
        "would_remain_visible": max(linked_total - would_hide, 0),
    }


def collect_created_refs(import_type, row_results):
    """
    Extract references that can be rolled back for created records in one import batch.
    """
    refs = []
    for row in row_results:
        if not getattr(row, "success", False) or getattr(row, "action", "") != "created":
            continue

        data = row.data or {}
        if import_type in ("products", "abrazaderas"):
            value = str(data.get("sku") or data.get("codigo") or "").strip()
        elif import_type == "categories":
            value = slugify(str(data.get("nombre") or "").strip())
        elif import_type == "clients":
            value = str(data.get("username") or data.get("usuario") or data.get("email") or "").strip()
        else:
            value = ""

        if value:
            refs.append(value)

    return list(dict.fromkeys(refs))


@staff_member_required
def dashboard(request):
    """Admin dashboard with key metrics."""
    active_company = get_active_company(request)
    last_30_days = timezone.now() - timedelta(days=30)
    analytics_qs = CatalogAnalyticsEvent.objects.filter(created_at__gte=last_30_days)

    top_zero_result_searches = (
        analytics_qs.filter(event_type=CatalogAnalyticsEvent.EVENT_SEARCH, results_count=0)
        .exclude(query="")
        .values("query")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )
    top_category_views = (
        analytics_qs.filter(event_type=CatalogAnalyticsEvent.EVENT_CATEGORY_VIEW)
        .exclude(category_slug="")
        .values("category_slug")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )
    top_filter_sets = (
        analytics_qs.filter(event_type=CatalogAnalyticsEvent.EVENT_FILTER)
        .exclude(query="")
        .values("query")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )

    recent_orders_qs = Order.objects.select_related('user').order_by('-created_at')
    if active_company:
        recent_orders_qs = recent_orders_qs.filter(company=active_company)
    recent_orders = list(recent_orders_qs[:5])
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    orders_today_qs = Order.objects.filter(created_at__gte=today_start)
    if active_company:
        orders_today_qs = orders_today_qs.filter(company=active_company)
    orders_today_summary = orders_today_qs.aggregate(
        count=Count('id'),
        total=Sum('total'),
    )

    active_clients_orders_qs = Order.objects.filter(
        created_at__gte=last_30_days,
        user_id__isnull=False,
    )
    if active_company:
        active_clients_orders_qs = active_clients_orders_qs.filter(company=active_company)
    active_clients_30d = set(active_clients_orders_qs.values_list('user_id', flat=True))
    active_clients_payments_qs = ClientPayment.objects.filter(
        paid_at__gte=last_30_days,
        is_cancelled=False,
        client_profile__user_id__isnull=False,
    )
    if active_company:
        active_clients_payments_qs = active_clients_payments_qs.filter(company=active_company)
    active_clients_30d.update(
        active_clients_payments_qs.values_list('client_profile__user_id', flat=True)
    )

    top_products_qs = OrderItem.objects.filter(order__created_at__gte=last_30_days)
    if active_company:
        top_products_qs = top_products_qs.filter(order__company=active_company)
    top_products_30d = (
        top_products_qs.values('product_sku', 'product_name')
        .annotate(total_qty=Sum('quantity'), total_amount=Sum('subtotal'))
        .order_by('-total_qty', '-total_amount')[:5]
    )

    # Delivery cycle estimation (hours) from status history.
    delivered_candidates_qs = (
        Order.objects.filter(status=Order.STATUS_DELIVERED, created_at__gte=last_30_days)
        .prefetch_related('status_history')
        .order_by('-created_at')
    )
    if active_company:
        delivered_candidates_qs = delivered_candidates_qs.filter(company=active_company)
    delivered_candidates = list(delivered_candidates_qs[:120])
    cycle_hours = []
    for order in delivered_candidates:
        history = list(order.status_history.all())
        confirmed_at = None
        delivered_at = None
        for event in sorted(history, key=lambda item: item.created_at):
            if event.to_status == Order.STATUS_CONFIRMED and confirmed_at is None:
                confirmed_at = event.created_at
            if event.to_status == Order.STATUS_DELIVERED:
                delivered_at = event.created_at
        if confirmed_at and delivered_at and delivered_at > confirmed_at:
            cycle_hours.append((delivered_at - confirmed_at).total_seconds() / 3600)
    avg_delivery_cycle_hours = (
        Decimal(str(sum(cycle_hours) / len(cycle_hours))).quantize(Decimal('0.01'))
        if cycle_hours
        else None
    )

    # Internal alerts.
    stale_days = int(getattr(settings, 'ALERT_PREPARING_STALE_DAYS', 3) or 3)
    stale_threshold = timezone.now() - timedelta(days=stale_days)
    stale_preparing_qs = (
        Order.objects.select_related('user')
        .filter(status=Order.STATUS_PREPARING, status_updated_at__lt=stale_threshold)
        .order_by('status_updated_at')
    )
    if active_company:
        stale_preparing_qs = stale_preparing_qs.filter(company=active_company)
    stale_preparing_orders = list(stale_preparing_qs[:10])

    high_debt_threshold = Decimal(str(getattr(settings, 'ALERT_HIGH_DEBT_THRESHOLD', 500000)))
    high_debt_items = []
    high_debt_qs = ClientTransaction.objects.values('client_profile_id')
    if active_company:
        high_debt_qs = high_debt_qs.filter(company=active_company)
    high_debt_raw = (
        high_debt_qs
        .annotate(balance=Coalesce(Sum('amount'), Value(Decimal('0.00'))))
        .filter(balance__gt=high_debt_threshold)
        .order_by('-balance')[:10]
    )
    if high_debt_raw:
        profiles_map = {
            profile.id: profile
            for profile in ClientProfile.objects.select_related('user').filter(
                id__in=[row['client_profile_id'] for row in high_debt_raw]
            )
        }
        for row in high_debt_raw:
            profile = profiles_map.get(row['client_profile_id'])
            if not profile:
                continue
            high_debt_items.append({
                'client': profile,
                'balance': row['balance'],
            })

    products_without_active_category = Product.objects.filter(is_active=True).filter(
        ~Q(category__is_active=True),
        ~Q(categories__is_active=True),
    ).distinct()
    products_without_active_category_count = products_without_active_category.count()
    products_without_active_category_sample = list(
        products_without_active_category.only('id', 'sku', 'name').order_by('name')[:8]
    )

    import_error_threshold = int(getattr(settings, 'ALERT_IMPORT_ERROR_RATE_PERCENT', 30) or 30)
    high_error_imports = []
    for execution in ImportExecution.objects.filter(
        created_at__gte=last_30_days
    ).exclude(status=ImportExecution.STATUS_PROCESSING).order_by('-created_at')[:40]:
        total = (execution.created_count or 0) + (execution.updated_count or 0) + (execution.error_count or 0)
        if total <= 0:
            continue
        error_rate = (execution.error_count * 100.0) / float(total)
        if execution.error_count > 0 and error_rate >= import_error_threshold:
            high_error_imports.append({
                'execution': execution,
                'error_rate': round(error_rate, 2),
            })

    internal_alerts = []
    if stale_preparing_orders:
        internal_alerts.append({
            'title': f'Pedidos trabados en preparacion (> {stale_days} dias)',
            'kind': 'warning',
            'count': len(stale_preparing_orders),
        })
    if high_debt_items:
        internal_alerts.append({
            'title': f'Clientes con deuda alta (> ${high_debt_threshold:.2f})',
            'kind': 'danger',
            'count': len(high_debt_items),
        })
    if products_without_active_category_count:
        internal_alerts.append({
            'title': 'Productos activos sin categoria activa',
            'kind': 'warning',
            'count': products_without_active_category_count,
        })
    if high_error_imports:
        internal_alerts.append({
            'title': f'Importaciones con tasa de error >= {import_error_threshold}%',
            'kind': 'danger',
            'count': len(high_error_imports),
        })

    context = {
        'product_count': Product.objects.count(),
        'active_product_count': Product.objects.filter(is_active=True).count(),
        'category_count': Category.objects.count(),
        'supplier_count': Supplier.objects.count(),
        'client_count': ClientProfile.objects.count(),
        'pending_requests': AccountRequest.objects.filter(status='pending').count(),
        'pending_orders': Order.objects.filter(status__in=[Order.STATUS_DRAFT, Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]).count(),
        'recent_orders': recent_orders,
        'recent_requests': AccountRequest.objects.filter(status='pending').order_by('-created_at')[:5],
        'top_zero_result_searches': top_zero_result_searches,
        'top_category_views': top_category_views,
        'top_filter_sets': top_filter_sets,
        'audit_count_last_30': AdminAuditLog.objects.filter(created_at__gte=last_30_days).count(),
        'recent_audit_logs': AdminAuditLog.objects.select_related('user').order_by('-created_at')[:8],
        'kpi_orders_today_count': orders_today_summary.get('count') or 0,
        'kpi_orders_today_total': orders_today_summary.get('total') or Decimal('0.00'),
        'kpi_active_clients_30d': len(active_clients_30d),
        'kpi_avg_delivery_cycle_hours': avg_delivery_cycle_hours,
        'top_products_30d': top_products_30d,
        'internal_alerts': internal_alerts,
        'stale_preparing_orders': stale_preparing_orders,
        'high_debt_items': high_debt_items,
        'products_without_active_category_count': products_without_active_category_count,
        'products_without_active_category_sample': products_without_active_category_sample,
        'high_error_imports': high_error_imports,
    }
    return render(request, 'admin_panel/dashboard.html', context)


# ===================== PRODUCTS =====================

@staff_member_required
def product_list(request):
    """Product list with search, filters, and pagination."""
    products, search, current_category_id, active_filter = get_product_queryset(request.GET)
    
    # Ordering
    order = request.GET.get('order', '-updated_at')
    products = products.order_by(order)
    
    # Pagination
    paginator = Paginator(products, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    enrich_products_with_category_state(page_obj.object_list)
    
    category_options = get_cached_category_options(only_active=True, include_inactive_suffix=False)
    filter_chips = build_product_filter_chips(request.GET, category_options)
    
    context = {
        'page_obj': page_obj,
        'category_options': category_options,
        'search': search,
        'current_category_id': current_category_id,
        'active_filter': active_filter,
        'order_by': order,
        'active_filter_chips': filter_chips,
        'total_count': products.count(),
        'pagination_count': len(page_obj.object_list),
    }
    return render(request, 'admin_panel/products/list.html', context)


def build_product_filter_chips(query_params, category_options):
    """Build removable chips for admin product filters."""
    chips = []
    category_labels = {str(cat['id']): cat['label'] for cat in category_options}
    category_labels['__uncategorized__'] = 'Sin categorias'
    active_labels = {'1': 'Activos', '0': 'Inactivos'}
    order_labels = {
        '-updated_at': 'Mas recientes',
        'updated_at': 'Mas antiguos',
        'name': 'Nombre A-Z',
        '-name': 'Nombre Z-A',
        'price': 'Menor precio',
        '-price': 'Mayor precio',
        'stock': 'Menor stock',
        '-stock': 'Mayor stock',
        'sku': 'SKU A-Z',
        '-sku': 'SKU Z-A',
    }

    def _remove_url(param_key):
        params = query_params.copy()
        params.pop(param_key, None)
        params.pop('page', None)
        encoded = params.urlencode()
        return f"{reverse('admin_product_list')}?{encoded}" if encoded else reverse('admin_product_list')

    search = (query_params.get('q') or '').strip()
    if search:
        chips.append({
            'label': 'Busqueda',
            'value': search,
            'remove_url': _remove_url('q'),
        })

    category = (query_params.get('category') or '').strip()
    if category:
        chips.append({
            'label': 'Categoria',
            'value': category_labels.get(category, category),
            'remove_url': _remove_url('category'),
        })

    active = (query_params.get('active') or '').strip()
    if active:
        chips.append({
            'label': 'Estado',
            'value': active_labels.get(active, active),
            'remove_url': _remove_url('active'),
        })

    order = (query_params.get('order') or '').strip()
    if order:
        chips.append({
            'label': 'Orden',
            'value': order_labels.get(order, order),
            'remove_url': _remove_url('order'),
        })

    return chips


def get_product_queryset(data):
    """Resusable filter logic for products."""
    products = Product.objects.select_related('category', 'supplier_ref').prefetch_related('categories').all()
    
    # Search
    products, search = apply_admin_text_search(
        products,
        data.get('q', ''),
        ["sku", "name", "supplier", "supplier_ref__name", "description", "filter_1", "filter_2", "filter_3"],
    )
    
    # Category filter
    category_id = (data.get('category', '') or '').strip()
    current_category_id = category_id or None
    if category_id == '__uncategorized__':
        products = products.filter(category__isnull=True, categories__isnull=True).distinct()
    elif category_id:
        try:
            current_category_id = int(category_id)
            selected_category = Category.objects.filter(pk=current_category_id).first()
            if selected_category:
                descendant_ids = selected_category.get_descendant_ids(include_self=True)
                products = products.filter(
                    Q(category_id__in=descendant_ids) | Q(categories__id__in=descendant_ids)
                ).distinct()
        except (ValueError, TypeError):
            current_category_id = None
        
    # Active filter
    active_filter = data.get('active', '')
    if active_filter:
        is_active = active_filter == '1'
        products = products.filter(is_active=is_active)
        
    return products, search, current_category_id, active_filter


def _delete_orphan_product_image(image_name):
    image_name = str(image_name or "").strip()
    if not image_name:
        return
    if Product.objects.filter(image=image_name).exists():
        return
    try:
        storage = Product._meta.get_field("image").storage
        if storage.exists(image_name):
            storage.delete(image_name)
    except Exception:
        logger.warning("No se pudo eliminar imagen huerfana: %s", image_name)


def _validate_admin_image_upload(uploaded_file):
    if not uploaded_file:
        raise ValueError("Debes seleccionar una imagen.")

    allowed_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    extension = os.path.splitext(uploaded_file.name or "")[1].lower()
    if extension not in allowed_ext:
        raise ValueError("Formato de imagen no permitido. Usa JPG, PNG, WEBP o GIF.")

    max_size_bytes = 8 * 1024 * 1024  # 8 MB
    if uploaded_file.size and uploaded_file.size > max_size_bytes:
        raise ValueError("La imagen supera 8MB. Reduce el peso e intenta nuevamente.")


def _store_bulk_product_image(uploaded_file):
    _validate_admin_image_upload(uploaded_file)
    extension = os.path.splitext(uploaded_file.name or "")[1].lower() or ".jpg"
    base_name = slugify(os.path.splitext(uploaded_file.name or "")[0]) or "producto"
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    relative_name = f"products/bulk/{base_name}-{stamp}{extension}"
    storage = Product._meta.get_field("image").storage
    payload = ContentFile(uploaded_file.read())
    return storage.save(relative_name, payload)


def extract_target_product_ids_from_post(post_data, raw_body=b""):
    """
    Accept multiple input formats for bulk product selection.
    """
    raw_ids = []
    raw_ids.extend(post_data.getlist('product_ids'))
    raw_ids.extend(post_data.getlist('product_ids[]'))
    raw_ids.extend(post_data.getlist('ids'))

    # Extra tolerance for indexed/nested names (e.g. product_ids[0], product_ids.0)
    if hasattr(post_data, "lists"):
        for key, values in post_data.lists():
            if key.startswith("product_ids"):
                raw_ids.extend(values)

    csv_raw = (post_data.get('product_ids_csv', '') or '').strip()
    if csv_raw:
        raw_ids.extend([part.strip() for part in csv_raw.split(',') if part.strip()])

    # Last-resort fallback from raw request body.
    if not raw_ids and raw_body:
        try:
            decoded = raw_body.decode("utf-8", errors="ignore")
            parsed = parse_qs(decoded, keep_blank_values=False)
            for key, values in parsed.items():
                if key in ("product_ids", "product_ids[]", "ids", "product_ids_csv") or key.startswith("product_ids"):
                    if key == "product_ids_csv":
                        for value in values:
                            raw_ids.extend([part.strip() for part in str(value).split(",") if part.strip()])
                    else:
                        raw_ids.extend(values)
        except Exception:
            pass

    return normalize_category_ids(raw_ids)


def _redirect_admin_product_list_with_filters(request):
    """Redirect to product list preserving active filters."""
    params = {}
    for key in ('q', 'category', 'active', 'order', 'page'):
        raw_value = request.POST.get(key, '')
        value = str(raw_value).strip()
        if value:
            params[key] = value

    base_url = reverse('admin_product_list')
    if params:
        return redirect(f"{base_url}?{urlencode(params)}")
    return redirect(base_url)


def enrich_products_with_category_state(products):
    """
    Attach category status metadata for admin templates.
    """
    for product in products:
        linked_categories = product.get_linked_categories()
        category_status_rows = []
        active_category_count = 0

        for cat in linked_categories:
            is_category_active = bool(cat.is_active)
            is_effective_active = bool(product.is_active and is_category_active)
            if is_effective_active:
                active_category_count += 1

            category_status_rows.append({
                'id': cat.id,
                'name': cat.name,
                'is_category_active': is_category_active,
                'is_effective_active': is_effective_active,
            })

        product.linked_categories = linked_categories
        product.category_status_rows = category_status_rows
        product.active_category_count = active_category_count
        product.catalog_visibility = bool(
            product.is_active and (
                active_category_count > 0 or len(linked_categories) == 0
            )
        )


def validate_attributes_for_category(primary_category_id, attributes_dict):
    """
    Validate required/recommended attributes from category templates.
    """
    if not str(primary_category_id).isdigit():
        return [], []

    attributes = attributes_dict or {}
    cat_attrs = CategoryAttribute.objects.filter(category_id=int(primary_category_id))
    missing_required = []
    missing_recommended = []
    for attr in cat_attrs:
        value = attributes.get(attr.slug)
        has_value = bool(str(value).strip()) if value is not None else False
        if attr.required and not has_value:
            missing_required.append(attr.name)
        elif attr.is_recommended and not has_value:
            missing_recommended.append(attr.name)
    return missing_required, missing_recommended


@staff_member_required
@superuser_required_for_modifications
def product_create(request):
    """Create new product."""
    category_options = get_cached_category_options(only_active=True, include_inactive_suffix=False)
    supplier_suggestions = list(Supplier.objects.order_by('name').values_list('name', flat=True)[:400])
    
    if request.method == 'POST':
        try:
            sku = request.POST.get('sku', '').strip()
            name = request.POST.get('name', '').strip()
            supplier_name = clean_supplier_name(request.POST.get('supplier', ''))
            supplier_obj = ensure_supplier(supplier_name) if supplier_name else None
            price = request.POST.get('price', '0')
            stock = request.POST.get('stock', '0')
            price_value = parse_admin_decimal_input(price, 'Precio', min_value='0')
            stock_value = parse_int_value(stock, 'Stock', min_value=0)
            primary_category_id = request.POST.get('category', '')
            selected_category_ids = normalize_category_ids(request.POST.getlist('categories'))
            description = request.POST.get('description', '').strip()
            attributes_payload = request.POST.get('attributes_json', '{}')
            uploaded_image = request.FILES.get('image')
            settings = SiteSettings.get_settings()

            if uploaded_image:
                _validate_admin_image_upload(uploaded_image)

            try:
                attributes_data = json.loads(attributes_payload or '{}')
            except json.JSONDecodeError:
                attributes_data = {}

            if (
                settings.require_primary_category_for_multicategory
                and len(selected_category_ids) > 1
                and not str(primary_category_id).isdigit()
            ):
                messages.error(
                    request,
                    "Debes definir una categoria principal cuando vinculas multiples categorias.",
                )
                return render(request, 'admin_panel/products/form.html', {
                    'category_options': category_options,
                    'selected_category_ids': selected_category_ids,
                    'supplier_suggestions': supplier_suggestions,
                        'action': 'Crear',
                    })

            missing_required, missing_recommended = validate_attributes_for_category(
                primary_category_id,
                attributes_data,
            )
            if missing_required:
                messages.error(
                    request,
                    f'Faltan atributos obligatorios para la categoria principal: {", ".join(missing_required)}.',
                )
                return render(request, 'admin_panel/products/form.html', {
                    'category_options': category_options,
                    'selected_category_ids': selected_category_ids,
                    'supplier_suggestions': supplier_suggestions,
                    'action': 'Crear',
                })
            if missing_recommended:
                messages.warning(
                    request,
                    f'Atributos recomendados sin completar: {", ".join(missing_recommended)}.',
                )
            
            if Product.objects.filter(sku=sku).exists():
                messages.error(request, f'Ya existe un producto con SKU "{sku}"')
            else:
                product = Product.objects.create(
                    sku=sku,
                    name=name,
                    supplier=supplier_name,
                    supplier_ref=supplier_obj,
                    price=price_value,
                    stock=stock_value,
                    category_id=int(primary_category_id) if str(primary_category_id).isdigit() else None,
                    description=description,
                    attributes=attributes_data,
                    image=uploaded_image,
                )
                assign_categories_to_product(product, selected_category_ids, primary_category_id)
                log_admin_action(
                    request,
                    action="product_create",
                    target_type="product",
                    target_id=product.pk,
                    details={
                        "sku": product.sku,
                        "supplier": product.supplier,
                        "categories": selected_category_ids,
                    },
                )
                messages.success(request, f'Producto "{sku}" creado exitosamente.')
                return redirect('admin_product_list')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
    
    return render(request, 'admin_panel/products/form.html', {
        'category_options': category_options,
        'selected_category_ids': [],
        'supplier_suggestions': supplier_suggestions,
        'action': 'Crear',
    })


@staff_member_required
@superuser_required_for_modifications
def product_edit(request, pk):
    """Edit existing product."""
    product = get_object_or_404(Product, pk=pk)
    category_options = get_cached_category_options(only_active=True, include_inactive_suffix=False)
    supplier_suggestions = list(Supplier.objects.order_by('name').values_list('name', flat=True)[:400])
    
    if request.method == 'POST':
        try:
            product.sku = request.POST.get('sku', '').strip()
            product.name = request.POST.get('name', '').strip()
            product.supplier = clean_supplier_name(request.POST.get('supplier', ''))
            product.supplier_ref = ensure_supplier(product.supplier) if product.supplier else None
            product.price = parse_admin_decimal_input(request.POST.get('price', '0'), 'Precio', min_value='0')
            product.stock = parse_int_value(request.POST.get('stock', '0'), 'Stock', min_value=0)
            product.description = request.POST.get('description', '').strip()
            product.is_active = request.POST.get('is_active') == 'on'
            uploaded_image = request.FILES.get('image')
            remove_image = request.POST.get('remove_image') == 'on'
            old_image_name = str(product.image.name or '').strip() if product.image else ''
            new_image_applied = False

            if uploaded_image:
                _validate_admin_image_upload(uploaded_image)

            primary_category_id = request.POST.get('category', '')
            selected_category_ids = normalize_category_ids(request.POST.getlist('categories'))
            settings = SiteSettings.get_settings()
            if (
                settings.require_primary_category_for_multicategory
                and len(selected_category_ids) > 1
                and not str(primary_category_id).isdigit()
            ):
                messages.error(
                    request,
                    "Debes definir una categoria principal cuando vinculas multiples categorias.",
                )
                return render(request, 'admin_panel/products/form.html', {
                    'product': product,
                    'category_options': category_options,
                    'selected_category_ids': selected_category_ids,
                    'supplier_suggestions': supplier_suggestions,
                    'action': 'Editar',
                })

            product.category_id = int(primary_category_id) if str(primary_category_id).isdigit() else None
            
            # Update attributes
            attributes_data = product.attributes or {}
            attributes_json = request.POST.get('attributes_json', '{}')
            if attributes_json:
                try:
                    attributes_data = json.loads(attributes_json)
                except json.JSONDecodeError:
                    attributes_data = product.attributes or {}

            missing_required, missing_recommended = validate_attributes_for_category(
                primary_category_id,
                attributes_data,
            )
            if missing_required:
                messages.error(
                    request,
                    f'Faltan atributos obligatorios para la categoria principal: {", ".join(missing_required)}.',
                )
                return render(request, 'admin_panel/products/form.html', {
                    'product': product,
                    'category_options': category_options,
                    'selected_category_ids': selected_category_ids,
                    'supplier_suggestions': supplier_suggestions,
                    'action': 'Editar',
                })
            if missing_recommended:
                messages.warning(
                    request,
                    f'Atributos recomendados sin completar: {", ".join(missing_recommended)}.',
                )

            product.attributes = attributes_data

            if remove_image and not uploaded_image:
                product.image = None
                new_image_applied = True
            if uploaded_image:
                product.image = uploaded_image
                new_image_applied = True
            
            product.save()
            assign_categories_to_product(product, selected_category_ids, primary_category_id)
            if new_image_applied and old_image_name:
                _delete_orphan_product_image(old_image_name)
            log_admin_action(
                request,
                action="product_edit",
                target_type="product",
                target_id=product.pk,
                details={
                    "sku": product.sku,
                    "supplier": product.supplier,
                    "categories": selected_category_ids,
                },
            )
            messages.success(request, f'Producto "{product.sku}" actualizado.')
            return redirect('admin_product_list')
            
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
            
    selected_category_ids = list(product.categories.values_list('id', flat=True))
    if not selected_category_ids and product.category_id:
        selected_category_ids = [product.category_id]

    return render(request, 'admin_panel/products/form.html', {
        'product': product,
        'category_options': category_options,
        'selected_category_ids': selected_category_ids,
        'supplier_suggestions': supplier_suggestions,
        'action': 'Editar',
    })


@staff_member_required
@superuser_required_for_modifications
def product_delete(request, pk):
    """Delete single product."""
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        sku = product.sku
        product_id = product.pk
        product.delete()
        log_admin_action(
            request,
            action="product_delete",
            target_type="product",
            target_id=product_id,
            details={"sku": sku},
        )
        messages.success(request, f'Producto "{sku}" eliminado.')
        return redirect('admin_product_list')
        
    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"{product.name} ({product.sku})",
        'cancel_url': reverse('admin_product_list')
    })


@staff_member_required
@require_POST
@superuser_required_for_modifications
def product_toggle_active(request):
    """Toggle product active status (AJAX)."""
    try:
        data = json.loads(request.body)
        product_ids = data.get('ids', [])
        active = data.get('active', True)
        
        Product.objects.filter(id__in=product_ids).update(is_active=active)
        log_admin_action(
            request,
            action="product_toggle_active",
            target_type="product_bulk",
            details={"ids": product_ids, "active": bool(active)},
        )
        
        return JsonResponse({
            'success': True,
            'message': f'{len(product_ids)} productos actualizados'
        })
    except Exception as e:
        logger.exception("Error toggling product active status")
        return JsonResponse({'success': False, 'error': 'No se pudieron actualizar los productos.'}, status=400)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def product_bulk_category_update(request):
    """Bulk categorize selected products."""
    try:
        raw_post_body = request.body
        category_id = request.POST.get('category_id')
        mode = request.POST.get('mode', 'append')
        select_all_pages = request.POST.get('select_all_pages') == 'true'

        if not category_id:
            messages.warning(request, 'No se selecciono una categoria.')
            return _redirect_admin_product_list_with_filters(request)

        if select_all_pages:
            products_to_update, _, _, _ = get_product_queryset(request.POST)
        else:
            product_ids = extract_target_product_ids_from_post(request.POST, raw_post_body)
            if not product_ids:
                logger.warning(
                    "product_bulk_category_update without selected products | user=%s | keys=%s | product_ids=%s | product_ids_csv=%s",
                    getattr(request.user, "username", "unknown"),
                    list(request.POST.keys()),
                    request.POST.getlist("product_ids"),
                    request.POST.get("product_ids_csv", ""),
                )
                messages.warning(request, 'No se seleccionaron productos.')
                return _redirect_admin_product_list_with_filters(request)
            products_to_update = Product.objects.filter(id__in=product_ids)

        target_ids = list(products_to_update.values_list('id', flat=True))
        if mode == 'replace':
            count = replace_categories_for_products(target_ids, category_id)
        else:
            count = add_category_to_products(target_ids, category_id)

        category = Category.objects.get(pk=category_id)
        if mode == 'replace':
            messages.success(request, f'{count} productos recategorizados a "{category.name}".')
        else:
            messages.success(request, f'{count} productos vinculados a "{category.name}".')

        log_admin_action(
            request,
            action="product_bulk_category_update",
            target_type="category",
            target_id=category.pk,
            details={"mode": mode, "count": count},
        )

    except Exception as e:
        messages.error(request, f'Error al actualizar categorias: {str(e)}')

    return _redirect_admin_product_list_with_filters(request)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def product_bulk_status_update(request):
    """Bulk activate/deactivate selected products."""
    raw_post_body = request.body
    set_active_raw = str(request.POST.get('set_active', '')).strip()
    if set_active_raw not in {'0', '1'}:
        messages.warning(request, 'Accion de estado invalida.')
        return _redirect_admin_product_list_with_filters(request)

    set_active = set_active_raw == '1'
    select_all_pages = request.POST.get('select_all_pages') == 'true'

    if select_all_pages:
        products_to_update, _, _, _ = get_product_queryset(request.POST)
    else:
        product_ids = extract_target_product_ids_from_post(request.POST, raw_post_body)
        if not product_ids:
            logger.warning(
                "product_bulk_status_update without selected products | user=%s | keys=%s | product_ids=%s | product_ids_csv=%s",
                getattr(request.user, "username", "unknown"),
                list(request.POST.keys()),
                request.POST.getlist("product_ids"),
                request.POST.get("product_ids_csv", ""),
            )
            messages.warning(request, 'No se seleccionaron productos.')
            return _redirect_admin_product_list_with_filters(request)
        products_to_update = Product.objects.filter(id__in=product_ids)

    target_ids = list(products_to_update.values_list('id', flat=True))
    if not target_ids:
        messages.info(request, 'No hubo productos para actualizar.')
        return _redirect_admin_product_list_with_filters(request)

    count = Product.objects.filter(id__in=target_ids).update(is_active=set_active)
    action_label = 'activados' if set_active else 'desactivados'
    messages.success(request, f'{count} productos {action_label}.')
    log_admin_action(
        request,
        action='product_bulk_status_update',
        target_type='product_bulk',
        details={'count': count, 'set_active': set_active, 'select_all_pages': select_all_pages},
    )
    return _redirect_admin_product_list_with_filters(request)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def product_bulk_image_update(request):
    """Bulk assign/clear product images for selected or filtered products."""
    image_mode = str(request.POST.get('image_mode', 'set')).strip().lower()
    select_all_pages = request.POST.get('select_all_pages') == 'true'
    only_missing = request.POST.get('only_missing') == '1'

    if image_mode not in {'set', 'clear'}:
        messages.warning(request, 'Modo de imagen invalido.')
        return _redirect_admin_product_list_with_filters(request)

    if select_all_pages:
        products_to_update, _, _, _ = get_product_queryset(request.POST)
    else:
        product_ids = extract_target_product_ids_from_post(request.POST, b"")
        if not product_ids:
            logger.warning(
                "product_bulk_image_update without selected products | user=%s | keys=%s | product_ids=%s | product_ids_csv=%s",
                getattr(request.user, "username", "unknown"),
                list(request.POST.keys()),
                request.POST.getlist("product_ids"),
                request.POST.get("product_ids_csv", ""),
            )
            messages.warning(request, 'No se seleccionaron productos.')
            return _redirect_admin_product_list_with_filters(request)
        products_to_update = Product.objects.filter(id__in=product_ids)

    # get_product_queryset() includes select_related/prefetch_related for list rendering;
    # reset those joins here to safely use deferred fields in bulk updates.
    products_to_update = products_to_update.select_related(None).prefetch_related(None)

    if only_missing and image_mode == 'set':
        products_to_update = products_to_update.filter(Q(image__isnull=True) | Q(image=''))

    if image_mode == 'set':
        uploaded_file = request.FILES.get('image_file')
        try:
            shared_image_name = _store_bulk_product_image(uploaded_file)
        except ValueError as exc:
            messages.error(request, str(exc))
            return _redirect_admin_product_list_with_filters(request)
        except Exception:
            messages.error(request, 'No se pudo almacenar la imagen. Intenta nuevamente.')
            return _redirect_admin_product_list_with_filters(request)

        # Bulk update to avoid worker timeouts when updating thousands of products.
        updated_count = (
            products_to_update
            .exclude(image=shared_image_name)
            .update(image=shared_image_name, updated_at=timezone.now())
        )

        if updated_count == 0:
            _delete_orphan_product_image(shared_image_name)
            messages.info(request, 'No hubo cambios: todos los productos ya tenian esa imagen.')
            return _redirect_admin_product_list_with_filters(request)

        messages.success(request, f'Imagen aplicada a {updated_count} productos.')
        log_admin_action(
            request,
            action='product_bulk_image_update',
            target_type='product_bulk',
            details={
                'count': updated_count,
                'mode': image_mode,
                'select_all_pages': select_all_pages,
                'only_missing': only_missing,
                'image': shared_image_name,
            },
        )
        return _redirect_admin_product_list_with_filters(request)

    # image_mode == "clear"
    updated_count = (
        products_to_update
        .exclude(Q(image__isnull=True) | Q(image=''))
        .update(image='', updated_at=timezone.now())
    )

    if updated_count:
        messages.success(request, f'Se quitaron imagenes en {updated_count} productos.')
    else:
        messages.info(request, 'No habia imagenes para quitar.')
    log_admin_action(
        request,
        action='product_bulk_image_update',
        target_type='product_bulk',
        details={
            'count': updated_count,
            'mode': image_mode,
            'select_all_pages': select_all_pages,
        },
    )
    return _redirect_admin_product_list_with_filters(request)


# ===================== SUPPLIERS =====================

@staff_member_required
def supplier_list(request):
    """Supplier directory with KPI summary."""
    search = sanitize_search_token(request.GET.get('q', ''))
    only_active = request.GET.get('only_active') == '1'

    suppliers_qs = Supplier.objects.all()

    if only_active:
        suppliers_qs = suppliers_qs.filter(is_active=True)
    suppliers_qs, search = apply_admin_text_search(
        suppliers_qs,
        search,
        ["name", "normalized_name", "slug"],
    )
    suppliers_qs = suppliers_qs.annotate(
        products_count=Count('products', distinct=True),
        active_products_count=Count('products', filter=Q(products__is_active=True), distinct=True),
        stock_total=Sum('products__stock'),
    ).order_by('name')

    uncategorized_products_count = Product.objects.filter(
        Q(supplier='') | Q(supplier__isnull=True) | Q(supplier_ref__isnull=True)
    ).count()

    paginator = Paginator(suppliers_qs, 40)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        'admin_panel/suppliers/list.html',
        {
            'page_obj': page_obj,
            'search': search,
            'only_active': only_active,
            'total_suppliers': suppliers_qs.count(),
            'uncategorized_products_count': uncategorized_products_count,
        },
    )


def build_supplier_products_queryset(supplier, req_get):
    """
    Shared filter logic for supplier detail/export/actions.
    """
    products = Product.objects.select_related('category', 'supplier_ref').prefetch_related('categories').filter(
        supplier_ref=supplier
    )

    products, search = apply_admin_text_search(
        products,
        req_get.get('q', ''),
        ["sku", "name", "description", "supplier", "supplier_ref__name"],
    )

    active_filter = req_get.get('active', '').strip()
    if active_filter == '1':
        products = products.filter(is_active=True)
    elif active_filter == '0':
        products = products.filter(is_active=False)

    return products.order_by('name'), search, active_filter


@staff_member_required
def supplier_detail(request, supplier_id):
    """List products belonging to one supplier."""
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    products, search, active_filter = build_supplier_products_queryset(supplier, request.GET)

    paginator = Paginator(products, 40)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    enrich_products_with_category_state(page_obj.object_list)

    metrics = products.aggregate(
        stock_total=Sum('stock'),
        avg_price=Avg('price'),
    )
    audit_logs = AdminAuditLog.objects.filter(
        target_type='supplier',
        target_id=str(supplier.pk),
    ).select_related('user').order_by('-created_at')[:12]

    return render(
        request,
        'admin_panel/suppliers/detail.html',
        {
            'supplier': supplier,
            'page_obj': page_obj,
            'search': search,
            'active_filter': active_filter,
            'total_products': products.count(),
            'stock_total': metrics.get('stock_total') or 0,
            'avg_price': metrics.get('avg_price') or Decimal('0.00'),
            'audit_logs': audit_logs,
        },
    )


@staff_member_required
@superuser_required_for_modifications
@require_POST
def supplier_bulk_action(request, supplier_id):
    """
    Apply bulk operations to products of one supplier.
    """
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    products, _, _ = build_supplier_products_queryset(supplier, request.POST)

    action = request.POST.get('action', '').strip()
    percent_raw = request.POST.get('percent', '').strip()
    affected = 0

    if action in ('activate', 'deactivate'):
        is_active = action == 'activate'
        affected = products.update(is_active=is_active)
        messages.success(request, f'{affected} productos actualizados para {supplier.name}.')
    elif action in ('increase_pct', 'decrease_pct'):
        try:
            percent = Decimal(percent_raw)
            if percent <= 0:
                raise InvalidOperation()
        except Exception:
            messages.error(request, 'Porcentaje invalido. Debe ser mayor que cero.')
            return redirect(f"{reverse('admin_supplier_detail', args=[supplier.pk])}?q={request.POST.get('q', '')}&active={request.POST.get('active', '')}")

        factor = (Decimal('1') + (percent / Decimal('100'))) if action == 'increase_pct' else (
            Decimal('1') - (percent / Decimal('100'))
        )
        if factor <= 0:
            messages.error(request, 'El porcentaje deja los precios en cero o negativo.')
            return redirect(f"{reverse('admin_supplier_detail', args=[supplier.pk])}?q={request.POST.get('q', '')}&active={request.POST.get('active', '')}")

        to_update = []
        for product in products:
            product.price = (product.price * factor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            to_update.append(product)

        if to_update:
            Product.objects.bulk_update(to_update, ['price'], batch_size=500)
        affected = len(to_update)
        messages.success(request, f'Precios actualizados en {affected} productos de {supplier.name}.')
    else:
        messages.warning(request, 'Accion invalida.')
        return redirect(f"{reverse('admin_supplier_detail', args=[supplier.pk])}?q={request.POST.get('q', '')}&active={request.POST.get('active', '')}")

    log_admin_action(
        request,
        action='supplier_bulk_action',
        target_type='supplier',
        target_id=supplier.pk,
        details={
            'supplier': supplier.name,
            'action': action,
            'affected': affected,
            'percent': percent_raw or None,
            'filters': {
                'q': request.POST.get('q', ''),
                'active': request.POST.get('active', ''),
            },
        },
    )

    return redirect(f"{reverse('admin_supplier_detail', args=[supplier.pk])}?q={request.POST.get('q', '')}&active={request.POST.get('active', '')}")


@staff_member_required
def supplier_export(request, supplier_id):
    """
    Export supplier products to CSV/XLSX.
    """
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    export_format = request.GET.get('format', 'xlsx').strip().lower()
    products, _, _ = build_supplier_products_queryset(supplier, request.GET)

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="proveedor_{supplier.slug}.csv"'
        writer = csv.writer(response)
        writer.writerow(['SKU', 'Nombre', 'Proveedor', 'Precio', 'Stock', 'Descripcion'])
        for product in products:
            writer.writerow([
                product.sku,
                product.name,
                supplier.name,
                str(product.price),
                product.stock,
                product.description or '',
            ])
        log_admin_action(
            request,
            action='supplier_export',
            target_type='supplier',
            target_id=supplier.pk,
            details={'format': 'csv', 'rows': products.count()},
        )
        return response

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Productos'
    sheet.append(['SKU', 'Nombre', 'Proveedor', 'Precio', 'Stock', 'Descripcion'])
    for product in products:
        sheet.append([
            product.sku,
            product.name,
            supplier.name,
            float(product.price),
            product.stock,
            product.description or '',
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="proveedor_{supplier.slug}.xlsx"'
    workbook.save(response)

    log_admin_action(
        request,
        action='supplier_export',
        target_type='supplier',
        target_id=supplier.pk,
        details={'format': 'xlsx', 'rows': products.count()},
    )
    return response


@staff_member_required
def supplier_print(request, supplier_id):
    """
    Print-friendly supplier report (can be saved as PDF from browser).
    """
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    products, _, _ = build_supplier_products_queryset(supplier, request.GET)
    metrics = products.aggregate(stock_total=Sum('stock'))
    log_admin_action(
        request,
        action='supplier_export_print',
        target_type='supplier',
        target_id=supplier.pk,
        details={'rows': products.count()},
    )
    return render(
        request,
        'admin_panel/suppliers/print.html',
        {
            'supplier': supplier,
            'products': products,
            'stock_total': metrics.get('stock_total') or 0,
            'generated_at': timezone.now(),
        },
    )


# ===================== CATALOG EXCEL EXPORT =====================

def _export_template_detail_url(template_id):
    return reverse("admin_catalog_excel_template_detail", args=[template_id])


CATALOG_EXCEL_TEMPLATE_SNAPSHOT_FIELDS = [
    "name",
    "slug",
    "description",
    "is_active",
    "is_client_download_enabled",
    "client_download_label",
    "updated_by_id",
]
CATALOG_EXCEL_SHEET_SNAPSHOT_FIELDS = [
    "name",
    "order",
    "include_header",
    "only_active_products",
    "only_catalog_visible",
    "include_descendant_categories",
    "search_query",
    "max_rows",
    "sort_by",
]
CATALOG_EXCEL_COLUMN_SNAPSHOT_FIELDS = [
    "key",
    "header",
    "order",
    "is_active",
]
CATALOG_EXCEL_AUTO_DEFAULT_COLUMNS = [
    ("sku", "SKU"),
    ("name", "Articulo"),
    ("price", "Precio"),
]


def _build_excel_sheet_base_name(value):
    cleaned = re.sub(r"[\[\]\*\:/\\\?]", " ", str(value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:31] or "Hoja")


def _build_unique_excel_sheet_name(base_name, used_names):
    if base_name not in used_names:
        return base_name

    counter = 2
    while True:
        suffix = f" ({counter})"
        trimmed = base_name[: max(1, 31 - len(suffix))]
        candidate = f"{trimmed}{suffix}"
        if candidate not in used_names:
            return candidate
        counter += 1


def _resolve_auto_columns_from_template(template):
    first_sheet = (
        template.sheets.prefetch_related("columns")
        .order_by("order", "id")
        .first()
    )
    if first_sheet:
        current_columns = list(
            first_sheet.columns.filter(is_active=True).order_by("order", "id")
        )
        if current_columns:
            return [
                (column.key, column.header or "")
                for column in current_columns
            ]
    return CATALOG_EXCEL_AUTO_DEFAULT_COLUMNS


def _replace_sheet_columns(sheet, columns_spec):
    sheet.columns.all().delete()
    for idx, (key, header) in enumerate(columns_spec):
        CatalogExcelTemplateColumn.objects.create(
            sheet=sheet,
            key=key,
            header=header or "",
            order=idx,
            is_active=True,
        )


@staff_member_required
def catalog_excel_template_list(request):
    search = sanitize_search_token(request.GET.get("q", ""))
    only_active = request.GET.get("only_active") == "1"

    templates_qs = CatalogExcelTemplate.objects.all().order_by("name")
    templates_qs, search = apply_admin_text_search(
        templates_qs,
        search,
        ["name", "description", "slug"],
    )
    if only_active:
        templates_qs = templates_qs.filter(is_active=True)

    templates = list(
        templates_qs.prefetch_related("sheets__columns")
    )
    for template in templates:
        template.sheet_count = len(template.sheets.all())
        template.column_count = sum(
            sheet.columns.filter(is_active=True).count()
            for sheet in template.sheets.all()
        )

    return render(
        request,
        "admin_panel/exports/templates_list.html",
        {
            "templates": templates,
            "search": search,
            "only_active": only_active,
        },
    )


@staff_member_required
def catalog_excel_template_detail(request, template_id):
    template = get_object_or_404(
        CatalogExcelTemplate.objects.prefetch_related(
            "sheets__columns",
            "sheets__categories",
            "sheets__suppliers",
        ),
        pk=template_id,
    )
    sheets = list(template.sheets.all().order_by("order", "id"))
    for sheet in sheets:
        sheet.active_columns = list(sheet.columns.filter(is_active=True).order_by("order", "id"))

    return render(
        request,
        "admin_panel/exports/template_detail.html",
        {
            "template": template,
            "sheets": sheets,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_template_create(request):
    if request.method == "POST":
        form = CatalogExcelTemplateForm(request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            template.created_by = request.user
            template.updated_by = request.user
            template.save()
            log_admin_action(
                request,
                action="catalog_excel_template_create",
                target_type="catalog_excel_template",
                target_id=template.pk,
                details={"name": template.name},
            )
            messages.success(request, "Plantilla creada correctamente.")
            return redirect(_export_template_detail_url(template.pk))
    else:
        form = CatalogExcelTemplateForm()

    return render(
        request,
        "admin_panel/exports/template_form.html",
        {
            "form": form,
            "action": "Nueva",
            "template_obj": None,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_template_edit(request, template_id):
    template = get_object_or_404(CatalogExcelTemplate, pk=template_id)
    before = model_snapshot(template, CATALOG_EXCEL_TEMPLATE_SNAPSHOT_FIELDS)

    if request.method == "POST":
        form = CatalogExcelTemplateForm(request.POST, instance=template)
        if form.is_valid():
            template = form.save(commit=False)
            template.updated_by = request.user
            template.save()
            after = model_snapshot(template, CATALOG_EXCEL_TEMPLATE_SNAPSHOT_FIELDS)
            log_admin_change(
                request=request,
                action="catalog_excel_template_edit",
                target_type="catalog_excel_template",
                target_id=template.pk,
                before=before,
                after=after,
            )
            messages.success(request, "Plantilla actualizada.")
            return redirect(_export_template_detail_url(template.pk))
    else:
        form = CatalogExcelTemplateForm(instance=template)

    return render(
        request,
        "admin_panel/exports/template_form.html",
        {
            "form": form,
            "action": "Editar",
            "template_obj": template,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_template_delete(request, template_id):
    template = get_object_or_404(CatalogExcelTemplate, pk=template_id)
    if request.method == "POST":
        deleted_id = template.pk
        deleted_name = template.name
        template.delete()
        log_admin_action(
            request,
            action="catalog_excel_template_delete",
            target_type="catalog_excel_template",
            target_id=deleted_id,
            details={"name": deleted_name},
        )
        messages.success(request, "Plantilla eliminada.")
        return redirect("admin_catalog_excel_template_list")

    return render(
        request,
        "admin_panel/delete_confirm.html",
        {
            "title": "Eliminar Plantilla Excel",
            "object": template.name,
            "question": "Se eliminara la plantilla con todas sus hojas y columnas:",
            "warning": "Esta accion no se puede deshacer.",
            "cancel_url": _export_template_detail_url(template.pk),
            "confirm_label": "Eliminar plantilla",
        },
    )


@staff_member_required
def catalog_excel_template_download(request, template_id):
    template = get_object_or_404(
        CatalogExcelTemplate.objects.prefetch_related("sheets__columns", "sheets__categories", "sheets__suppliers"),
        pk=template_id,
    )
    workbook, stats = build_catalog_workbook(template)
    file_name = build_export_filename(template)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    workbook.save(response)

    log_admin_action(
        request,
        action="catalog_excel_template_download",
        target_type="catalog_excel_template",
        target_id=template.pk,
        details={
            "name": template.name,
            "file_name": file_name,
            "total_rows": stats.get("total_rows", 0),
            "rows_by_sheet": stats.get("rows_by_sheet", {}),
        },
    )
    return response


@staff_member_required
@superuser_required_for_modifications
@require_POST
def catalog_excel_template_autogenerate_main_category_sheets(request, template_id):
    template = get_object_or_404(
        CatalogExcelTemplate.objects.prefetch_related("sheets__columns", "sheets__categories"),
        pk=template_id,
    )

    include_inactive_categories = str(
        request.POST.get("include_inactive_categories", "")
    ).strip().lower() in {"1", "true", "on", "yes"}

    root_categories_qs = Category.objects.filter(parent__isnull=True)
    if not include_inactive_categories:
        root_categories_qs = root_categories_qs.filter(is_active=True)

    root_categories = list(root_categories_qs.order_by("order", "name", "id"))
    if not root_categories:
        if include_inactive_categories:
            messages.warning(request, "No hay categorias principales para generar hojas.")
        else:
            messages.warning(request, "No hay categorias principales activas para generar hojas.")
        return redirect(_export_template_detail_url(template.pk))

    base_columns = _resolve_auto_columns_from_template(template)
    existing_sheets = {
        sheet.name: sheet
        for sheet in template.sheets.all()
    }
    used_names = set(existing_sheets.keys())
    created_count = 0
    updated_count = 0

    with transaction.atomic():
        for position, category in enumerate(root_categories, start=1):
            base_name = _build_excel_sheet_base_name(category.name)
            target_sheet = existing_sheets.get(base_name)

            if target_sheet is None:
                unique_name = _build_unique_excel_sheet_name(base_name, used_names)
                target_sheet = CatalogExcelTemplateSheet.objects.create(
                    template=template,
                    name=unique_name,
                    order=position,
                    include_header=True,
                    only_active_products=True,
                    only_catalog_visible=not include_inactive_categories,
                    include_descendant_categories=True,
                    search_query="",
                    max_rows=None,
                    sort_by="name_asc",
                )
                existing_sheets[unique_name] = target_sheet
                used_names.add(unique_name)
                created_count += 1
            else:
                target_sheet.order = position
                target_sheet.include_header = True
                target_sheet.only_active_products = True
                target_sheet.only_catalog_visible = not include_inactive_categories
                target_sheet.include_descendant_categories = True
                target_sheet.search_query = ""
                target_sheet.max_rows = None
                target_sheet.sort_by = "name_asc"
                target_sheet.save(
                    update_fields=[
                        "order",
                        "include_header",
                        "only_active_products",
                        "only_catalog_visible",
                        "include_descendant_categories",
                        "search_query",
                        "max_rows",
                        "sort_by",
                        "updated_at",
                    ]
                )
                updated_count += 1

            target_sheet.categories.set([category])
            target_sheet.suppliers.clear()
            _replace_sheet_columns(target_sheet, base_columns)

    log_admin_action(
        request,
        action="catalog_excel_template_autogenerate_main_category_sheets",
        target_type="catalog_excel_template",
        target_id=template.pk,
        details={
            "created_count": created_count,
            "updated_count": updated_count,
            "roots_count": len(root_categories),
            "include_inactive_categories": include_inactive_categories,
            "base_columns": [key for key, _ in base_columns],
        },
    )
    messages.success(
        request,
        (
            f"Hojas generadas por categorias principales ({'activas e inactivas' if include_inactive_categories else 'solo activas'}): "
            f"{created_count} nuevas, {updated_count} actualizadas."
        ),
    )
    return redirect(_export_template_detail_url(template.pk))


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_sheet_create(request, template_id):
    template = get_object_or_404(CatalogExcelTemplate, pk=template_id)
    if request.method == "POST":
        form = CatalogExcelTemplateSheetForm(request.POST)
        if form.is_valid():
            sheet = form.save(commit=False)
            sheet.template = template
            sheet.save()
            form.save_m2m()
            if sheet.columns.count() == 0:
                for idx, (key, header) in enumerate(CATALOG_EXCEL_AUTO_DEFAULT_COLUMNS):
                    CatalogExcelTemplateColumn.objects.create(
                        sheet=sheet,
                        key=key,
                        header=header,
                        order=idx,
                        is_active=True,
                    )
            log_admin_action(
                request,
                action="catalog_excel_sheet_create",
                target_type="catalog_excel_template_sheet",
                target_id=sheet.pk,
                details={"template_id": template.pk, "sheet_name": sheet.name},
            )
            messages.success(request, "Hoja creada.")
            return redirect(_export_template_detail_url(template.pk))
    else:
        form = CatalogExcelTemplateSheetForm()

    return render(
        request,
        "admin_panel/exports/sheet_form.html",
        {
            "form": form,
            "action": "Nueva",
            "template": template,
            "sheet_obj": None,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_sheet_edit(request, sheet_id):
    sheet = get_object_or_404(
        CatalogExcelTemplateSheet.objects.select_related("template"), pk=sheet_id
    )
    before = model_snapshot(sheet, CATALOG_EXCEL_SHEET_SNAPSHOT_FIELDS)
    before_extra = {
        "categories": list(sheet.categories.values_list("id", flat=True)),
        "suppliers": list(sheet.suppliers.values_list("id", flat=True)),
    }
    if request.method == "POST":
        form = CatalogExcelTemplateSheetForm(request.POST, instance=sheet)
        if form.is_valid():
            sheet = form.save()
            after = model_snapshot(sheet, CATALOG_EXCEL_SHEET_SNAPSHOT_FIELDS)
            after_extra = {
                "categories": list(sheet.categories.values_list("id", flat=True)),
                "suppliers": list(sheet.suppliers.values_list("id", flat=True)),
            }
            log_admin_change(
                request=request,
                action="catalog_excel_sheet_edit",
                target_type="catalog_excel_template_sheet",
                target_id=sheet.pk,
                before=before,
                after=after,
                extra={
                    "before_relations": before_extra,
                    "after_relations": after_extra,
                },
            )
            messages.success(request, "Hoja actualizada.")
            return redirect(_export_template_detail_url(sheet.template_id))
    else:
        form = CatalogExcelTemplateSheetForm(instance=sheet)

    return render(
        request,
        "admin_panel/exports/sheet_form.html",
        {
            "form": form,
            "action": "Editar",
            "template": sheet.template,
            "sheet_obj": sheet,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_sheet_delete(request, sheet_id):
    sheet = get_object_or_404(
        CatalogExcelTemplateSheet.objects.select_related("template"), pk=sheet_id
    )
    template_id = sheet.template_id
    if request.method == "POST":
        sheet_name = sheet.name
        deleted_id = sheet.pk
        sheet.delete()
        log_admin_action(
            request,
            action="catalog_excel_sheet_delete",
            target_type="catalog_excel_template_sheet",
            target_id=deleted_id,
            details={"template_id": template_id, "sheet_name": sheet_name},
        )
        messages.success(request, "Hoja eliminada.")
        return redirect(_export_template_detail_url(template_id))

    return render(
        request,
        "admin_panel/delete_confirm.html",
        {
            "title": "Eliminar Hoja",
            "object": sheet.name,
            "question": "Se eliminara esta hoja con todas sus columnas:",
            "warning": "Esta accion no se puede deshacer.",
            "cancel_url": _export_template_detail_url(template_id),
            "confirm_label": "Eliminar hoja",
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_column_create(request, sheet_id):
    sheet = get_object_or_404(
        CatalogExcelTemplateSheet.objects.select_related("template"), pk=sheet_id
    )
    if request.method == "POST":
        form = CatalogExcelTemplateColumnForm(request.POST)
        if form.is_valid():
            column = form.save(commit=False)
            column.sheet = sheet
            column.save()
            log_admin_action(
                request,
                action="catalog_excel_column_create",
                target_type="catalog_excel_template_column",
                target_id=column.pk,
                details={"sheet_id": sheet.pk, "key": column.key},
            )
            messages.success(request, "Columna agregada.")
            return redirect(_export_template_detail_url(sheet.template_id))
    else:
        form = CatalogExcelTemplateColumnForm()

    return render(
        request,
        "admin_panel/exports/column_form.html",
        {
            "form": form,
            "action": "Nueva",
            "template": sheet.template,
            "sheet": sheet,
            "column_obj": None,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_column_edit(request, column_id):
    column = get_object_or_404(
        CatalogExcelTemplateColumn.objects.select_related("sheet__template"), pk=column_id
    )
    before = model_snapshot(column, CATALOG_EXCEL_COLUMN_SNAPSHOT_FIELDS)
    if request.method == "POST":
        form = CatalogExcelTemplateColumnForm(request.POST, instance=column)
        if form.is_valid():
            column = form.save()
            after = model_snapshot(column, CATALOG_EXCEL_COLUMN_SNAPSHOT_FIELDS)
            log_admin_change(
                request=request,
                action="catalog_excel_column_edit",
                target_type="catalog_excel_template_column",
                target_id=column.pk,
                before=before,
                after=after,
            )
            messages.success(request, "Columna actualizada.")
            return redirect(_export_template_detail_url(column.sheet.template_id))
    else:
        form = CatalogExcelTemplateColumnForm(instance=column)

    return render(
        request,
        "admin_panel/exports/column_form.html",
        {
            "form": form,
            "action": "Editar",
            "template": column.sheet.template,
            "sheet": column.sheet,
            "column_obj": column,
        },
    )


@staff_member_required
@superuser_required_for_modifications
def catalog_excel_column_delete(request, column_id):
    column = get_object_or_404(
        CatalogExcelTemplateColumn.objects.select_related("sheet__template"), pk=column_id
    )
    template_id = column.sheet.template_id
    if request.method == "POST":
        column_key = column.key
        deleted_id = column.pk
        column.delete()
        log_admin_action(
            request,
            action="catalog_excel_column_delete",
            target_type="catalog_excel_template_column",
            target_id=deleted_id,
            details={"template_id": template_id, "key": column_key},
        )
        messages.success(request, "Columna eliminada.")
        return redirect(_export_template_detail_url(template_id))

    return render(
        request,
        "admin_panel/delete_confirm.html",
        {
            "title": "Eliminar Columna",
            "object": column.get_effective_header(),
            "question": "Se eliminara esta columna de la hoja:",
            "warning": "Esta accion no se puede deshacer.",
            "cancel_url": _export_template_detail_url(template_id),
            "confirm_label": "Eliminar columna",
        },
    )


@staff_member_required
def supplier_unassigned(request):
    """
    Products without supplier assigned.
    """
    products = Product.objects.select_related('category').prefetch_related('categories').filter(
        Q(supplier='') | Q(supplier__isnull=True) | Q(supplier_ref__isnull=True)
    ).order_by('name')

    products, search = apply_admin_text_search(
        products,
        request.GET.get('q', ''),
        ["sku", "name", "description", "supplier", "supplier_ref__name"],
    )

    paginator = Paginator(products, 40)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    enrich_products_with_category_state(page_obj.object_list)

    return render(
        request,
        'admin_panel/suppliers/unassigned.html',
        {
            'page_obj': page_obj,
            'search': search,
            'total_products': products.count(),
        },
    )


@staff_member_required
@superuser_required_for_modifications
@require_POST
def supplier_toggle_active(request, supplier_id):
    """
    Activate/deactivate supplier entity.
    """
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    supplier.is_active = request.POST.get('is_active') == '1'
    supplier.save(update_fields=['is_active', 'updated_at'])
    log_admin_action(
        request,
        action='supplier_toggle_active',
        target_type='supplier',
        target_id=supplier.pk,
        details={'supplier': supplier.name, 'is_active': supplier.is_active},
    )
    messages.success(request, f'Proveedor {supplier.name} actualizado.')
    return redirect('admin_supplier_detail', supplier_id=supplier.pk)


# ===================== PAYMENTS =====================

def _parse_payment_amount(raw_amount):
    raw = str(raw_amount or '').strip().replace(',', '.')
    if not raw:
        raise ValueError('Ingresa un monto para el pago.')
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError('Monto de pago invalido.')
    if amount <= 0:
        raise ValueError('El monto del pago debe ser mayor a 0.')
    return amount


def _parse_adjustment_amount(raw_amount):
    raw = str(raw_amount or '').strip().replace(',', '.')
    if not raw:
        raise ValueError('Ingresa un monto para el ajuste.')
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError('Monto de ajuste invalido.')
    if amount == 0:
        raise ValueError('El ajuste no puede ser 0.')
    return amount


def _parse_paid_at(raw_paid_at):
    raw = str(raw_paid_at or '').strip()
    if not raw:
        return timezone.now()

    parsed_datetime = parse_datetime(raw)
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
        return timezone.localtime(parsed_datetime, timezone.get_current_timezone())

    parsed_date = parse_date(raw)
    if parsed_date:
        combined = datetime.combine(parsed_date, time.min)
        return timezone.make_aware(combined, timezone.get_current_timezone())

    raise ValueError('Fecha/hora de pago invalida.')


@staff_member_required
def payment_list(request):
    """Payments control panel with search and order assignment."""
    companies = Company.objects.filter(is_active=True).order_by("name")
    active_company = get_admin_company_filter(request)
    selected_company_id = "all" if active_company is None else str(active_company.pk)
    if request.method == 'POST':
        action = request.POST.get('action', 'create').strip()
        company_id_raw = request.POST.get("company_id", "").strip()
        sales_document_type_id_raw = request.POST.get("sales_document_type_id", "").strip()
        company_for_action = None
        if company_id_raw and company_id_raw.isdigit():
            company_for_action = Company.objects.filter(pk=int(company_id_raw), is_active=True).first()
        if not company_for_action and active_company:
            company_for_action = active_company
        selected_sales_document_type = None
        if sales_document_type_id_raw.isdigit() and company_for_action:
            selected_sales_document_type = SalesDocumentType.objects.filter(
                pk=int(sales_document_type_id_raw),
                company=company_for_action,
                enabled=True,
                billing_mode="INTERNAL_DOCUMENT",
            ).first()

        if action == 'cancel':
            payment_id = request.POST.get('payment_id', '').strip()
            cancel_reason = request.POST.get('cancel_reason', '').strip()
            with transaction.atomic():
                payment = get_object_or_404(
                    ClientPayment.objects.select_related('order', 'client_profile').select_for_update(),
                    pk=payment_id,
                )
                if payment.is_cancelled:
                    messages.info(request, 'Ese pago ya estaba anulado.')
                    return redirect('admin_payment_list')

                before = model_snapshot(
                    payment,
                    ['is_cancelled', 'cancelled_at', 'cancel_reason', 'amount', 'order_id', 'client_profile_id'],
                )
                payment.is_cancelled = True
                payment.cancelled_at = timezone.now()
                payment.cancel_reason = cancel_reason
                payment.save(update_fields=['is_cancelled', 'cancelled_at', 'cancel_reason', 'updated_at'])
                after = model_snapshot(
                    payment,
                    ['is_cancelled', 'cancelled_at', 'cancel_reason', 'amount', 'order_id', 'client_profile_id'],
                )
            log_admin_change(
                request,
                action='payment_cancel',
                target_type='client_payment',
                target_id=payment.pk,
                before=before,
                after=after,
                extra={
                    'client': payment.client_profile.company_name,
                    'order_id': payment.order_id,
                    'amount': f'{payment.amount:.2f}',
                },
            )
            messages.success(request, 'Pago anulado correctamente.')
            return redirect('admin_payment_list')

        if action == 'adjust':
            client_id = request.POST.get('client_profile_id', '').strip()
            amount_raw = request.POST.get('amount', '').strip()
            notes = request.POST.get('notes', '').strip()

            client_profile = None
            if client_id.isdigit():
                client_profile = ClientProfile.objects.select_related('user').filter(pk=int(client_id)).first()
            if not client_profile:
                messages.error(request, 'Selecciona un cliente valido para registrar ajuste.')
                return redirect('admin_payment_list')

            try:
                amount = _parse_adjustment_amount(amount_raw)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('admin_payment_list')

            if not company_for_action:
                messages.error(request, 'Selecciona una empresa valida para registrar el ajuste.')
                return redirect('admin_payment_list')

            try:
                tx = create_adjustment_transaction(
                    client_profile=client_profile,
                    amount=amount,
                    reason=notes or 'Ajuste manual de cuenta corriente',
                    actor=request.user,
                    company=company_for_action,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('admin_payment_list')
            if selected_sales_document_type and selected_sales_document_type.document_behavior == SALES_BEHAVIOR_NOTA_DEBITO:
                ensure_document_for_adjustment(
                    tx,
                    sales_document_type=selected_sales_document_type,
                )
            log_admin_action(
                request,
                action='client_adjustment_create',
                target_type='client_transaction',
                target_id=tx.pk,
                details={
                    'client_profile_id': client_profile.pk,
                    'amount': f'{amount:.2f}',
                    'notes': notes,
                },
            )
            messages.success(request, 'Ajuste de cuenta corriente registrado.')
            return redirect('admin_payment_list')

        client_id = request.POST.get('client_profile_id', '').strip()
        order_id = request.POST.get('order_id', '').strip().replace('#', '')
        amount_raw = request.POST.get('amount', '').strip()
        method = request.POST.get('method', '').strip()
        paid_at_raw = request.POST.get('paid_at', '').strip()
        reference = request.POST.get('reference', '').strip()
        notes = request.POST.get('notes', '').strip()

        order = None
        if order_id:
            if not order_id.isdigit():
                messages.error(request, 'Pedido invalido.')
                return redirect('admin_payment_list')
            order = Order.objects.select_related('user').filter(pk=order_id).first()
            if not order:
                messages.error(request, 'El pedido indicado no existe.')
                return redirect('admin_payment_list')
            if order.company_id and company_for_action and order.company_id != company_for_action.id:
                messages.error(request, 'La empresa seleccionada no coincide con el pedido.')
                return redirect('admin_payment_list')

        client_profile = None
        if client_id.isdigit():
            client_profile = ClientProfile.objects.select_related('user').filter(pk=int(client_id)).first()
        if not client_profile and order and order.user_id:
            client_profile = ClientProfile.objects.select_related('user').filter(user_id=order.user_id).first()

        if not client_profile:
            messages.error(request, 'Selecciona un cliente valido o un pedido asociado a un cliente.')
            return redirect('admin_payment_list')

        if order and order.user_id and order.user_id != client_profile.user_id:
            messages.error(request, 'El pedido no corresponde al cliente seleccionado.')
            return redirect('admin_payment_list')

        allowed_methods = {value for value, _ in ClientPayment.METHOD_CHOICES}
        if method not in allowed_methods:
            messages.error(request, 'Medio de pago invalido.')
            return redirect('admin_payment_list')

        try:
            amount = _parse_payment_amount(amount_raw)
            paid_at = _parse_paid_at(paid_at_raw)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('admin_payment_list')

        with transaction.atomic():
            locked_order = None
            if order:
                locked_order = Order.objects.select_for_update().select_related('user').get(pk=order.pk)
                order = locked_order
                company_for_action = order.company
            if not order and not company_for_action:
                messages.error(request, 'Selecciona una empresa valida para el pago.')
                return redirect('admin_payment_list')

            try:
                payment = ClientPayment.objects.create(
                    client_profile=client_profile,
                    order=order,
                    company=company_for_action,
                    amount=amount,
                    method=method,
                    paid_at=paid_at,
                    reference=reference,
                    notes=notes,
                    created_by=request.user if request.user.is_authenticated else None,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect('admin_payment_list')
            if selected_sales_document_type and selected_sales_document_type.document_behavior == SALES_BEHAVIOR_RECIBO:
                ensure_document_for_payment(
                    payment,
                    sales_document_type=selected_sales_document_type,
                )

        log_admin_change(
            request,
            action='payment_create',
            target_type='client_payment',
            target_id=payment.pk,
            before={},
            after=model_snapshot(
                payment,
                [
                    'client_profile_id',
                    'order_id',
                    'amount',
                    'method',
                    'paid_at',
                    'reference',
                    'notes',
                    'is_cancelled',
                ],
            ),
            extra={
                'client': client_profile.company_name,
                'order_id': order.pk if order else None,
                'amount': f'{amount:.2f}',
                'method': method,
            },
        )

        if order:
            paid_amount = order.get_paid_amount()
            pending_amount = order.get_pending_amount()
            if pending_amount <= 0:
                messages.success(
                    request,
                    f'Pago registrado. Pedido #{order.pk} ya esta pago y se puede confirmar.',
                )
            else:
                messages.success(
                    request,
                    f'Pago registrado. Pedido #{order.pk}: pagado ${paid_amount:.2f}, pendiente ${pending_amount:.2f}.',
                )
        else:
            messages.success(request, 'Pago registrado correctamente.')

        return redirect('admin_payment_list')

    q = sanitize_search_token(request.GET.get('q', ''))
    client_id = request.GET.get('client_id', '').strip()
    order_id = request.GET.get('order_id', '').strip().replace('#', '')
    sync_status = request.GET.get('sync_status', '').strip()
    suggested_action = request.GET.get('suggested_action', '').strip().lower()
    selected_sales_document_type_id = request.GET.get('sales_document_type_id', '').strip()

    if order_id.isdigit() and not client_id:
        order_for_prefill = Order.objects.select_related('user').filter(pk=order_id)
        if active_company:
            order_for_prefill = order_for_prefill.filter(company=active_company)
        order_for_prefill = order_for_prefill.first()
        if order_for_prefill and order_for_prefill.user_id:
            profile = ClientProfile.objects.filter(user_id=order_for_prefill.user_id).first()
            if profile:
                client_id = str(profile.pk)

    payments = ClientPayment.objects.select_related(
        'client_profile',
        'client_profile__user',
        'order',
        'created_by',
    ).all()
    if active_company:
        payments = payments.filter(company=active_company)

    if client_id.isdigit():
        payments = payments.filter(client_profile_id=int(client_id))
    if order_id.isdigit():
        payments = payments.filter(order_id=int(order_id))
    if sync_status:
        payments = payments.filter(sync_status=sync_status)
    if q:
        parsed_q = normalize_admin_search_query(q)
        text_filtered = apply_parsed_text_search(
            payments,
            parsed_q,
            [
                "client_profile__company_name",
                "client_profile__user__username",
                "client_profile__cuit_dni",
                "reference",
                "notes",
                "external_number",
                "external_id",
            ],
            order_by_similarity=False,
        )
        numeric_terms = set()
        if parsed_q.get("raw", "").isdigit():
            numeric_terms.add(int(parsed_q["raw"]))
        for term in [*parsed_q.get("phrases", []), *parsed_q.get("include_terms", [])]:
            if str(term).isdigit():
                numeric_terms.add(int(term))
        if numeric_terms:
            numeric_query = Q()
            for num in numeric_terms:
                numeric_query |= Q(order__id=num) | Q(id=num)
            payments = payments.filter(
                Q(pk__in=text_filtered.values("pk")) | Q(pk__in=payments.filter(numeric_query).values("pk"))
            )
        else:
            payments = text_filtered

    summary = payments.filter(is_cancelled=False).aggregate(
        total=Sum('amount'),
        count=Count('id'),
    )

    paginator = Paginator(payments.order_by('-paid_at'), 40)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    payment_docs = {}
    payment_ids = [payment.pk for payment in page_obj]
    if payment_ids:
        for doc in InternalDocument.objects.select_related('sales_document_type').filter(payment_id__in=payment_ids):
            payment_docs.setdefault(doc.payment_id, []).append(doc)
    for payment in page_obj:
        payment.internal_documents = payment_docs.get(payment.pk, [])

    clients = ClientProfile.objects.select_related('user').order_by('company_name')
    if active_company:
        clients = clients.filter(
            company_links__company=active_company,
            company_links__is_active=True,
        ).distinct()
    selected_order = None
    if order_id.isdigit():
        selected_order_qs = Order.objects.select_related('user').filter(pk=order_id)
        if active_company:
            selected_order_qs = selected_order_qs.filter(company=active_company)
        selected_order = selected_order_qs.first()
    selected_client = (
        ClientProfile.objects.select_related('user').filter(pk=client_id).first()
        if client_id.isdigit()
        else None
    )
    if not selected_client and selected_order and selected_order.user_id:
        selected_client = ClientProfile.objects.select_related('user').filter(user_id=selected_order.user_id).first()
    selected_client_id = str(selected_client.pk) if selected_client else client_id
    selected_client_metrics = None
    if selected_client:
        orders_total = selected_client.get_total_orders_for_balance(company=active_company)
        total_paid = selected_client.get_total_paid(company=active_company)
        ledger_balance = selected_client.get_current_balance(company=active_company)
        selected_client_metrics = {
            'orders_total': orders_total,
            'total_paid': total_paid,
            'current_balance': ledger_balance,
        }
    payment_sales_document_types = SalesDocumentType.objects.none()
    if active_company:
        payment_sales_document_types = SalesDocumentType.objects.filter(
            company=active_company,
            enabled=True,
            billing_mode="INTERNAL_DOCUMENT",
            document_behavior__in=[SALES_BEHAVIOR_RECIBO, SALES_BEHAVIOR_NOTA_DEBITO],
        ).order_by("display_order", "name")

    return render(request, 'admin_panel/payments/list.html', {
        'page_obj': page_obj,
        'search': q,
        'client_id': client_id,
        'selected_client_id': selected_client_id,
        'order_id': order_id,
        'sync_status': sync_status,
        'clients': clients,
        'companies': companies,
        'selected_company_id': selected_company_id,
        'active_company': active_company,
        'selected_client': selected_client,
        'selected_client_metrics': selected_client_metrics,
        'selected_order': selected_order,
        'payment_methods': ClientPayment.METHOD_CHOICES,
        'sync_status_choices': ClientPayment.SYNC_STATUS_CHOICES,
        'summary_total': summary.get('total') or Decimal('0.00'),
        'summary_count': summary.get('count') or 0,
        'payment_documents': payment_docs,
        'payment_sales_document_types': payment_sales_document_types,
        'selected_sales_document_type_id': selected_sales_document_type_id,
        'suggested_action': suggested_action,
    })


@staff_member_required
def payment_export_saas(request):
    company = get_admin_company_required(request)
    if not company:
        messages.error(request, 'Selecciona una empresa valida para exportar pagos.')
        return redirect('admin_payment_list')

    sync_status = request.GET.get('sync_status', '').strip()
    payments = (
        ClientPayment.objects.select_related('client_profile', 'order', 'company')
        .filter(company=company, is_cancelled=False)
    )
    if sync_status and sync_status != "all":
        payments = payments.filter(sync_status=sync_status)

    if not payments.exists():
        messages.info(request, 'No hay pagos para exportar con esos filtros.')
        return redirect('admin_payment_list')

    def _fmt_dt(value):
        if not value:
            return ""
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")

    file_stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
    company_slug = company.slug or slugify(company.name) or f"company{company.pk}"
    filename = f"saas_pagos_{company_slug}_{file_stamp}.csv"

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    writer.writerow([
        "payment_id",
        "paid_at",
        "company_slug",
        "company_cuit",
        "client_id",
        "client_company_name",
        "client_cuit",
        "order_id",
        "amount",
        "method",
        "reference",
        "notes",
        "origin",
        "external_system",
        "external_id",
        "external_number",
        "sync_status",
        "synced_at",
    ])

    rows_count = 0
    for payment in payments:
        writer.writerow([
            payment.pk,
            _fmt_dt(payment.paid_at),
            company_slug,
            company.cuit or "",
            payment.client_profile_id,
            payment.client_profile.company_name if payment.client_profile_id else "",
            payment.client_profile.cuit_dni if payment.client_profile_id else "",
            payment.order_id or "",
            f"{payment.amount:.2f}",
            payment.method,
            payment.reference,
            payment.notes,
            payment.origin,
            payment.external_system,
            payment.external_id,
            payment.external_number,
            payment.sync_status,
            _fmt_dt(payment.synced_at),
        ])
        rows_count += 1

    log_admin_action(
        request,
        action="payment_export_saas",
        target_type="client_payment",
        target_id=0,
        details={
            "company_id": company.pk,
            "rows": rows_count,
            "sync_status": sync_status or "",
        },
    )
    return response


# ===================== CLAMP QUOTER =====================

def _format_currency_ars(value):
    decimal_value = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    formatted = f"{decimal_value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _build_clamp_quote_download_response(quote):
    """
    Build a PDF-like downloadable quote voucher.
    Falls back to plain text if reportlab is unavailable.
    """
    filename = f"cotizacion_{quote.quote_number.lower()}.pdf"
    company_name = SiteSettings.get_settings().company_name or "FLEXS"
    client_label = quote.client_name or "Cliente no especificado"
    closed_label = quote.closed_at.strftime("%d/%m/%Y %H:%M") if quote.closed_at else "-"
    created_label = quote.created_at.strftime("%d/%m/%Y %H:%M")
    clamp_type_label = dict(ClampQuotation.CLAMP_TYPE_CHOICES).get(quote.clamp_type, quote.clamp_type)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except Exception:
        payload = (
            f"{company_name}\n"
            f"COTIZACION {quote.quote_number}\n"
            f"Fecha: {created_label}\n"
            f"Cliente: {client_label}\n"
            f"Estado: {quote.get_status_display()}\n"
            f"Cerrada: {closed_label}\n"
            "\n"
            f"Descripcion: {quote.description}\n"
            f"Tipo abrazadera: {clamp_type_label}\n"
            f"Medida: {quote.diameter} x {quote.width_mm} x {quote.length_mm} {quote.profile_type}\n"
            f"Cantidad: {quote.quantity}\n"
            f"Precio unitario: ${_format_currency_ars(quote.final_price)}\n"
            f"Total cotizado: ${_format_currency_ars(quote.total_price)}\n"
        )
        response = HttpResponse(payload, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="cotizacion_{quote.quote_number.lower()}.txt"'
        )
        return response

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4
    left = 20 * mm
    right = page_w - (20 * mm)
    y = page_h - (20 * mm)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, y, company_name)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(left, y - (8 * mm), "COMPROBANTE DE COTIZACION")
    c.setFont("Helvetica", 10)
    c.drawRightString(right, y, f"Nro: {quote.quote_number}")
    c.drawRightString(right, y - (6 * mm), f"Fecha: {created_label}")
    c.drawRightString(right, y - (12 * mm), f"Estado: {quote.get_status_display()}")

    y -= 24 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Cliente")
    c.setFont("Helvetica", 10)
    c.drawString(left, y - (5 * mm), client_label)
    c.drawString(left, y - (10 * mm), f"Cierre: {closed_label}")

    y -= 20 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Detalle de cotizacion")
    c.setFont("Helvetica", 10)
    c.drawString(left, y - (6 * mm), quote.description)
    c.drawString(
        left,
        y - (12 * mm),
        f"Tipo: {clamp_type_label} | Medida: {quote.diameter} x {quote.width_mm} x {quote.length_mm} {quote.profile_type}",
    )
    c.drawString(
        left,
        y - (18 * mm),
        f"Base: ${_format_currency_ars(quote.base_cost)} | Lista: {quote.get_price_list_display()}",
    )

    y -= 32 * mm
    c.line(left, y, right, y)
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Cantidad")
    c.drawString(left + (45 * mm), y, "Precio unitario")
    c.drawString(left + (95 * mm), y, "Total")
    y -= 4 * mm
    c.line(left, y, right, y)
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.drawString(left, y, str(quote.quantity))
    c.drawString(left + (45 * mm), y, f"${_format_currency_ars(quote.final_price)}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left + (95 * mm), y, f"${_format_currency_ars(quote.total_price)}")
    y -= 8 * mm
    c.line(left, y, right, y)

    y -= 14 * mm
    c.setFont("Helvetica", 9)
    c.drawString(left, y, "Documento informativo. No fiscal.")
    if quote.closed_note:
        y -= 6 * mm
        c.drawString(left, y, f"Nota de cierre: {quote.closed_note[:120]}")

    c.showPage()
    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_member_required
def clamp_quoter(request):
    """Mini cotizador de abrazaderas."""
    default_form = {
        "client_name": "",
        "dollar_rate": "1450",
        "dollar_mode": "manual",
        "steel_price_usd": "1.45",
        "supplier_discount_pct": "0",
        "general_increase_pct": "40",
        "clamp_type": "trefilada",
        "is_zincated": False,
        "diameter": "7/16",
        "width_mm": "",
        "length_mm": "",
        "quote_quantity": "1",
        "profile_type": "PLANA",
    }

    form_values = default_form.copy()
    if request.method == "POST":
        form_values.update({
            "client_name": str(request.POST.get("client_name", "")).strip(),
            "dollar_rate": str(request.POST.get("dollar_rate", "1450")).strip(),
            "dollar_mode": str(request.POST.get("dollar_mode", "manual")).strip().lower(),
            "steel_price_usd": str(request.POST.get("steel_price_usd", "1.45")).strip(),
            "supplier_discount_pct": str(request.POST.get("supplier_discount_pct", "0")).strip(),
            "general_increase_pct": str(request.POST.get("general_increase_pct", "40")).strip(),
            "clamp_type": str(request.POST.get("clamp_type", "trefilada")).strip().lower(),
            "is_zincated": str(request.POST.get("is_zincated", "")).strip().lower() in {"1", "true", "on", "yes"},
            "diameter": str(request.POST.get("diameter", "7/16")).strip(),
            "width_mm": str(request.POST.get("width_mm", "")).strip(),
            "length_mm": str(request.POST.get("length_mm", "")).strip(),
            "quote_quantity": str(request.POST.get("quote_quantity", "1")).strip() or "1",
            "profile_type": str(request.POST.get("profile_type", "PLANA")).strip().upper(),
        })
        if (
            form_values["clamp_type"] == "laminada"
            and form_values["diameter"] not in CLAMP_LAMINATED_ALLOWED_DIAMETERS
        ):
            form_values["diameter"] = CLAMP_LAMINATED_ALLOWED_DIAMETERS[0]

        action = str(request.POST.get("action", "save_quote")).strip().lower()
        if action == "save_quote":
            try:
                result = calculate_clamp_quote(request.POST)
                selected_key = str(request.POST.get("price_list_key", "")).strip()
                selected_map = {row["key"]: row for row in result["price_rows"]}
                selected_price = selected_map.get(selected_key)
                if not selected_price:
                    raise ValueError("Selecciona una lista valida para guardar.")
                quote_quantity = parse_int_value(
                    request.POST.get("quote_quantity", "1"),
                    "Cantidad cotizada",
                    min_value=1,
                )
                saved_quote = ClampQuotation.objects.create(
                    client_name=result["inputs"]["client_name"],
                    dollar_rate=result["inputs"]["dollar_rate"],
                    steel_price_usd=result["inputs"]["steel_price_usd"],
                    supplier_discount_pct=result["inputs"]["supplier_discount_pct"],
                    general_increase_pct=result["inputs"]["general_increase_pct"],
                    clamp_type=result["inputs"]["clamp_type"],
                    is_zincated=result["inputs"]["is_zincated"],
                    diameter=result["inputs"]["diameter"],
                    width_mm=result["inputs"]["width_mm"],
                    length_mm=result["inputs"]["length_mm"],
                    profile_type=result["inputs"]["profile_type"],
                    description=result["description"],
                    base_cost=result["base_cost"],
                    price_list=selected_price["key"],
                    final_price=selected_price["final_price"],
                    quantity=quote_quantity,
                    created_by=request.user if request.user.is_authenticated else None,
                )
                log_admin_action(
                    request,
                    action="clamp_quote_saved",
                    target_type="clamp_quotation",
                    target_id=saved_quote.pk,
                    details={
                        "price_list": selected_price["label"],
                        "final_price": f"{selected_price['final_price']:.2f}",
                        "quantity": quote_quantity,
                        "total_price": f"{saved_quote.total_price:.2f}",
                        "description": result["description"],
                        "generated_code": result.get("generated_code", ""),
                        "client_name": result["inputs"]["client_name"],
                    },
                )
                messages.success(
                    request,
                    f"Cotizacion guardada en {selected_price['label']} por ${_format_currency_ars(saved_quote.total_price)}.",
                )
                return redirect("admin_clamp_quoter")
            except ValueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Este modulo ahora solo permite generar y guardar cotizaciones.")

    saved_quotes = list(ClampQuotation.objects.select_related("created_by").all()[:30])
    for quote in saved_quotes:
        quote.calculated_weight_kg = None
        quote.calculated_development_meters = None
        try:
            preview = calculate_clamp_quote(
                {
                    "client_name": quote.client_name,
                    "dollar_rate": quote.dollar_rate,
                    "steel_price_usd": quote.steel_price_usd,
                    "supplier_discount_pct": quote.supplier_discount_pct,
                    "general_increase_pct": quote.general_increase_pct,
                    "clamp_type": quote.clamp_type,
                    "is_zincated": quote.is_zincated,
                    "diameter": quote.diameter,
                    "width_mm": quote.width_mm,
                    "length_mm": quote.length_mm,
                    "profile_type": quote.profile_type,
                }
            )
            quote.calculated_weight_kg = preview.get("total_weight_kg")
            quote.calculated_development_meters = preview.get("development_meters")
        except ValueError:
            # Keep legacy rows visible even if inputs are no longer valid.
            pass

    weight_map_json = json.dumps({key: float(value) for key, value in CLAMP_WEIGHT_MAP.items()})
    diameter_code_map_json = json.dumps(DIAMETER_HUMAN_TO_COMPACT_DEFAULT)
    all_diameter_options_json = json.dumps(get_allowed_diameter_options())
    laminated_diameter_options_json = json.dumps(list(CLAMP_LAMINATED_ALLOWED_DIAMETERS))
    price_lists_json = json.dumps([
        {"key": key, "label": label, "multiplier": float(multiplier)}
        for key, label, multiplier in CLAMP_PRICE_LISTS
    ])

    return render(request, "admin_panel/tools/clamp_quoter.html", {
        "form_values": form_values,
        "diameter_options": get_allowed_diameter_options(form_values.get("clamp_type")),
        "profile_options": ["PLANA", "SEMICURVA", "CURVA"],
        "price_lists": CLAMP_PRICE_LISTS,
        "weight_map_json": weight_map_json,
        "diameter_code_map_json": diameter_code_map_json,
        "all_diameter_options_json": all_diameter_options_json,
        "laminated_diameter_options_json": laminated_diameter_options_json,
        "price_lists_json": price_lists_json,
        "saved_quotes": saved_quotes,
    })


@staff_member_required
@require_POST
def clamp_quote_close(request, quote_id):
    """Close a saved quote to lock it and allow voucher download."""
    quote = get_object_or_404(ClampQuotation, pk=quote_id)
    close_note = str(request.POST.get("close_note", "")).strip()
    changed = quote.close(actor=request.user, note=close_note)

    log_admin_action(
        request,
        action="clamp_quote_close",
        target_type="clamp_quotation",
        target_id=quote.pk,
        details={
            "changed": changed,
            "status": quote.status,
            "closed_note": quote.closed_note,
        },
    )

    if changed:
        messages.success(request, f"Cotizacion {quote.quote_number} cerrada. Ya podes descargar el comprobante.")
    else:
        messages.info(request, f"La cotizacion {quote.quote_number} ya estaba cerrada.")
    return redirect("admin_clamp_quoter")


@staff_member_required
def clamp_quote_download(request, quote_id):
    """Download a closed quote voucher."""
    quote = get_object_or_404(
        ClampQuotation.objects.select_related("created_by", "closed_by"),
        pk=quote_id,
    )
    if quote.status != ClampQuotation.STATUS_CLOSED:
        messages.warning(request, "Primero cerra la cotizacion para descargar el comprobante.")
        return redirect("admin_clamp_quoter")

    log_admin_action(
        request,
        action="clamp_quote_download",
        target_type="clamp_quotation",
        target_id=quote.pk,
        details={"quote_number": quote.quote_number},
    )
    return _build_clamp_quote_download_response(quote)


def _find_admin_clamp_request_matches(clamp_request, limit=20):
    """Find products with same technical clamp dimensions for admin review."""
    queryset = (
        Product.objects.select_related("category")
        .prefetch_related("categories")
        .filter(
            clamp_specs__fabrication=clamp_request.clamp_type.upper(),
            clamp_specs__diameter=clamp_request.diameter,
            clamp_specs__width=clamp_request.width_mm,
            clamp_specs__length=clamp_request.length_mm,
            clamp_specs__shape=clamp_request.profile_type,
        )
        .distinct()
        .order_by("name")
    )
    return list(queryset[:limit])


@staff_member_required
def clamp_request_list(request):
    """Admin queue for client custom clamp requests."""
    status_filter = str(request.GET.get("status", ClampMeasureRequest.STATUS_PENDING)).strip().lower()
    search = sanitize_search_token(request.GET.get("q", ""))

    queryset = ClampMeasureRequest.objects.select_related("client_user", "processed_by")

    valid_statuses = {value for value, _ in ClampMeasureRequest.STATUS_CHOICES}
    if status_filter in valid_statuses:
        queryset = queryset.filter(status=status_filter)
    elif status_filter == "all":
        pass
    else:
        status_filter = "all"

    if search:
        queryset = apply_parsed_text_search(
            queryset,
            normalize_admin_search_query(search),
            [
                "client_name",
                "client_email",
                "client_phone",
                "description",
                "generated_code",
            ],
            order_by_similarity=False,
        )

    status_summary = (
        ClampMeasureRequest.objects.values("status")
        .annotate(total=Count("id"))
        .order_by("status")
    )
    summary_map = {row["status"]: row["total"] for row in status_summary}

    paginator = Paginator(queryset, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "admin_panel/clamp_requests/list.html",
        {
            "page_obj": page_obj,
            "search": search,
            "status_filter": status_filter,
            "status_choices": ClampMeasureRequest.STATUS_CHOICES,
            "summary_map": summary_map,
        },
    )


@staff_member_required
def clamp_request_detail(request, pk):
    """Detail and workflow actions for one custom clamp request."""
    clamp_request = get_object_or_404(
        ClampMeasureRequest.objects.select_related("client_user", "processed_by"),
        pk=pk,
    )

    def _build_quote_preview(
        *,
        clamp_type,
        is_zincated,
        diameter,
        width_mm,
        length_mm,
        profile_type,
        dollar_rate,
        steel_price_usd,
        supplier_discount_pct,
        general_increase_pct,
    ):
        return calculate_clamp_quote(
            {
                "client_name": clamp_request.client_name,
                "dollar_rate": dollar_rate,
                "steel_price_usd": steel_price_usd,
                "supplier_discount_pct": supplier_discount_pct,
                "general_increase_pct": general_increase_pct,
                "clamp_type": clamp_type,
                "is_zincated": is_zincated,
                "diameter": diameter,
                "width_mm": width_mm,
                "length_mm": length_mm,
                "profile_type": profile_type,
            }
        )

    quote_preview = None
    selected_price_row = None
    price_map = {}
    try:
        quote_preview = _build_quote_preview(
            clamp_type=clamp_request.clamp_type,
            is_zincated=clamp_request.is_zincated,
            diameter=clamp_request.diameter,
            width_mm=clamp_request.width_mm,
            length_mm=clamp_request.length_mm,
            profile_type=clamp_request.profile_type,
            dollar_rate=clamp_request.dollar_rate,
            steel_price_usd=clamp_request.steel_price_usd,
            supplier_discount_pct=clamp_request.supplier_discount_pct,
            general_increase_pct=clamp_request.general_increase_pct,
        )
        price_map = {row["key"]: row for row in quote_preview["price_rows"]}
        selected_price_row = price_map.get(clamp_request.selected_price_list)
    except ValueError:
        quote_preview = None
        price_map = {}

    if request.method == "POST":
        new_status = str(request.POST.get("status", "")).strip().lower()
        admin_note = str(request.POST.get("admin_note", "")).strip()
        client_response_note = str(request.POST.get("client_response_note", "")).strip()
        selected_price_list = str(request.POST.get("selected_price_list", "")).strip()
        confirmed_price_list = str(request.POST.get("confirmed_price_list", "")).strip()
        confirmed_price_raw = str(request.POST.get("confirmed_price", "")).strip().replace(",", ".")
        clamp_type = str(request.POST.get("clamp_type", clamp_request.clamp_type)).strip().lower()
        is_zincated = str(request.POST.get("is_zincated", "")).strip().lower() in {"1", "true", "on", "yes"}
        diameter = str(request.POST.get("diameter", clamp_request.diameter)).strip()
        profile_type = str(request.POST.get("profile_type", clamp_request.profile_type)).strip().upper()
        width_mm_raw = str(request.POST.get("width_mm", clamp_request.width_mm)).strip()
        length_mm_raw = str(request.POST.get("length_mm", clamp_request.length_mm)).strip()
        quantity_raw = str(request.POST.get("quantity", clamp_request.quantity)).strip()
        dollar_rate_raw = str(request.POST.get("dollar_rate", "")).strip()
        steel_price_usd_raw = str(request.POST.get("steel_price_usd", "")).strip()
        supplier_discount_pct_raw = str(request.POST.get("supplier_discount_pct", "")).strip()
        general_increase_pct_raw = str(request.POST.get("general_increase_pct", "")).strip()

        valid_statuses = {value for value, _ in ClampMeasureRequest.STATUS_CHOICES}
        valid_price_lists = {value for value, _ in ClampMeasureRequest.PRICE_LIST_CHOICES}
        valid_clamp_types = {value for value, _ in ClampMeasureRequest.CLAMP_TYPE_CHOICES}
        valid_profile_types = {value for value, _ in ClampMeasureRequest.PROFILE_TYPE_CHOICES}
        if new_status not in valid_statuses:
            messages.error(request, "Estado invalido.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
        if selected_price_list and selected_price_list not in valid_price_lists:
            messages.error(request, "Lista de estimacion invalida.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
        if confirmed_price_list and confirmed_price_list not in valid_price_lists:
            messages.error(request, "Lista confirmada invalida.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
        if clamp_type not in valid_clamp_types:
            messages.error(request, "Tipo de abrazadera invalido.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
        if profile_type not in valid_profile_types:
            messages.error(request, "Forma invalida.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
        if clamp_type == "laminada" and diameter not in CLAMP_LAMINATED_ALLOWED_DIAMETERS:
            messages.error(request, "Para laminada solo se permiten diametros 3/4, 1 y 7/8.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)

        try:
            width_mm = parse_int_value(width_mm_raw, "Ancho (mm)", min_value=1)
            length_mm = parse_int_value(length_mm_raw, "Largo (mm)", min_value=1)
            quantity = parse_int_value(quantity_raw, "Cantidad", min_value=1)
            dollar_rate = parse_decimal_value(
                dollar_rate_raw or clamp_request.dollar_rate,
                "Dolar",
                min_value=Decimal("0.0001"),
            )
            steel_price_usd = parse_decimal_value(
                steel_price_usd_raw or clamp_request.steel_price_usd,
                "Precio acero USD",
                min_value=Decimal("0.0001"),
            )
            supplier_discount_pct = parse_decimal_value(
                supplier_discount_pct_raw or clamp_request.supplier_discount_pct,
                "Desc. proveedor (%)",
                min_value=Decimal("0"),
            )
            general_increase_pct = parse_decimal_value(
                general_increase_pct_raw or clamp_request.general_increase_pct,
                "Aumento gral. (%)",
                min_value=Decimal("0"),
            )
            quote_preview = _build_quote_preview(
                clamp_type=clamp_type,
                is_zincated=is_zincated,
                diameter=diameter,
                width_mm=width_mm,
                length_mm=length_mm,
                profile_type=profile_type,
                dollar_rate=dollar_rate,
                steel_price_usd=steel_price_usd,
                supplier_discount_pct=supplier_discount_pct,
                general_increase_pct=general_increase_pct,
            )
            price_map = {row["key"]: row for row in quote_preview["price_rows"]}
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)

        if not selected_price_list:
            selected_price_list = clamp_request.selected_price_list
        selected_row = price_map.get(selected_price_list)
        if not selected_row:
            messages.error(request, "No se pudo calcular la lista seleccionada.")
            return redirect("admin_clamp_request_detail", pk=clamp_request.pk)

        estimated_final_price = selected_row["final_price"]
        recalculated_base_cost = quote_preview["base_cost"]
        recalculated_description = quote_preview["description"]
        recalculated_code = quote_preview.get("generated_code", "")

        confirmed_price = clamp_request.confirmed_price
        if confirmed_price_raw:
            try:
                confirmed_price = Decimal(confirmed_price_raw)
            except (InvalidOperation, ValueError):
                messages.error(request, "Precio confirmado invalido.")
                return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
            if confirmed_price <= 0:
                messages.error(request, "El precio confirmado debe ser mayor a cero.")
                return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
        elif confirmed_price_list and confirmed_price_list in price_map:
            confirmed_price = price_map[confirmed_price_list]["final_price"]

        if new_status in {ClampMeasureRequest.STATUS_QUOTED, ClampMeasureRequest.STATUS_COMPLETED}:
            if confirmed_price is None:
                if estimated_final_price:
                    confirmed_price = estimated_final_price
                else:
                    messages.error(request, "Confirma un precio para marcar la solicitud como cotizada/completada.")
                    return redirect("admin_clamp_request_detail", pk=clamp_request.pk)
            if not confirmed_price_list:
                confirmed_price_list = clamp_request.confirmed_price_list or selected_price_list

        changed = False
        technical_shape_changed = any(
            [
                clamp_request.clamp_type != clamp_type,
                clamp_request.is_zincated != is_zincated,
                clamp_request.diameter != diameter,
                clamp_request.width_mm != width_mm,
                clamp_request.length_mm != length_mm,
                clamp_request.profile_type != profile_type,
            ]
        )
        if clamp_request.admin_note != admin_note:
            clamp_request.admin_note = admin_note
            changed = True
        if clamp_request.client_response_note != client_response_note:
            clamp_request.client_response_note = client_response_note
            changed = True
        if clamp_request.status != new_status:
            clamp_request.status = new_status
            changed = True
        if clamp_request.confirmed_price_list != confirmed_price_list:
            clamp_request.confirmed_price_list = confirmed_price_list
            changed = True
        if clamp_request.confirmed_price != confirmed_price:
            clamp_request.confirmed_price = confirmed_price
            changed = True
        if clamp_request.clamp_type != clamp_type:
            clamp_request.clamp_type = clamp_type
            changed = True
        if clamp_request.is_zincated != is_zincated:
            clamp_request.is_zincated = is_zincated
            changed = True
        if clamp_request.diameter != diameter:
            clamp_request.diameter = diameter
            changed = True
        if clamp_request.width_mm != width_mm:
            clamp_request.width_mm = width_mm
            changed = True
        if clamp_request.length_mm != length_mm:
            clamp_request.length_mm = length_mm
            changed = True
        if clamp_request.profile_type != profile_type:
            clamp_request.profile_type = profile_type
            changed = True
        if clamp_request.quantity != quantity:
            clamp_request.quantity = quantity
            changed = True
        if clamp_request.description != recalculated_description:
            clamp_request.description = recalculated_description
            changed = True
        if clamp_request.generated_code != recalculated_code:
            clamp_request.generated_code = recalculated_code
            changed = True
        if clamp_request.selected_price_list != selected_price_list:
            clamp_request.selected_price_list = selected_price_list
            changed = True
        if clamp_request.estimated_final_price != estimated_final_price:
            clamp_request.estimated_final_price = estimated_final_price
            changed = True
        if clamp_request.base_cost != recalculated_base_cost:
            clamp_request.base_cost = recalculated_base_cost
            changed = True
        if clamp_request.dollar_rate != dollar_rate:
            clamp_request.dollar_rate = dollar_rate
            changed = True
        if clamp_request.steel_price_usd != steel_price_usd:
            clamp_request.steel_price_usd = steel_price_usd
            changed = True
        if clamp_request.supplier_discount_pct != supplier_discount_pct:
            clamp_request.supplier_discount_pct = supplier_discount_pct
            changed = True
        if clamp_request.general_increase_pct != general_increase_pct:
            clamp_request.general_increase_pct = general_increase_pct
            changed = True

        matching_exists = Product.objects.filter(
            clamp_specs__fabrication=clamp_type.upper(),
            clamp_specs__diameter=diameter,
            clamp_specs__width=width_mm,
            clamp_specs__length=length_mm,
            clamp_specs__shape=profile_type,
        ).exists()
        if clamp_request.exists_in_catalog != matching_exists:
            clamp_request.exists_in_catalog = matching_exists
            changed = True

        technical_unlinked = False
        if technical_shape_changed and clamp_request.linked_product_id:
            clamp_request.linked_product = None
            clamp_request.published_to_catalog_at = None
            technical_unlinked = True
            changed = True

        if (
            clamp_request.status in {ClampMeasureRequest.STATUS_QUOTED, ClampMeasureRequest.STATUS_COMPLETED}
            and clamp_request.confirmed_price is not None
            and not clamp_request.quoted_at
        ):
            clamp_request.quoted_at = timezone.now()
            changed = True

        if changed:
            clamp_request.processed_by = request.user
            clamp_request.processed_at = timezone.now()
            update_fields = [
                "status",
                "admin_note",
                "client_response_note",
                "clamp_type",
                "is_zincated",
                "diameter",
                "width_mm",
                "length_mm",
                "profile_type",
                "quantity",
                "description",
                "generated_code",
                "selected_price_list",
                "estimated_final_price",
                "base_cost",
                "dollar_rate",
                "steel_price_usd",
                "supplier_discount_pct",
                "general_increase_pct",
                "confirmed_price_list",
                "confirmed_price",
                "exists_in_catalog",
                "linked_product",
                "published_to_catalog_at",
                "quoted_at",
                "processed_by",
                "processed_at",
                "updated_at",
            ]
            clamp_request.save(update_fields=update_fields)
            log_admin_action(
                request,
                action="clamp_request_updated",
                target_type="clamp_measure_request",
                target_id=clamp_request.pk,
                details={
                    "status": clamp_request.status,
                    "clamp_type": clamp_request.clamp_type,
                    "diameter": clamp_request.diameter,
                    "width_mm": clamp_request.width_mm,
                    "length_mm": clamp_request.length_mm,
                    "profile_type": clamp_request.profile_type,
                    "confirmed_price_list": clamp_request.confirmed_price_list,
                    "confirmed_price": str(clamp_request.confirmed_price or ""),
                    "client_response_note": clamp_request.client_response_note[:200],
                    "admin_note": clamp_request.admin_note[:200],
                },
            )
            if technical_unlinked:
                messages.warning(
                    request,
                    "Se desvinculo el producto asociado porque cambiaste las medidas tecnicas.",
                )
            messages.success(request, "Solicitud actualizada.")
        else:
            messages.info(request, "No hubo cambios para guardar.")
        return redirect("admin_clamp_request_detail", pk=clamp_request.pk)

    matching_products = _find_admin_clamp_request_matches(clamp_request)

    return render(
        request,
        "admin_panel/clamp_requests/detail.html",
        {
            "clamp_request": clamp_request,
            "status_choices": ClampMeasureRequest.STATUS_CHOICES,
            "price_list_choices": ClampMeasureRequest.PRICE_LIST_CHOICES,
            "clamp_type_choices": ClampMeasureRequest.CLAMP_TYPE_CHOICES,
            "profile_type_choices": ClampMeasureRequest.PROFILE_TYPE_CHOICES,
            "diameter_options": get_allowed_diameter_options(clamp_request.clamp_type),
            "all_diameter_options_json": json.dumps(get_allowed_diameter_options()),
            "laminated_diameter_options_json": json.dumps(list(CLAMP_LAMINATED_ALLOWED_DIAMETERS)),
            "quote_preview": quote_preview,
            "selected_price_row": selected_price_row,
            "matching_products": matching_products,
        },
    )


# ===================== CLIENTS =====================

@staff_member_required
def client_dashboard(request):
    """Simple dashboard for the clients module."""
    active_company = get_active_company(request)

    clients = ClientProfile.objects.select_related("user", "client_category")
    if active_company:
        clients = clients.filter(company_links__company=active_company).distinct()

    total_clients = clients.count()
    approved_clients = clients.filter(is_approved=True).count()
    portal_enabled_clients = clients.filter(user__is_active=True).count()
    new_this_month = clients.filter(created_at__date__gte=timezone.now().date().replace(day=1)).count()
    pending_requests = AccountRequest.objects.filter(status="pending").count()

    top_categories = (
        clients.values("client_category__name")
        .annotate(total=Count("id"))
        .order_by("-total", "client_category__name")[:5]
    )
    recent_clients = clients.order_by("-created_at")[:8]

    return render(
        request,
        "admin_panel/clients/dashboard.html",
        {
            "total_clients": total_clients,
            "approved_clients": approved_clients,
            "portal_enabled_clients": portal_enabled_clients,
            "new_this_month": new_this_month,
            "pending_requests": pending_requests,
            "top_categories": top_categories,
            "recent_clients": recent_clients,
            "can_create_client": can_edit_client_profile(request.user),
            "can_manage_client_categories": can_edit_client_profile(request.user),
        },
    )


@staff_member_required
def client_tools_hub(request):
    """Hub for client operational tools."""
    active_company = get_active_company(request)
    tool_cards = [
        {
            "title": "Exportar clientes",
            "description": "Descarga la base plana de clientes por empresa activa para trabajo operativo o compatibilidad externa.",
            "url": reverse("admin_client_export"),
            "icon": "&#128228;",
        },
        {
            "title": "Importar o actualizar",
            "description": "Reutiliza el importador existente para altas masivas o actualizaciones desde Excel.",
            "url": reverse("admin_import_process", args=["clients"]),
            "icon": "&#128229;",
        },
        {
            "title": "Solicitudes",
            "description": "Gestiona aprobaciones y cola de ingresos pendientes del portal.",
            "url": reverse("admin_request_list"),
            "icon": "&#128221;",
        },
    ]

    return render(
        request,
        "admin_panel/clients/tools_hub.html",
        {
            "tool_cards": tool_cards,
            "client_tools_panel": "hub",
            "client_tools_nav_items": _client_tools_nav(),
            "tools_requires_company": not bool(active_company),
        },
    )


@staff_member_required
def client_export(request):
    """Export client base using operational or import-compatible presets."""
    active_company = get_active_company(request)
    action = str(request.GET.get("action", "")).strip().lower()
    selected_encoding = str(request.GET.get("encoding", "utf8")).strip() or "utf8"
    selected_preset = str(request.GET.get("preset", "operational")).strip() or "operational"
    if selected_encoding not in dict(CLIENT_EXPORT_ENCODING_CHOICES):
        selected_encoding = "utf8"
    if selected_preset not in dict(CLIENT_EXPORT_PRESET_CHOICES):
        selected_preset = "operational"

    export_count = None
    if active_company:
        export_count = _build_client_report_queryset(
            active_company,
            include_balance_prefetch=selected_preset == "operational",
        ).count()

    if action == "download" and active_company:
        headers, rows = _get_client_export_rows(active_company, preset=selected_preset)
        filename_prefix = "clientes_base_operativa" if selected_preset == "operational" else "clientes_importacion"
        filename = (
            f"{filename_prefix}_{getattr(active_company, 'slug', 'sin-empresa')}_"
            f"{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
        )
        return _client_export_csv_response(
            filename,
            headers,
            rows,
            encoding_key=selected_encoding,
        )

    return render(
        request,
        "admin_panel/clients/export.html",
        {
            "client_tools_panel": "export",
            "client_tools_nav_items": _client_tools_nav(),
            "tools_requires_company": not bool(active_company),
            "encoding_choices": CLIENT_EXPORT_ENCODING_CHOICES,
            "preset_choices": CLIENT_EXPORT_PRESET_CHOICES,
            "selected_encoding": selected_encoding,
            "selected_preset": selected_preset,
            "export_count": export_count,
            "active_company": active_company,
        },
    )


@staff_member_required
def client_reports_hub(request):
    active_company = get_active_company(request)
    report_cards = [
        {
            "title": "Lista de clientes",
            "description": "Filtra la cartera, elegi columnas y genera una vista operativa o una descarga.",
            "url": reverse("admin_client_report_list"),
            "icon": "&#128196;",
        },
        {
            "title": "Ranking de clientes",
            "description": "Mide volumen comercial por periodos y detecta a los clientes con mayor o menor movimiento.",
            "url": reverse("admin_client_report_ranking"),
            "icon": "&#128200;",
        },
        {
            "title": "Clientes deudores",
            "description": "Controla saldos deudores, acreedores y cuentas no habilitadas con diferencia pendiente.",
            "url": reverse("admin_client_report_debtors"),
            "icon": "&#128184;",
        },
    ]

    return render(
        request,
        "admin_panel/clients/reports_hub.html",
        {
            "report_cards": report_cards,
            "client_report_panel": "hub",
            "client_report_nav_items": _client_reports_nav(),
            "reports_requires_company": not bool(active_company),
        },
    )


@staff_member_required
def client_report_list(request):
    active_company = get_active_company(request)
    action = str(request.GET.get("action", "")).strip().lower()
    report_requested = action in {"generate", "download"}
    standalone_report = report_requested and action == "generate" and _is_standalone_report_request(request)
    selected_columns = [
        column
        for column in request.GET.getlist("columns")
        if column in dict(CLIENT_REPORT_OPTIONAL_COLUMNS)
    ]
    selected_locality = str(request.GET.get("locality", "")).strip()
    selected_category = str(request.GET.get("category", "")).strip()
    selected_state = str(request.GET.get("state", "all")).strip() or "all"
    selected_iva = str(request.GET.get("iva_condition", "all")).strip() or "all"
    selected_text_field = str(request.GET.get("text_field", "company_name")).strip() or "company_name"
    if selected_text_field not in dict(CLIENT_REPORT_TEXT_FIELD_CHOICES):
        selected_text_field = "company_name"
    selected_text = str(request.GET.get("text", "")).strip()

    locality_choices = []
    categories = ClientCategory.objects.order_by("sort_order", "name")
    rows = []

    if active_company:
        clients = list(_build_client_report_queryset(active_company).order_by("company_name", "user__username"))
        locality_choices = _get_client_report_locality_choices(active_company)

        if report_requested:
            for client in clients:
                company_link = _get_report_company_link(client, active_company)
                row = _build_client_report_row(
                    client,
                    active_company=active_company,
                    company_link=company_link,
                    include_balance="balance" in selected_columns,
                )

                if selected_locality and row["locality"] != selected_locality:
                    continue
                if selected_category and str(row["category_id"] or "") != selected_category:
                    continue
                if selected_state == "enabled" and not row["is_enabled"]:
                    continue
                if selected_state == "disabled" and row["is_enabled"]:
                    continue
                if selected_iva != "all" and (client.iva_condition or "") != selected_iva:
                    continue

                if selected_text:
                    lookup_map = {
                        "company_name": row["company_name"],
                        "client_id": row["client_id"],
                        "username": row["username"],
                        "email": row["email"],
                        "phone": row["phones"],
                        "document": row["document_detail"],
                    }
                    if not _client_report_matches_text(lookup_map.get(selected_text_field, ""), selected_text):
                        continue

                rows.append(row)

        for row in rows:
            row["selected_values"] = []
            for column_key, _label in CLIENT_REPORT_OPTIONAL_COLUMNS:
                if column_key not in selected_columns:
                    continue
                value = row.get(column_key)
                if isinstance(value, Decimal):
                    value = f"${value:.2f}"
                row["selected_values"].append(value or "-")

    if action == "download":
        filename = f"clientes_lista_{getattr(active_company, 'slug', 'sin-empresa')}_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
        headers = ["ID", "Cliente", "Usuario", "Estado", "Condicion IVA", "CUIT/DNI"]
        for key, label in CLIENT_REPORT_OPTIONAL_COLUMNS:
            if key in selected_columns:
                headers.append(label)

        csv_rows = []
        for row in rows:
            csv_row = [
                row["client_id"],
                row["company_name"],
                row["username"],
                row["state"],
                row["iva_condition"],
                row["cuit_dni"],
            ]
            for column_key, _label in CLIENT_REPORT_OPTIONAL_COLUMNS:
                if column_key not in selected_columns:
                    continue
                value = row.get(column_key)
                if isinstance(value, Decimal):
                    value = f"{value:.2f}"
                csv_row.append(value or "-")
            csv_rows.append(csv_row)
        return _client_report_csv_response(filename, headers, csv_rows)

    if standalone_report:
        visible_headers = ["ID", "Cliente", "Usuario", "Estado", "Cond. IVA", "CUIT/DNI"]
        visible_headers.extend(
            [label for key, label in CLIENT_REPORT_OPTIONAL_COLUMNS if key in selected_columns]
        )
        return render(
            request,
            "admin_panel/clients/report_output_list.html",
            {
                "report_title": "Lista de clientes",
                "report_subtitle": "Cartera filtrada por empresa, categoria, estado y condiciones comerciales.",
                "report_generated_at": timezone.now(),
                "report_company": active_company,
                "visible_headers": visible_headers,
                "rows": rows,
                "selected_locality": selected_locality or "Todas las localidades",
                "selected_state_label": dict(CLIENT_REPORT_STATE_CHOICES).get(selected_state, "Todos los estados"),
                "selected_iva_label": dict([("all", "Todas las condiciones de IVA"), *ClientProfile.IVA_CHOICES]).get(
                    selected_iva,
                    "Todas las condiciones de IVA",
                ),
                "selected_text": selected_text,
                "selected_text_field_label": dict(CLIENT_REPORT_TEXT_FIELD_CHOICES).get(selected_text_field, "Nombre"),
            },
        )

    return render(
        request,
        "admin_panel/clients/reports_list.html",
        {
            "client_report_panel": "list",
            "client_report_nav_items": _client_reports_nav(),
            "reports_requires_company": not bool(active_company),
            "report_requested": report_requested,
            "rows": rows,
            "selected_columns": selected_columns,
            "locality_choices": locality_choices,
            "categories": categories,
            "state_choices": CLIENT_REPORT_STATE_CHOICES,
            "iva_choices": ClientProfile.IVA_CHOICES,
            "text_field_choices": CLIENT_REPORT_TEXT_FIELD_CHOICES,
            "optional_columns": CLIENT_REPORT_OPTIONAL_COLUMNS,
            "selected_locality": selected_locality,
            "selected_category": selected_category,
            "selected_state": selected_state,
            "selected_iva": selected_iva,
            "selected_text_field": selected_text_field,
            "selected_text": selected_text,
        },
    )


@staff_member_required
def client_report_ranking(request):
    active_company = get_active_company(request)
    action = str(request.GET.get("action", "")).strip().lower()
    report_requested = action in {"generate", "download"}
    standalone_report = report_requested and action == "generate" and _is_standalone_report_request(request)
    selected_range = str(request.GET.get("date_range", "all")).strip() or "all"
    if selected_range not in dict(CLIENT_REPORT_DATE_RANGE_CHOICES):
        selected_range = "all"
    selected_ranking = str(request.GET.get("ranking", "top_10")).strip() or "top_10"
    if selected_ranking not in dict(CLIENT_REPORT_RANKING_CHOICES):
        selected_ranking = "top_10"
    start_date_raw = str(request.GET.get("start_date", "")).strip()
    end_date_raw = str(request.GET.get("end_date", "")).strip()
    start_date, end_date = _resolve_report_date_range(selected_range, start_date_raw, end_date_raw)
    range_label = _client_report_date_label(selected_range, start_date, end_date)
    rows = []

    if active_company and report_requested:
        ranking_queryset = Order.objects.filter(
            company=active_company,
            status__in=CLIENT_REPORT_ORDER_STATUSES,
            user__client_profile__isnull=False,
        )
        if start_date:
            ranking_queryset = ranking_queryset.filter(created_at__date__gte=start_date)
        if end_date:
            ranking_queryset = ranking_queryset.filter(created_at__date__lte=end_date)

        limit = 100 if selected_ranking.endswith("100") else 10
        direction = CLIENT_REPORT_RESULTS_SORT_FIELDS[selected_ranking]
        ranking_rows = list(
            ranking_queryset.values(
                "user__client_profile",
                "user__client_profile__company_name",
                "user__username",
                "user__client_profile__cuit_dni",
            )
            .annotate(
                total_sales=Coalesce(
                    Sum("total"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                orders_count=Count("id"),
                last_order_at=Max("created_at"),
                average_ticket=Coalesce(
                    Avg("total"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .order_by(*direction)[:limit]
        )

        profile_map = {
            client.pk: client
            for client in _build_client_report_queryset(active_company).filter(
                pk__in=[row["user__client_profile"] for row in ranking_rows]
            )
        }

        for position, row in enumerate(ranking_rows, start=1):
            client = profile_map.get(row["user__client_profile"])
            company_link = _get_report_company_link(client, active_company) if client else None
            category = _get_report_client_category(client, active_company, company_link) if client else None
            rows.append(
                {
                    "position": position,
                    "client_id": row["user__client_profile"],
                    "company_name": row["user__client_profile__company_name"] or "-",
                    "username": row["user__username"] or "-",
                    "cuit_dni": row["user__client_profile__cuit_dni"] or "-",
                    "category": category.name if category else "Sin categoria",
                    "orders_count": row["orders_count"],
                    "total_sales": row["total_sales"] or Decimal("0.00"),
                    "average_ticket": row["average_ticket"] or Decimal("0.00"),
                    "last_order_at": row["last_order_at"],
                }
            )

    if action == "download":
        filename = f"clientes_ranking_{getattr(active_company, 'slug', 'sin-empresa')}_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
        headers = [
            "Posicion",
            "Cliente",
            "Usuario",
            "CUIT/DNI",
            "Categoria",
            "Pedidos",
            "Total comprado",
            "Ticket promedio",
            "Ultimo pedido",
        ]
        csv_rows = [
            [
                row["position"],
                row["company_name"],
                row["username"],
                row["cuit_dni"],
                row["category"],
                row["orders_count"],
                f"{row['total_sales']:.2f}",
                f"{row['average_ticket']:.2f}",
                timezone.localtime(row["last_order_at"]).strftime("%d/%m/%Y %H:%M") if row["last_order_at"] else "-",
            ]
            for row in rows
        ]
        return _client_report_csv_response(filename, headers, csv_rows)

    if standalone_report:
        return render(
            request,
            "admin_panel/clients/report_output_ranking.html",
            {
                "report_title": "Ranking de clientes",
                "report_subtitle": dict(CLIENT_REPORT_RANKING_CHOICES).get(
                    selected_ranking,
                    "Ranking comercial",
                ),
                "report_generated_at": timezone.now(),
                "report_company": active_company,
                "range_label": range_label,
                "rows": rows,
            },
        )

    return render(
        request,
        "admin_panel/clients/reports_ranking.html",
        {
            "client_report_panel": "ranking",
            "client_report_nav_items": _client_reports_nav(),
            "reports_requires_company": not bool(active_company),
            "report_requested": report_requested,
            "rows": rows,
            "date_range_choices": CLIENT_REPORT_DATE_RANGE_CHOICES,
            "ranking_choices": CLIENT_REPORT_RANKING_CHOICES,
            "selected_range": selected_range,
            "selected_ranking": selected_ranking,
            "start_date": start_date_raw,
            "end_date": end_date_raw,
            "range_label": range_label,
        },
    )


@staff_member_required
def client_report_debtors(request):
    active_company = get_active_company(request)
    action = str(request.GET.get("action", "")).strip().lower()
    report_requested = action in {"generate", "download"}
    standalone_report = report_requested and action == "generate" and _is_standalone_report_request(request)
    report_type = str(request.GET.get("report_type", "enabled_debtors")).strip() or "enabled_debtors"
    if report_type not in dict(CLIENT_REPORT_DEBTOR_CHOICES):
        report_type = "enabled_debtors"
    selected_currency = str(request.GET.get("currency", "all")).strip() or "all"
    if selected_currency not in dict(CLIENT_REPORT_CURRENCY_CHOICES):
        selected_currency = "all"
    tolerance_raw = str(request.GET.get("tolerance", "1.00")).strip() or "1.00"
    try:
        tolerance = parse_admin_decimal_input(tolerance_raw, "Tolerancia para considerar deuda", min_value="0")
    except ValueError:
        tolerance = Decimal("1.00")
        tolerance_raw = "1.00"

    rows = []
    total_balance = Decimal("0.00")

    if active_company and report_requested:
        clients = list(
            _build_client_report_queryset(active_company, include_balance_prefetch=True).order_by(
                "company_name",
                "user__username",
            )
        )

        for client in clients:
            company_link = _get_report_company_link(client, active_company)
            client_state = _get_report_client_state(client, active_company, company_link)
            balance = _get_report_client_balance(client, active_company)
            include_row = False

            if report_type == "enabled_debtors":
                include_row = client_state["enabled"] and balance > tolerance
            elif report_type == "enabled_creditors":
                include_row = client_state["enabled"] and balance < (tolerance * Decimal("-1"))
            elif report_type == "disabled_non_zero":
                include_row = (not client_state["enabled"]) and abs(balance) > tolerance

            if not include_row:
                continue

            latest_transaction = getattr(client, "report_transactions", [])
            latest_order = getattr(getattr(client, "user", None), "report_balance_orders", [])
            latest_event_at = None
            if latest_transaction:
                latest_event_at = latest_transaction[0].occurred_at
            elif latest_order:
                latest_event_at = latest_order[0].created_at

            category = _get_report_client_category(client, active_company, company_link)
            rows.append(
                {
                    "client": client,
                    "company_name": client.company_name or "-",
                    "username": getattr(client.user, "username", "-") or "-",
                    "category": category.name if category else "Sin categoria",
                    "state": client_state["label"],
                    "cuit_dni": client.cuit_dni or client.document_number or "-",
                    "balance": balance,
                    "last_event_at": latest_event_at,
                }
            )

        if report_type == "enabled_creditors":
            rows.sort(key=lambda item: item["balance"])
        else:
            rows.sort(key=lambda item: abs(item["balance"]), reverse=True)

        total_balance = _sum_decimal_values(row["balance"] for row in rows)

    if action == "download":
        filename = f"clientes_deudores_{getattr(active_company, 'slug', 'sin-empresa')}_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
        headers = [
            "Cliente",
            "Usuario",
            "CUIT/DNI",
            "Categoria",
            "Estado",
            "Saldo",
            "Ultimo movimiento",
        ]
        csv_rows = [
            [
                row["company_name"],
                row["username"],
                row["cuit_dni"],
                row["category"],
                row["state"],
                f"{row['balance']:.2f}",
                timezone.localtime(row["last_event_at"]).strftime("%d/%m/%Y %H:%M") if row["last_event_at"] else "-",
            ]
            for row in rows
        ]
        return _client_report_csv_response(filename, headers, csv_rows)

    if standalone_report:
        return render(
            request,
            "admin_panel/clients/report_output_debtors.html",
            {
                "report_title": "Clientes deudores",
                "report_subtitle": dict(CLIENT_REPORT_DEBTOR_CHOICES).get(
                    report_type,
                    "Estado de cuenta corriente por cliente",
                ),
                "report_generated_at": timezone.now(),
                "report_company": active_company,
                "tolerance": tolerance_raw,
                "selected_currency_label": dict(CLIENT_REPORT_CURRENCY_CHOICES).get(selected_currency, "Todas las monedas"),
                "rows": rows,
                "total_balance": total_balance,
            },
        )

    return render(
        request,
        "admin_panel/clients/reports_debtors.html",
        {
            "client_report_panel": "debtors",
            "client_report_nav_items": _client_reports_nav(),
            "reports_requires_company": not bool(active_company),
            "report_requested": report_requested,
            "rows": rows,
            "debtor_type_choices": CLIENT_REPORT_DEBTOR_CHOICES,
            "currency_choices": CLIENT_REPORT_CURRENCY_CHOICES,
            "report_type": report_type,
            "selected_currency": selected_currency,
            "tolerance": tolerance_raw,
            "total_balance": total_balance,
        },
    )


@staff_member_required
def client_list(request):
    """Client list with search."""
    active_company = get_active_company(request)
    clients = ClientProfile.objects.select_related('user', 'client_category').all()
    if active_company:
        clients = clients.filter(
            company_links__company=active_company,
            company_links__is_active=True,
        ).distinct()
    
    clients, search = apply_admin_text_search(
        clients,
        request.GET.get('q', ''),
        ["company_name", "user__username", "cuit_dni", "user__email"],
    )
    
    paginator = Paginator(clients.order_by('-created_at'), 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)
    
    return render(request, 'admin_panel/clients/list.html', {
        'page_obj': page_obj,
        'search': search,
        'can_manage_client_categories': can_edit_client_profile(request.user),
        'can_edit_client_profile': can_edit_client_profile(request.user),
        'can_create_client': can_edit_client_profile(request.user),
        'can_manage_client_credentials': can_manage_client_credentials(request.user),
        'can_delete_client_record': can_delete_client_record(request.user),
    })


@staff_member_required
def client_category_list(request):
    """List client categories used for discount/account-current rules."""
    categories = ClientCategory.objects.all()
    categories, search = apply_admin_text_search(
        categories,
        request.GET.get("q", ""),
        ["name", "price_list_name", "slug"],
    )
    status = str(request.GET.get("status", "")).strip().lower()
    if status == "active":
        categories = categories.filter(is_active=True)
    elif status == "inactive":
        categories = categories.filter(is_active=False)

    paginator = Paginator(categories.order_by("sort_order", "name"), 50)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "admin_panel/clients/categories_list.html",
        {
            "page_obj": page_obj,
            "search": search,
            "status": status,
        },
    )


@staff_member_required
def client_category_create(request):
    """Create a client category."""
    if request.method == "POST":
        name = str(request.POST.get("name", "")).strip()
        default_sale_condition = str(
            request.POST.get("default_sale_condition", ClientCategory.SALE_CONDITION_CASH)
        ).strip()
        allows_account_current = request.POST.get("allows_account_current") == "on"
        expose_cost = request.POST.get("expose_cost") == "on"
        is_active = request.POST.get("is_active") == "on"
        price_list_name = str(request.POST.get("price_list_name", "Principal")).strip() or "Principal"

        try:
            sort_order = int(str(request.POST.get("sort_order", "0")).strip() or "0")
        except ValueError:
            messages.error(request, "Orden invalido.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": None, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )

        try:
            discount_percentage = parse_admin_decimal_input(
                request.POST.get("discount_percentage", "0"),
                "Descuento (%)",
                min_value="0",
                max_value="100",
            )
            account_current_limit = parse_admin_decimal_input(
                request.POST.get("account_current_limit", "0"),
                "Limite de cuenta corriente",
                min_value="0",
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": None, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )

        if not name:
            messages.error(request, "El nombre es obligatorio.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": None, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )
        if default_sale_condition not in dict(ClientCategory.SALE_CONDITION_CHOICES):
            messages.error(request, "Condicion de venta invalida.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": None, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )
        if ClientCategory.objects.filter(name__iexact=name).exists():
            messages.error(request, "Ya existe una categoria con ese nombre.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": None, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )

        category = ClientCategory.objects.create(
            name=name,
            default_sale_condition=default_sale_condition,
            allows_account_current=allows_account_current,
            account_current_limit=account_current_limit,
            expose_cost=expose_cost,
            discount_percentage=discount_percentage,
            price_list_name=price_list_name,
            sort_order=max(sort_order, 0),
            is_active=is_active,
        )
        log_admin_action(
            request,
            action="client_category_create",
            target_type="client_category",
            target_id=category.pk,
            details={
                "name": category.name,
                "discount_percentage": str(category.discount_percentage),
                "allows_account_current": category.allows_account_current,
                "account_current_limit": str(category.account_current_limit),
                "price_list_name": category.price_list_name,
                "is_active": category.is_active,
            },
        )
        messages.success(request, f'Categoria "{category.name}" creada.')
        return redirect("admin_client_category_list")

    return render(
        request,
        "admin_panel/clients/categories_form.html",
        {"category": None, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
    )


@staff_member_required
def client_category_edit(request, pk):
    """Edit a client category."""
    category = get_object_or_404(ClientCategory, pk=pk)
    if request.method == "POST":
        before = model_snapshot(
            category,
            [
                "name",
                "default_sale_condition",
                "allows_account_current",
                "account_current_limit",
                "expose_cost",
                "discount_percentage",
                "price_list_name",
                "sort_order",
                "is_active",
            ],
        )

        name = str(request.POST.get("name", "")).strip()
        default_sale_condition = str(
            request.POST.get("default_sale_condition", ClientCategory.SALE_CONDITION_CASH)
        ).strip()
        allows_account_current = request.POST.get("allows_account_current") == "on"
        expose_cost = request.POST.get("expose_cost") == "on"
        is_active = request.POST.get("is_active") == "on"
        price_list_name = str(request.POST.get("price_list_name", "Principal")).strip() or "Principal"

        try:
            sort_order = int(str(request.POST.get("sort_order", "0")).strip() or "0")
            discount_percentage = parse_admin_decimal_input(
                request.POST.get("discount_percentage", "0"),
                "Descuento (%)",
                min_value="0",
                max_value="100",
            )
            account_current_limit = parse_admin_decimal_input(
                request.POST.get("account_current_limit", "0"),
                "Limite de cuenta corriente",
                min_value="0",
            )
        except (ValueError, TypeError) as exc:
            messages.error(request, str(exc))
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": category, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )

        if not name:
            messages.error(request, "El nombre es obligatorio.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": category, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )
        if default_sale_condition not in dict(ClientCategory.SALE_CONDITION_CHOICES):
            messages.error(request, "Condicion de venta invalida.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": category, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )
        if ClientCategory.objects.filter(name__iexact=name).exclude(pk=category.pk).exists():
            messages.error(request, "Ya existe una categoria con ese nombre.")
            return render(
                request,
                "admin_panel/clients/categories_form.html",
                {"category": category, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
            )

        category.name = name
        category.default_sale_condition = default_sale_condition
        category.allows_account_current = allows_account_current
        category.account_current_limit = account_current_limit
        category.expose_cost = expose_cost
        category.discount_percentage = discount_percentage
        category.price_list_name = price_list_name
        category.sort_order = max(sort_order, 0)
        category.is_active = is_active
        category.save()

        # Keep assigned clients aligned with category discount rule.
        ClientProfile.objects.filter(client_category=category).update(discount=category.discount_percentage)

        after = model_snapshot(
            category,
            [
                "name",
                "default_sale_condition",
                "allows_account_current",
                "account_current_limit",
                "expose_cost",
                "discount_percentage",
                "price_list_name",
                "sort_order",
                "is_active",
            ],
        )
        log_admin_change(
            request,
            action="client_category_update",
            target_type="client_category",
            target_id=category.pk,
            before=before,
            after=after,
        )
        messages.success(request, f'Categoria "{category.name}" actualizada.')
        return redirect("admin_client_category_list")

    return render(
        request,
        "admin_panel/clients/categories_form.html",
        {"category": category, "sale_condition_choices": ClientCategory.SALE_CONDITION_CHOICES},
    )


@staff_member_required
@require_POST
def client_category_delete(request, pk):
    """Delete/deactivate a client category."""
    category = get_object_or_404(ClientCategory, pk=pk)
    before = model_snapshot(
        category,
        ["name", "is_active", "discount_percentage", "allows_account_current", "account_current_limit"],
    )
    impacted_clients = ClientProfile.objects.filter(client_category=category).count()
    ClientProfile.objects.filter(client_category=category).update(client_category=None)
    category.delete()
    log_admin_change(
        request,
        action="client_category_delete",
        target_type="client_category",
        target_id=pk,
        before=before,
        after={"deleted": True, "impacted_clients": impacted_clients},
    )
    messages.success(request, "Categoria eliminada.")
    return redirect("admin_client_category_list")


@staff_member_required
def client_create(request):
    """Create a client user, profile and initial company relation from the admin panel."""
    if not can_edit_client_profile(request.user):
        messages.error(request, "No tienes permisos para crear clientes.")
        return redirect("admin_client_list")

    active_company, companies = _resolve_client_editor_company(request)
    initial_values = _build_client_form_values(active_company=active_company)
    if active_company and not initial_values.get("company_id"):
        initial_values["company_id"] = str(active_company.pk)

    if request.method == "POST":
        form_values = _build_client_form_values(form_data=request.POST)
        company = None
        company_id = form_values.get("company_id", "")
        if company_id.isdigit():
            company = companies.filter(pk=int(company_id), is_active=True).first()
        if not company:
            company = active_company or get_default_client_origin_company()
            if company:
                form_values["company_id"] = str(company.pk)

        company_id = form_values.get("company_id", "")
        if not company_id or not str(company_id).isdigit():
            messages.error(request, "Selecciona una empresa operativa valida.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )

        if not company:
            messages.error(request, "Selecciona una empresa operativa valida.")
            return _render_client_form(
                request,
                active_company=active_company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )

        selected_companies = _resolve_linked_companies(form_values, companies)
        if not selected_companies:
            messages.error(request, "Selecciona al menos una empresa habilitada para este cliente.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )
        if company not in selected_companies:
            messages.error(request, "La empresa en edicion tambien debe estar habilitada para el cliente.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )

        try:
            selected_category = parse_optional_client_category(form_values.get("client_category", ""))
            discount_value = parse_admin_decimal_input(
                form_values.get("discount", "0"),
                "Descuento (%)",
                min_value="0",
                max_value="100",
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )

        username = form_values.get("username", "")
        password = str(request.POST.get("password", "")).strip()
        password_confirm = str(request.POST.get("password_confirm", "")).strip()
        company_name = form_values.get("company_name", "")

        if not username:
            messages.error(request, "El nombre de usuario es obligatorio.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, f'El usuario "{username}" ya existe.')
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )
        if not company_name:
            messages.error(request, "La razon social o empresa es obligatoria.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )
        if not password:
            messages.error(request, "La contrasena inicial es obligatoria.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )
        if password != password_confirm:
            messages.error(request, "Las contrasenas no coinciden.")
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )
        try:
            validate_password(password)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return _render_client_form(
                request,
                active_company=company,
                companies=companies,
                client_company=None,
                form_values=form_values,
            )

        document_type_choices = {choice[0] for choice in ClientProfile.DOCUMENT_TYPE_CHOICES}
        client_type_choices = {choice[0] for choice in ClientProfile.CLIENT_TYPE_CHOICES}
        iva_choices = {choice[0] for choice in ClientProfile.IVA_CHOICES}
        user_is_active = form_values.get("user_is_active", True)
        client_is_approved = form_values.get("client_is_approved", True)
        company_is_active = form_values.get("company_is_active", True)
        default_company = get_default_client_origin_company()
        should_update_legacy = (
            not company
            or (default_company and company and default_company.id == company.id)
        )

        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                email=form_values.get("email", ""),
                password=password,
                first_name=form_values.get("first_name", ""),
                last_name=form_values.get("last_name", ""),
                is_active=user_is_active,
            )
            client = ClientProfile.objects.create(
                user=user,
                company_name=company_name,
                document_type=form_values.get("document_type", "") if form_values.get("document_type", "") in document_type_choices else "",
                document_number=form_values.get("document_number", ""),
                cuit_dni=form_values.get("cuit_dni", ""),
                province=form_values.get("province", ""),
                fiscal_province=form_values.get("fiscal_province", ""),
                fiscal_city=form_values.get("fiscal_city", ""),
                address=form_values.get("address", ""),
                fiscal_address=form_values.get("fiscal_address", ""),
                postal_code=form_values.get("postal_code", ""),
                phone=form_values.get("phone", ""),
                discount=(selected_category.discount_percentage if selected_category else discount_value) if should_update_legacy else Decimal("0"),
                iva_condition=form_values.get("iva_condition", "") if form_values.get("iva_condition", "") in iva_choices else "",
                client_type=form_values.get("client_type", "") if form_values.get("client_type", "") in client_type_choices else "",
                client_category=selected_category if should_update_legacy else None,
                is_approved=client_is_approved,
                notes=form_values.get("notes", ""),
            )
            client_links = []
            for linked_company in selected_companies:
                client_link = ClientCompany.objects.create(
                    client_profile=client,
                    company=linked_company,
                    client_category=selected_category,
                    discount_percentage=selected_category.discount_percentage if selected_category else discount_value,
                    is_active=True,
                )
                client_links.append(client_link)

        client_company = next((link for link in client_links if link.company_id == company.id), None)

        log_admin_action(
            request,
            action="client_create",
            target_type="client_profile",
            target_id=client.pk,
            details={
                "username": user.username,
                "email": user.email,
                "company_name": client.company_name,
                "company_id": company.pk,
                "client_company_id": client_company.pk if client_company else None,
                "linked_company_ids": [link.company_id for link in client_links],
                "client_category_id": selected_category.pk if selected_category else None,
                "discount": str(client_company.discount_percentage),
            },
        )
        messages.success(request, f'Cliente "{client.company_name}" creado correctamente.')
        return redirect(f"{reverse('admin_client_edit', args=[client.pk])}?company_id={company.pk}")

    return _render_client_form(
        request,
        active_company=active_company,
        companies=companies,
        form_values=initial_values,
    )


@staff_member_required
def client_edit(request, pk):
    """Edit client profile."""
    client = get_object_or_404(ClientProfile.objects.select_related("user", "client_category"), pk=pk)
    active_company, companies = _resolve_client_editor_company(request, client=client)
    client_company = None
    if active_company:
        client_company = (
            ClientCompany.objects.select_related("company", "client_category")
            .filter(client_profile=client, company=active_company)
            .first()
        )
    effective_category = (
        client_company.client_category
        if client_company and client_company.client_category_id
        else client.client_category
    )
    effective_category_id = effective_category.pk if effective_category else None
    effective_discount = client.get_effective_discount_percentage(company=active_company)
    company_is_active = client_company.is_active if client_company else bool(client.is_approved)
    uses_legacy = client.uses_legacy_commercial_rules(active_company)

    if not can_edit_client_profile(request.user):
        messages.error(
            request,
            'No tienes permisos para editar clientes.',
        )
        return redirect('admin_client_order_history', pk=client.pk)
    
    if request.method == 'POST':
        form_values = _build_client_form_values(form_data=request.POST)
        company = None
        company_id = form_values.get("company_id", "")
        if company_id.isdigit():
            company = companies.filter(pk=int(company_id), is_active=True).first()
        if not company:
            company = active_company or get_default_client_origin_company()
            if company:
                form_values["company_id"] = str(company.pk)

        try:
            selected_category = parse_optional_client_category(form_values.get("client_category", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )

        try:
            discount_value = parse_admin_decimal_input(
                form_values.get('discount', '0'),
                'Descuento (%)',
                min_value='0',
                max_value='100',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )

        username = form_values.get("username", "")
        if not username:
            messages.error(request, "El nombre de usuario es obligatorio.")
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )
        username_qs = User.objects.filter(username__iexact=username)
        if client.user_id:
            username_qs = username_qs.exclude(pk=client.user_id)
        if username_qs.exists():
            messages.error(request, f'El usuario "{username}" ya existe.')
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )
        if not form_values.get("company_name", ""):
            messages.error(request, "La razon social o empresa es obligatoria.")
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )
        if not company:
            messages.error(request, "Selecciona una empresa operativa valida.")
            return _render_client_form(
                request,
                client=client,
                active_company=active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )

        selected_companies = _resolve_linked_companies(form_values, companies)
        if not selected_companies:
            messages.error(request, "Selecciona al menos una empresa habilitada para este cliente.")
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )
        if company not in selected_companies:
            messages.error(request, "La empresa en edicion tambien debe estar habilitada para el cliente.")
            return _render_client_form(
                request,
                client=client,
                active_company=company or active_company,
                companies=companies,
                client_company=client_company,
                form_values=form_values,
            )

        document_type_choices = {choice[0] for choice in ClientProfile.DOCUMENT_TYPE_CHOICES}
        client_type_choices = {choice[0] for choice in ClientProfile.CLIENT_TYPE_CHOICES}
        iva_choices = {choice[0] for choice in ClientProfile.IVA_CHOICES}
        before = {
            "user": model_snapshot(
                client.user,
                ["username", "email", "first_name", "last_name", "is_active"],
            ) if client.user_id else {},
            "client": model_snapshot(
                client,
                [
                    'company_name',
                    'cuit_dni',
                    'document_type',
                    'document_number',
                    'province',
                    'fiscal_province',
                    'fiscal_city',
                    'address',
                    'fiscal_address',
                    'postal_code',
                    'phone',
                    'discount',
                    'client_type',
                    'iva_condition',
                    'client_category_id',
                    'is_approved',
                    'notes',
                ],
            ),
            "client_company": model_snapshot(
                client_company,
                ["client_category_id", "discount_percentage", "is_active"],
            ) if client_company else {},
            "linked_companies": [
                {
                    "company_id": link.company_id,
                    "is_active": link.is_active,
                    "client_category_id": link.client_category_id,
                    "discount_percentage": str(link.discount_percentage or Decimal("0")),
                }
                for link in client.company_links.filter(company__is_active=True).order_by("company__name", "company_id")
            ],
        }

        user = client.user
        if user:
            user.username = username
            user.email = form_values.get("email", "")
            user.first_name = form_values.get("first_name", "")
            user.last_name = form_values.get("last_name", "")
            user.is_active = form_values.get("user_is_active", True)
            user.save(update_fields=["username", "email", "first_name", "last_name", "is_active"])

        client.company_name = form_values.get('company_name', '')
        client.document_type = form_values.get('document_type', '') if form_values.get("document_type", "") in document_type_choices else ''
        client.document_number = form_values.get('document_number', '')
        client.cuit_dni = form_values.get('cuit_dni', '')
        client.province = form_values.get('province', '')
        client.fiscal_province = form_values.get('fiscal_province', '')
        client.fiscal_city = form_values.get('fiscal_city', '')
        client.address = form_values.get('address', '')
        client.fiscal_address = form_values.get('fiscal_address', '')
        client.postal_code = form_values.get('postal_code', '')
        client.phone = form_values.get('phone', '')
        client.is_approved = form_values.get("client_is_approved", True)
        client.notes = form_values.get("notes", "")
        default_company = get_default_client_origin_company()
        should_update_legacy = (
            not company
            or (default_company and company and default_company.id == company.id)
        )
        if should_update_legacy:
            client.client_category = selected_category
            client.discount = (
                selected_category.discount_percentage
                if selected_category
                else discount_value
            )
        client.client_type = form_values.get('client_type', '') if form_values.get("client_type", "") in client_type_choices else ''
        client.iva_condition = form_values.get('iva_condition', '') if form_values.get("iva_condition", "") in iva_choices else ''
        client.save()

        link = None
        selected_company_ids = {linked_company.id for linked_company in selected_companies}
        ClientCompany.objects.filter(
            client_profile=client,
            company__is_active=True,
        ).exclude(company_id__in=selected_company_ids).update(is_active=False)

        for linked_company in selected_companies:
            current_link, created = ClientCompany.objects.get_or_create(
                client_profile=client,
                company=linked_company,
                defaults={
                    "is_active": True,
                    "client_category": selected_category,
                    "discount_percentage": (
                        selected_category.discount_percentage if selected_category else discount_value
                    ),
                },
            )
            if linked_company.id == company.id:
                current_link.client_category = selected_category
                current_link.discount_percentage = (
                    selected_category.discount_percentage
                    if selected_category
                    else discount_value
                )
            elif created:
                current_link.client_category = selected_category
                current_link.discount_percentage = (
                    selected_category.discount_percentage
                    if selected_category
                    else discount_value
                )
            current_link.is_active = True
            current_link.save()
            if linked_company.id == company.id:
                link = current_link
        after = {
            "user": model_snapshot(
                client.user,
                ["username", "email", "first_name", "last_name", "is_active"],
            ) if client.user_id else {},
            "client": model_snapshot(
                client,
                [
                    'company_name',
                    'cuit_dni',
                    'document_type',
                    'document_number',
                    'province',
                    'fiscal_province',
                    'fiscal_city',
                    'address',
                    'fiscal_address',
                    'postal_code',
                    'phone',
                    'discount',
                    'client_type',
                    'iva_condition',
                    'client_category_id',
                    'is_approved',
                    'notes',
                ],
            ),
            "client_company": model_snapshot(
                link,
                ["client_category_id", "discount_percentage", "is_active"],
            ) if link else {},
            "linked_companies": [
                {
                    "company_id": company_link.company_id,
                    "is_active": company_link.is_active,
                    "client_category_id": company_link.client_category_id,
                    "discount_percentage": str(company_link.discount_percentage or Decimal("0")),
                }
                for company_link in client.company_links.filter(company__is_active=True).order_by("company__name", "company_id")
            ],
        }
        log_admin_change(
            request,
            action='client_update',
            target_type='client_profile',
            target_id=client.pk,
            before=before,
            after=after,
            extra={
                'username': client.user.username if client.user_id else '',
                'company_id': company.pk,
            },
        )
        
        if selected_category:
            messages.success(
                request,
                f'Cliente "{client.company_name}" actualizado con categoria "{selected_category.name}".',
            )
        else:
            messages.success(request, f'Cliente "{client.company_name}" actualizado.')
        return redirect(f"{reverse('admin_client_edit', args=[client.pk])}?company_id={company.pk}")
    
    return _render_client_form(
        request,
        client=client,
        active_company=active_company,
        companies=companies,
        client_company=client_company,
    )


@staff_member_required
@require_POST
def client_quick_order(request, pk):
    client = get_object_or_404(ClientProfile.objects.select_related('user'), pk=pk)
    action = request.POST.get("action", "quote").strip().lower()
    active_company = get_admin_selected_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa valida para crear el documento.")
        return _redirect_client_history(client)

    client_company = client.get_company_link(active_company)
    if not client_company:
        messages.error(request, "El cliente no tiene relacion comercial activa con esta empresa.")
        return _redirect_client_history(client, active_company)

    selected_sales_document_type = None
    selected_sales_document_type_id = str(request.POST.get("sales_document_type_id", "")).strip()
    if selected_sales_document_type_id.isdigit():
        selected_sales_document_type = SalesDocumentType.objects.filter(
            pk=int(selected_sales_document_type_id),
            company=active_company,
            enabled=True,
        ).first()
        if not selected_sales_document_type:
            messages.error(request, "El tipo comercial seleccionado no esta disponible para esta empresa.")
            return _redirect_client_history(client, active_company)
        action = {
            SALES_BEHAVIOR_COTIZACION: "quote",
            SALES_BEHAVIOR_PRESUPUESTO: "quote",
            SALES_BEHAVIOR_PEDIDO: "order",
            SALES_BEHAVIOR_REMITO: "remito",
            SALES_BEHAVIOR_FACTURA: "invoice",
            SALES_BEHAVIOR_NOTA_CREDITO: "credit_note",
        }.get(selected_sales_document_type.document_behavior, action)

    if action not in {"quote", "order", "remito", "invoice", "credit_note"}:
        messages.error(request, "Accion rapida invalida.")
        return _redirect_client_history(client, active_company)

    if action == "remito":
        from core.services.documents import ensure_document_for_order

        orders_qs = _get_client_orders_queryset(client, company=active_company).order_by(
            "-status_updated_at",
            "-created_at",
        )
        remito_order = orders_qs.filter(status__in=CLIENT_REMITO_READY_STATUSES).first()
        if remito_order:
            remito_document = (
                InternalDocument.objects.filter(
                    company=active_company,
                    order=remito_order,
                    doc_type=DocumentSeries.DOC_REM,
                )
                .order_by("-issued_at", "-id")
                .first()
            )
            if not remito_document:
                if selected_sales_document_type:
                    try:
                        remito_document, _ = create_internal_document_from_sales_type(
                            order=remito_order,
                            sales_document_type=selected_sales_document_type,
                            actor=request.user,
                        )
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("admin_order_detail", pk=remito_order.pk)
                else:
                    remito_document = ensure_document_for_order(remito_order, doc_type=DocumentSeries.DOC_REM)
            if remito_document:
                messages.success(
                    request,
                    f"Se abrio el remito mas reciente del cliente (pedido #{remito_order.pk}).",
                )
                print_url = (
                    f"{reverse('admin_internal_document_print', args=[remito_document.pk])}"
                    f"?{urlencode({'copy': 'original'})}"
                )
                return redirect(print_url)

            messages.info(request, f"Se abrio el pedido #{remito_order.pk} para revisar el remito.")
            return redirect("admin_order_detail", pk=remito_order.pk)

        pending_order = orders_qs.filter(
            status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]
        ).first()
        if pending_order:
            messages.warning(
                request,
                f"No hay pedidos enviados o entregados. Se abrio el pedido #{pending_order.pk} para avanzar a remito.",
            )
            return redirect("admin_order_detail", pk=pending_order.pk)

        messages.error(request, "No hay pedidos del cliente listos para remito en esta empresa.")
        return _redirect_client_history(client, active_company)

    if action == "invoice":
        facturable_orders = list(
            _get_client_orders_queryset(client, company=active_company)
            .filter(status__in=CLIENT_FACTURABLE_STATUSES)
            .order_by("-status_updated_at", "-created_at")[:30]
        )
        order_ids = [order.pk for order in facturable_orders]
        invoice_docs_by_order = {}
        if order_ids:
            for document in (
                FiscalDocument.objects.select_related("point_of_sale")
                .filter(company=active_company, order_id__in=order_ids, doc_type__in=["FA", "FB"])
                .exclude(status="voided")
                .order_by("-created_at", "-id")
            ):
                invoice_docs_by_order.setdefault(document.order_id, document)

        for order in facturable_orders:
            if order.pk in invoice_docs_by_order:
                continue
            if order.saas_document_type or order.saas_document_number:
                continue

            invoice_ready, invoice_errors = is_invoice_ready(order)
            if invoice_ready and selected_sales_document_type:
                try:
                    fiscal_doc, _ = create_fiscal_document_from_sales_type(
                        order=order,
                        sales_document_type=selected_sales_document_type,
                        actor=request.user,
                    )
                except ValidationError as exc:
                    errors_preview = "; ".join(exc.messages[:2])
                    messages.warning(
                        request,
                        f"Se abrio el pedido #{order.pk}. No se pudo emitir {selected_sales_document_type.name}: {errors_preview}",
                    )
                    return redirect("admin_order_detail", pk=order.pk)
                messages.success(
                    request,
                    f"Se genero {fiscal_doc.commercial_type_label} para el pedido #{order.pk}.",
                )
                return redirect("admin_fiscal_document_detail", pk=fiscal_doc.pk)
            if invoice_ready:
                messages.success(
                    request,
                    f"Se abrio el pedido #{order.pk} para crear la factura del cliente.",
                )
            else:
                errors_preview = "; ".join(invoice_errors[:2])
                messages.warning(
                    request,
                    f"Se abrio el pedido #{order.pk}. Antes de facturar faltan datos: {errors_preview}",
                )
            return redirect("admin_order_detail", pk=order.pk)

        existing_invoice = next(
            (invoice_docs_by_order.get(order.pk) for order in facturable_orders if invoice_docs_by_order.get(order.pk)),
            None,
        )
        if existing_invoice:
            messages.info(
                request,
                "El pedido facturable mas reciente ya tiene comprobante fiscal. Se abrio el documento existente.",
            )
            return redirect("admin_fiscal_document_detail", pk=existing_invoice.pk)

        existing_saas_order = next(
            (
                order
                for order in facturable_orders
                if order.saas_document_type or order.saas_document_number
            ),
            None,
        )
        if existing_saas_order:
            messages.info(
                request,
                f"El pedido facturable mas reciente ya tiene comprobante externo. Se abrio el pedido #{existing_saas_order.pk}.",
            )
            return redirect("admin_order_detail", pk=existing_saas_order.pk)

        messages.error(request, "No hay pedidos del cliente listos para facturar en esta empresa.")
        return _redirect_client_history(client, active_company)

    if action == "credit_note":
        latest_invoice_document = (
            FiscalDocument.objects.select_related("order", "point_of_sale", "related_document")
            .filter(
                company=active_company,
                doc_type__in=["FA", "FB"],
            )
            .filter(
                Q(client_profile=client) | Q(client_company_ref__client_profile=client)
            )
            .exclude(status="voided")
            .order_by("-created_at", "-id")
            .first()
        )
        if latest_invoice_document:
            messages.success(
                request,
                "Se abrio el comprobante base mas reciente para gestionar la nota de credito.",
            )
            return redirect("admin_fiscal_document_detail", pk=latest_invoice_document.pk)

        saas_invoice_order = (
            _get_client_orders_queryset(client, company=active_company)
            .filter(Q(saas_document_type__gt="") | Q(saas_document_number__gt=""))
            .order_by("-created_at")
            .first()
        )
        if saas_invoice_order:
            messages.info(
                request,
                f"El cliente solo tiene comprobantes externos recientes. Se abrio el pedido #{saas_invoice_order.pk}.",
            )
            return redirect("admin_order_detail", pk=saas_invoice_order.pk)

        messages.error(
            request,
            "No hay comprobantes fiscales del cliente para usar como base de nota de credito.",
        )
        return _redirect_client_history(client, active_company)

    try:
        from core.services.pricing import (
            resolve_pricing_context,
            resolve_effective_discount_percentage,
            resolve_effective_price_list,
        )

        _, _, client_category = resolve_pricing_context(client.user, active_company)
        discount_percentage = resolve_effective_discount_percentage(
            client_profile=client,
            company=active_company,
            client_company=client_company,
            client_category=client_category,
        )
        price_list = resolve_effective_price_list(active_company, client_company, client_category)
    except Exception:
        discount_percentage = Decimal("0")
        price_list = None

    created_label = (
        selected_sales_document_type.name
        if selected_sales_document_type
        else ("Cotizacion" if action == "quote" else "Pedido")
    )
    order = Order.objects.create(
        user=client.user,
        company=active_company,
        status=Order.STATUS_DRAFT,
        priority=Order.PRIORITY_NORMAL,
        notes="",
        admin_notes=f"{created_label} creada desde ficha cliente.",
        subtotal=Decimal("0.00"),
        discount_percentage=discount_percentage,
        discount_amount=Decimal("0.00"),
        total=Decimal("0.00"),
        client_company=client.company_name or "",
        client_cuit=client.cuit_dni or "",
        client_address=client.address or "",
        client_phone=client.phone or "",
        client_company_ref=client_company,
        saas_document_type="",
        saas_document_number="",
        saas_document_cae="",
        follow_up_note="",
    )
    OrderStatusHistory.objects.create(
        order=order,
        from_status="",
        to_status=order.status,
        changed_by=request.user if request.user.is_authenticated else None,
        note=f"{created_label} creado desde ficha cliente",
    )
    if price_list:
        order.admin_notes = f"{order.admin_notes} Lista: {price_list.name}"
        order.save(update_fields=["admin_notes", "updated_at"])

    if (
        selected_sales_document_type
        and selected_sales_document_type.billing_mode == "INTERNAL_DOCUMENT"
        and selected_sales_document_type.internal_doc_type == DocumentSeries.DOC_COT
    ):
        try:
            create_internal_document_from_sales_type(
                order=order,
                sales_document_type=selected_sales_document_type,
                actor=request.user,
            )
        except ValidationError:
            pass

    messages.success(request, f"{created_label} creada. Ahora podes cargar productos.")
    return redirect("admin_order_detail", pk=order.pk)


@staff_member_required
def client_cuit_lookup(request):
    cuit = request.GET.get("cuit", "").strip()
    if not cuit:
        return JsonResponse({"ok": False, "message": "CUIT requerido."}, status=400)
    return JsonResponse(
        {
            "ok": False,
            "message": "Autocompletado fiscal no disponible. Integracion pendiente.",
        }
    )


@staff_member_required
def client_order_history(request, pk):
    """Show order history for one client profile."""
    client = get_object_or_404(ClientProfile.objects.select_related('user'), pk=pk)
    history_base_url = reverse("admin_client_order_history", args=[client.pk])

    def build_history_url(**updates):
        params = request.GET.copy()
        for key, value in updates.items():
            if value in (None, ""):
                params.pop(key, None)
            else:
                params[key] = str(value)
        encoded = params.urlencode()
        return f"{history_base_url}?{encoded}" if encoded else history_base_url

    companies = Company.objects.filter(is_active=True).order_by("name")
    active_company = get_admin_company_filter(request)
    selected_company_id = "all" if active_company is None else str(active_company.pk)
    active_company_label = active_company.name if active_company else "Todas las empresas"
    client_company = None
    if active_company:
        client_company = (
            ClientCompany.objects.select_related("company", "client_category", "price_list")
            .filter(client_profile=client, company=active_company)
            .first()
        )
    client_company_missing = bool(active_company and not client_company)
    operations_locked = bool(not active_company or client_company_missing)
    effective_category = (
        client_company.client_category
        if client_company and client_company.client_category_id
        else client.client_category
    )
    effective_discount = client.get_effective_discount_percentage(company=active_company)
    try:
        from core.services.pricing import resolve_pricing_context, resolve_effective_price_list

        _, _, client_category = resolve_pricing_context(client.user, active_company)
        effective_price_list = resolve_effective_price_list(active_company, client_company, client_category)
    except Exception:
        effective_price_list = None
    sale_condition_label = "-"
    if effective_category and getattr(effective_category, "default_sale_condition", None):
        sale_condition_label = dict(ClientCategory.SALE_CONDITION_CHOICES).get(
            effective_category.default_sale_condition,
            effective_category.default_sale_condition,
        )

    orders = _get_client_orders_queryset(client, company=active_company).prefetch_related('items')

    status = request.GET.get('status', '').strip()
    orders_filtered = orders
    if status:
        orders_filtered = orders_filtered.filter(status=status)

    summary = orders_filtered.aggregate(
        orders_count=Count('id'),
        total_amount=Sum('total'),
        avg_ticket=Avg('total'),
        last_order_at=Max('created_at'),
    )
    open_orders_count = orders.filter(
        status__in=[Order.STATUS_DRAFT, Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]
    ).count()

    balance_orders_qs = client.get_orders_queryset_for_balance(company=active_company)
    balance_orders_summary = balance_orders_qs.aggregate(
        orders_count=Count('id'),
        total_amount=Sum('total'),
        last_order_at=Max('created_at'),
    )

    paginator = Paginator(orders_filtered.order_by('-created_at'), 30)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    payments_qs = ClientPayment.objects.select_related('order', 'created_by', 'company').filter(
        client_profile=client,
        is_cancelled=False,
    )
    if active_company:
        payments_qs = payments_qs.filter(company=active_company)
    payments_summary = payments_qs.aggregate(
        total_paid=Sum('amount'),
        payments_count=Count('id'),
        last_payment_at=Max('paid_at'),
    )
    payments_recent = list(payments_qs.order_by('-paid_at')[:20])

    documents_qs = InternalDocument.objects.filter(
        Q(client_profile=client) | Q(client_company_ref__client_profile=client)
    )
    if active_company:
        documents_qs = documents_qs.filter(company=active_company)
    documents_qs = documents_qs.select_related("company", "sales_document_type").order_by("-issued_at")
    recent_internal_documents = list(documents_qs[:16])
    documents_by_type = {
        "COT": list(documents_qs.filter(doc_type="COT")[:10]),
        "PED": list(documents_qs.filter(doc_type="PED")[:10]),
        "REM": list(documents_qs.filter(doc_type="REM")[:10]),
        "REC": list(documents_qs.filter(doc_type="REC")[:10]),
        "AJU": list(documents_qs.filter(doc_type="AJU")[:10]),
    }

    official_docs_qs = Order.objects.filter(
        user_id=client.user_id,
    ).exclude(
        saas_document_number=""
    )
    if active_company:
        official_docs_qs = official_docs_qs.filter(company=active_company)
    official_docs = list(
        official_docs_qs.only(
            "id",
            "saas_document_type",
            "saas_document_number",
            "saas_document_date",
            "total",
        ).order_by("-created_at")[:10]
    )

    fiscal_documents_qs = (
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "sales_document_type",
            "order",
            "related_document",
        )
        .filter(Q(client_profile=client) | Q(client_company_ref__client_profile=client))
        .order_by("-created_at", "-id")
    )
    if active_company:
        fiscal_documents_qs = fiscal_documents_qs.filter(company=active_company)
    recent_fiscal_documents = list(fiscal_documents_qs[:12])
    latest_internal_document = recent_internal_documents[0] if recent_internal_documents else None
    latest_fiscal_document = recent_fiscal_documents[0] if recent_fiscal_documents else None

    ledger_rows = []
    ledger_warning = ""
    running_balance = Decimal('0.00')
    payment_history_rows = []
    try:
        ledger_entries = list(
            client.get_ledger_queryset(company=active_company)
            .select_related('order', 'payment', 'payment__order', 'created_by', 'company')
        )
        ledger_order_ids = {tx.order_id for tx in ledger_entries if tx.order_id}
        ledger_payment_ids = {tx.payment_id for tx in ledger_entries if tx.payment_id}
        ledger_transaction_ids = {tx.pk for tx in ledger_entries}

        related_internal_documents = InternalDocument.objects.none()
        if ledger_order_ids or ledger_payment_ids or ledger_transaction_ids:
            related_internal_documents = InternalDocument.objects.select_related(
                "company",
                "sales_document_type",
            ).filter(
                Q(order_id__in=ledger_order_ids)
                | Q(payment_id__in=ledger_payment_ids)
                | Q(transaction_id__in=ledger_transaction_ids)
            )
            if active_company:
                related_internal_documents = related_internal_documents.filter(company=active_company)
            related_internal_documents = related_internal_documents.order_by("-issued_at", "-id")

        related_fiscal_documents = FiscalDocument.objects.none()
        if ledger_order_ids:
            related_fiscal_documents = FiscalDocument.objects.select_related(
                "company",
                "point_of_sale",
                "sales_document_type",
            ).filter(order_id__in=ledger_order_ids)
            if active_company:
                related_fiscal_documents = related_fiscal_documents.filter(company=active_company)
            related_fiscal_documents = related_fiscal_documents.exclude(status="voided").order_by("-created_at", "-id")

        receipt_documents_by_payment = {}
        adjustment_documents_by_transaction = {}
        remito_documents_by_order = {}
        quote_documents_by_order = {}
        order_documents_by_order = {}
        for doc in related_internal_documents:
            if doc.payment_id and doc.doc_type == DocumentSeries.DOC_REC:
                receipt_documents_by_payment.setdefault(doc.payment_id, doc)
            if doc.transaction_id and doc.doc_type == DocumentSeries.DOC_AJU:
                adjustment_documents_by_transaction.setdefault(doc.transaction_id, doc)
            if doc.order_id and doc.doc_type == DocumentSeries.DOC_REM:
                remito_documents_by_order.setdefault(doc.order_id, doc)
            if doc.order_id and doc.doc_type == DocumentSeries.DOC_COT:
                quote_documents_by_order.setdefault(doc.order_id, doc)
            if doc.order_id and doc.doc_type == DocumentSeries.DOC_PED:
                order_documents_by_order.setdefault(doc.order_id, doc)

        fiscal_documents_by_order = {}
        for doc in related_fiscal_documents:
            if doc.order_id:
                fiscal_documents_by_order.setdefault(doc.order_id, doc)

        for tx in ledger_entries:
            running_balance += tx.amount
            debit = tx.amount if tx.amount > 0 else Decimal('0.00')
            credit = abs(tx.amount) if tx.amount < 0 else Decimal('0.00')
            doc_category = 'account'
            order_status = ''
            type_family_label = ""
            source_origin_label = "Movimiento"
            source_document = None

            if tx.transaction_type == ClientTransaction.TYPE_PAYMENT:
                doc_category = 'payment'
                source_document = receipt_documents_by_payment.get(tx.payment_id)
                payment_order = getattr(getattr(tx, "payment", None), "order", None)
                linked_invoice = fiscal_documents_by_order.get(payment_order.pk) if payment_order else None
                if source_document:
                    type_label = source_document.commercial_type_label
                    type_family_label = source_document.get_doc_type_display()
                    number_label = source_document.display_number
                    source_origin_label = "Interno"
                else:
                    type_label = 'Recibo de pago'
                    number_label = f'RP{tx.payment_id:07d}' if tx.payment_id else '-'
                    source_origin_label = "Pago"
            elif tx.transaction_type == ClientTransaction.TYPE_ORDER_CHARGE:
                order_obj = tx.order
                if order_obj:
                    order_status = order_obj.status
                    source_document = fiscal_documents_by_order.get(order_obj.pk)
                    if source_document:
                        doc_category = 'invoice'
                        type_label = source_document.commercial_type_label
                        type_family_label = source_document.get_doc_type_display()
                        number_label = source_document.display_number
                        source_origin_label = "Fiscal"
                    elif order_obj.saas_document_number or order_obj.saas_document_type:
                        doc_category = 'invoice'
                        type_label = order_obj.saas_document_type or 'Factura'
                        number_label = order_obj.saas_document_number or f'FC{order_obj.pk:07d}'
                        source_origin_label = "SaaS"
                    elif order_obj.pk in remito_documents_by_order:
                        source_document = remito_documents_by_order.get(order_obj.pk)
                        doc_category = 'remito'
                        type_label = source_document.commercial_type_label
                        type_family_label = source_document.get_doc_type_display()
                        number_label = source_document.display_number
                        source_origin_label = "Interno"
                    elif order_obj.pk in quote_documents_by_order or order_obj.status == Order.STATUS_DRAFT:
                        source_document = quote_documents_by_order.get(order_obj.pk)
                        doc_category = 'quote'
                        if source_document:
                            type_label = source_document.commercial_type_label
                            type_family_label = source_document.get_doc_type_display()
                            number_label = source_document.display_number
                            source_origin_label = "Interno"
                        else:
                            type_label = 'Cotizacion'
                            number_label = f'CT{order_obj.pk:07d}'
                            source_origin_label = "Pedido"
                    elif order_obj.pk in order_documents_by_order:
                        source_document = order_documents_by_order.get(order_obj.pk)
                        doc_category = 'order'
                        type_label = source_document.commercial_type_label
                        type_family_label = source_document.get_doc_type_display()
                        number_label = source_document.display_number
                        source_origin_label = "Interno"
                    else:
                        doc_category = 'order'
                        type_label = 'Pedido'
                        number_label = f'PD{order_obj.pk:07d}'
                        source_origin_label = "Pedido"
                else:
                    doc_category = 'order'
                    type_label = 'Pedido'
                    number_label = f'PD{tx.order_id:07d}' if tx.order_id else '-'
                    source_origin_label = "Pedido"
            else:
                source_document = adjustment_documents_by_transaction.get(tx.pk)
                if source_document:
                    type_label = source_document.commercial_type_label
                    type_family_label = source_document.get_doc_type_display()
                    number_label = source_document.display_number
                    source_origin_label = "Interno"
                else:
                    type_label = 'Ajuste'
                    number_label = f'AJ{tx.pk:07d}'

            document_url = ""
            document_action_label = ""
            document_target_blank = False
            if isinstance(source_document, InternalDocument):
                document_url = f"{reverse('admin_internal_document_print', args=[source_document.pk])}?copy=original"
                document_action_label = "Documento"
                document_target_blank = True
            elif isinstance(source_document, FiscalDocument):
                document_url = reverse("admin_fiscal_document_detail", args=[source_document.pk])
                document_action_label = "Comprobante"

            detail_url = ""
            detail_label = ""
            if tx.order_id:
                detail_url = reverse("admin_order_detail", args=[tx.order_id])
                detail_label = f"Pedido #{tx.order_id}"
            elif tx.payment_id:
                detail_url = (
                    f"{reverse('admin_payment_list')}?"
                    f"{urlencode({'client_id': client.pk, 'company_id': selected_company_id})}"
                )
                detail_label = f"Pago #{tx.payment_id}"

            if tx.order_id:
                if doc_category == 'invoice' and isinstance(source_document, FiscalDocument):
                    reference_title = "Venta facturada"
                    reference_meta_parts = [
                        f"{source_document.commercial_type_label} {source_document.display_number}",
                        f"Pedido #{tx.order_id}",
                        getattr(tx.company, "name", "") or "-",
                    ]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
                elif doc_category == 'invoice' and source_origin_label == "SaaS":
                    reference_title = "Venta facturada en SaaS"
                    reference_meta_parts = [
                        f"{type_label} {number_label}",
                        f"Pedido #{tx.order_id}",
                        getattr(tx.company, "name", "") or "-",
                    ]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
                elif doc_category == 'remito' and isinstance(source_document, InternalDocument):
                    reference_title = "Venta remitida"
                    reference_meta_parts = [
                        f"{source_document.commercial_type_label} {source_document.display_number}",
                        f"Pedido #{tx.order_id}",
                        getattr(tx.company, "name", "") or "-",
                    ]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
                elif doc_category == 'quote':
                    reference_title = "Cotizacion comercial"
                    reference_meta_parts = [f"Pedido #{tx.order_id}", getattr(tx.company, "name", "") or "-"]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
                else:
                    reference_title = f"Pedido #{tx.order_id}"
                    reference_meta_parts = [getattr(tx.company, "name", "") or "-"]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
            elif tx.payment_id:
                payment_obj = getattr(tx, "payment", None)
                payment_order = getattr(payment_obj, "order", None)
                linked_invoice = fiscal_documents_by_order.get(payment_order.pk) if payment_order else None
                if linked_invoice:
                    reference_title = f"Pago aplicado a {linked_invoice.commercial_type_label}"
                    reference_meta_parts = [
                        linked_invoice.display_number,
                        f"Pedido #{payment_order.pk}",
                        getattr(tx.company, "name", "") or "-",
                    ]
                else:
                    reference_title = getattr(payment_obj, "reference", "") or tx.description or f"Pago #{tx.payment_id}"
                    reference_meta_parts = [getattr(tx.company, "name", "") or "-"]
                    payment_order_id = getattr(payment_obj, "order_id", None)
                    if payment_order_id:
                        reference_meta_parts.append(f"Pedido #{payment_order_id}")
                reference_meta = " | ".join(part for part in reference_meta_parts if part)
            else:
                reference_title = tx.description or "Ajuste manual"
                reference_meta = getattr(tx.company, "name", "") or "-"

            ledger_rows.append({
                'tx': tx,
                'debit': debit,
                'credit': credit,
                'running_balance': running_balance,
                'type_label': type_label,
                'type_family_label': type_family_label,
                'number_label': number_label,
                'doc_category': doc_category,
                'order_status': order_status,
                'source_origin_label': source_origin_label,
                'reference_title': reference_title,
                'reference_meta': reference_meta,
                'document_url': document_url,
                'document_action_label': document_action_label,
                'document_target_blank': document_target_blank,
                'detail_url': detail_url,
                'detail_label': detail_label,
            })

        for payment in payments_recent:
            receipt_document = receipt_documents_by_payment.get(payment.pk)
            linked_invoice = fiscal_documents_by_order.get(payment.order_id) if payment.order_id else None
            receipt_label = receipt_document.commercial_type_label if receipt_document else "Recibo de pago"
            receipt_number = (
                receipt_document.display_number
                if receipt_document
                else (f"RP{payment.pk:07d}" if payment.pk else "-")
            )
            if linked_invoice:
                applied_title = f"{linked_invoice.commercial_type_label} {linked_invoice.display_number}"
                applied_meta_parts = ["Factura vinculada", getattr(payment.company, "name", "") or "-"]
                if payment.order_id:
                    applied_meta_parts.append(f"Pedido #{payment.order_id}")
                applied_meta = " | ".join(part for part in applied_meta_parts if part)
                applied_url = reverse("admin_fiscal_document_detail", args=[linked_invoice.pk])
                applied_label = "Factura"
            elif payment.order_id:
                applied_title = f"Pedido #{payment.order_id}"
                applied_meta_parts = ["Aplicado a pedido", getattr(payment.company, "name", "") or "-"]
                if payment.order:
                    applied_meta_parts.append(payment.order.get_status_display())
                applied_meta = " | ".join(part for part in applied_meta_parts if part)
                applied_url = reverse("admin_order_detail", args=[payment.order_id])
                applied_label = "Pedido"
            else:
                applied_title = "Pago a cuenta"
                applied_meta = getattr(payment.company, "name", "") or "-"
                applied_url = ""
                applied_label = "Cuenta corriente"

            payment_history_rows.append({
                "payment": payment,
                "receipt_label": receipt_label,
                "receipt_number": receipt_number,
                "receipt_origin_label": "Interno" if receipt_document else "Pago",
                "applied_title": applied_title,
                "applied_meta": applied_meta,
                "applied_url": applied_url,
                "applied_label": applied_label,
                "document_url": (
                    f"{reverse('admin_internal_document_print', args=[receipt_document.pk])}?copy=original"
                    if receipt_document
                    else ""
                ),
            })
    except DatabaseError as exc:
        ledger_warning = "La cuenta corriente no pudo cargarse en este entorno."
        logger.warning(
            "Client history ledger unavailable for client %s and company %s: %s",
            client.pk,
            getattr(active_company, "pk", None),
            exc,
        )

    ledger_tab = request.GET.get('ledger_tab', 'account').strip().lower()
    valid_ledger_tabs = {'account', 'payments', 'orders', 'invoices', 'remitos', 'quotes'}
    if ledger_tab not in valid_ledger_tabs:
        ledger_tab = 'account'

    if ledger_tab == 'payments':
        ledger_rows_filtered = [row for row in ledger_rows if row['doc_category'] == 'payment']
    elif ledger_tab == 'orders':
        ledger_rows_filtered = [row for row in ledger_rows if row['doc_category'] == 'order']
        if status:
            ledger_rows_filtered = [
                row
                for row in ledger_rows_filtered
                if row['order_status'] == status
            ]
    elif ledger_tab == 'invoices':
        ledger_rows_filtered = [row for row in ledger_rows if row['doc_category'] == 'invoice']
    elif ledger_tab == 'remitos':
        ledger_rows_filtered = [row for row in ledger_rows if row['doc_category'] == 'remito']
    elif ledger_tab == 'quotes':
        ledger_rows_filtered = [row for row in ledger_rows if row['doc_category'] == 'quote']
    else:
        ledger_rows_filtered = ledger_rows

    ledger_show_all = request.GET.get('show_all') == '1'
    limit_raw = request.GET.get('limit', '80').strip()
    try:
        ledger_limit = max(20, min(int(limit_raw), 500))
    except ValueError:
        ledger_limit = 80
    if request.GET.get('more') == '1':
        ledger_limit = min(ledger_limit + 80, 500)
    if ledger_show_all:
        ledger_rows_visible = ledger_rows_filtered
    else:
        ledger_rows_visible = ledger_rows_filtered[:ledger_limit]
    ledger_hidden_count = max(0, len(ledger_rows_filtered) - len(ledger_rows_visible))
    open_orders = list(
        orders.filter(
            status__in=[
                Order.STATUS_DRAFT,
                Order.STATUS_CONFIRMED,
                Order.STATUS_PREPARING,
                Order.STATUS_SHIPPED,
            ]
        )
        .order_by('-created_at')[:8]
    )
    _annotate_client_orders_with_documents(page_obj.object_list, active_company)
    _annotate_client_orders_with_documents(open_orders, active_company)

    if active_company:
        quick_remito_available = (
            orders.filter(status__in=CLIENT_REMITO_READY_STATUSES).exists()
            or orders.filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]).exists()
        )
        quick_invoice_available = (
            orders.filter(status__in=CLIENT_FACTURABLE_STATUSES).exists()
            or fiscal_documents_qs.filter(doc_type__in=["FA", "FB"]).exclude(status="voided").exists()
            or official_docs_qs.exists()
        )
        quick_credit_note_available = (
            fiscal_documents_qs.filter(doc_type__in=["FA", "FB"]).exclude(status="voided").exists()
            or official_docs_qs.exists()
        )
    else:
        quick_remito_available = False
        quick_invoice_available = False
        quick_credit_note_available = False

    quick_sales_document_actions = []
    if active_company:
        quick_type_queryset = SalesDocumentType.objects.filter(
            company=active_company,
            enabled=True,
            document_behavior__in=[
                SALES_BEHAVIOR_RECIBO,
                SALES_BEHAVIOR_COTIZACION,
                SALES_BEHAVIOR_PRESUPUESTO,
                SALES_BEHAVIOR_PEDIDO,
                SALES_BEHAVIOR_REMITO,
                SALES_BEHAVIOR_FACTURA,
                SALES_BEHAVIOR_NOTA_CREDITO,
                SALES_BEHAVIOR_NOTA_DEBITO,
            ],
        ).order_by("display_order", "name")
        for item in quick_type_queryset:
            behavior = item.document_behavior
            action_meta = {
                "sales_document_type": item,
                "label": item.name,
                "help_text": "",
                "method": "post",
                "url": reverse("admin_client_quick_order", args=[client.pk]),
                "action_value": "",
                "disabled": operations_locked,
                "target_blank": False,
                "css_class": "",
            }
            if behavior in {SALES_BEHAVIOR_COTIZACION, SALES_BEHAVIOR_PRESUPUESTO}:
                action_meta["action_value"] = "quote"
                action_meta["help_text"] = "Crea un borrador comercial desde la ficha del cliente."
                action_meta["css_class"] = "is-quote"
            elif behavior == SALES_BEHAVIOR_PEDIDO:
                action_meta["action_value"] = "order"
                action_meta["help_text"] = "Abre un pedido nuevo para cargar productos."
                action_meta["css_class"] = "is-order"
            elif behavior == SALES_BEHAVIOR_REMITO:
                action_meta["action_value"] = "remito"
                action_meta["help_text"] = "Busca el pedido mas reciente listo para remito."
                action_meta["disabled"] = operations_locked or not quick_remito_available
                action_meta["css_class"] = "is-remito"
            elif behavior == SALES_BEHAVIOR_FACTURA:
                action_meta["action_value"] = "invoice"
                action_meta["help_text"] = "Usa el pedido facturable mas reciente y aplica el tipo elegido."
                action_meta["disabled"] = operations_locked or not quick_invoice_available
                action_meta["css_class"] = "is-fiscal"
            elif behavior == SALES_BEHAVIOR_NOTA_CREDITO:
                action_meta["action_value"] = "credit_note"
                action_meta["help_text"] = "Abre el comprobante base mas reciente para gestionar la nota."
                action_meta["disabled"] = operations_locked or not quick_credit_note_available
                action_meta["css_class"] = "is-credit-note"
            elif behavior == SALES_BEHAVIOR_RECIBO:
                action_meta["method"] = "get"
                action_meta["url"] = (
                    f"{reverse('admin_payment_list')}?"
                    f"{urlencode({'client_id': client.pk, 'company_id': selected_company_id, 'sales_document_type_id': item.pk, 'suggested_action': 'create'})}"
                )
                action_meta["help_text"] = "Abre pagos con este tipo comercial preseleccionado."
                action_meta["css_class"] = "is-payment"
            elif behavior == SALES_BEHAVIOR_NOTA_DEBITO:
                action_meta["method"] = "get"
                action_meta["url"] = (
                    f"{reverse('admin_payment_list')}?"
                    f"{urlencode({'client_id': client.pk, 'company_id': selected_company_id, 'sales_document_type_id': item.pk, 'suggested_action': 'adjust'})}"
                )
                action_meta["help_text"] = "Abre ajustes de cuenta con este tipo comercial."
                action_meta["css_class"] = "is-adjustment"
            else:
                continue
            quick_sales_document_actions.append(action_meta)

    client_tab = request.GET.get('client_tab', 'account').strip().lower()
    valid_client_tabs = {'overview', 'account', 'orders', 'payments', 'documents'}
    if client_tab not in valid_client_tabs:
        client_tab = 'account'

    client_tabs = [
        {
            'key': 'overview',
            'label': 'Resumen',
            'is_active': client_tab == 'overview',
            'url': build_history_url(
                client_tab='overview',
                page=None,
                status=None,
                ledger_tab=None,
                more=None,
                show_all=None,
            ),
        },
        {
            'key': 'account',
            'label': 'Cuenta corriente',
            'is_active': client_tab == 'account',
            'url': build_history_url(
                client_tab='account',
                page=None,
                more=None,
                show_all=None,
            ),
        },
        {
            'key': 'orders',
            'label': 'Pedidos',
            'is_active': client_tab == 'orders',
            'url': build_history_url(
                client_tab='orders',
                page=None,
                ledger_tab=None,
                more=None,
                show_all=None,
            ),
        },
        {
            'key': 'payments',
            'label': 'Pagos',
            'is_active': client_tab == 'payments',
            'url': build_history_url(
                client_tab='payments',
                page=None,
                status=None,
                ledger_tab=None,
                more=None,
                show_all=None,
            ),
        },
        {
            'key': 'documents',
            'label': 'Documentos',
            'is_active': client_tab == 'documents',
            'url': build_history_url(
                client_tab='documents',
                page=None,
                status=None,
                ledger_tab=None,
                more=None,
                show_all=None,
            ),
        },
    ]

    ledger_tabs_ui = [
        {
            'key': item['key'],
            'label': item['label'],
            'is_active': ledger_tab == item['key'],
            'url': build_history_url(
                client_tab='account',
                ledger_tab=item['key'],
                page=None,
                more=None,
                show_all=None,
            ),
        }
        for item in [
            {'key': 'account', 'label': 'Cuenta Corriente'},
            {'key': 'payments', 'label': 'Pagos'},
            {'key': 'invoices', 'label': 'Facturas'},
            {'key': 'remitos', 'label': 'Remitos'},
            {'key': 'orders', 'label': 'Pedidos'},
            {'key': 'quotes', 'label': 'Presupuestos'},
        ]
    ]

    ledger_more_url = ""
    ledger_show_all_url = ""
    if ledger_hidden_count > 0:
        ledger_more_url = build_history_url(
            client_tab='account',
            ledger_tab=ledger_tab,
            more='1',
            limit=ledger_limit,
        )
        ledger_show_all_url = build_history_url(
            client_tab='account',
            ledger_tab=ledger_tab,
            show_all='1',
            more=None,
        )

    orders_clear_url = build_history_url(
        client_tab='orders',
        status=None,
        page=None,
    )

    latest_document_label = "-"
    latest_document_hint = "Sin actividad reciente"
    latest_internal_at = getattr(latest_internal_document, "issued_at", None)
    latest_fiscal_at = getattr(latest_fiscal_document, "created_at", None)
    latest_official = official_docs[0] if official_docs else None
    latest_official_at = getattr(latest_official, "saas_document_date", None) if latest_official else None
    if latest_fiscal_document and (not latest_internal_at or (latest_fiscal_at and latest_fiscal_at >= latest_internal_at)):
        latest_document_label = latest_fiscal_document.commercial_type_label
        latest_document_hint = "Ultimo fiscal"
    elif latest_internal_document:
        latest_document_label = latest_internal_document.commercial_type_label
        latest_document_hint = "Ultimo interno"
    elif latest_official:
        latest_document_label = latest_official.saas_document_type or "Factura"
        latest_document_hint = "Ultimo SaaS"

    documents_summary = {
        'internal_count': documents_qs.count(),
        'fiscal_count': fiscal_documents_qs.count(),
        'official_count': official_docs_qs.count(),
        'latest_internal_label': latest_internal_document.commercial_type_label if latest_internal_document else "-",
        'latest_fiscal_label': latest_fiscal_document.commercial_type_label if latest_fiscal_document else "-",
        'latest_document_label': latest_document_label,
        'latest_document_hint': latest_document_hint,
    }

    return render(request, 'admin_panel/clients/order_history.html', {
        'client': client,
        'page_obj': page_obj,
        'status': status,
        'can_edit_client_profile': can_edit_client_profile(request.user),
        'can_manage_client_credentials': can_manage_client_credentials(request.user),
        'can_delete_client_record': can_delete_client_record(request.user),
        'status_choices': Order.STATUS_CHOICES,
        'companies': companies,
        'active_company': active_company,
        'active_company_label': active_company_label,
        'selected_company_id': selected_company_id,
        'operations_locked': operations_locked,
        'client_tab': client_tab,
        'client_tabs': client_tabs,
        'summary': {
            'orders_count': summary.get('orders_count') or 0,
            'total_amount': summary.get('total_amount') or Decimal('0.00'),
            'avg_ticket': summary.get('avg_ticket') or Decimal('0.00'),
            'last_order_at': summary.get('last_order_at'),
        },
        'balance_summary': {
            'orders_count': balance_orders_summary.get('orders_count') or 0,
            'orders_total': balance_orders_summary.get('total_amount') or Decimal('0.00'),
            'last_order_at': balance_orders_summary.get('last_order_at'),
            'total_paid': payments_summary.get('total_paid') or Decimal('0.00'),
            'current_balance': client.get_current_balance(company=active_company),
        },
        'payments_recent': payments_recent,
        'payment_history_rows': payment_history_rows,
        'payments_summary': {
            'total_paid': payments_summary.get('total_paid') or Decimal('0.00'),
            'payments_count': payments_summary.get('payments_count') or 0,
            'last_payment_at': payments_summary.get('last_payment_at'),
        },
        'ledger_rows': ledger_rows_visible,
        'ledger_rows_total': len(ledger_rows_filtered),
        'ledger_hidden_count': ledger_hidden_count,
        'ledger_show_all': ledger_show_all,
        'ledger_limit': ledger_limit,
        'ledger_tab': ledger_tab,
        'ledger_warning': ledger_warning,
        'ledger_tabs': ledger_tabs_ui,
        'ledger_more_url': ledger_more_url,
        'ledger_show_all_url': ledger_show_all_url,
        'open_orders': open_orders,
        'open_orders_count': open_orders_count,
        'client_company': client_company,
        'client_company_missing': client_company_missing,
        'effective_category': effective_category,
        'effective_discount': effective_discount,
        'effective_price_list': effective_price_list,
        'sale_condition_label': sale_condition_label,
        'orders_clear_url': orders_clear_url,
        'documents_by_type': documents_by_type,
        'recent_internal_documents': recent_internal_documents,
        'documents_summary': documents_summary,
        'recent_fiscal_documents': recent_fiscal_documents,
        'official_docs': official_docs,
        'quick_remito_available': quick_remito_available,
        'quick_invoice_available': quick_invoice_available,
        'quick_credit_note_available': quick_credit_note_available,
        'quick_sales_document_actions': quick_sales_document_actions,
    })


@staff_member_required
def client_password_change(request, pk):
    """Change client password."""
    client = get_object_or_404(ClientProfile, pk=pk)

    if not can_manage_client_credentials(request.user):
        messages.error(
            request,
            f'Solo "{PRIMARY_SUPERADMIN_USERNAME}" puede cambiar credenciales de clientes.',
        )
        return redirect('admin_client_order_history', pk=client.pk)

    if not client.user:
        messages.error(request, 'Este cliente no tiene un usuario asociado.')
        return redirect('admin_client_edit', pk=pk)

    if request.method == 'POST':
        form = SetPasswordForm(client.user, request.POST)
        if form.is_valid():
            form.save()
            log_admin_action(
                request,
                action='client_password_reset',
                target_type='user',
                target_id=client.user.pk,
                details={
                    'client_profile_id': client.pk,
                    'username': client.user.username,
                },
            )
            messages.success(request, f'Contrasena actualizada para el usuario "{client.user.username}".')
            return redirect('admin_client_list')
    else:
        form = SetPasswordForm(client.user)

    return render(request, 'admin_panel/clients/password_form.html', {
        'form': form,
        'client': client
    })


@staff_member_required
def client_delete(request, pk):
    """Deactivate single client without hard delete."""
    client = get_object_or_404(ClientProfile, pk=pk)

    if not can_delete_client_record(request.user):
        messages.error(
            request,
            f'Solo "{PRIMARY_SUPERADMIN_USERNAME}" puede desactivar clientes.',
        )
        return redirect('admin_client_order_history', pk=client.pk)

    if request.method == 'POST':
        reason = request.POST.get('cancel_reason', '').strip()
        user = client.user
        before = {
            'client': model_snapshot(client, ['is_approved', 'notes']),
            'user': {'is_active': getattr(user, 'is_active', None)},
        }

        if user and user.is_active:
            user.is_active = False
            user.save(update_fields=['is_active'])

        client.is_approved = False
        if reason:
            stamp = timezone.localtime().strftime('%d/%m/%Y %H:%M')
            note_line = f"[{stamp}] Cliente desactivado por {request.user.username}: {reason}"
            client.notes = f"{client.notes}\n{note_line}".strip() if client.notes else note_line
            client.save(update_fields=['is_approved', 'notes', 'updated_at'])
        else:
            client.save(update_fields=['is_approved', 'updated_at'])

        after = {
            'client': model_snapshot(client, ['is_approved', 'notes']),
            'user': {'is_active': getattr(user, 'is_active', None)},
        }
        log_admin_change(
            request,
            action='client_deactivate',
            target_type='client_profile',
            target_id=client.pk,
            before=before,
            after=after,
            extra={
                'reason': reason,
                'username': user.username if user else '',
            },
        )

        messages.success(request, f'Cliente "{client.company_name}" desactivado sin borrar historial.')
        return redirect('admin_client_list')

    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"{client.company_name} (Usuario: {client.user.username if client.user else 'Sin usuario'})",
        'cancel_url': reverse('admin_client_list'),
        'title': 'Confirmar Desactivacion',
        'question': 'Estas por desactivar este cliente.',
        'warning': 'No se borraran pedidos, pagos ni historial. El usuario quedara inactivo.',
        'confirm_label': 'Confirmar Desactivacion',
        'show_reason_input': True,
        'reason_label': 'Motivo (opcional)',
        'reason_name': 'cancel_reason',
    })


# ===================== ACCOUNT REQUESTS =====================

@staff_member_required
def request_list(request):
    """Account requests list."""
    requests = AccountRequest.objects.all()
    
    status_filter = request.GET.get('status', 'pending')
    if status_filter:
        requests = requests.filter(status=status_filter)
    
    paginator = Paginator(requests.order_by('-created_at'), 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)
    
    return render(request, 'admin_panel/requests/list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
    })


@staff_member_required
def request_approve(request, pk):
    """Approve account request and create user."""
    account_request = get_object_or_404(AccountRequest, pk=pk)
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        try:
            selected_category = parse_optional_client_category(request.POST.get("client_category", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(
                request,
                'admin_panel/requests/approve.html',
                {
                    'account_request': account_request,
                    'client_categories': get_active_client_categories(),
                },
            )
        try:
            discount = parse_admin_decimal_input(
                request.POST.get('discount', '0'),
                'Descuento (%)',
                min_value='0',
                max_value='100',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(
                request,
                'admin_panel/requests/approve.html',
                {
                    'account_request': account_request,
                    'client_categories': get_active_client_categories(),
                },
            )
        if selected_category:
            discount = selected_category.discount_percentage
        before = model_snapshot(account_request, ['status', 'admin_notes', 'processed_at', 'created_user_id'])

        if not username:
            messages.error(request, 'El nombre de usuario es obligatorio.')
            return render(
                request,
                'admin_panel/requests/approve.html',
                {
                    'account_request': account_request,
                    'client_categories': get_active_client_categories(),
                },
            )

        if User.objects.filter(username=username).exists():
            messages.error(request, f'El usuario "{username}" ya existe.')
            return render(
                request,
                'admin_panel/requests/approve.html',
                {
                    'account_request': account_request,
                    'client_categories': get_active_client_categories(),
                },
            )

        if not password:
            messages.error(request, 'La contrasena es obligatoria.')
            return render(
                request,
                'admin_panel/requests/approve.html',
                {
                    'account_request': account_request,
                    'client_categories': get_active_client_categories(),
                },
            )

        try:
            validate_password(password)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return render(
                request,
                'admin_panel/requests/approve.html',
                {
                    'account_request': account_request,
                    'client_categories': get_active_client_categories(),
                },
            )

        else:
            # Create user
            user = User.objects.create_user(
                username=username,
                email=account_request.email,
                password=password,
                first_name=account_request.contact_name,
            )

            # Create client profile
            profile = ClientProfile.objects.create(
                user=user,
                company_name=account_request.company_name,
                cuit_dni=account_request.cuit_dni,
                province=account_request.province,
                address=account_request.address,
                phone=account_request.phone,
                discount=discount,
                client_category=selected_category,
            )
            default_company = get_default_client_origin_company()
            if default_company:
                ClientCompany.objects.create(
                    client_profile=profile,
                    company=default_company,
                    client_category=selected_category,
                    discount_percentage=discount,
                    is_active=bool(profile.is_approved),
                )

            # Update request
            account_request.status = 'approved'
            account_request.created_user = user
            account_request.processed_at = timezone.now()
            account_request.save()
            after = model_snapshot(account_request, ['status', 'admin_notes', 'processed_at', 'created_user_id'])
            log_admin_change(
                request,
                action='account_request_approve',
                target_type='account_request',
                target_id=account_request.pk,
                before=before,
                after=after,
                extra={
                    'created_username': username,
                    'discount': str(discount),
                    'client_category': selected_category.name if selected_category else '',
                },
            )

            messages.success(
                request,
                f'Cuenta aprobada. Usuario "{username}" creado correctamente.'
            )
            return redirect('admin_request_list')
    
    return render(
        request,
        'admin_panel/requests/approve.html',
        {
            'account_request': account_request,
            'client_categories': get_active_client_categories(),
        },
    )


@staff_member_required
@require_POST
def request_reject(request, pk):
    """Reject account request."""
    account_request = get_object_or_404(AccountRequest, pk=pk)
    before = model_snapshot(account_request, ['status', 'admin_notes', 'processed_at', 'created_user_id'])
    account_request.status = 'rejected'
    account_request.processed_at = timezone.now()
    account_request.admin_notes = request.POST.get('notes', '')
    account_request.save()
    after = model_snapshot(account_request, ['status', 'admin_notes', 'processed_at', 'created_user_id'])
    log_admin_change(
        request,
        action='account_request_reject',
        target_type='account_request',
        target_id=account_request.pk,
        before=before,
        after=after,
    )
    
    messages.info(request, 'Solicitud rechazada.')
    return redirect('admin_request_list')


# ===================== ORDERS =====================

@staff_member_required
def order_list(request):
    """Order list with filters."""
    orders = Order.objects.select_related('user', 'company').all()
    companies = Company.objects.filter(is_active=True).order_by("name")
    active_company = get_admin_company_filter(request)
    selected_company_id = "all" if active_company is None else str(active_company.pk)
    if active_company:
        orders = orders.filter(company=active_company)
    
    # Status filter
    status = request.GET.get('status', '')
    if status:
        orders = orders.filter(status=status)

    # Sync status filter
    sync_status = request.GET.get('sync_status', '').strip()
    if sync_status:
        orders = orders.filter(sync_status=sync_status)
    
    # Client filter
    client = request.GET.get('client', '')
    if client:
        orders = orders.filter(user__username__icontains=client)
    
    paginator = Paginator(orders.order_by('-created_at'), 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)
    
    return render(request, 'admin_panel/orders/list.html', {
        'page_obj': page_obj,
        'status': status,
        'sync_status': sync_status,
        'client': client,
        'status_choices': Order.STATUS_CHOICES,
        'sync_status_choices': Order.SYNC_STATUS_CHOICES,
        'companies': companies,
        'selected_company_id': selected_company_id,
    })


@staff_member_required
def order_export_saas(request):
    company = get_admin_company_required(request)
    if not company:
        messages.error(request, 'Selecciona una empresa valida para exportar pedidos.')
        return redirect('admin_order_list')

    status = request.GET.get('status', '').strip()
    sync_status = request.GET.get('sync_status', '').strip()
    orders = (
        Order.objects.select_related('user', 'company', 'client_company_ref')
        .prefetch_related('items')
        .filter(company=company)
    )
    if status:
        orders = orders.filter(status=status)
    else:
        orders = orders.filter(
            status__in=[
                Order.STATUS_CONFIRMED,
                Order.STATUS_PREPARING,
                Order.STATUS_SHIPPED,
                Order.STATUS_DELIVERED,
            ]
        )
    if sync_status and sync_status != "all":
        orders = orders.filter(sync_status=sync_status)

    if not orders.exists():
        messages.info(request, 'No hay pedidos para exportar con esos filtros.')
        return redirect('admin_order_list')

    def _fmt_dt(value):
        if not value:
            return ""
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")

    file_stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
    company_slug = company.slug or slugify(company.name) or f"company{company.pk}"
    filename = f"saas_pedidos_{company_slug}_{file_stamp}.csv"

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    writer.writerow([
        "order_id",
        "status",
        "created_at",
        "company_slug",
        "company_cuit",
        "client_username",
        "client_company_name",
        "client_cuit",
        "subtotal",
        "discount_percentage",
        "discount_amount",
        "total",
        "notes",
        "admin_notes",
        "external_system",
        "external_id",
        "external_number",
        "sync_status",
        "synced_at",
        "items",
    ])

    rows_count = 0
    for order in orders:
        items_payload = []
        for item in order.items.all():
            items_payload.append({
                "sku": item.product_sku,
                "name": item.product_name,
                "qty": item.quantity,
                "unit_price_base": f"{item.unit_price_base:.2f}",
                "discount_pct": f"{item.discount_percentage_used:.2f}",
                "unit_price": f"{item.price_at_purchase:.2f}",
                "subtotal": f"{item.subtotal:.2f}",
            })

        writer.writerow([
            order.pk,
            order.status,
            _fmt_dt(order.created_at),
            company_slug,
            company.cuit or "",
            order.user.username if order.user_id else "",
            order.client_company or (order.user.username if order.user_id else ""),
            order.client_cuit or "",
            f"{order.subtotal:.2f}",
            f"{order.discount_percentage:.2f}",
            f"{order.discount_amount:.2f}",
            f"{order.total:.2f}",
            order.notes,
            order.admin_notes,
            order.external_system,
            order.external_id,
            order.external_number,
            order.sync_status,
            _fmt_dt(order.synced_at),
            json.dumps(items_payload, ensure_ascii=False),
        ])
        rows_count += 1

    log_admin_action(
        request,
        action="order_export_saas",
        target_type="order",
        target_id=0,
        details={
            "company_id": company.pk,
            "rows": rows_count,
            "status_filter": status or "confirmed+",
            "sync_status": sync_status or "",
        },
    )
    return response


@staff_member_required
def order_detail(request, pk):
    """Order detail and status management."""
    active_company = get_active_company(request)
    order = get_object_or_404(
        Order.objects.select_related(
            "company",
            "client_company_ref",
            "client_company_ref__client_profile",
        ).prefetch_related("status_history__changed_by"),
        pk=pk,
    )
    if active_company and order.company_id != active_company.id:
        messages.error(request, "El pedido no pertenece a la empresa activa.")
        return redirect('admin_order_list')
    try:
        from core.services.fiscal import is_invoice_ready

        invoice_ready, invoice_errors = is_invoice_ready(order)
    except Exception:
        invoice_ready, invoice_errors = False, ["No se pudo validar estado fiscal."]
    order_items = list(
        order.items.select_related(
            'product',
            'clamp_request',
            'clamp_request__linked_product',
        )
    )
    order_discount_percentage = (order.discount_percentage or Decimal('0')).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    for item in order_items:
        unit_discount_amount = Decimal('0.00')
        item_discount_percentage = (
            item.discount_percentage_used
            if getattr(item, "discount_percentage_used", None) not in (None, 0)
            else order_discount_percentage
        )
        base_price = item.unit_price_base if getattr(item, "unit_price_base", None) else item.price_at_purchase
        if item_discount_percentage and item_discount_percentage > 0:
            unit_discount_amount = (
                base_price * item_discount_percentage / Decimal('100')
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        item.unit_discount_amount = unit_discount_amount

        clamp_request = getattr(item, 'clamp_request', None)
        linked_product = getattr(clamp_request, 'linked_product', None) if clamp_request else None
        published_to_catalog = bool(
            clamp_request
            and linked_product
            and linked_product.is_visible_in_catalog(include_uncategorized=False)
        )
        item.published_to_catalog = published_to_catalog
        item.can_publish_to_catalog = bool(clamp_request) and not published_to_catalog

    order_client_profile = (
        ClientProfile.objects.only('id').filter(user_id=order.user_id).first()
        if order.user_id
        else None
    )
    pricing_snapshot = {}
    if order.user_id and order.company_id:
        try:
            from core.services.pricing import (
                resolve_pricing_context,
                resolve_effective_discount_percentage,
                resolve_effective_price_list,
            )

            client_profile, client_company, client_category = resolve_pricing_context(order.user, order.company)
            price_list = resolve_effective_price_list(order.company, client_company, client_category)
            discount_percentage = resolve_effective_discount_percentage(
                client_profile=client_profile,
                company=order.company,
                client_company=client_company,
                client_category=client_category,
            )
            pricing_snapshot = {
                "client_company": client_company,
                "client_category": client_category,
                "price_list": price_list,
                "discount_percentage": discount_percentage,
                "sale_condition": (
                    dict(ClientCategory.SALE_CONDITION_CHOICES).get(
                        client_category.default_sale_condition,
                        client_category.default_sale_condition,
                    )
                    if client_category and client_category.default_sale_condition
                    else "-"
                ),
            }
        except Exception:
            pricing_snapshot = {}
    if request.method == 'POST':
        new_status = request.POST.get('status', '')
        if new_status:
            status_note = request.POST.get('status_note', '').strip()
            admin_notes_input = request.POST.get('admin_notes', '')
            try:
                with transaction.atomic():
                    locked_order = Order.objects.select_for_update().get(pk=order.pk)
                    before = model_snapshot(locked_order, ['status', 'admin_notes', 'status_updated_at'])
                    locked_order.admin_notes = admin_notes_input

                    allowed, reason = can_user_transition_order(request.user, locked_order, new_status)
                    if not allowed:
                        raise ValueError(reason)

                    changed = locked_order.change_status(
                        new_status=new_status,
                        changed_by=request.user,
                        note=status_note or f"Actualizacion desde panel por {request.user.username}",
                    )
                    locked_order.save(update_fields=['admin_notes', 'updated_at'])
                    sync_order_charge_transaction(order=locked_order, actor=request.user)
                    order = locked_order

                if changed:
                    messages.success(request, f'Estado del pedido #{order.pk} actualizado.')
                    log_admin_change(
                        request,
                        action='order_status_change',
                        target_type='order',
                        target_id=order.pk,
                        before=before,
                        after=model_snapshot(locked_order, ['status', 'admin_notes', 'status_updated_at']),
                        extra={
                            'status': locked_order.status,
                            'note': status_note,
                        },
                    )
                else:
                    messages.info(request, f'El pedido #{order.pk} ya estaba en ese estado.')
            except ValueError as exc:
                messages.error(request, str(exc))

    sales_internal_document_types = [
        item
        for item in SalesDocumentType.objects.select_related("default_warehouse")
        .filter(company=order.company, enabled=True, billing_mode="INTERNAL_DOCUMENT")
        .exclude(internal_doc_type="")
        .order_by("-is_default", "display_order", "name")
        if order.status in ORDER_INTERNAL_DOC_STATUS_RULES.get(item.internal_doc_type, set())
    ]
    order_fiscal_documents = (
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "sales_document_type",
            "client_company_ref__client_profile",
            "order",
        )
        .filter(company=order.company, order=order)
        .order_by("-created_at")
    )
    order_invoice_document = _get_order_active_invoice(order)
    order_invoice_state = _get_fiscal_workflow_state(order_invoice_document)
    order_has_external_invoice = bool(order.saas_document_type or order.saas_document_number)
    order_can_invoice = (
        order.status in CLIENT_FACTURABLE_STATUSES
        and not order_invoice_document
        and not order_has_external_invoice
    )
    order_invoice_action_label = "Facturar" if invoice_ready else "Abrir factura"

    return render(request, 'admin_panel/orders/detail.html', {
        'order': order,
        'order_items': order_items,
        'status_choices': Order.STATUS_CHOICES,
        'status_history': order.status_history.all()[:20],
        'order_paid_amount': order.get_paid_amount(),
        'order_pending_amount': order.get_pending_amount(),
        'order_is_paid': order.is_paid(),
        'order_client_profile_id': order_client_profile.pk if order_client_profile else '',
        'order_documents': InternalDocument.objects.select_related('sales_document_type').filter(order=order).order_by('issued_at'),
        'document_company': order.company,
        'pricing_snapshot': pricing_snapshot,
        'sales_internal_document_types': sales_internal_document_types,
        'order_fiscal_documents': order_fiscal_documents,
        'order_invoice_document': order_invoice_document,
        'order_invoice_state': order_invoice_state,
        'order_can_invoice': order_can_invoice,
        'order_has_external_invoice': order_has_external_invoice,
        'order_invoice_action_label': order_invoice_action_label,
    })


@staff_member_required
@require_POST
def order_invoice_open(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    order = get_object_or_404(
        Order.objects.select_related("company", "client_company_ref", "client_company_ref__client_profile"),
        pk=pk,
    )
    if order.company_id != active_company.id:
        messages.error(request, "No podes facturar pedidos de otra empresa.")
        return redirect("admin_order_list")

    existing_invoice = _get_order_active_invoice(order)
    if existing_invoice:
        messages.info(request, "El pedido ya tiene una factura activa. Se abrio el comprobante existente.")
        return redirect("admin_fiscal_document_detail", pk=existing_invoice.pk)

    if order.saas_document_type or order.saas_document_number:
        messages.warning(
            request,
            "El pedido ya tiene un comprobante externo registrado. Revisa la trazabilidad antes de generar otra factura.",
        )
        return redirect("admin_order_detail", pk=order.pk)

    invoice_ready, invoice_errors = is_invoice_ready(order)

    selected_sales_document_type = _resolve_invoice_sales_document_type_for_order(order)
    try:
        if selected_sales_document_type:
            fiscal_doc, created = create_fiscal_document_from_sales_type(
                order=order,
                sales_document_type=selected_sales_document_type,
                actor=request.user,
                require_invoice_ready=invoice_ready,
            )
        else:
            point_of_sale = _resolve_default_point_of_sale(order.company)
            fiscal_doc, created = create_local_fiscal_document_from_order(
                order=order,
                company=active_company,
                doc_type=_resolve_preferred_invoice_doc_type(order),
                point_of_sale=point_of_sale,
                issue_mode=FISCAL_ISSUE_MODE_MANUAL,
                actor=request.user,
                require_invoice_ready=invoice_ready,
            )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_detail", pk=order.pk)

    if created:
        if invoice_ready:
            messages.success(
                request,
                f"Se genero {fiscal_doc.commercial_type_label} para el pedido #{order.pk}.",
            )
        else:
            errors_preview = "; ".join(invoice_errors[:3])
            messages.warning(
                request,
                f"Se abrio {fiscal_doc.commercial_type_label} para el pedido #{order.pk}. "
                f"Completa los datos fiscales antes de cerrar o emitir. Pendientes: {errors_preview}",
            )
    else:
        messages.info(request, "La factura ya existia y se reutilizo.")
    return redirect("admin_fiscal_document_detail", pk=fiscal_doc.pk)


@staff_member_required
@require_POST
def order_internal_document_create(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    order = get_object_or_404(
        Order.objects.select_related("company", "client_company_ref", "client_company_ref__client_profile"),
        pk=pk,
    )
    if order.company_id != active_company.id:
        messages.error(request, "No podes generar documentos de otra empresa.")
        return redirect("admin_order_list")

    sales_document_type = None
    sales_document_type_id = str(request.POST.get("sales_document_type_id", "")).strip()
    if sales_document_type_id.isdigit():
        sales_document_type = SalesDocumentType.objects.filter(
            pk=int(sales_document_type_id),
            company=active_company,
            enabled=True,
            billing_mode="INTERNAL_DOCUMENT",
        ).exclude(internal_doc_type="").first()

    if not sales_document_type:
        messages.error(request, "Selecciona un tipo comercial interno valido.")
        return redirect("admin_order_detail", pk=order.pk)

    try:
        document, created = create_internal_document_from_sales_type(
            order=order,
            sales_document_type=sales_document_type,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_detail", pk=order.pk)

    if created:
        messages.success(
            request,
            f"Documento interno creado ({document.commercial_type_label}) para el pedido #{order.pk}.",
        )
    else:
        messages.info(
            request,
            f"El documento interno ya existia y se reutilizo ({document.commercial_type_label}).",
        )
    return redirect("admin_order_detail", pk=order.pk)


@staff_member_required
@require_POST
def order_fiscal_create_local(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    order = get_object_or_404(
        Order.objects.select_related("company", "client_company_ref", "client_company_ref__client_profile"),
        pk=pk,
    )
    if order.company_id != active_company.id:
        messages.error(request, "No podes crear comprobantes de otra empresa.")
        return redirect("admin_order_list")

    sales_document_type = None
    sales_document_type_id = str(request.POST.get("sales_document_type_id", "")).strip()
    if sales_document_type_id.isdigit():
        sales_document_type = SalesDocumentType.objects.filter(
            pk=int(sales_document_type_id),
            company=active_company,
            enabled=True,
        ).first()

    try:
        if sales_document_type:
            fiscal_doc, created = create_fiscal_document_from_sales_type(
                order=order,
                sales_document_type=sales_document_type,
                actor=request.user,
            )
        else:
            doc_type = str(request.POST.get("doc_type", "")).strip().upper()
            issue_mode = str(request.POST.get("issue_mode", "manual")).strip()
            point_id = str(request.POST.get("point_of_sale_id", "")).strip()
            point_of_sale = None
            if point_id.isdigit():
                point_of_sale = FiscalPointOfSale.objects.filter(
                    pk=int(point_id),
                    company=active_company,
                ).first()
            fiscal_doc, created = create_local_fiscal_document_from_order(
                order=order,
                company=active_company,
                doc_type=doc_type,
                point_of_sale=point_of_sale,
                issue_mode=issue_mode,
                actor=request.user,
            )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_detail", pk=order.pk)

    if created:
        messages.success(
            request,
            f"Comprobante fiscal local creado ({fiscal_doc.doc_type}) para el pedido #{order.pk}.",
        )
    else:
        messages.info(request, "El comprobante fiscal local ya existia y se reutilizo.")
    return redirect("admin_fiscal_document_detail", pk=fiscal_doc.pk)


@staff_member_required
@require_POST
def order_fiscal_register_external(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    order = get_object_or_404(
        Order.objects.select_related("company", "client_company_ref", "client_company_ref__client_profile"),
        pk=pk,
    )
    if order.company_id != active_company.id:
        messages.error(request, "No podes registrar comprobantes externos de otra empresa.")
        return redirect("admin_order_list")

    external_system = str(request.POST.get("external_system", "saas")).strip().lower()
    external_id = str(request.POST.get("external_id", "")).strip()
    external_number = str(request.POST.get("external_number", "")).strip()
    sales_document_type = None
    sales_document_type_id = str(request.POST.get("sales_document_type_id", "")).strip()
    if sales_document_type_id.isdigit():
        sales_document_type = SalesDocumentType.objects.filter(
            pk=int(sales_document_type_id),
            company=active_company,
            enabled=True,
        ).first()

    try:
        if sales_document_type:
            fiscal_doc, created = create_fiscal_document_from_sales_type(
                order=order,
                sales_document_type=sales_document_type,
                actor=request.user,
                external_system=external_system,
                external_id=external_id,
                external_number=external_number,
            )
        else:
            doc_type = str(request.POST.get("doc_type", "")).strip().upper()
            point_id = str(request.POST.get("point_of_sale_id", "")).strip()
            point_of_sale = None
            if point_id.isdigit():
                point_of_sale = FiscalPointOfSale.objects.filter(
                    pk=int(point_id),
                    company=active_company,
                ).first()
            fiscal_doc, created = register_external_fiscal_document_for_order(
                order=order,
                company=active_company,
                doc_type=doc_type,
                point_of_sale=point_of_sale,
                external_system=external_system,
                external_id=external_id,
                external_number=external_number,
                actor=request.user,
            )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_detail", pk=order.pk)

    if created:
        messages.success(
            request,
            f"Comprobante externo registrado ({fiscal_doc.doc_type}) para el pedido #{order.pk}.",
        )
    else:
        messages.info(request, "El comprobante externo ya existia y se reutilizo.")
    return redirect("admin_fiscal_document_detail", pk=fiscal_doc.pk)


@staff_member_required
def fiscal_document_list(request):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    status = str(request.GET.get("status", "")).strip()
    doc_type = str(request.GET.get("doc_type", "")).strip()
    issue_mode = str(request.GET.get("issue_mode", "")).strip()
    query = sanitize_search_token(request.GET.get("q", ""))

    documents = (
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "client_company_ref__client_profile",
            "order",
        )
        .filter(company=active_company)
        .order_by("-created_at")
    )
    if status:
        documents = documents.filter(status=status)
    if doc_type:
        documents = documents.filter(doc_type=doc_type)
    if issue_mode:
        documents = documents.filter(issue_mode=issue_mode)
    if query:
        search_filter = (
            Q(source_key__icontains=query)
            | Q(external_number__icontains=query)
            | Q(external_id__icontains=query)
            | Q(client_company_ref__client_profile__company_name__icontains=query)
        )
        if query.isdigit():
            search_filter = search_filter | Q(order_id=int(query))
        documents = documents.filter(search_filter)

    paginator = Paginator(documents, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "admin_panel/fiscal/list.html",
        {
            "active_company": active_company,
            "page_obj": page_obj,
            "status": status,
            "doc_type": doc_type,
            "issue_mode": issue_mode,
            "search": query,
            "status_choices": FiscalDocument.STATUS_CHOICES,
            "doc_type_choices": FiscalDocument.DOC_TYPE_CHOICES,
            "issue_mode_choices": FiscalDocument.ISSUE_MODE_CHOICES,
        },
    )


@staff_member_required
def fiscal_document_detail(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "client_company_ref__client_profile",
            "client_profile",
            "order",
            "related_document",
            "sales_document_type",
        ).prefetch_related("items__product"),
        pk=pk,
    )
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "El comprobante fiscal no pertenece a la empresa activa.")
        return redirect("admin_fiscal_document_list")

    can_emit = (
        fiscal_document.issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE
        and fiscal_document.doc_type in {FISCAL_DOC_TYPE_FA, FISCAL_DOC_TYPE_FB}
        and fiscal_document.status in {
            FISCAL_STATUS_READY_TO_ISSUE,
            FISCAL_STATUS_PENDING_RETRY,
            FISCAL_STATUS_REJECTED,
        }
    )
    document_invoice_ready = True
    document_invoice_errors = []
    if fiscal_document.order_id:
        document_invoice_ready, document_invoice_errors = is_invoice_ready(fiscal_document.order)
    can_emit = can_emit and document_invoice_ready
    workflow_state = _get_fiscal_workflow_state(fiscal_document)
    can_close_document = (
        fiscal_document.issue_mode == FISCAL_ISSUE_MODE_MANUAL
        and fiscal_document.status in {
            FISCAL_STATUS_READY_TO_ISSUE,
            FISCAL_STATUS_PENDING_RETRY,
            FISCAL_STATUS_REJECTED,
        }
        and document_invoice_ready
    )
    can_reopen_document = (
        fiscal_document.issue_mode == FISCAL_ISSUE_MODE_MANUAL
        and fiscal_document.status == FISCAL_STATUS_EXTERNAL_RECORDED
    )
    can_void_document = fiscal_document.status in {
        FISCAL_STATUS_READY_TO_ISSUE,
        FISCAL_STATUS_PENDING_RETRY,
        FISCAL_STATUS_REJECTED,
        FISCAL_STATUS_EXTERNAL_RECORDED,
    }

    return render(
        request,
        "admin_panel/fiscal/detail.html",
        {
            "active_company": active_company,
            "fiscal_document": fiscal_document,
            "items": fiscal_document.items.all().order_by("line_number"),
            "attempts": fiscal_document.emission_attempts.select_related("triggered_by").all()[:20],
            "can_emit": can_emit,
            "workflow_state": workflow_state,
            "can_close_document": can_close_document,
            "can_reopen_document": can_reopen_document,
            "can_void_document": can_void_document,
            "document_invoice_ready": document_invoice_ready,
            "document_invoice_errors": document_invoice_errors,
        },
    )


@staff_member_required
@require_POST
def fiscal_document_emit(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "client_company_ref",
            "client_profile",
            "order",
        ),
        pk=pk,
    )
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "No podes emitir comprobantes de otra empresa.")
        return redirect("admin_fiscal_document_list")

    try:
        outcome = emit_fiscal_document_now(
            fiscal_document=fiscal_document,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)
    except Exception as exc:
        messages.error(request, f"Fallo inesperado al emitir: {exc}")
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)

    if outcome.state == "authorized":
        messages.success(
            request,
            f"Comprobante autorizado. CAE: {outcome.document.cae or '-'}",
        )
    elif outcome.state == "pending_retry":
        messages.warning(request, outcome.message)
    elif outcome.state == "rejected":
        messages.error(request, outcome.message)
    else:
        messages.info(request, outcome.message)

    return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)


@staff_member_required
@require_POST
def fiscal_document_close(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(FiscalDocument, pk=pk)
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "No podes operar comprobantes de otra empresa.")
        return redirect("admin_fiscal_document_list")

    try:
        document, changed = close_fiscal_document(
            fiscal_document=fiscal_document,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)

    if changed:
        messages.success(request, f"{document.commercial_type_label} cerrada correctamente.")
    else:
        messages.info(request, "La factura ya estaba cerrada.")
    return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)


@staff_member_required
@require_POST
def fiscal_document_reopen(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(FiscalDocument, pk=pk)
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "No podes operar comprobantes de otra empresa.")
        return redirect("admin_fiscal_document_list")

    try:
        document, changed = reopen_fiscal_document(
            fiscal_document=fiscal_document,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)

    if changed:
        messages.success(request, f"{document.commercial_type_label} reabierta.")
    else:
        messages.info(request, "La factura ya estaba abierta.")
    return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)


@staff_member_required
@require_POST
def fiscal_document_void(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(FiscalDocument, pk=pk)
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "No podes operar comprobantes de otra empresa.")
        return redirect("admin_fiscal_document_list")

    try:
        document, changed = void_fiscal_document(
            fiscal_document=fiscal_document,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)

    if changed:
        messages.success(request, f"{document.commercial_type_label} anulada.")
    else:
        messages.info(request, "La factura ya estaba anulada.")
    return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)


@staff_member_required
def fiscal_document_print(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "client_company_ref__client_profile",
            "client_profile",
            "order",
            "sales_document_type",
        ).prefetch_related("items__product"),
        pk=pk,
    )
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "El comprobante fiscal no pertenece a la empresa activa.")
        return redirect("admin_fiscal_document_list")

    copy_type = str(request.GET.get("copy", "original")).strip().lower()
    if copy_type not in {"original", "duplicado", "triplicado"}:
        copy_type = "original"

    site_settings = SiteSettings.get_settings()
    client_profile = fiscal_document.client_profile
    if not client_profile and getattr(fiscal_document, "client_company_ref", None):
        client_profile = fiscal_document.client_company_ref.client_profile

    company = fiscal_document.company
    order = fiscal_document.order
    company_meta = FISCAL_PRINT_DOC_META.get(fiscal_document.doc_type, {"letter": "-", "code": "---"})
    copy_label = FISCAL_PRINT_COPY_LABELS.get(copy_type, "ORIGINAL")
    sale_condition_label = "-"
    operator_label = "-"
    observations = []

    if order:
        if order.notes:
            observations.append(order.notes)
        if order.admin_notes:
            observations.append(order.admin_notes)
        if getattr(order, "assigned_to", None):
            operator_label = order.assigned_to.get_full_name() or order.assigned_to.username

    client_company_link = getattr(fiscal_document, "client_company_ref", None)
    effective_category = None
    if client_company_link and getattr(client_company_link, "client_category_id", None):
        effective_category = client_company_link.client_category
    elif client_profile:
        try:
            effective_category = client_profile.get_effective_client_category(company=company)
        except Exception:
            effective_category = None
    if effective_category and getattr(effective_category, "default_sale_condition", None):
        sale_condition_label = dict(ClientCategory.SALE_CONDITION_CHOICES).get(
            effective_category.default_sale_condition,
            effective_category.default_sale_condition,
        )
    elif getattr(order, "billing_mode", "") == "official":
        sale_condition_label = "Cuenta corriente"

    company_address_bits = [
        company.fiscal_address,
        company.fiscal_city,
        company.fiscal_province,
        company.postal_code,
    ]
    client_address_bits = []
    if client_profile:
        client_address_bits = [
            client_profile.fiscal_address or client_profile.address,
            client_profile.fiscal_city or client_profile.province,
            client_profile.fiscal_province,
            client_profile.postal_code,
        ]

    subtotal_before_discount = Decimal(fiscal_document.subtotal_net or 0)
    discount_total = Decimal(fiscal_document.discount_total or 0)
    taxable_net = subtotal_before_discount - discount_total
    if taxable_net < 0:
        taxable_net = Decimal("0.00")

    order_total_discount_percentage = Decimal("0.00")
    if order and getattr(order, "discount_percentage", None):
        order_total_discount_percentage = Decimal(order.discount_percentage or 0)

    return render(
        request,
        "admin_panel/fiscal/print.html",
        {
            "fiscal_document": fiscal_document,
            "items": fiscal_document.items.all().order_by("line_number"),
            "copy_type": copy_type,
            "copy_label": copy_label,
            "document_letter": company_meta["letter"],
            "document_code": company_meta["code"],
            "document_number_display": (
                f"{str(fiscal_document.point_of_sale.number or '').zfill(5)}-"
                f"{str(fiscal_document.number or 0).zfill(8)}"
            ),
            "company_address_line": " / ".join(bit for bit in company_address_bits if bit),
            "company_contact_line": " / ".join(
                bit for bit in [site_settings.company_phone, site_settings.company_phone_2, company.email or site_settings.company_email] if bit
            ),
            "company_contact_site": site_settings.company_address,
            "client_profile": client_profile,
            "client_address_line": " / ".join(bit for bit in client_address_bits if bit),
            "client_document_label": (
                client_profile.get_document_type_display() if client_profile and client_profile.document_type else "CUIT/DNI"
            ) if client_profile else "CUIT/DNI",
            "client_document_value": (
                client_profile.document_number or client_profile.cuit_dni
            ) if client_profile else "",
            "sale_condition_label": sale_condition_label,
            "operator_label": operator_label,
            "observations_text": "\n".join(bit for bit in observations if bit).strip(),
            "subtotal_before_discount": subtotal_before_discount,
            "taxable_net": taxable_net,
            "order_total_discount_percentage": order_total_discount_percentage,
        },
    )


@staff_member_required
@require_POST
def order_item_add(request, pk):
    order = get_object_or_404(Order.objects.select_related('company', 'user'), pk=pk)
    if not order.is_mutable_for_items():
        messages.error(request, "Solo podes editar items en pedidos borrador.")
        return redirect("admin_order_detail", pk=order.pk)

    sku = request.POST.get("sku", "").strip()
    product_id = request.POST.get("product_id", "").strip()
    qty_raw = request.POST.get("quantity", "1").strip()
    try:
        quantity = int(qty_raw)
    except ValueError:
        quantity = 0
    if quantity <= 0:
        messages.error(request, "Cantidad invalida.")
        return redirect("admin_order_detail", pk=order.pk)

    product = None
    if product_id.isdigit():
        product = Product.objects.filter(pk=int(product_id)).first()
    if not product and sku:
        product_matches = _find_products_for_order_query(sku, limit=5)
        if len(product_matches) == 1:
            product = product_matches[0]
        elif len(product_matches) > 1:
            matches_label = ", ".join(match.sku for match in product_matches[:3])
            messages.error(
                request,
                f'Hay varias coincidencias para "{sku}". Elegi una sugerencia mas precisa ({matches_label}).',
            )
            return redirect("admin_order_detail", pk=order.pk)
    if not product:
        messages.error(request, "Producto no encontrado.")
        return redirect("admin_order_detail", pk=order.pk)

    try:
        from core.services.pricing import (
            resolve_pricing_context,
            resolve_effective_discount_percentage,
            resolve_effective_price_list,
            get_product_pricing,
        )

        client_profile, client_company, client_category = resolve_pricing_context(order.user, order.company)
        discount_percentage = resolve_effective_discount_percentage(
            client_profile=client_profile,
            company=order.company,
            client_company=client_company,
            client_category=client_category,
        )
        price_list = resolve_effective_price_list(order.company, client_company, client_category)
        pricing = get_product_pricing(
            product,
            user=order.user,
            company=order.company,
            price_list=price_list,
            context=(client_profile, client_company, client_category),
        )
    except Exception:
        discount_percentage = Decimal("0")
        price_list = None
        pricing = None

    unit_price_base = pricing.base_price if pricing else product.price
    final_price = pricing.final_price if pricing else product.price
    discount_used = pricing.discount_percentage if pricing else discount_percentage
    OrderItem.objects.create(
        order=order,
        product=product,
        clamp_request=None,
        product_sku=product.sku,
        product_name=product.name,
        quantity=quantity,
        price_at_purchase=final_price,
        unit_price_base=unit_price_base,
        discount_percentage_used=discount_used,
        price_list=price_list,
    )

    items = list(order.items.all())
    subtotal = sum((item.unit_price_base or Decimal("0")) * item.quantity for item in items)
    discount_amount = (subtotal * (Decimal(discount_used) / Decimal("100"))).quantize(Decimal("0.01"))
    total = (subtotal - discount_amount).quantize(Decimal("0.01"))
    order.subtotal = subtotal
    order.discount_percentage = discount_used
    order.discount_amount = discount_amount
    order.total = total
    order.save(update_fields=["subtotal", "discount_percentage", "discount_amount", "total", "updated_at"])

    messages.success(request, "Item agregado al documento.")
    return redirect("admin_order_detail", pk=order.pk)


@staff_member_required
@require_POST
def order_item_delete(request, pk, item_id):
    order = get_object_or_404(Order.objects.select_related('company', 'user'), pk=pk)
    if not order.is_mutable_for_items():
        messages.error(request, "Solo podes eliminar items en pedidos borrador.")
        return redirect("admin_order_detail", pk=order.pk)

    order_item = get_object_or_404(OrderItem, pk=item_id, order=order)
    try:
        order_item.delete()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_detail", pk=order.pk)

    items = list(order.items.all())
    subtotal = sum((item.unit_price_base or Decimal("0")) * item.quantity for item in items)
    discount_percentage = order.discount_percentage or Decimal("0")
    discount_amount = (subtotal * (Decimal(discount_percentage) / Decimal("100"))).quantize(Decimal("0.01"))
    total = (subtotal - discount_amount).quantize(Decimal("0.01"))
    order.subtotal = subtotal
    order.discount_amount = discount_amount
    order.total = total
    order.save(update_fields=["subtotal", "discount_amount", "total", "updated_at"])

    messages.success(request, "Item eliminado.")
    return redirect("admin_order_detail", pk=order.pk)


@staff_member_required
def internal_document_print(request, doc_id):
    document = get_object_or_404(
        InternalDocument.objects.select_related(
            "company",
            "client_company_ref__client_profile",
            "client_profile",
            "order",
            "payment",
            "sales_document_type",
        ),
        pk=doc_id,
    )
    copy_key = request.GET.get("copy", "original").strip().lower()
    copy_labels = {
        "original": "ORIGINAL",
        "duplicado": "DUPLICADO",
        "triplicado": "TRIPLICADO",
    }
    copy_label = copy_labels.get(copy_key, "ORIGINAL")
    order_items = []
    if document.order_id:
        order_items = list(
            document.order.items.select_related("product").all()
        )

    response = render(
        request,
        "admin_panel/documents/print.html",
        {
            "document": document,
            "copy_label": copy_label,
            "order_items": order_items,
        },
    )
    if request.GET.get("download") == "1":
        filename = f"{document.doc_type}_{document.number:07d}.html"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_member_required
@require_POST
def order_item_publish_clamp(request, pk, item_id):
    """Publish a clamp measure item from order detail into catalog products."""
    order = get_object_or_404(Order, pk=pk)
    order_item = get_object_or_404(
        OrderItem.objects.select_related('clamp_request'),
        pk=item_id,
        order_id=order.pk,
    )

    if not order_item.clamp_request_id:
        messages.error(request, 'Este item no proviene de una solicitud de abrazadera a medida.')
        return redirect('admin_order_detail', pk=order.pk)

    clamp_request = order_item.clamp_request
    if clamp_request.status != ClampMeasureRequest.STATUS_COMPLETED:
        messages.error(
            request,
            'La solicitud debe estar Completada antes de publicarla en el catalogo.',
        )
        return redirect('admin_order_detail', pk=order.pk)

    product, created, published_now = publish_clamp_request_product(clamp_request)

    order_item.product = product
    order_item.product_sku = product.sku
    order_item.product_name = product.name
    order_item._force_item_write = True
    try:
        order_item.save(update_fields=['product', 'product_sku', 'product_name'])
    finally:
        if hasattr(order_item, "_force_item_write"):
            delattr(order_item, "_force_item_write")

    log_admin_action(
        request,
        action='order_item_publish_clamp',
        target_type='order_item',
        target_id=order_item.pk,
        details={
            'order_id': order.pk,
            'clamp_request_id': clamp_request.pk,
            'product_id': product.pk,
            'product_sku': product.sku,
            'created_product': created,
            'published_now': published_now,
        },
    )

    if published_now:
        messages.success(
            request,
            f'Abrazadera publicada como producto {product.sku} y visible en catalogo.',
        )
    else:
        messages.success(
            request,
            f'Item vinculado al producto {product.sku}.',
        )
    return redirect('admin_order_detail', pk=order.pk)


@staff_member_required
def order_delete(request, pk):
    """Cancel order preserving full history (no hard delete)."""
    order = get_object_or_404(Order, pk=pk)

    if request.method == 'POST':
        cancel_reason = request.POST.get('cancel_reason', '').strip()
        status_note = cancel_reason or f"Pedido cancelado desde panel por {request.user.username}"
        try:
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                before = model_snapshot(locked_order, ['status', 'admin_notes', 'status_updated_at'])
                changed = locked_order.change_status(
                    new_status=Order.STATUS_CANCELLED,
                    changed_by=request.user,
                    note=status_note,
                )
                if cancel_reason:
                    stamp = timezone.localtime().strftime('%d/%m/%Y %H:%M')
                    reason_line = f"[{stamp}] Cancelacion: {cancel_reason}"
                    locked_order.admin_notes = (
                        f"{locked_order.admin_notes}\n{reason_line}".strip()
                        if locked_order.admin_notes
                        else reason_line
                    )
                    locked_order.save(update_fields=['admin_notes', 'updated_at'])
                sync_order_charge_transaction(order=locked_order, actor=request.user)
                order = locked_order
        except ValueError as exc:
            messages.error(
                request,
                str(exc),
            )
            return redirect('admin_order_detail', pk=order.pk)

        log_admin_change(
            request,
            action='order_cancel',
            target_type='order',
            target_id=order.pk,
            before=before,
            after=model_snapshot(order, ['status', 'admin_notes', 'status_updated_at']),
            extra={
                'changed': changed,
                'cancel_reason': cancel_reason,
            },
        )
        if changed:
            messages.success(request, f'Pedido #{order.pk} cancelado correctamente.')
        else:
            messages.info(request, f'El pedido #{order.pk} ya estaba cancelado.')
        return redirect('admin_order_detail', pk=order.pk)

    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"Pedido #{order.pk} (Cliente: {order.user.username if order.user else 'Anonimo'})",
        'cancel_url': reverse('admin_order_detail', args=[pk]),
        'title': 'Confirmar Cancelacion',
        'question': 'Estas por cancelar este pedido.',
        'warning': 'No se borraran items, pagos ni historial del pedido.',
        'confirm_label': 'Confirmar Cancelacion',
        'show_reason_input': True,
        'reason_label': 'Motivo (opcional)',
        'reason_name': 'cancel_reason',
    })


# ===================== SETTINGS =====================

SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS = [
    "company_id",
    "code",
    "name",
    "letter",
    "point_of_sale_id",
    "last_number",
    "enabled",
    "document_behavior",
    "generate_stock_movement",
    "generate_account_movement",
    "group_equal_products",
    "default_warehouse_id",
    "prioritize_default_warehouse",
    "default_sales_user_id",
    "billing_mode",
    "internal_doc_type",
    "fiscal_doc_type",
    "is_default",
    "display_order",
]

WAREHOUSE_SNAPSHOT_FIELDS = [
    "company_id",
    "code",
    "name",
    "is_active",
    "notes",
]

@user_passes_test(is_primary_superadmin)
def company_list(request):
    """List and access company configuration."""
    companies = Company.objects.order_by("name")
    active_company = get_active_company(request)
    return render(
        request,
        "admin_panel/companies/list.html",
        {
            "companies": companies,
            "active_company": active_company,
        },
    )


@user_passes_test(is_primary_superadmin)
def company_edit(request, pk):
    """Edit company/legal entity details."""
    company = get_object_or_404(Company, pk=pk)
    active_company = get_active_company(request)
    price_lists = PriceList.objects.filter(company=company).order_by("name")
    if request.method == "POST":
        was_active = company.is_active
        before = model_snapshot(
            company,
            [
                "name",
                "legal_name",
                "email",
                "cuit",
                "tax_condition",
                "fiscal_address",
                "fiscal_city",
                "fiscal_province",
                "postal_code",
                "point_of_sale_default",
                "default_price_list_id",
                "is_active",
            ],
        )
        company.name = request.POST.get("name", "").strip()
        company.legal_name = request.POST.get("legal_name", "").strip()
        company.email = request.POST.get("email", "").strip()
        company.cuit = request.POST.get("cuit", "").strip()
        company.tax_condition = request.POST.get("tax_condition", "").strip()
        company.fiscal_address = request.POST.get("fiscal_address", "").strip()
        company.fiscal_city = request.POST.get("fiscal_city", "").strip()
        company.fiscal_province = request.POST.get("fiscal_province", "").strip()
        company.postal_code = request.POST.get("postal_code", "").strip()
        company.point_of_sale_default = request.POST.get("point_of_sale_default", "").strip()
        default_price_list_id = str(request.POST.get("default_price_list", "")).strip()
        if default_price_list_id:
            selected_price_list = price_lists.filter(pk=default_price_list_id).first()
            company.default_price_list = selected_price_list
        else:
            company.default_price_list = None
        if "is_active" in request.POST:
            company.is_active = str(request.POST.get("is_active", "")).lower() in {"1", "true", "on"}

        if not company.name:
            messages.error(request, "El nombre es obligatorio.")
        else:
            if was_active and not company.is_active:
                has_other_active = Company.objects.filter(is_active=True).exclude(pk=company.pk).exists()
                if not has_other_active:
                    messages.error(
                        request,
                        "No podes desactivar la unica empresa activa del sistema.",
                    )
                    company.is_active = True
                    return render(
                        request,
                        "admin_panel/companies/edit.html",
                        {
                            "company": company,
                            "active_company": active_company,
                            "tax_condition_choices": Company.TAX_CONDITION_CHOICES,
                            "price_lists": price_lists,
                        },
                    )
            company.save()
            log_admin_change(
                request,
                action="company_update",
                target_type="company",
                target_id=company.pk,
                before=before,
                after=model_snapshot(
                    company,
                    [
                        "name",
                        "legal_name",
                        "email",
                        "cuit",
                        "tax_condition",
                        "fiscal_address",
                        "fiscal_city",
                        "fiscal_province",
                        "postal_code",
                        "point_of_sale_default",
                        "default_price_list_id",
                        "is_active",
                    ],
                ),
            )
            messages.success(request, "Empresa actualizada.")
            return redirect("admin_company_list")

    return render(
        request,
        "admin_panel/companies/edit.html",
        {
            "company": company,
            "active_company": active_company,
            "tax_condition_choices": Company.TAX_CONDITION_CHOICES,
            "price_lists": price_lists,
        },
    )


def _get_settings_active_company(request, *, message):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, message)
        return None
    return active_company


@user_passes_test(is_primary_superadmin)
def warehouse_list(request):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para gestionar depositos.",
    )
    if not active_company:
        return redirect("select_company")

    warehouses = Warehouse.objects.filter(company=active_company).order_by("name")
    return render(
        request,
        "admin_panel/settings/warehouses_list.html",
        {
            "active_company": active_company,
            "warehouses": warehouses,
        },
    )


@user_passes_test(is_primary_superadmin)
def warehouse_create(request):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para crear depositos.",
    )
    if not active_company:
        return redirect("select_company")

    form = WarehouseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        warehouse = form.save(commit=False)
        warehouse.company = active_company
        warehouse.save()
        log_admin_action(
            request,
            action="warehouse_create",
            target_type="warehouse",
            target_id=warehouse.pk,
            details=model_snapshot(warehouse, WAREHOUSE_SNAPSHOT_FIELDS),
        )
        messages.success(request, "Deposito creado.")
        return redirect("admin_warehouse_list")

    return render(
        request,
        "admin_panel/settings/warehouse_form.html",
        {
            "active_company": active_company,
            "form": form,
            "warehouse": None,
        },
    )


@user_passes_test(is_primary_superadmin)
def warehouse_edit(request, pk):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para editar depositos.",
    )
    if not active_company:
        return redirect("select_company")

    warehouse = get_object_or_404(Warehouse, pk=pk)
    if warehouse.company_id != active_company.id:
        messages.error(request, "El deposito no pertenece a la empresa activa.")
        return redirect("admin_warehouse_list")

    before = model_snapshot(warehouse, WAREHOUSE_SNAPSHOT_FIELDS)
    form = WarehouseForm(request.POST or None, instance=warehouse)
    if request.method == "POST" and form.is_valid():
        warehouse = form.save()
        log_admin_change(
            request,
            action="warehouse_update",
            target_type="warehouse",
            target_id=warehouse.pk,
            before=before,
            after=model_snapshot(warehouse, WAREHOUSE_SNAPSHOT_FIELDS),
        )
        messages.success(request, "Deposito actualizado.")
        return redirect("admin_warehouse_list")

    return render(
        request,
        "admin_panel/settings/warehouse_form.html",
        {
            "active_company": active_company,
            "form": form,
            "warehouse": warehouse,
        },
    )


@user_passes_test(is_primary_superadmin)
def sales_document_type_list(request):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para gestionar tipos de documento.",
    )
    if not active_company:
        return redirect("select_company")

    document_types = list(
        SalesDocumentType.objects.select_related(
            "point_of_sale",
            "default_warehouse",
            "default_sales_user",
        )
        .filter(company=active_company)
        .order_by("display_order", "name")
    )
    summary = {
        "total": len(document_types),
        "enabled": sum(1 for item in document_types if item.enabled),
        "with_stock": sum(1 for item in document_types if item.generate_stock_movement),
        "with_account": sum(1 for item in document_types if item.generate_account_movement),
        "fiscal": sum(1 for item in document_types if item.billing_mode != "INTERNAL_DOCUMENT"),
    }
    behavior_overview = []
    for behavior_value, behavior_label in SALES_DOCUMENT_BEHAVIOR_CHOICES:
        matches = [item for item in document_types if item.document_behavior == behavior_value]
        if not matches:
            continue
        behavior_overview.append(
            {
                "key": behavior_value,
                "label": behavior_label,
                "count": len(matches),
                "default_item": next((item for item in matches if item.is_default), None),
                "enabled_count": sum(1 for item in matches if item.enabled),
            }
        )
    return render(
        request,
        "admin_panel/settings/sales_document_types_list.html",
        {
            "active_company": active_company,
            "document_types": document_types,
            "document_summary": summary,
            "behavior_overview": behavior_overview,
            "warehouses_count": Warehouse.objects.filter(company=active_company).count(),
        },
    )


@user_passes_test(is_primary_superadmin)
def sales_document_type_create(request):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para crear tipos de documento.",
    )
    if not active_company:
        return redirect("select_company")

    form = SalesDocumentTypeForm(request.POST or None, company=active_company)
    if request.method == "POST" and form.is_valid():
        document_type = form.save()
        log_admin_action(
            request,
            action="sales_document_type_create",
            target_type="sales_document_type",
            target_id=document_type.pk,
            details=model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS),
        )
        messages.success(request, "Tipo de documento guardado.")
        return redirect("admin_sales_document_type_list")

    return render(
        request,
        "admin_panel/settings/sales_document_type_form.html",
        {
            "active_company": active_company,
            "form": form,
            "document_type": None,
        },
    )


@user_passes_test(is_primary_superadmin)
def sales_document_type_edit(request, pk):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para editar tipos de documento.",
    )
    if not active_company:
        return redirect("select_company")

    document_type = get_object_or_404(SalesDocumentType, pk=pk)
    if document_type.company_id != active_company.id:
        messages.error(request, "El tipo de documento no pertenece a la empresa activa.")
        return redirect("admin_sales_document_type_list")

    before = model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS)
    form = SalesDocumentTypeForm(request.POST or None, instance=document_type, company=active_company)
    if request.method == "POST" and form.is_valid():
        document_type = form.save()
        log_admin_change(
            request,
            action="sales_document_type_update",
            target_type="sales_document_type",
            target_id=document_type.pk,
            before=before,
            after=model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS),
        )
        messages.success(request, "Tipo de documento actualizado.")
        return redirect("admin_sales_document_type_list")

    return render(
        request,
        "admin_panel/settings/sales_document_type_form.html",
        {
            "active_company": active_company,
            "form": form,
            "document_type": document_type,
        },
    )


@user_passes_test(is_primary_superadmin)
@require_POST
def sales_document_type_toggle_enabled(request, pk):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para editar tipos de documento.",
    )
    if not active_company:
        return redirect("select_company")

    document_type = get_object_or_404(SalesDocumentType, pk=pk)
    if document_type.company_id != active_company.id:
        messages.error(request, "El tipo de documento no pertenece a la empresa activa.")
        return redirect("admin_sales_document_type_list")

    before = model_snapshot(document_type, ["enabled"])
    document_type.enabled = not document_type.enabled
    document_type.save(update_fields=["enabled", "updated_at"])
    log_admin_change(
        request,
        action="sales_document_type_toggle_enabled",
        target_type="sales_document_type",
        target_id=document_type.pk,
        before=before,
        after=model_snapshot(document_type, ["enabled"]),
    )
    messages.success(
        request,
        "Tipo de documento habilitado." if document_type.enabled else "Tipo de documento deshabilitado.",
    )
    return redirect("admin_sales_document_type_list")


def _sync_company_default_pos(company, point_of_sale):
    if not company or not point_of_sale:
        return
    if company.point_of_sale_default != point_of_sale.number:
        company.point_of_sale_default = point_of_sale.number
        company.save(update_fields=["point_of_sale_default", "updated_at"])


def _get_fiscal_active_company(request):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para configurar factura electronica.")
        return None
    return active_company


@user_passes_test(is_primary_superadmin)
def fiscal_config(request):
    """Fiscal configuration dashboard scoped to active company."""
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")

    points = FiscalPointOfSale.objects.filter(company=active_company).order_by("number")
    is_ready, readiness_errors = is_company_fiscal_ready(active_company)
    default_point = points.filter(is_default=True).first()
    active_points_count = points.filter(is_active=True).count()

    return render(
        request,
        "admin_panel/fiscal/config.html",
        {
            "active_company": active_company,
            "points": points,
            "is_ready": is_ready,
            "readiness_errors": readiness_errors,
            "default_point": default_point,
            "active_points_count": active_points_count,
            "environment_choices": FiscalPointOfSale.ENV_CHOICES,
        },
    )


@user_passes_test(is_primary_superadmin)
def fiscal_point_create(request):
    """Create fiscal point of sale for active company."""
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")

    if request.method == "POST":
        number = str(request.POST.get("number", "")).strip()
        name = str(request.POST.get("name", "")).strip()
        environment = str(request.POST.get("environment", FiscalPointOfSale.ENV_HOMOLOGATION)).strip()
        is_active = request.POST.get("is_active") == "on"
        is_default = request.POST.get("is_default") == "on"

        if not number:
            messages.error(request, "El numero de punto de venta es obligatorio.")
        elif environment not in dict(FiscalPointOfSale.ENV_CHOICES):
            messages.error(request, "Entorno invalido.")
        elif FiscalPointOfSale.objects.filter(company=active_company, number=number).exists():
            messages.error(request, f"Ya existe un punto de venta {number} para esta empresa.")
        else:
            pos = FiscalPointOfSale.objects.create(
                company=active_company,
                number=number,
                name=name or f"PV {number}",
                environment=environment,
                is_active=True if is_default else is_active,
                is_default=is_default,
                notes=str(request.POST.get("notes", "")).strip(),
            )
            if pos.is_default:
                _sync_company_default_pos(active_company, pos)
            log_admin_action(
                request,
                action="fiscal_pos_create",
                target_type="fiscal_point_of_sale",
                target_id=pos.pk,
                details={
                    "company_id": active_company.pk,
                    "number": pos.number,
                    "environment": pos.environment,
                    "is_active": pos.is_active,
                    "is_default": pos.is_default,
                },
            )
            messages.success(request, f"Punto de venta {pos.number} creado.")
            return redirect("admin_fiscal_config")

    return render(
        request,
        "admin_panel/fiscal/pos_form.html",
        {
            "active_company": active_company,
            "point": None,
            "environment_choices": FiscalPointOfSale.ENV_CHOICES,
            "form_mode": "create",
        },
    )


@user_passes_test(is_primary_superadmin)
def fiscal_point_edit(request, pk):
    """Edit fiscal point of sale for active company."""
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")
    point = get_object_or_404(FiscalPointOfSale, pk=pk, company=active_company)

    if request.method == "POST":
        before = model_snapshot(point, ["number", "name", "environment", "is_active", "is_default", "notes"])
        number = str(request.POST.get("number", "")).strip()
        name = str(request.POST.get("name", "")).strip()
        environment = str(request.POST.get("environment", FiscalPointOfSale.ENV_HOMOLOGATION)).strip()
        is_active = request.POST.get("is_active") == "on"
        is_default = request.POST.get("is_default") == "on"

        if not number:
            messages.error(request, "El numero de punto de venta es obligatorio.")
        elif environment not in dict(FiscalPointOfSale.ENV_CHOICES):
            messages.error(request, "Entorno invalido.")
        elif (
            FiscalPointOfSale.objects.filter(company=active_company, number=number)
            .exclude(pk=point.pk)
            .exists()
        ):
            messages.error(request, f"Ya existe un punto de venta {number} para esta empresa.")
        else:
            point.number = number
            point.name = name or f"PV {number}"
            point.environment = environment
            point.is_default = is_default
            point.is_active = True if is_default else is_active
            point.notes = str(request.POST.get("notes", "")).strip()
            point.save()
            if point.is_default:
                _sync_company_default_pos(active_company, point)
            log_admin_change(
                request,
                action="fiscal_pos_update",
                target_type="fiscal_point_of_sale",
                target_id=point.pk,
                before=before,
                after=model_snapshot(point, ["number", "name", "environment", "is_active", "is_default", "notes"]),
            )
            messages.success(request, f"Punto de venta {point.number} actualizado.")
            return redirect("admin_fiscal_config")

    return render(
        request,
        "admin_panel/fiscal/pos_form.html",
        {
            "active_company": active_company,
            "point": point,
            "environment_choices": FiscalPointOfSale.ENV_CHOICES,
            "form_mode": "edit",
        },
    )


@user_passes_test(is_primary_superadmin)
@require_POST
def fiscal_point_toggle_active(request, pk):
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")
    point = get_object_or_404(FiscalPointOfSale, pk=pk, company=active_company)
    before = model_snapshot(point, ["is_active", "is_default"])

    point.is_active = not point.is_active
    if not point.is_active and point.is_default:
        point.is_default = False
    point.save(update_fields=["is_active", "is_default", "updated_at"])

    if not point.is_active and active_company.point_of_sale_default == point.number:
        replacement = (
            FiscalPointOfSale.objects.filter(company=active_company, is_active=True)
            .exclude(pk=point.pk)
            .order_by("number")
            .first()
        )
        if replacement:
            replacement.is_default = True
            replacement.save(update_fields=["is_default", "updated_at"])
            _sync_company_default_pos(active_company, replacement)

    log_admin_change(
        request,
        action="fiscal_pos_toggle_active",
        target_type="fiscal_point_of_sale",
        target_id=point.pk,
        before=before,
        after=model_snapshot(point, ["is_active", "is_default"]),
    )
    state_text = "activo" if point.is_active else "inactivo"
    messages.success(request, f"Punto de venta {point.number} marcado como {state_text}.")
    return redirect("admin_fiscal_config")


@user_passes_test(is_primary_superadmin)
@require_POST
def fiscal_point_set_default(request, pk):
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")
    point = get_object_or_404(FiscalPointOfSale, pk=pk, company=active_company)
    before = model_snapshot(point, ["is_active", "is_default"])

    point.is_active = True
    point.is_default = True
    point.save(update_fields=["is_active", "is_default", "updated_at"])
    _sync_company_default_pos(active_company, point)

    log_admin_change(
        request,
        action="fiscal_pos_set_default",
        target_type="fiscal_point_of_sale",
        target_id=point.pk,
        before=before,
        after=model_snapshot(point, ["is_active", "is_default"]),
    )
    messages.success(request, f"Punto de venta {point.number} configurado como default.")
    return redirect("admin_fiscal_config")


@user_passes_test(is_primary_superadmin)
def settings_view(request):
    """Site settings management."""
    settings = SiteSettings.get_settings()
    
    if request.method == 'POST':
        settings.show_public_prices = request.POST.get('show_public_prices') == 'on'
        settings.require_primary_category_for_multicategory = (
            request.POST.get('require_primary_category_for_multicategory') == 'on'
        )
        settings.public_prices_message = request.POST.get('public_prices_message', '').strip()
        settings.company_name = request.POST.get('company_name', '').strip()
        settings.company_email = request.POST.get('company_email', '').strip()
        settings.company_phone = request.POST.get('company_phone', '').strip()
        settings.company_phone_2 = request.POST.get('company_phone_2', '').strip()
        settings.company_address = request.POST.get('company_address', '').strip()
        settings.save()
        log_admin_action(
            request,
            action="settings_update",
            target_type="site_settings",
            target_id=settings.pk,
            details={
                "show_public_prices": settings.show_public_prices,
                "require_primary_category_for_multicategory": settings.require_primary_category_for_multicategory,
            },
        )
        
        messages.success(request, 'Configuración guardada.')
    
    return render(request, 'admin_panel/settings.html', {'settings': settings})


@user_passes_test(is_primary_superadmin)
def admin_user_list(request):
    """
    Superadmin-only list to manage admin accounts and permissions.
    """
    search = sanitize_search_token(request.GET.get('q', ''))
    admins = get_managed_admin_users_queryset()
    if search:
        admins = apply_parsed_text_search(
            admins,
            normalize_admin_search_query(search),
            ["username", "first_name", "last_name", "email"],
            order_by_similarity=False,
        )

    admin_rows = []
    for admin_user in admins:
        visible_companies = list(get_user_companies(admin_user))
        admin_rows.append(
            {
                "user": admin_user,
                "role_label": get_admin_role_label(admin_user),
                "role_labels": get_admin_role_labels(admin_user),
                "company_scope_mode": get_admin_company_scope_mode(admin_user),
                "visible_companies": visible_companies,
            }
        )

    return render(
        request,
        'admin_panel/admin_users/list.html',
        {
            'admin_rows': admin_rows,
            'search': search,
            'total_admins': admins.count(),
        },
    )


@user_passes_test(is_primary_superadmin)
def admin_user_edit(request, user_id):
    """
    Superadmin-only edit for admin identity fields.
    """
    admin_user = get_object_or_404(get_managed_admin_users_queryset(), pk=user_id)
    is_primary_account = admin_user.username.lower() == PRIMARY_SUPERADMIN_USERNAME

    if request.method == 'POST':
        submitted_username = str(request.POST.get('username', admin_user.username)).strip()
        submitted_email = str(request.POST.get('email', admin_user.email or '')).strip()
        submitted_first_name = str(request.POST.get('first_name', admin_user.first_name or '')).strip()
        submitted_last_name = str(request.POST.get('last_name', admin_user.last_name or '')).strip()

        if not submitted_username:
            messages.error(request, 'El usuario no puede quedar vacio.')
            return redirect('admin_user_edit', user_id=admin_user.pk)

        username_field = admin_user._meta.get_field('username')
        try:
            cleaned_username = username_field.clean(submitted_username, admin_user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else 'Usuario invalido.')
            return redirect('admin_user_edit', user_id=admin_user.pk)

        if is_primary_account and cleaned_username.lower() != PRIMARY_SUPERADMIN_USERNAME:
            messages.error(
                request,
                f'La cuenta "{PRIMARY_SUPERADMIN_USERNAME}" debe conservar su usuario principal.',
            )
            return redirect('admin_user_edit', user_id=admin_user.pk)

        if User.objects.exclude(pk=admin_user.pk).filter(username__iexact=cleaned_username).exists():
            messages.error(request, f'Ya existe otro usuario con el nombre "{cleaned_username}".')
            return redirect('admin_user_edit', user_id=admin_user.pk)

        before = build_admin_user_snapshot(admin_user)
        admin_user.username = cleaned_username
        admin_user.email = submitted_email
        admin_user.first_name = submitted_first_name
        admin_user.last_name = submitted_last_name
        admin_user.save(update_fields=['username', 'email', 'first_name', 'last_name'])
        after = build_admin_user_snapshot(admin_user)

        log_admin_change(
            request,
            action='admin_user_profile_update',
            target_type='auth_user',
            target_id=admin_user.pk,
            before=before,
            after=after,
            extra={
                'username': admin_user.username,
            },
        )
        messages.success(request, f'Informacion actualizada para "{admin_user.username}".')
        return redirect('admin_user_list')

    return render(
        request,
        'admin_panel/admin_users/profile_form.html',
        {
            'admin_user': admin_user,
            'recent_admin_audit_logs': get_recent_admin_user_audit_logs(admin_user),
        },
    )


@user_passes_test(is_primary_superadmin)
def admin_user_password_change(request, user_id):
    """
    Superadmin-only password reset for operator/admin accounts.
    """
    admin_user = get_object_or_404(get_managed_admin_users_queryset(), pk=user_id)

    if request.method == 'POST':
        form = SetPasswordForm(admin_user, request.POST)
        if form.is_valid():
            form.save()
            log_admin_action(
                request,
                action='admin_user_password_reset',
                target_type='auth_user',
                target_id=admin_user.pk,
                details={
                    'username': admin_user.username,
                },
            )
            messages.success(request, f'Contrasena actualizada para "{admin_user.username}".')
            return redirect('admin_user_list')
    else:
        form = SetPasswordForm(admin_user)

    return render(
        request,
        'admin_panel/admin_users/password_form.html',
        {
            'form': form,
            'admin_user': admin_user,
            'recent_admin_audit_logs': get_recent_admin_user_audit_logs(admin_user),
        },
    )


@user_passes_test(is_primary_superadmin)
def admin_user_delete(request, user_id):
    """
    Safe delete/deactivate for operator accounts while preserving audit trail.
    """
    admin_user = get_object_or_404(get_managed_admin_users_queryset(), pk=user_id)

    if admin_user.username.lower() == PRIMARY_SUPERADMIN_USERNAME:
        messages.error(
            request,
            f'La cuenta "{PRIMARY_SUPERADMIN_USERNAME}" no se puede eliminar ni desactivar desde el panel.',
        )
        return redirect('admin_user_list')

    if admin_user.pk == request.user.pk:
        messages.error(request, 'No puedes eliminar tu propia cuenta mientras estas operando en el panel.')
        return redirect('admin_user_list')

    if request.method == 'POST':
        reason = request.POST.get('cancel_reason', '').strip()
        before = build_admin_user_snapshot(admin_user)

        admin_user.is_active = False
        admin_user.is_staff = False
        admin_user.is_superuser = False
        admin_user.save(update_fields=['is_active', 'is_staff', 'is_superuser'])
        set_admin_role_for_user(admin_user, '')
        AdminCompanyAccess.objects.filter(user=admin_user).update(is_active=False)

        after = build_admin_user_snapshot(admin_user)
        log_admin_change(
            request,
            action='admin_user_deactivate',
            target_type='auth_user',
            target_id=admin_user.pk,
            before=before,
            after=after,
            extra={
                'username': admin_user.username,
                'reason': reason,
            },
        )
        messages.success(request, f'La cuenta "{admin_user.username}" fue dada de baja del panel.')
        return redirect('admin_user_list')

    return render(
        request,
        'admin_panel/delete_confirm.html',
        {
            'object': f'{admin_user.username} ({admin_user.first_name} {admin_user.last_name})'.strip(),
            'cancel_url': reverse('admin_user_list'),
            'title': 'Confirmar baja de cuenta operadora',
            'question': 'Estas por dar de baja esta cuenta del panel.',
            'warning': 'No se borrara historial ni auditoria. Se desactivara el acceso y se quitaran permisos de panel.',
            'confirm_label': 'Confirmar baja',
            'show_reason_input': True,
            'reason_label': 'Motivo (opcional)',
            'reason_name': 'cancel_reason',
        },
    )


@user_passes_test(is_primary_superadmin)
def admin_user_permissions(request, user_id):
    """
    Superadmin-only edit for core admin flags.
    """
    admin_user = get_object_or_404(get_managed_admin_users_queryset(), pk=user_id)
    ensure_admin_role_groups()
    available_companies = list(Company.objects.filter(is_active=True).order_by("name"))
    current_scope_links = list(
        AdminCompanyAccess.objects.filter(user=admin_user, is_active=True, company__is_active=True).select_related("company")
    )
    current_scope_ids = {link.company_id for link in current_scope_links}
    current_roles = get_admin_role_values(admin_user) or [ROLE_ADMIN]
    current_scope_mode = get_admin_company_scope_mode(admin_user)
    visible_companies = list(get_user_companies(admin_user))

    if request.method == 'POST':
        new_is_active = request.POST.get('is_active') == 'on'
        new_is_staff = request.POST.get('is_staff') == 'on'
        new_is_superuser = request.POST.get('is_superuser') == 'on'
        selected_roles = []
        seen_roles = set()
        valid_roles = {choice[0] for choice in ADMIN_ROLE_CHOICES}
        for raw_role in request.POST.getlist("admin_roles"):
            normalized_role = str(raw_role or "").strip().lower()
            if normalized_role in valid_roles and normalized_role not in seen_roles:
                selected_roles.append(normalized_role)
                seen_roles.add(normalized_role)
        selected_scope_mode = str(request.POST.get("company_scope_mode", current_scope_mode or "all")).strip().lower()
        selected_company_ids = []
        seen_company_ids = set()
        for raw_company_id in request.POST.getlist("allowed_company_ids"):
            normalized = str(raw_company_id or "").strip()
            if not normalized.isdigit():
                continue
            company_id = int(normalized)
            if company_id in seen_company_ids:
                continue
            if any(company.pk == company_id for company in available_companies):
                selected_company_ids.append(company_id)
                seen_company_ids.add(company_id)

        if new_is_superuser and admin_user.username.lower() != PRIMARY_SUPERADMIN_USERNAME:
            messages.error(
                request,
                f'Solo "{PRIMARY_SUPERADMIN_USERNAME}" puede tener permisos de superadmin.',
            )
            return redirect('admin_user_permissions', user_id=admin_user.pk)

        if admin_user.username.lower() == PRIMARY_SUPERADMIN_USERNAME:
            if not new_is_superuser or not new_is_staff or not new_is_active:
                messages.warning(
                    request,
                    f'La cuenta "{PRIMARY_SUPERADMIN_USERNAME}" debe mantenerse activa, con acceso al panel y superadmin.',
                )
            new_is_superuser = True
            new_is_staff = True
            new_is_active = True
        else:
            new_is_superuser = False

        # Prevent locking out own account accidentally.
        if admin_user.pk == request.user.pk:
            if not new_is_staff:
                messages.error(request, 'No puedes quitarte el acceso al panel a ti mismo.')
                return redirect('admin_user_permissions', user_id=admin_user.pk)
            if not new_is_superuser:
                messages.error(request, 'No puedes quitarte permisos de superadmin a ti mismo.')
                return redirect('admin_user_permissions', user_id=admin_user.pk)
            if not new_is_active:
                messages.error(request, 'No puedes desactivar tu propia cuenta.')
                return redirect('admin_user_permissions', user_id=admin_user.pk)

        if new_is_staff and not new_is_superuser:
            if not selected_roles:
                messages.error(request, "Selecciona al menos un rol operativo.")
                return redirect('admin_user_permissions', user_id=admin_user.pk)
            if selected_scope_mode == "limited" and not selected_company_ids:
                messages.error(request, "Selecciona al menos una empresa para acceso limitado.")
                return redirect('admin_user_permissions', user_id=admin_user.pk)
        else:
            selected_roles = []
            selected_scope_mode = "all"
            selected_company_ids = []

        before = build_admin_user_snapshot(admin_user)

        admin_user.is_active = new_is_active
        admin_user.is_staff = new_is_staff
        admin_user.is_superuser = new_is_superuser
        admin_user.save(update_fields=['is_active', 'is_staff', 'is_superuser'])

        if admin_user.is_staff and not admin_user.is_superuser:
            set_admin_roles_for_user(admin_user, selected_roles)
            AdminCompanyAccess.objects.filter(user=admin_user).update(is_active=False)
            if selected_scope_mode == "limited":
                for company in available_companies:
                    if company.pk not in selected_company_ids:
                        continue
                    AdminCompanyAccess.objects.update_or_create(
                        user=admin_user,
                        company=company,
                        defaults={"is_active": True},
                    )
        else:
            AdminCompanyAccess.objects.filter(user=admin_user).update(is_active=False)
            set_admin_roles_for_user(admin_user, [])

        after = build_admin_user_snapshot(admin_user)

        log_admin_change(
            request,
            action='admin_user_permissions_update',
            target_type='auth_user',
            target_id=admin_user.pk,
            before=before,
            after=after,
            extra={
                'username': admin_user.username,
            },
        )
        messages.success(request, f'Permisos actualizados para "{admin_user.username}".')
        return redirect('admin_user_list')

    recent_admin_audit_logs = get_recent_admin_user_audit_logs(admin_user)

    return render(
        request,
        'admin_panel/admin_users/form.html',
        {
            'admin_user': admin_user,
            'role_choices': ADMIN_ROLE_CHOICES,
            'current_roles': current_roles,
            'current_scope_mode': current_scope_mode,
            'available_companies': available_companies,
            'current_scope_ids': current_scope_ids,
            'visible_companies': visible_companies,
            'current_role_labels': get_admin_role_labels(admin_user),
            'recent_admin_audit_logs': recent_admin_audit_logs,
        },
    )


# ===================== CATEGORIES =====================

@staff_member_required
def category_list(request):
    """Category list."""
    search = sanitize_search_token(request.GET.get('q', ''))
    status = request.GET.get('status', 'all').strip().lower()
    focus_raw = request.GET.get('focus', '').strip()

    all_categories = list(
        Category.objects.select_related('parent').order_by('order', 'name')
    )
    all_category_map = {c.id: c for c in all_categories}

    if status == 'active':
        filtered_categories = [c for c in all_categories if c.is_active]
    elif status == 'inactive':
        filtered_categories = [c for c in all_categories if not c.is_active]
    else:
        filtered_categories = all_categories

    category_map = {c.id: c for c in filtered_categories}
    child_index = {}
    for cat in filtered_categories:
        child_index.setdefault(cat.parent_id, []).append(cat.id)

    if search:
        parsed_search = normalize_admin_search_query(search)
        include_terms = [*parsed_search.get('phrases', []), *parsed_search.get('include_terms', [])]
        exclude_terms = parsed_search.get('exclude_terms', [])

        def _matches(cat):
            haystack = cat.name.lower()
            include_ok = all(term.lower() in haystack for term in include_terms) if include_terms else True
            exclude_ok = all(term.lower() not in haystack for term in exclude_terms)
            return include_ok and exclude_ok

        matched_ids = {c.id for c in filtered_categories if _matches(c)}
        visible_ids = set(matched_ids)

        # Keep the path to root visible so matches remain understandable in the tree.
        for cid in list(matched_ids):
            node = category_map.get(cid)
            parent = node.parent if node else None
            while parent:
                if parent.id not in category_map:
                    break
                visible_ids.add(parent.id)
                parent = parent.parent

        # Keep full branches when a parent node matches.
        pending = list(matched_ids)
        while pending:
            current = pending.pop()
            for child_id in child_index.get(current, []):
                if child_id not in visible_ids:
                    visible_ids.add(child_id)
                    pending.append(child_id)

        filtered_categories = [c for c in filtered_categories if c.id in visible_ids]
        category_map = {c.id: c for c in filtered_categories}

    children_map = {}
    for cat in filtered_categories:
        children_map.setdefault(cat.parent_id, []).append(cat)

    for child_list in children_map.values():
        child_list.sort(key=lambda c: (c.order, c.name.lower()))

    tree_rows = []

    def walk(node, depth):
        children = children_map.get(node.id, [])
        tree_rows.append({
            'category': node,
            'depth': depth,
            'has_children': bool(children),
            'children_count': len(children),
        })
        for child in children:
            walk(child, depth + 1)

    roots = [cat for cat in filtered_categories if cat.parent_id not in category_map]
    roots.sort(key=lambda c: (c.order, c.name.lower()))
    for root in roots:
        walk(root, 0)

    for row in tree_rows:
        category = row['category']
        tree_ids = category.get_descendant_ids(include_self=True)
        category.tree_products_count = Product.objects.filter(
            Q(categories__id__in=tree_ids) | Q(category_id__in=tree_ids)
        ).distinct().count()
        category.direct_products_count = category.products_m2m.count()

    integrity_issues = detect_category_integrity_issues(all_categories)
    focus_category_id = None
    auto_expand_ids = []
    if focus_raw.isdigit():
        candidate_id = int(focus_raw)
        if candidate_id in all_category_map:
            focus_category_id = candidate_id
            node = all_category_map[candidate_id]
            while node and node.parent_id:
                auto_expand_ids.append(node.parent_id)
                node = all_category_map.get(node.parent_id)

    return render(request, 'admin_panel/categories/list.html', {
        'tree_rows': tree_rows,
        'visible_total': len(tree_rows),
        'search': search,
        'status': status,
        'integrity_issues': integrity_issues,
        'move_parent_options': build_category_options(
            all_categories,
            include_inactive_suffix=True,
        ),
        'focus_category_id': focus_category_id,
        'auto_expand_ids_json': json.dumps(auto_expand_ids),
    })


@staff_member_required
@require_POST
@superuser_required_for_modifications
def category_reorder(request):
    """
    Reorder categories by a user-provided list of IDs.
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
        ordered_ids = payload.get("ordered_ids", [])
        if not isinstance(ordered_ids, list) or not ordered_ids:
            return JsonResponse({"success": False, "error": "Datos invalidos."}, status=400)

        normalized_ids = normalize_category_ids(ordered_ids)
        categories = {cat.id: cat for cat in Category.objects.filter(id__in=normalized_ids)}
        updates = []
        for index, category_id in enumerate(normalized_ids):
            category = categories.get(category_id)
            if category is None:
                continue
            category.order = (index + 1) * 10
            category.updated_at = timezone.now()
            updates.append(category)

        if updates:
            Category.objects.bulk_update(updates, ["order", "updated_at"], batch_size=500)

        log_admin_action(
            request,
            action="category_reorder",
            target_type="category_bulk",
            details={"ordered_ids": normalized_ids[:100], "count": len(updates)},
        )
        return JsonResponse({"success": True, "updated": len(updates)})
    except Exception as exc:
        logger.exception("Error reordering categories")
        return JsonResponse({"success": False, "error": str(exc)}, status=400)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def category_bulk_status(request):
    """
    Bulk activate/deactivate selected categories.
    """
    action = request.POST.get("bulk_action", "").strip().lower()
    selected_ids = normalize_category_ids(request.POST.getlist("category_ids"))
    q = request.POST.get("q", "").strip()
    status = request.POST.get("status", "all").strip().lower()

    redirect_url = reverse("admin_category_list")
    if q or status != "all":
        params = {}
        if q:
            params["q"] = q
        if status:
            params["status"] = status
        redirect_url = f"{redirect_url}?{urlencode(params)}"

    if not selected_ids:
        messages.warning(request, "No se seleccionaron categorias.")
        return redirect(redirect_url)

    if action not in {"activate", "deactivate"}:
        messages.error(request, "Accion masiva invalida.")
        return redirect(redirect_url)

    categories_map = {
        category.id: category
        for category in Category.objects.select_related("parent").filter(id__in=selected_ids)
    }
    if not categories_map:
        messages.warning(request, "No se encontraron categorias validas para actualizar.")
        return redirect(redirect_url)

    ordered_categories = sorted(
        categories_map.values(),
        key=lambda cat: len(cat.get_ancestor_ids(include_self=True)),
    )

    if action == "deactivate":
        impacted_ids = set()
        for category in ordered_categories:
            impacted_ids.update(category.get_descendant_ids(include_self=True))

        active_before_ids = set(
            Category.objects.filter(id__in=impacted_ids, is_active=True).values_list("id", flat=True)
        )

        for category in ordered_categories:
            if category.is_active:
                category.is_active = False
                category.save()

        active_after_ids = set(
            Category.objects.filter(id__in=impacted_ids, is_active=True).values_list("id", flat=True)
        )
        deactivated_total = len(active_before_ids - active_after_ids)
        direct_selected = len(
            [cid for cid in categories_map.keys() if cid in active_before_ids]
        )
        cascaded = max(deactivated_total - direct_selected, 0)

        messages.success(
            request,
            f"Categorias desactivadas: {deactivated_total} en total "
            f"({direct_selected} seleccionadas y {cascaded} por cascada).",
        )
        log_admin_action(
            request,
            action="category_bulk_deactivate",
            target_type="category_bulk",
            details={
                "selected_count": len(categories_map),
                "selected_ids": list(categories_map.keys())[:200],
                "deactivated_total": deactivated_total,
                "cascaded": cascaded,
            },
        )
        return redirect(redirect_url)

    # action == "activate"
    selected_before_active = {
        category.id: category.is_active for category in ordered_categories
    }

    for category in ordered_categories:
        if not category.is_active:
            category.is_active = True
            category.save()

    refreshed = {
        category.id: category
        for category in Category.objects.select_related("parent").filter(id__in=categories_map.keys())
    }
    activated = sum(
        1
        for cid, was_active in selected_before_active.items()
        if not was_active and refreshed.get(cid) and refreshed[cid].is_active
    )
    blocked = sum(
        1
        for cid, category in refreshed.items()
        if not category.is_active
    )

    if blocked:
        messages.warning(
            request,
            f"Se activaron {activated} categorias. "
            f"{blocked} no pudieron activarse porque tienen un padre inactivo.",
        )
    else:
        messages.success(request, f"Se activaron {activated} categorias seleccionadas.")

    log_admin_action(
        request,
        action="category_bulk_activate",
        target_type="category_bulk",
        details={
            "selected_count": len(categories_map),
            "selected_ids": list(categories_map.keys())[:200],
            "activated": activated,
            "blocked": blocked,
        },
    )
    return redirect(redirect_url)


@staff_member_required
@superuser_required_for_modifications
def category_create(request):
    """Create category."""
    parent_from_query = None
    parent_query_id = request.GET.get('parent', '').strip()
    if parent_query_id.isdigit():
        parent_from_query = Category.objects.filter(pk=int(parent_query_id)).first()

    if request.method == 'POST':
        form = CategoryForm(request.POST)
        form.fields['parent'].queryset = Category.objects.order_by('order', 'name')
        if form.is_valid():
            category = form.save()
            log_admin_action(
                request,
                action="category_create",
                target_type="category",
                target_id=category.pk,
                details={"name": category.name, "parent_id": category.parent_id},
            )
            messages.success(request, f'Categoria "{category.name}" creada.')
            return redirect('admin_category_list')
    else:
        initial = {}
        if parent_from_query:
            initial['parent'] = parent_from_query.pk
        form = CategoryForm(initial=initial)
        form.fields['parent'].queryset = Category.objects.order_by('order', 'name')

    selected_parent_id = form['parent'].value()
    selected_parent = None
    if str(selected_parent_id).isdigit():
        selected_parent = Category.objects.filter(pk=int(selected_parent_id)).first()

    parent_options = build_category_options(
        form.fields['parent'].queryset,
        include_inactive_suffix=True,
    )

    return render(request, 'admin_panel/categories/form.html', {
        'form': form,
        'parent_options': parent_options,
        'selected_parent_id': str(selected_parent_id or ''),
        'selected_parent': selected_parent,
        'action': 'Crear',
    })

@staff_member_required
@superuser_required_for_modifications
def category_edit(request, pk):
    """Edit category."""
    category = get_object_or_404(Category, pk=pk)
    
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        form.fields['parent'].queryset = Category.objects.exclude(pk=pk).order_by('order', 'name')
        if form.is_valid():
            updated = form.save()
            log_admin_action(
                request,
                action="category_edit",
                target_type="category",
                target_id=updated.pk,
                details={
                    "name": updated.name,
                    "parent_id": updated.parent_id,
                    "is_active": updated.is_active,
                },
            )
            messages.success(request, f'Categoría "{category.name}" actualizada.')
            return redirect('admin_category_list')
    else:
        form = CategoryForm(instance=category)
        # Exclude self from parents to avoid recursion
        form.fields['parent'].queryset = Category.objects.exclude(pk=pk).order_by('order', 'name')

    selected_parent_id = form['parent'].value()
    selected_parent = None
    if str(selected_parent_id).isdigit():
        selected_parent = Category.objects.filter(pk=int(selected_parent_id)).first()

    parent_options = build_category_options(
        form.fields['parent'].queryset,
        include_inactive_suffix=True,
    )
    deactivation_impact = calculate_category_deactivation_impact(category)

    return render(request, 'admin_panel/categories/form.html', {
        'form': form,
        'parent_options': parent_options,
        'selected_parent_id': str(selected_parent_id or ''),
        'selected_parent': selected_parent,
        'deactivation_impact': deactivation_impact,
        'category': category, # Keep category in context for attributes links
        'action': 'Editar',
    })


@staff_member_required
@superuser_required_for_modifications
@require_POST
def category_move(request, pk):
    """
    Move one category node to a new parent category.
    The full subtree moves with it.
    """
    category = get_object_or_404(Category, pk=pk)
    parent_raw = request.POST.get('parent_id', '').strip()

    new_parent = None
    if parent_raw:
        if not parent_raw.isdigit():
            messages.error(request, 'Categoria padre invalida.')
            return redirect('admin_category_list')
        new_parent = get_object_or_404(Category, pk=int(parent_raw))

    if category.parent_id == (new_parent.pk if new_parent else None):
        messages.info(request, f'La categoria "{category.name}" ya estaba en esa ubicacion.')
        return redirect(f"{reverse('admin_category_list')}?focus={category.pk}")

    if not category.can_move_to(new_parent):
        messages.error(
            request,
            'Movimiento invalido: no puedes mover una categoria dentro de si misma o de una subcategoria suya.',
        )
        return redirect(f"{reverse('admin_category_list')}?focus={category.pk}")

    previous_parent = category.parent
    subtree_size = len(category.get_descendant_ids(include_self=True))
    forced_deactivation = bool(new_parent and not new_parent.is_active and category.is_active)

    category.move_to(new_parent)

    log_admin_action(
        request,
        action='category_move',
        target_type='category',
        target_id=category.pk,
        details={
            'name': category.name,
            'from_parent_id': previous_parent.pk if previous_parent else None,
            'to_parent_id': new_parent.pk if new_parent else None,
            'to_parent_name': new_parent.name if new_parent else '',
            'subtree_size': subtree_size,
            'forced_deactivation': forced_deactivation,
        },
    )

    destination = new_parent.name if new_parent else 'raiz'
    if forced_deactivation:
        messages.success(
            request,
            f'Categoria "{category.name}" movida a "{destination}". '
            'Se desactivo automaticamente junto con su arbol porque el nuevo padre esta inactivo.',
        )
    else:
        messages.success(
            request,
            f'Categoria "{category.name}" movida a "{destination}" con {subtree_size} nodo(s) en su arbol.',
        )
    return redirect(f"{reverse('admin_category_list')}?focus={category.pk}")


@staff_member_required
@superuser_required_for_modifications
def category_delete(request, pk):
    """Delete single category."""
    category = get_object_or_404(Category, pk=pk)
    
    if request.method == 'POST':
        name = category.name
        category_id = category.pk
        category.delete()
        log_admin_action(
            request,
            action="category_delete",
            target_type="category",
            target_id=category_id,
            details={"name": name},
        )
        messages.success(request, f'Categoría "{name}" eliminada.')
        return redirect('admin_category_list')
        
    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"Categoría: {category.name}",
        'cancel_url': reverse('admin_category_list')
    })


@staff_member_required
@superuser_required_for_modifications
def category_attribute_create(request, category_id):
    """Create new category attribute."""
    category = get_object_or_404(Category, pk=category_id)
    
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            slug = request.POST.get('slug', '').strip()
            attr_type = request.POST.get('type', 'text')
            options = request.POST.get('options', '')
            required = request.POST.get('required') == 'on'
            is_recommended = request.POST.get('is_recommended') == 'on'
            regex_pattern = request.POST.get('regex_pattern', '').strip()
            
            # Simple validation for slug
            if CategoryAttribute.objects.filter(category=category, slug=slug).exists():
                messages.error(request, f'El slug "{slug}" ya existe en esta categoría.')
            else:
                CategoryAttribute.objects.create(
                    category=category,
                    name=name,
                    slug=slug,
                    type=attr_type,
                    options=options,
                    required=required,
                    is_recommended=is_recommended,
                    regex_pattern=regex_pattern
                )
                messages.success(request, f'Atributo "{name}" agregado.')
                return redirect('admin_category_edit', pk=category.pk)
        except Exception as e:
            messages.error(request, f'Error al crear atributo: {str(e)}')
    
    return render(request, 'admin_panel/categories/attribute_form.html', {
        'category': category,
        'action': 'Crear',
    })


@staff_member_required
@superuser_required_for_modifications
def category_attribute_edit(request, category_id, attribute_id):
    """Edit existing category attribute."""
    category = get_object_or_404(Category, pk=category_id)
    attribute = get_object_or_404(CategoryAttribute, pk=attribute_id, category=category)
    
    if request.method == 'POST':
        try:
            attribute.name = request.POST.get('name', '').strip()
            # Slug shouldn't change generally, but legal here if unique
            new_slug = request.POST.get('slug', '').strip()
            if new_slug != attribute.slug and CategoryAttribute.objects.filter(category=category, slug=new_slug).exists():
                messages.error(request, f'El slug "{new_slug}" ya existe.')
                return redirect(request.path)
            
            attribute.slug = new_slug
            attribute.type = request.POST.get('type', 'text')
            attribute.options = request.POST.get('options', '')
            attribute.required = request.POST.get('required') == 'on'
            attribute.is_recommended = request.POST.get('is_recommended') == 'on'
            attribute.regex_pattern = request.POST.get('regex_pattern', '').strip()
            attribute.save()
            
            messages.success(request, f'Atributo "{attribute.name}" actualizado.')
            return redirect('admin_category_edit', pk=category.pk)
        except Exception as e:
            messages.error(request, f'Error al actualizar: {str(e)}')
            
    return render(request, 'admin_panel/categories/attribute_form.html', {
        'category': category,
        'attribute': attribute,
        'action': 'Editar',
    })


@staff_member_required
@superuser_required_for_modifications
def category_attribute_delete(request, category_id, attribute_id):
    """Delete a category attribute."""
    category = get_object_or_404(Category, pk=category_id)
    attribute = get_object_or_404(CategoryAttribute, pk=attribute_id, category=category)
    
    name = attribute.name
    attribute.delete()
    messages.success(request, f'Atributo "{name}" eliminado.')
    
    return redirect('admin_category_edit', pk=category.pk)


@staff_member_required
@superuser_required_for_modifications
def category_manage_products(request, pk):
    """
    Manage direct category links for products (many-to-many).
    """
    category = get_object_or_404(Category, pk=pk)

    def get_filtered_queryset(req_data):
        qs = Product.objects.select_related('category').prefetch_related('categories').all()

        qs, search = apply_admin_text_search(
            qs,
            req_data.get('q', ''),
            ["sku", "name", "description", "supplier", "supplier_ref__name"],
        )

        status = req_data.get('status')
        if status == 'active':
            qs = qs.filter(is_active=True)
        elif status == 'inactive':
            qs = qs.filter(is_active=False)

        cat_filter = req_data.get('category_filter', 'current')
        if cat_filter == 'current':
            qs = qs.filter(categories=category)
        elif cat_filter == 'none':
            qs = qs.filter(category__isnull=True, categories__isnull=True)
        elif cat_filter == 'all':
            pass
        elif cat_filter.isdigit():
            qs = qs.filter(categories__id=int(cat_filter))

        return qs.distinct(), search, status, cat_filter

    if request.method == 'POST':
        raw_post_body = request.body
        action = request.POST.get('action', 'assign').strip()
        select_all_pages = request.POST.get('select_all_pages') == 'true'

        if select_all_pages:
            products_to_update, _, _, _ = get_filtered_queryset(request.POST)
            target_ids = list(products_to_update.values_list('id', flat=True))
        else:
            target_ids = extract_target_product_ids_from_post(request.POST, raw_post_body)
            if not target_ids:
                logger.warning(
                    "category_manage_products without selected products | user=%s | category=%s | keys=%s | action=%s | product_ids=%s | product_ids_csv=%s",
                    getattr(request.user, "username", "unknown"),
                    pk,
                    list(request.POST.keys()),
                    action,
                    request.POST.getlist("product_ids"),
                    request.POST.get("product_ids_csv", ""),
                )
                messages.warning(request, 'No se seleccionaron productos.')
                return redirect('admin_category_products', pk=pk)

        count = 0
        if action == 'assign':
            count = add_category_to_products(target_ids, category.id)
            messages.success(request, f'{count} productos vinculados a "{category.name}".')
        elif action == 'remove':
            count = remove_category_from_products(target_ids, category.id)
            messages.success(request, f'{count} vinculos removidos de "{category.name}".')

        return redirect('admin_category_products', pk=pk)

    products, search, status, cat_filter = get_filtered_queryset(request.GET)

    paginator = Paginator(products.order_by('name'), 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    enrich_products_with_category_state(page_obj.object_list)
    for product in page_obj.object_list:
        product.is_linked_to_current = any(cat.id == category.id for cat in product.linked_categories)

    all_categories = Category.objects.exclude(pk=pk).select_related('parent').order_by('order', 'name')
    all_category_options = build_category_options(all_categories, include_inactive_suffix=True)

    return render(request, 'admin_panel/categories/manage_products.html', {
        'category': category,
        'page_obj': page_obj,
        'search': search,
        'status': status,
        'category_filter': cat_filter,
        'all_category_options': all_category_options,
        'total_count': products.count(),
        'pagination_count': len(page_obj.object_list),
    })

# ===================== API =====================

@staff_member_required
def get_category_attributes(request, category_id):
    """API: Get attributes for a category."""
    attributes = CategoryAttribute.objects.filter(category_id=category_id).values(
        'name', 'slug', 'type', 'options', 'required', 'is_recommended', 'regex_pattern'
    )
    return JsonResponse({'attributes': list(attributes)})


@staff_member_required
@require_POST
def parse_product_description(request):
    """API: Parse description against category attributes."""
    try:
        data = json.loads(request.body)
        description = data.get('description', '')
        category_id = data.get('category_id')
        
        if not category_id:
            return JsonResponse({'success': False, 'error': 'Category ID required'})
            
        category = Category.objects.get(pk=category_id)
        # Instantiate dummy product to use extraction logic
        product = Product(description=description, category=category)
        extracted = product.extract_attributes_from_description()
        
        return JsonResponse({'success': True, 'attributes': extracted})
    except Exception as e:
        logger.exception("Error parsing product description")
        return JsonResponse({'success': False, 'error': 'No se pudo procesar la descripción.'}, status=400)


@staff_member_required
@require_POST
def parse_clamp_code_api(request):
    """API: Parse ABL/ABT code into attributes."""
    try:
        data = json.loads(request.body)
        code = str(data.get("code", "")).strip()
        known_widths = data.get("known_widths") or None
        known_lengths = data.get("known_lengths") or None

        if not code:
            return JsonResponse({"success": False, "error": "code es obligatorio."}, status=400)

        parsed = parsearCodigo(
            code,
            known_widths=known_widths,
            known_lengths=known_lengths,
        )
        return JsonResponse({"success": True, "result": parsed})
    except ValueError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception("Error parsing clamp code")
        return JsonResponse({"success": False, "error": "No se pudo parsear el codigo."}, status=500)


@staff_member_required
@require_POST
def generate_clamp_code_api(request):
    """API: Generate ABL/ABT code from attributes."""
    try:
        data = json.loads(request.body)
        result = generarCodigo(
            tipo=data.get("tipo"),
            diametro=data.get("diametro"),
            ancho=data.get("ancho"),
            largo=data.get("largo"),
            forma=data.get("forma"),
            with_metadata=True,
        )
        return JsonResponse({"success": True, "result": result})
    except ValueError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception("Error generating clamp code")
        return JsonResponse({"success": False, "error": "No se pudo generar el codigo."}, status=500)


# ===================== IMPORTERS =====================

def run_background_import(task_id, execution_id, import_type, ImporterClass, file_path, dry_run):
    """Function to run in a separate thread."""
    execution = ImportExecution.objects.filter(pk=execution_id).first()
    try:
        preflight_errors = []
        if not dry_run:
            preflight_importer = ImporterClass(file_path)
            preflight_result = preflight_importer.run(dry_run=True)
            preflight_errors = [
                {'row': r.row_number, 'message': str(r.errors)}
                for r in preflight_result.row_results if not r.success
            ][:50]
            if preflight_result.has_errors:
                result_data = {
                    'created': 0,
                    'updated': 0,
                    'errors': preflight_result.errors,
                    'has_errors': True,
                    'row_errors': preflight_errors,
                    'execution_id': execution_id,
                    'import_type': import_type,
                    'message': 'Validacion previa fallida. No se aplicaron cambios.',
                }
                ImportTaskManager.fail_task(task_id, 'La validacion previa detecto errores.')
                if execution:
                    execution.status = ImportExecution.STATUS_FAILED
                    execution.result_summary = result_data
                    execution.error_count = preflight_result.errors
                    execution.finished_at = timezone.now()
                    execution.save(update_fields=['status', 'result_summary', 'error_count', 'finished_at'])
                return

        def progress_callback(current, total):
            ImportTaskManager.update_progress(task_id, current, total, f"Procesando fila {current} de {total}")

        importer = ImporterClass(file_path)
        result = importer.run(dry_run=dry_run, progress_callback=progress_callback)

        created_refs = collect_created_refs(import_type, result.row_results) if not dry_run else []
        result_data = {
            'created': result.created,
            'updated': result.updated,
            'errors': result.errors,
            'has_errors': result.has_errors,
            'row_errors': [
                {'row': r.row_number, 'message': str(r.errors)}
                for r in result.row_results if not r.success
            ][:50],
            'preflight_errors': preflight_errors,
            'execution_id': execution_id,
            'import_type': import_type,
        }

        ImportTaskManager.complete_task(task_id, result_data)

        if execution:
            execution.status = ImportExecution.STATUS_COMPLETED
            execution.created_count = result.created
            execution.updated_count = result.updated
            execution.error_count = result.errors
            execution.result_summary = result_data
            execution.created_refs = created_refs
            execution.finished_at = timezone.now()
            execution.save(
                update_fields=[
                    'status',
                    'created_count',
                    'updated_count',
                    'error_count',
                    'result_summary',
                    'created_refs',
                    'finished_at',
                ]
            )
    except Exception as e:
        traceback.print_exc()
        ImportTaskManager.fail_task(task_id, str(e))
        if execution:
            execution.status = ImportExecution.STATUS_FAILED
            execution.result_summary = {'error': str(e)}
            execution.finished_at = timezone.now()
            execution.save(update_fields=['status', 'result_summary', 'finished_at'])
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass


def import_status(request, task_id):
    """API to poll status."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'failed', 'message': 'Sesion expirada. Inicia sesion nuevamente.'},
            status=401,
        )
    if not request.user.is_staff:
        return JsonResponse(
            {'status': 'failed', 'message': 'No tienes permisos para consultar esta importacion.'},
            status=403,
        )

    status = ImportTaskManager.get_status(task_id)
    if status:
        return JsonResponse(status)

    execution_id = request.GET.get('execution_id', '').strip()
    if execution_id.isdigit():
        try:
            execution = ImportExecution.objects.filter(pk=int(execution_id)).first()
        except DatabaseError:
            return JsonResponse({
                'status': 'processing',
                'current': 0,
                'total': 1,
                'message': 'Esperando base de datos...',
            })

        if execution:
            if execution.status == ImportExecution.STATUS_COMPLETED:
                result = execution.result_summary or {
                    'created': execution.created_count,
                    'updated': execution.updated_count,
                    'errors': execution.error_count,
                    'execution_id': execution.pk,
                    'import_type': execution.import_type,
                }
                return JsonResponse({'status': 'completed', 'result': result})

            if execution.status == ImportExecution.STATUS_FAILED:
                error_msg = str((execution.result_summary or {}).get('error') or 'La importacion fallo.')
                return JsonResponse({'status': 'failed', 'message': error_msg})

            if execution.status == ImportExecution.STATUS_ROLLED_BACK:
                result = execution.result_summary or {
                    'created': execution.created_count,
                    'updated': execution.updated_count,
                    'errors': execution.error_count,
                    'execution_id': execution.pk,
                    'import_type': execution.import_type,
                }
                return JsonResponse({'status': 'completed', 'result': result})

            return JsonResponse({
                'status': 'processing',
                'current': 0,
                'total': 1,
                'message': 'Procesando en segundo plano...',
            })

    return JsonResponse({'status': 'unknown', 'message': 'No se encontro el estado de la importacion.'}, status=404)


@user_passes_test(is_primary_superadmin)
def import_dashboard(request):
    """Import dashboard / hub."""
    active_company = get_active_company(request)
    executions = ImportExecution.objects.select_related('user').order_by('-created_at')
    if active_company:
        executions = executions.filter(company=active_company)
    executions = executions[:40]
    return render(
        request,
        'admin_panel/importers/dashboard.html',
        {'executions': executions, 'active_company': active_company},
    )


@user_passes_test(is_primary_superadmin)
def import_process(request, import_type):
    """Handle file upload and processing for imports."""
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa antes de importar.")
        return redirect('select_company')
    if import_type == 'products':
        FormClass = ProductImportForm
        ImporterClass = ProductImporter
        template = 'admin_panel/importers/import_form.html'
    elif import_type == 'clients':
        FormClass = ClientImportForm
        ImporterClass = ClientImporter
        template = 'admin_panel/importers/import_form.html'
    elif import_type == 'categories':
        FormClass = CategoryImportForm
        ImporterClass = CategoryImporter
        template = 'admin_panel/importers/import_form.html'
    elif import_type == 'abrazaderas':
        FormClass = ProductImportForm
        ImporterClass = AbrazaderaImporter
        template = 'admin_panel/importers/import_form.html'
    else:
        messages.error(request, 'Tipo de importacion no valido.')
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = FormClass(request.POST, request.FILES)
        if form.is_valid():
            try:
                uploaded_file = request.FILES['file']
                preview_only = request.POST.get('preview_only') == '1'
                temp_dir = os.path.join(settings.BASE_DIR, 'media', 'temp_imports')
                os.makedirs(temp_dir, exist_ok=True)

                file_basename = os.path.basename(uploaded_file.name)
                stamp = timezone.now().strftime('%Y%m%d%H%M%S%f')
                file_path = os.path.join(temp_dir, f'import_{stamp}_{file_basename}')
                with open(file_path, 'wb+') as destination:
                    for chunk in uploaded_file.chunks():
                        destination.write(chunk)

                if preview_only:
                    importer = ImporterClass(file_path)
                    preview = importer.run(dry_run=True)
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except OSError:
                        pass

                    return JsonResponse({
                        'success': True,
                        'preview': {
                            'total_rows': preview.total_rows,
                            'created': preview.created,
                            'updated': preview.updated,
                            'errors': preview.errors,
                            'has_errors': preview.has_errors,
                            'row_errors': [
                                {'row': r.row_number, 'message': str(r.errors)}
                                for r in preview.row_results if not r.success
                            ][:50],
                        },
                    })

                dry_run = form.cleaned_data.get('dry_run', True)
                confirm_apply = form.cleaned_data.get('confirm_apply', False)
                if not dry_run and not confirm_apply:
                    return JsonResponse(
                        {
                            'success': False,
                            'error': 'Debes confirmar explicitamente la aplicacion real antes de ejecutar.',
                        },
                        status=400,
                    )
                task_id = ImportTaskManager.start_task()
                execution = ImportExecution.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    company=active_company,
                    import_type=import_type,
                    file_name=file_basename,
                    dry_run=dry_run,
                    status=ImportExecution.STATUS_PROCESSING,
                    metrics={},
                    supplier_name="",
                )

                importer_class_path = f"{ImporterClass.__module__}.{ImporterClass.__name__}"
                dispatch_result = dispatch_import_job(
                    task_id=task_id,
                    execution_id=execution.pk,
                    import_type=import_type,
                    importer_class_path=importer_class_path,
                    file_path=file_path,
                    dry_run=dry_run,
                )

                log_admin_action(
                    request,
                    action='import_start',
                    target_type='import_execution',
                    target_id=execution.pk,
                    details={
                        'import_type': import_type,
                        'dry_run': dry_run,
                        'confirm_apply': bool(confirm_apply),
                        'file_name': file_basename,
                        'backend': dispatch_result.get('backend', 'thread'),
                    },
                )

                return JsonResponse({
                    'success': True,
                    'task_id': task_id,
                    'execution_id': execution.pk,
                    'message': 'Iniciando importacion...',
                })
            except Exception:
                logger.exception('Error starting import process')
                return JsonResponse({'success': False, 'error': 'No se pudo iniciar la importacion.'}, status=500)
    else:
        form = FormClass()

    return render(
        request,
        template,
        {
            'form': form,
            'import_type': import_type,
        },
    )


@user_passes_test(is_primary_superadmin)
@require_POST
@superuser_required_for_modifications
def import_rollback(request, execution_id):
    """Rollback records created by one import execution."""
    execution = get_object_or_404(ImportExecution, pk=execution_id)

    if execution.dry_run:
        messages.warning(request, 'No se puede aplicar rollback sobre una simulacion (dry run).')
        return redirect('admin_import_dashboard')
    if execution.status == ImportExecution.STATUS_ROLLED_BACK:
        messages.info(request, 'Este lote ya fue revertido.')
        return redirect('admin_import_dashboard')
    if execution.status != ImportExecution.STATUS_COMPLETED:
        messages.warning(request, 'Solo se pueden revertir importaciones completadas.')
        return redirect('admin_import_dashboard')

    refs = list(execution.created_refs or [])
    if not refs:
        messages.warning(request, 'No hay registros creados para revertir en este lote.')
        return redirect('admin_import_dashboard')

    deleted_count = 0
    try:
        with transaction.atomic():
            if execution.import_type in ('products', 'abrazaderas'):
                target_qs = Product.objects.filter(sku__in=refs)
            elif execution.import_type == 'categories':
                target_qs = Category.objects.filter(slug__in=refs)
            elif execution.import_type == 'clients':
                target_qs = User.objects.filter(username__in=refs)
            else:
                messages.error(request, 'Este tipo de importacion no soporta rollback automatico.')
                return redirect('admin_import_dashboard')

            deleted_count = target_qs.count()
            target_qs.delete()

            execution.status = ImportExecution.STATUS_ROLLED_BACK
            execution.rollback_at = timezone.now()
            execution.rollback_summary = {
                'deleted_count': deleted_count,
                'refs_total': len(refs),
                'refs_remaining': max(len(refs) - deleted_count, 0),
            }
            execution.save(update_fields=['status', 'rollback_at', 'rollback_summary'])

        log_admin_action(
            request,
            action='import_rollback',
            target_type='import_execution',
            target_id=execution.pk,
            details={
                'import_type': execution.import_type,
                'deleted_count': deleted_count,
                'refs_total': len(refs),
            },
        )
        messages.success(request, f'Rollback aplicado. Registros eliminados: {deleted_count}.')
    except Exception as exc:
        logger.exception('Rollback failed')
        messages.error(request, f'No se pudo completar el rollback: {exc}')

    return redirect('admin_import_dashboard')
# ===================== BULK DELETE ACTIONS =====================

@staff_member_required
@require_POST
@superuser_required_for_modifications
def product_delete_all(request):
    """Deletes ALL products if confirmation is correct."""
    confirmation = request.POST.get('confirmation', '').strip().lower()
    expected = "delete productos"
    
    if confirmation != expected:
        messages.error(request, f'Frase de confirmación incorrecta. Debe escribir: "{expected}"')
        return redirect('admin_product_list')
    
    count, _ = Product.objects.all().delete()
    log_admin_action(
        request,
        action='product_delete_all',
        target_type='product_bulk',
        details={'deleted_count': count},
    )
    messages.success(request, f'Se eliminaron {count} productos correctamente.')
    return redirect('admin_product_list')

@staff_member_required
@require_POST
@superuser_required_for_modifications
def client_delete_all(request):
    """Deactivate ALL clients without hard delete."""
    confirmation = request.POST.get('confirmation', '').strip().lower()
    expected = "delete clientes"
    
    if confirmation != expected:
        messages.error(request, f'Frase de confirmación incorrecta. Debe escribir: "{expected}"')
        return redirect('admin_client_list')

    active_user_ids = list(
        ClientProfile.objects.filter(user_id__isnull=False).values_list('user_id', flat=True)
    )
    deactivated_users = User.objects.filter(id__in=active_user_ids, is_active=True).update(is_active=False)
    deactivated_profiles = ClientProfile.objects.filter(is_approved=True).update(is_approved=False)

    log_admin_change(
        request,
        action='client_deactivate_all',
        target_type='client_bulk',
        before={},
        after={
            'deactivated_users': deactivated_users,
            'deactivated_profiles': deactivated_profiles,
        },
    )
    messages.success(
        request,
        f'Se desactivaron {deactivated_profiles} perfiles y {deactivated_users} usuarios de clientes.',
    )
    return redirect('admin_client_list')

@staff_member_required
@require_POST
@superuser_required_for_modifications
def category_delete_all(request):
    """Deletes ALL categories if confirmation is correct."""
    confirmation = request.POST.get('confirmation', '').strip().lower()
    expected = "delete categorias"
    
    if confirmation != expected:
        messages.error(request, f'Frase de confirmación incorrecta. Debe escribir: "{expected}"')
        return redirect('admin_category_list')
    
    count, _ = Category.objects.all().delete()
    log_admin_action(
        request,
        action='category_delete_all',
        target_type='category_bulk',
        details={'deleted_count': count},
    )
    messages.success(request, f'Se eliminaron {count} categorías correctamente.')
    return redirect('admin_category_list')




