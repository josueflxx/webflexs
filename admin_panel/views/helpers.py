"""
Admin Panel views - Custom admin interface.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.db import transaction, connection, IntegrityError
from django.db import DatabaseError
from django.db.models import (
    Q,
    Case,
    Count,
    Sum,
    Max,
    Avg,
    F,
    When,
    IntegerField,
    DecimalField,
    ExpressionWrapper,
    Value,
    Prefetch,
)
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from django.utils.http import url_has_allowed_host_and_scheme
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
from accounts.services.movement_lifecycle import (
    apply_transaction_state_transition,
    can_transition_transaction_state,
    is_transaction_reopen_locked as service_is_transaction_reopen_locked,
    movement_allows_print as service_movement_allows_print,
)
from orders.models import (
    ClampQuotation,
    Order,
    OrderItem,
    OrderProposal,
    OrderProposalItem,
    OrderRequest,
    OrderRequestEvent,
    OrderRequestItem,
    OrderStatusHistory,
)
from orders.services.workflow import (
    ROLE_ADMIN,
    ROLE_DEPOSITO,
    ROLE_FACTURACION,
    ROLE_VENTAS,
    can_user_transition_order,
    resolve_user_order_role,
)
from orders.services.request_workflow import (
    confirm_order_request,
    convert_request_to_order,
    create_order_proposal,
    record_order_request_event,
    reject_order_request,
)
from core.models import (
    AdminCompanyAccess,
    Company,
    DocumentSeries,
    FISCAL_BILLABLE_DOC_TYPES,
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_FC,
    FISCAL_INVOICE_DOC_TYPES,
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
    SALES_BILLING_MODE_INTERNAL_DOCUMENT,
    SALES_BILLING_MODE_MANUAL_FISCAL,
    SalesDocumentType,
    SiteSettings,
    CatalogAnalyticsEvent,
    AdminAuditLog,
    ImportExecution,
    CatalogExcelTemplate,
    CatalogExcelTemplateSheet,
    CatalogExcelTemplateColumn,
    StockMovement,
    Warehouse,
)
from core.services.company_context import (
    admin_company_access_table_available,
    get_active_company,
    get_default_company,
    get_default_client_origin_company,
    get_preferred_client_company,
    get_user_companies,
    set_active_company,
    user_has_company_access,
)
from django.contrib.auth.models import Group, User
from admin_panel.forms.import_forms import ProductImportForm, ClientImportForm, CategoryImportForm
from admin_panel.forms.category_forms import CategoryForm
from admin_panel.fiscal_views import fiscal_health_view, fiscal_report_view
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
from core.services.fiscal import (
    is_company_fiscal_ready,
    is_invoice_ready,
    resolve_payment_due_date,
)
from core.services.fiscal_notifications import send_fiscal_document_email
from core.services.arca_client import ArcaConfigurationError, ArcaTemporaryError, ArcaWsfeClient
from core.services.fiscal_documents import (
    close_fiscal_document,
    create_local_fiscal_document_from_order,
    reopen_fiscal_document,
    register_external_fiscal_document_for_order,
    void_fiscal_document,
)

from core.services.documents import (
    ensure_document_for_adjustment,
    ensure_document_for_order,
    ensure_document_for_payment,
)
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
PRIMARY_SUPERADMIN_USERNAME = getattr(settings, "ADMIN_PRIMARY_SUPERADMIN_USERNAME", "josueflexs")
ADMIN_ROLE_CHOICES = [
    (ROLE_ADMIN, "Administracion"),
    (ROLE_VENTAS, "Ventas"),
    (ROLE_DEPOSITO, "Deposito"),
    (ROLE_FACTURACION, "Facturacion"),
]
ADMIN_ROLE_LABELS = dict(ADMIN_ROLE_CHOICES)
FISCAL_PRINT_DOC_META = {
    "FA": {"letter": "A", "code": "001"},
    "FB": {"letter": "B", "code": "006"},
    "FC": {"letter": "C", "code": "011"},
    "NCA": {"letter": "A", "code": "003"},
    "NCB": {"letter": "B", "code": "008"},
    "NCC": {"letter": "C", "code": "013"},
    "NDA": {"letter": "A", "code": "002"},
    "NDB": {"letter": "B", "code": "007"},
    "NDC": {"letter": "C", "code": "012"},
}
FISCAL_PRINT_COPY_LABELS = {
    "original": "ORIGINAL",
    "duplicado": "DUPLICADO",
    "triplicado": "TRIPLICADO",
}
INVOICE_FISCAL_DOC_TYPES = tuple(sorted(FISCAL_INVOICE_DOC_TYPES))
BILLABLE_FISCAL_DOC_TYPES = tuple(sorted(FISCAL_BILLABLE_DOC_TYPES))
EMITTABLE_FISCAL_DOC_TYPES = tuple(choice[0] for choice in FiscalDocument.DOC_TYPE_CHOICES)
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
    if not admin_company_access_table_available():
        return "all"
    has_db_scope = AdminCompanyAccess.objects.filter(
        user=user,
        is_active=True,
        company__is_active=True,
    ).exists()
    return "limited" if has_db_scope else "all"


def get_admin_user_scope_ids(user):
    if not admin_company_access_table_available():
        return set()
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
    query = Q(is_staff=True) | Q(is_superuser=True) | Q(groups__name__in=valid_roles)
    if admin_company_access_table_available():
        query |= Q(company_access_links__is_active=True)
    return User.objects.filter(query).distinct().order_by("username")

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
    company = getattr(order, "company", None)
    company_tax_condition = str(getattr(company, "tax_condition", "") or "").strip().lower()
    if company_tax_condition in {"monotributista", "exento"}:
        return FISCAL_DOC_TYPE_FC
    client_profile = _get_order_client_profile(order)
    if client_profile and client_profile.iva_condition == "responsable_inscripto":
        return FISCAL_DOC_TYPE_FA
    return FISCAL_DOC_TYPE_FB


def _resolve_invoice_sales_document_type_for_order(order):
    preferred_doc_type = _resolve_preferred_invoice_doc_type(order)
    origin_channel = getattr(order, "origin_channel", "")
    configured = resolve_sales_document_type(
        company=order.company,
        behavior=SALES_BEHAVIOR_FACTURA,
        fiscal_doc_type=preferred_doc_type,
        origin_channel=origin_channel,
    )
    if configured:
        return configured
    return resolve_sales_document_type(
        company=order.company,
        behavior=SALES_BEHAVIOR_FACTURA,
        origin_channel=origin_channel,
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


def _build_fiscal_collection_snapshot(document):
    total = Decimal(document.total or 0).quantize(Decimal("0.01"))
    paid = Decimal("0.00")
    if getattr(document, "order_id", None):
        paid = (
            document.order.payments.filter(is_cancelled=False).aggregate(total=Sum("amount")).get("total")
            or Decimal("0.00")
        )
    paid = Decimal(paid or 0).quantize(Decimal("0.01"))
    pending = (total - paid).quantize(Decimal("0.01"))
    if pending < 0:
        pending = Decimal("0.00")

    due_date = getattr(document, "payment_due_date", None)
    if not due_date and document.status in {FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED}:
        due_date = resolve_payment_due_date(
            order=getattr(document, "order", None),
            issued_at=getattr(document, "issued_at", None) or getattr(document, "created_at", None),
        )
    days_to_due = None
    today = timezone.localdate()
    if due_date:
        days_to_due = (due_date - today).days

    if pending <= Decimal("0.00"):
        status_key = "paid"
        status_label = "Pagado"
    elif due_date and days_to_due is not None and days_to_due < 0:
        status_key = "overdue"
        status_label = "Vencido"
    elif due_date and days_to_due is not None and days_to_due <= 3:
        status_key = "due_soon"
        status_label = "Por vencer"
    else:
        status_key = "open"
        status_label = "Abierto"

    return {
        "total": total,
        "paid": paid,
        "pending": pending,
        "due_date": due_date,
        "days_to_due": days_to_due,
        "status_key": status_key,
        "status_label": status_label,
        "is_overdue": status_key == "overdue",
    }


def _resolve_order_charge_transaction(order):
    if not order:
        return None
    return (
        ClientTransaction.objects.filter(
            order=order,
            transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
        )
        .order_by("-occurred_at", "-id")
        .first()
    )


def _resolve_internal_document_transaction(document):
    if not document:
        return None
    if getattr(document, "transaction_id", None):
        return document.transaction
    if getattr(document, "payment_id", None):
        return (
            ClientTransaction.objects.filter(
                payment_id=document.payment_id,
                transaction_type=ClientTransaction.TYPE_PAYMENT,
            )
            .order_by("-occurred_at", "-id")
            .first()
        )
    if getattr(document, "order_id", None) and getattr(document, "doc_type", "") == DocumentSeries.DOC_PED:
        return _resolve_order_charge_transaction(getattr(document, "order", None))
    sales_document_type = getattr(document, "sales_document_type", None)
    if (
        getattr(document, "order_id", None)
        and getattr(document, "doc_type", "") == DocumentSeries.DOC_REM
        and (not sales_document_type or getattr(sales_document_type, "generate_account_movement", False))
    ):
        return _resolve_order_charge_transaction(getattr(document, "order", None))
    if sales_document_type and not getattr(sales_document_type, "generate_account_movement", False):
        return None
    if getattr(document, "order_id", None):
        return _resolve_order_charge_transaction(getattr(document, "order", None))
    return None


def _resolve_fiscal_document_transaction(document):
    if not document or not getattr(document, "order_id", None):
        return None
    transaction_obj = _resolve_order_charge_transaction(getattr(document, "order", None))
    if getattr(document, "status", "") in {
        FISCAL_STATUS_AUTHORIZED,
        FISCAL_STATUS_EXTERNAL_RECORDED,
    }:
        document_changed_at = getattr(document, "updated_at", None) or getattr(document, "created_at", None)
        tx_changed_at = getattr(transaction_obj, "updated_at", None) if transaction_obj else None
        should_sync_placeholder = (
            transaction_obj is None
            or (
                (transaction_obj.movement_state or ClientTransaction.STATE_OPEN) == ClientTransaction.STATE_OPEN
                and document_changed_at
                and tx_changed_at
                and tx_changed_at <= document_changed_at
            )
        )
        if should_sync_placeholder:
            try:
                transaction_obj = sync_order_charge_transaction(order=document.order)
            except Exception:
                logger.exception(
                    "No se pudo sincronizar el movimiento de cuenta corriente para el comprobante fiscal %s",
                    getattr(document, "pk", None),
                )
    return transaction_obj


def _movement_allows_print(transaction_obj):
    return service_movement_allows_print(transaction_obj)


def _is_transaction_reopen_locked(transaction_obj):
    return service_is_transaction_reopen_locked(transaction_obj)


def _is_order_items_edit_locked(order):
    """
    Items stay editable in draft orders unless the movement is already closed
    and hard-locked by final fiscal/commercial conditions.
    """
    if not order:
        return False
    movement_transaction = _resolve_order_charge_transaction(order)
    if not movement_transaction:
        return False
    movement_state = movement_transaction.movement_state or ClientTransaction.STATE_OPEN
    if movement_state != ClientTransaction.STATE_CLOSED:
        return False
    return _is_transaction_reopen_locked(movement_transaction)


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


def _resolve_order_item_pricing(order, product):
    discount_percentage = Decimal("0")
    price_list = None
    pricing = None
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

    unit_price_base = (
        Decimal(pricing.base_price) if pricing and pricing.base_price is not None else Decimal(product.price or 0)
    ).quantize(Decimal("0.01"))
    final_price = (
        Decimal(pricing.final_price) if pricing and pricing.final_price is not None else Decimal(product.price or 0)
    ).quantize(Decimal("0.01"))
    discount_used = (
        Decimal(pricing.discount_percentage)
        if pricing and pricing.discount_percentage is not None
        else Decimal(discount_percentage or 0)
    ).quantize(Decimal("0.01"))
    return unit_price_base, final_price, discount_used, price_list


def _recalculate_order_totals_from_items(order, *, discount_percentage=None):
    items = list(order.items.all())
    subtotal = sum(
        (
            (item.unit_price_base or item.price_at_purchase or Decimal("0")) * item.quantity
            for item in items
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    if discount_percentage is None:
        discount_percentage = order.discount_percentage or Decimal("0")
    discount_percentage = Decimal(discount_percentage or 0).quantize(Decimal("0.01"))
    discount_amount = (subtotal * (discount_percentage / Decimal("100"))).quantize(Decimal("0.01"))
    total = (subtotal - discount_amount).quantize(Decimal("0.01"))
    order.subtotal = subtotal
    order.discount_percentage = discount_percentage
    order.discount_amount = discount_amount
    order.total = total
    order.save(update_fields=["subtotal", "discount_percentage", "discount_amount", "total", "updated_at"])


def _resolve_related_order_for_quick_action(*, client, active_company, source_tx_id):
    """Resolve source order from a client ledger transaction used for related sales."""
    raw_value = str(source_tx_id or "").strip()
    if not raw_value:
        return None, None, ""
    if not raw_value.isdigit():
        return None, None, "El movimiento relacionado no es valido."

    source_tx = (
        ClientTransaction.objects.select_related("order", "payment__order", "company")
        .filter(pk=int(raw_value), client_profile=client)
        .first()
    )
    if not source_tx:
        return None, None, "No se encontro el movimiento seleccionado para relacionar."

    if (
        active_company
        and source_tx.company_id
        and source_tx.company_id != active_company.id
    ):
        return None, source_tx, "El movimiento seleccionado pertenece a otra empresa."

    related_order = None
    if source_tx.order_id:
        related_order = source_tx.order
    elif source_tx.payment_id and getattr(source_tx, "payment", None) and source_tx.payment.order_id:
        related_order = source_tx.payment.order

    if not related_order:
        return None, source_tx, "El movimiento seleccionado no tiene un pedido base para relacionar."

    if (
        active_company
        and related_order.company_id
        and related_order.company_id != active_company.id
    ):
        return None, source_tx, "El pedido base del movimiento pertenece a otra empresa."

    if (
        client.user_id
        and related_order.user_id
        and related_order.user_id != client.user_id
    ):
        return None, source_tx, "El pedido base no coincide con el cliente seleccionado."

    return related_order, source_tx, ""


def _resolve_related_order_from_order_id_for_quick_action(*, client, active_company, source_order_id):
    """Resolve source order directly from order id for related sales actions."""
    raw_value = str(source_order_id or "").strip()
    if not raw_value:
        return None, ""
    if not raw_value.isdigit():
        return None, "El pedido relacionado no es valido."

    source_order = (
        Order.objects.select_related("company", "user")
        .filter(pk=int(raw_value))
        .first()
    )
    if not source_order:
        return None, "No se encontro el pedido seleccionado para relacionar."

    if (
        active_company
        and source_order.company_id
        and source_order.company_id != active_company.id
    ):
        return None, "El pedido seleccionado pertenece a otra empresa."

    if (
        client.user_id
        and source_order.user_id
        and source_order.user_id != client.user_id
    ):
        return None, "El pedido seleccionado no coincide con el cliente."

    return source_order, ""


def _build_related_sales_document_actions(*, company, operations_locked=False, quick_order_url=""):
    """Build related-sale quick actions by configured sales document types."""
    if not company:
        return []

    actions = []
    quick_type_queryset = SalesDocumentType.objects.filter(
        company=company,
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
        relation_action_value = ""
        relation_help_text = ""
        relation_css_class = ""

        if behavior in {SALES_BEHAVIOR_COTIZACION, SALES_BEHAVIOR_PRESUPUESTO}:
            relation_action_value = "quote"
            relation_help_text = "Copia productos del movimiento base y crea un nuevo borrador."
            relation_css_class = "is-quote"
        elif behavior == SALES_BEHAVIOR_PEDIDO:
            relation_action_value = "order"
            relation_help_text = "Duplica el pedido base con sus productos para un nuevo movimiento."
            relation_css_class = "is-order"
        elif behavior == SALES_BEHAVIOR_REMITO:
            relation_action_value = "remito"
            relation_help_text = "Genera o abre el remito para el pedido del movimiento base."
            relation_css_class = "is-remito"
        elif behavior == SALES_BEHAVIOR_FACTURA:
            relation_action_value = "invoice"
            relation_help_text = "Genera o abre la factura del pedido del movimiento base."
            relation_css_class = "is-fiscal"
        elif behavior == SALES_BEHAVIOR_NOTA_CREDITO:
            relation_action_value = "credit_note"
            relation_help_text = "Abre la factura base del movimiento para gestionar nota de credito."
            relation_css_class = "is-credit-note"

        if relation_action_value:
            actions.append(
                {
                    "sales_document_type": item,
                    "label": item.name,
                    "help_text": relation_help_text,
                    "url": quick_order_url,
                    "action_value": relation_action_value,
                    "disabled": operations_locked,
                    "css_class": relation_css_class,
                }
            )

    return actions


def _create_related_order_from_source(
    *,
    source_order,
    client,
    client_company,
    company,
    origin_channel,
    actor=None,
    created_label="Pedido",
    selected_sales_document_type=None,
):
    """Create a new draft order copying commercial snapshot and items from another order."""
    if not source_order:
        raise ValidationError("No se encontro el pedido origen para relacionar.")
    if not company:
        raise ValidationError("Debes seleccionar una empresa para crear el movimiento relacionado.")

    source_items = list(
        source_order.items.select_related("product", "clamp_request", "price_list").all()
    )
    relation_note = f"{created_label} relacionada desde pedido #{source_order.pk}."
    if selected_sales_document_type:
        relation_note = (
            f"{relation_note} Tipo comercial: {selected_sales_document_type.name}."
        )

    with transaction.atomic():
        related_order = Order.objects.create(
            user=client.user or source_order.user,
            company=company,
            origin_channel=origin_channel or Order.ORIGIN_ADMIN,
            status=Order.STATUS_DRAFT,
            priority=source_order.priority or Order.PRIORITY_NORMAL,
            notes=source_order.notes or "",
            admin_notes=relation_note,
            subtotal=Decimal("0.00"),
            discount_percentage=source_order.discount_percentage or Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            total=Decimal("0.00"),
            client_company=source_order.client_company or client.company_name or "",
            client_cuit=source_order.client_cuit or client.cuit_dni or "",
            client_address=source_order.client_address or client.address or "",
            client_phone=source_order.client_phone or client.phone or "",
            client_company_ref=client_company or source_order.client_company_ref,
            saas_document_type="",
            saas_document_number="",
            saas_document_cae="",
            follow_up_note="",
        )

        for item in source_items:
            OrderItem.objects.create(
                order=related_order,
                product=item.product,
                clamp_request=item.clamp_request,
                product_sku=item.product_sku,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price_base=item.unit_price_base,
                discount_percentage_used=item.discount_percentage_used,
                price_list=item.price_list,
                price_at_purchase=item.price_at_purchase,
                subtotal=item.subtotal,
            )

        _recalculate_order_totals_from_items(
            related_order,
            discount_percentage=source_order.discount_percentage,
        )
        OrderStatusHistory.objects.create(
            order=related_order,
            from_status="",
            to_status=related_order.status,
            changed_by=actor if getattr(actor, "is_authenticated", False) else None,
            note=f"{created_label} relacionada creada desde pedido #{source_order.pk}",
        )

    return related_order


def _create_draft_order_for_client(
    *,
    client,
    client_company,
    company,
    origin_channel,
    actor=None,
    created_label="Pedido",
    admin_note="",
    history_note="",
):
    """Create a new draft order for a client using current commercial rules."""
    if not company:
        raise ValidationError("Debes seleccionar una empresa para crear el pedido.")
    if not getattr(client, "user_id", None):
        raise ValidationError("El cliente no tiene usuario vinculado para crear pedidos.")

    try:
        from core.services.pricing import (
            resolve_pricing_context,
            resolve_effective_discount_percentage,
            resolve_effective_price_list,
        )

        _, _, client_category = resolve_pricing_context(client.user, company)
        discount_percentage = resolve_effective_discount_percentage(
            client_profile=client,
            company=company,
            client_company=client_company,
            client_category=client_category,
        )
        price_list = resolve_effective_price_list(company, client_company, client_category)
    except Exception:
        discount_percentage = Decimal("0")
        price_list = None

    order = Order.objects.create(
        user=client.user,
        company=company,
        origin_channel=origin_channel or Order.ORIGIN_ADMIN,
        status=Order.STATUS_DRAFT,
        priority=Order.PRIORITY_NORMAL,
        notes="",
        admin_notes=admin_note or f"{created_label} creada desde panel admin.",
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
        changed_by=actor if getattr(actor, "is_authenticated", False) else None,
        note=history_note or f"{created_label} creado desde panel admin",
    )
    if price_list:
        order.admin_notes = f"{order.admin_notes} Lista: {price_list.name}"
        order.save(update_fields=["admin_notes", "updated_at"])

    return order


def _is_ajax_request(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def is_primary_superadmin(user):
    """Allow only designated primary superadmin account."""
    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_superuser
        and str(getattr(user, "username", "")).strip().lower()
        == str(PRIMARY_SUPERADMIN_USERNAME).strip().lower()
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


def can_manage_fiscal_operations(user):
    """
    Fiscal operations (emit/void/close/delete/send) require role Facturacion/Admin.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role_values = set(get_admin_role_values(user))
    return bool(ROLE_ADMIN in role_values or ROLE_FACTURACION in role_values)


def _deny_fiscal_operation_if_needed(request, *, redirect_url, action_label):
    if can_manage_fiscal_operations(request.user):
        return None
    messages.error(
        request,
        f"No tenes permisos para {action_label}. Requiere rol Facturacion o Administracion.",
    )
    if hasattr(redirect_url, "status_code"):
        return redirect_url
    return redirect(redirect_url)


def _resolve_safe_next_url(request, default_url):
    next_url = str(request.POST.get("next", "")).strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return default_url


def _send_password_reset_email_for_user(request, target_user):
    email = str(getattr(target_user, "email", "") or "").strip()
    if not email:
        return False, "El usuario no tiene email cargado."
    if not getattr(target_user, "is_active", False):
        return False, "El usuario esta inactivo y no puede recuperar contrasena."
    if not target_user.has_usable_password():
        return False, "El usuario no tiene una contrasena recuperable."

    form = PasswordResetForm({"email": email})
    if not form.is_valid():
        return False, "No se pudo generar el mail de recuperacion."

    target_is_eligible = any(user.pk == target_user.pk for user in form.get_users(email))
    if not target_is_eligible:
        return False, "El usuario no cumple condiciones para recuperar contrasena."

    form.save(
        request=request,
        use_https=request.is_secure(),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        email_template_name="accounts/password_reset_email.txt",
        html_email_template_name="accounts/password_reset_email.html",
        subject_template_name="accounts/password_reset_subject.txt",
    )
    return True, ""


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
            if document.doc_type in FISCAL_INVOICE_DOC_TYPES:
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
    values = {
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
    if form_data is None:
        return values

    text_fields = [
        "username",
        "email",
        "first_name",
        "last_name",
        "company_id",
        "company_name",
        "cuit_dni",
        "document_type",
        "document_number",
        "discount",
        "client_category",
        "province",
        "fiscal_province",
        "fiscal_city",
        "address",
        "fiscal_address",
        "postal_code",
        "phone",
        "client_type",
        "iva_condition",
        "notes",
    ]
    for field_name in text_fields:
        if field_name in form_data:
            values[field_name] = str(form_data.get(field_name, "")).strip()

    bool_defaults = {
        "user_is_active": values["user_is_active"] if client else False,
        "client_is_approved": values["client_is_approved"] if client else False,
        "company_is_active": values["company_is_active"] if client else False,
    }
    for field_name, default_value in bool_defaults.items():
        values[field_name] = _is_checked(form_data, field_name, default=default_value)

    if hasattr(form_data, "getlist"):
        posted_linked_ids = _extract_linked_company_ids(form_data)
        if client is None or "linked_company_ids" in form_data:
            values["linked_company_ids"] = posted_linked_ids

    return values


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


# ===================== CLAMP QUOTER =====================

def _format_currency_ars(value):
    decimal_value = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    formatted = f"{decimal_value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


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



__all__ = ['ADMIN_ROLE_CHOICES', 'ADMIN_ROLE_LABELS', 'BILLABLE_FISCAL_DOC_TYPES', 'CLIENT_EXPORT_ENCODING_CHOICES', 'CLIENT_EXPORT_PRESET_CHOICES', 'CLIENT_FACTURABLE_STATUSES', 'CLIENT_REMITO_READY_STATUSES', 'CLIENT_REPORT_CURRENCY_CHOICES', 'CLIENT_REPORT_DATE_RANGE_CHOICES', 'CLIENT_REPORT_DEBTOR_CHOICES', 'CLIENT_REPORT_OPTIONAL_COLUMNS', 'CLIENT_REPORT_ORDER_STATUSES', 'CLIENT_REPORT_RANKING_CHOICES', 'CLIENT_REPORT_RESULTS_SORT_FIELDS', 'CLIENT_REPORT_STATE_CHOICES', 'CLIENT_REPORT_TEXT_FIELD_CHOICES', 'EMITTABLE_FISCAL_DOC_TYPES', 'FISCAL_PRINT_COPY_LABELS', 'FISCAL_PRINT_DOC_META', 'INVOICE_FISCAL_DOC_TYPES', 'ORDER_INTERNAL_DOC_STATUS_RULES', 'ORDER_PRODUCT_SEARCH_FIELDS', 'PRIMARY_SUPERADMIN_USERNAME', '_annotate_client_orders_with_documents', '_build_client_company_summary_rows', '_build_client_form_values', '_build_client_report_queryset', '_build_client_report_row', '_build_fiscal_collection_snapshot', '_build_related_sales_document_actions', '_client_export_csv_response', '_client_report_csv_response', '_client_report_date_label', '_client_report_matches_text', '_client_reports_nav', '_client_tools_nav', '_create_draft_order_for_client', '_create_related_order_from_source', '_delete_orphan_product_image', '_deny_fiscal_operation_if_needed', '_extract_linked_company_ids', '_find_products_for_order_query', '_format_currency_ars', '_get_client_export_rows', '_get_client_orders_queryset', '_get_client_report_locality_choices', '_get_fiscal_workflow_state', '_get_order_client_profile', '_get_report_client_address', '_get_report_client_balance', '_get_report_client_category', '_get_report_client_contact_name', '_get_report_client_document_detail', '_get_report_client_locality', '_get_report_client_price_list_name', '_get_report_client_province', '_get_report_client_state', '_get_report_company_link', '_is_ajax_request', '_is_checked', '_is_order_items_edit_locked', '_is_standalone_report_request', '_is_transaction_reopen_locked', '_movement_allows_print', '_recalculate_order_totals_from_items', '_redirect_admin_product_list_with_filters', '_redirect_client_history', '_render_client_form', '_resolve_client_editor_company', '_resolve_default_point_of_sale', '_resolve_fiscal_document_transaction', '_resolve_internal_document_transaction', '_resolve_invoice_sales_document_type_for_order', '_resolve_linked_companies', '_resolve_order_charge_transaction', '_resolve_order_item_pricing', '_resolve_preferred_invoice_doc_type', '_resolve_related_order_for_quick_action', '_resolve_related_order_from_order_id_for_quick_action', '_resolve_report_date_range', '_resolve_safe_next_url', '_send_password_reset_email_for_user', '_store_bulk_product_image', '_sum_decimal_values', '_validate_admin_image_upload', 'apply_admin_text_search', 'build_admin_user_snapshot', 'build_category_options', 'build_category_tree_rows', 'build_product_filter_chips', 'build_supplier_products_queryset', 'calculate_category_deactivation_impact', 'can_delete_client_record', 'can_edit_client_profile', 'can_manage_client_credentials', 'can_manage_fiscal_operations', 'collect_created_refs', 'detect_category_integrity_issues', 'enrich_products_with_category_state', 'ensure_admin_role_groups', 'extract_target_product_ids_from_post', 'generate_clamp_code_api', 'get_active_client_categories', 'get_admin_company_filter', 'get_admin_company_required', 'get_admin_company_scope_mode', 'get_admin_role_label', 'get_admin_role_labels', 'get_admin_role_value', 'get_admin_role_values', 'get_admin_selected_company', 'get_admin_user_scope_ids', 'get_cached_category_options', 'get_client_categories_for_client', 'get_managed_admin_users_queryset', 'get_product_queryset', 'get_recent_admin_user_audit_logs', 'is_primary_superadmin', 'normalize_admin_search_query', 'parse_admin_decimal_input', 'set_admin_role_for_user', 'set_admin_roles_for_user', 'settings_view', 'validate_attributes_for_category']
__all__.append('admin_company_access_table_available')
