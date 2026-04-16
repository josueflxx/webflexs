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



from .helpers import *
from core.services.operational_timeline import build_client_activity_timeline



def parse_optional_client_category(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    try:
        return ClientCategory.objects.get(pk=int(raw))
    except (ValueError, TypeError, ClientCategory.DoesNotExist):
        raise ValueError("Categoria de cliente invalida.")


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

    missing_email_filter = Q(user__isnull=True) | Q(user__email__isnull=True) | Q(user__email__exact="")
    missing_email_only = str(request.GET.get("missing_email", "")).strip() == "1"
    missing_email_total = clients.filter(missing_email_filter).count()
    missing_email_sample = list(
        clients.filter(missing_email_filter)
        .order_by("company_name", "user__username")[:8]
    )
    if missing_email_only:
        clients = clients.filter(missing_email_filter)

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
        'missing_email_only': missing_email_only,
        'missing_email_total': missing_email_total,
        'missing_email_sample': missing_email_sample,
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
    default_client_company = get_preferred_client_company(companies)
    initial_company = default_client_company or active_company
    initial_values = _build_client_form_values(active_company=initial_company)
    if initial_company and not initial_values.get("company_id"):
        initial_values["company_id"] = str(initial_company.pk)

    if request.method == "POST":
        form_values = _build_client_form_values(form_data=request.POST)
        company = None
        company_id = form_values.get("company_id", "")
        if company_id.isdigit():
            company = companies.filter(pk=int(company_id), is_active=True).first()
        if not company:
            company = get_preferred_client_company(companies) or active_company or get_default_client_origin_company()
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
        form_values = _build_client_form_values(
            client=client,
            active_company=active_company,
            client_company=client_company,
            form_data=request.POST,
        )
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
        should_update_legacy = client.uses_legacy_commercial_rules(company=company)
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
    selected_origin_channel = str(
        request.POST.get("origin_channel", Order.ORIGIN_ADMIN)
    ).strip().lower()
    if selected_origin_channel not in dict(Order.ORIGIN_CHOICES):
        selected_origin_channel = Order.ORIGIN_ADMIN
    active_company = get_admin_selected_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa valida para crear el documento.")
        return _redirect_client_history(client)

    client_company = client.get_company_link(active_company)
    if not client_company:
        messages.error(request, "El cliente no tiene relacion comercial activa con esta empresa.")
        return _redirect_client_history(client, active_company)

    source_tx_id = str(request.POST.get("source_tx_id", "")).strip()
    source_order_id = str(request.POST.get("source_order_id", "")).strip()
    source_order = None
    source_transaction = None
    if source_tx_id:
        source_order, source_transaction, source_error = _resolve_related_order_for_quick_action(
            client=client,
            active_company=active_company,
            source_tx_id=source_tx_id,
        )
        if source_error:
            messages.error(request, source_error)
            return _redirect_client_history(client, active_company)
    elif source_order_id:
        source_order, source_error = _resolve_related_order_from_order_id_for_quick_action(
            client=client,
            active_company=active_company,
            source_order_id=source_order_id,
        )
        if source_error:
            messages.error(request, source_error)
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
    if action in {"invoice", "credit_note"}:
        history_url = reverse("admin_client_order_history", args=[client.pk])
        history_url = f"{history_url}?{urlencode({'company_id': active_company.pk})}"
        denied_response = _deny_fiscal_operation_if_needed(
            request,
            redirect_url=history_url,
            action_label="generar comprobantes fiscales",
        )
        if denied_response:
            return denied_response

    if action == "remito":
        from core.services.documents import ensure_document_for_order

        orders_qs = _get_client_orders_queryset(client, company=active_company).order_by(
            "-status_updated_at",
            "-created_at",
        )
        remito_order = source_order or orders_qs.filter(status__in=CLIENT_REMITO_READY_STATUSES).first()
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
                    if remito_order.status not in CLIENT_REMITO_READY_STATUSES:
                        messages.warning(
                            request,
                            f"El pedido #{remito_order.pk} aun no esta listo para remito. Debe estar enviado o entregado.",
                        )
                        return redirect("admin_order_detail", pk=remito_order.pk)
                    remito_document = ensure_document_for_order(remito_order, doc_type=DocumentSeries.DOC_REM)
            if remito_document:
                if source_order and source_transaction:
                    success_message = (
                        f"Se relaciono el movimiento #{source_transaction.pk} y se abrio el remito del pedido #{remito_order.pk}."
                    )
                elif source_order:
                    success_message = f"Se abrio el remito del pedido relacionado #{remito_order.pk}."
                else:
                    success_message = f"Se abrio el remito mas reciente del cliente (pedido #{remito_order.pk})."
                messages.success(
                    request,
                    success_message,
                )
                print_url = (
                    f"{reverse('admin_internal_document_print', args=[remito_document.pk])}"
                    f"?{urlencode({'copy': 'original'})}"
                )
                return redirect(print_url)

            if source_order:
                messages.info(request, f"Se abrio el pedido relacionado #{remito_order.pk} para revisar el remito.")
            else:
                messages.info(request, f"Se abrio el pedido #{remito_order.pk} para revisar el remito.")
            return redirect("admin_order_detail", pk=remito_order.pk)

        pending_order = orders_qs.filter(
            status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]
        ).first()
        if source_order:
            messages.warning(
                request,
                "El movimiento seleccionado no tiene un pedido listo para remito en este momento.",
            )
            return _redirect_client_history(client, active_company)
        if pending_order:
            messages.warning(
                request,
                f"No hay pedidos enviados o entregados. Se abrio el pedido #{pending_order.pk} para avanzar a remito.",
            )
            return redirect("admin_order_detail", pk=pending_order.pk)

        messages.error(request, "No hay pedidos del cliente listos para remito en esta empresa.")
        return _redirect_client_history(client, active_company)

    if action == "invoice":
        if source_order:
            facturable_orders = [source_order]
        else:
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
                .filter(company=active_company, order_id__in=order_ids, doc_type__in=INVOICE_FISCAL_DOC_TYPES)
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
                
                # Check if it should be emitted automatically
                if fiscal_doc.issue_mode == "arca_wsfe" and fiscal_doc.status == "ready_to_issue":
                    try:
                        from core.tasks import emit_fiscal_document_async_task
                        from core.services.fiscal_emission import _validate_before_submit
                        _validate_before_submit(fiscal_doc)
                        emit_fiscal_document_async_task.delay(document_id=fiscal_doc.pk, actor_id=request.user.pk)
                        messages.success(
                            request,
                            f"Se genero y encolo en AFIP la factura {fiscal_doc.commercial_type_label} para el pedido #{order.pk}.",
                        )
                    except Exception as exc:
                        messages.warning(
                            request,
                            f"Se genero {fiscal_doc.commercial_type_label} localmente, pero no se pudo encolar: {str(exc)}",
                        )
                else:
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
            if source_order and source_transaction:
                info_message = (
                    f"El movimiento #{source_transaction.pk} ya tiene comprobante fiscal relacionado. Se abrio el documento existente."
                )
            else:
                info_message = (
                    "El pedido facturable mas reciente ya tiene comprobante fiscal. Se abrio el documento existente."
                )
            messages.info(
                request,
                info_message,
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
            if source_order and source_transaction:
                info_message = (
                    f"El movimiento #{source_transaction.pk} ya tiene comprobante externo. Se abrio el pedido #{existing_saas_order.pk}."
                )
            else:
                info_message = (
                    f"El pedido facturable mas reciente ya tiene comprobante externo. Se abrio el pedido #{existing_saas_order.pk}."
                )
            messages.info(
                request,
                info_message,
            )
            return redirect("admin_order_detail", pk=existing_saas_order.pk)

        if source_order:
            messages.error(
                request,
                "El movimiento relacionado no tiene un pedido listo para facturar.",
            )
            return _redirect_client_history(client, active_company)
        messages.error(request, "No hay pedidos del cliente listos para facturar en esta empresa.")
        return _redirect_client_history(client, active_company)

    if action == "credit_note":
        latest_invoice_queryset = (
            FiscalDocument.objects.select_related("order", "point_of_sale", "related_document")
            .filter(
                company=active_company,
                doc_type__in=INVOICE_FISCAL_DOC_TYPES,
            )
            .filter(
                Q(client_profile=client) | Q(client_company_ref__client_profile=client)
            )
            .exclude(status="voided")
        )
        if source_order:
            latest_invoice_queryset = latest_invoice_queryset.filter(order=source_order)
        latest_invoice_document = latest_invoice_queryset.order_by("-created_at", "-id").first()
        if latest_invoice_document:
            if source_order and source_transaction:
                success_message = (
                    f"Se abrio el comprobante base del movimiento #{source_transaction.pk} para gestionar la nota de credito."
                )
            else:
                success_message = "Se abrio el comprobante base mas reciente para gestionar la nota de credito."
            messages.success(
                request,
                success_message,
            )
            return redirect("admin_fiscal_document_detail", pk=latest_invoice_document.pk)

        saas_invoice_queryset = (
            _get_client_orders_queryset(client, company=active_company)
            .filter(Q(saas_document_type__gt="") | Q(saas_document_number__gt=""))
            .order_by("-created_at")
        )
        if source_order:
            saas_invoice_queryset = saas_invoice_queryset.filter(pk=source_order.pk)
        saas_invoice_order = saas_invoice_queryset.first()
        if saas_invoice_order:
            if source_order and source_transaction:
                info_message = (
                    f"El movimiento #{source_transaction.pk} solo tiene comprobante externo. Se abrio el pedido #{saas_invoice_order.pk}."
                )
            else:
                info_message = (
                    f"El cliente solo tiene comprobantes externos recientes. Se abrio el pedido #{saas_invoice_order.pk}."
                )
            messages.info(
                request,
                info_message,
            )
            return redirect("admin_order_detail", pk=saas_invoice_order.pk)

        if source_order:
            messages.error(
                request,
                "El movimiento relacionado no tiene comprobantes fiscales para nota de credito.",
            )
            return _redirect_client_history(client, active_company)
        messages.error(
            request,
            "No hay comprobantes fiscales del cliente para usar como base de nota de credito.",
        )
        return _redirect_client_history(client, active_company)

    created_label = (
        selected_sales_document_type.name
        if selected_sales_document_type
        else ("Cotizacion" if action == "quote" else "Pedido")
    )

    if source_order and action in {"quote", "order"}:
        try:
            order = _create_related_order_from_source(
                source_order=source_order,
                client=client,
                client_company=client_company,
                company=active_company,
                origin_channel=selected_origin_channel,
                actor=request.user,
                created_label=created_label,
                selected_sales_document_type=selected_sales_document_type,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return _redirect_client_history(client, active_company)

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

        if source_transaction:
            messages.success(
                request,
                f"{created_label} relacionada creada desde movimiento #{source_transaction.pk}.",
            )
        else:
            messages.success(
                request,
                f"{created_label} relacionada creada desde pedido #{source_order.pk}.",
            )
        return redirect("admin_order_detail", pk=order.pk)

    try:
        order = _create_draft_order_for_client(
            client=client,
            client_company=client_company,
            company=active_company,
            origin_channel=selected_origin_channel,
            actor=request.user,
            created_label=created_label,
            admin_note=f"{created_label} creada desde ficha cliente.",
            history_note=f"{created_label} creado desde ficha cliente",
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return _redirect_client_history(client, active_company)

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
    normalized_cuit = "".join(ch for ch in cuit if ch.isdigit())
    if not normalized_cuit:
        return JsonResponse({"ok": False, "message": "CUIT requerido."}, status=400)
    if len(normalized_cuit) not in {8, 11}:
        return JsonResponse(
            {
                "ok": False,
                "message": "Ingresa un CUIT/DNI valido para preparar los datos fiscales.",
            },
            status=400,
        )
    return JsonResponse(
        {
            "ok": True,
            "source": "fallback",
            "message": (
                "No se pudo consultar ARCA/AFIP en este entorno. "
                "Se preparo el documento fiscal para seguir la carga manual."
            ),
            "company_name": "",
            "iva_condition": "",
            "fiscal_address": "",
            "fiscal_city": "",
            "fiscal_province": "",
            "postal_code": "",
            "document_type": "cuit" if len(normalized_cuit) == 11 else "dni",
            "document_number": normalized_cuit,
            "normalized_cuit": normalized_cuit,
        }
    )


CLIENT_HISTORY_MOVEMENT_TABS = {
    "sales": {
        "label": "Ventas",
        "title": "Ventas del cliente",
        "subtitle": "Movimientos ya cerrados que forman parte del historial comercial del cliente.",
    },
    "quotes": {
        "label": "Presupuestos",
        "title": "Presupuestos y cotizaciones",
        "subtitle": "Cotizaciones cerradas, listas para revisar o imprimir, sin impacto en cuenta corriente.",
    },
    "remitos": {
        "label": "Remitos",
        "title": "Remitos del cliente",
        "subtitle": "Documentos de entrega ya cerrados y vinculados al flujo comercial del cliente.",
    },
    "payments": {
        "label": "Cobros",
        "title": "Cobros del cliente",
        "subtitle": "Recibos y pagos ya registrados, con acceso directo a su comprobante y aplicación.",
    },
    "account": {
        "label": "Cuenta corriente",
        "title": "Cuenta corriente",
        "subtitle": "Saldo, debe/haber y movimientos que afectan la deuda del cliente.",
    },
    "activity": {
        "label": "Actividad",
        "title": "Actividad reciente",
        "subtitle": "Timeline operativo con solicitudes, ventas, documentos y cobros del cliente.",
    },
}

CLIENT_HISTORY_LEGACY_TAB_MAP = {
    "overview": "sales",
    "documents": "sales",
    "facturas": "sales",
    "invoices": "sales",
    "orders": "sales",
    "payments": "payments",
    "remitos": "remitos",
    "quotes": "quotes",
    "presupuestos": "quotes",
    "account": "account",
    "activity": "activity",
}


def _resolve_client_history_movement_tab(request):
    explicit = str(request.GET.get("movement_tab", "")).strip().lower()
    if explicit in CLIENT_HISTORY_MOVEMENT_TABS:
        return explicit

    legacy_ledger_tab = str(request.GET.get("ledger_tab", "")).strip().lower()
    if legacy_ledger_tab:
        return CLIENT_HISTORY_LEGACY_TAB_MAP.get(legacy_ledger_tab, "sales")

    legacy_client_tab = str(request.GET.get("client_tab", "")).strip().lower()
    if legacy_client_tab:
        return CLIENT_HISTORY_LEGACY_TAB_MAP.get(legacy_client_tab, "sales")

    return "sales"


def _row_has_visible_amount(row):
    return (
        row.get("debit", Decimal("0.00")) > 0
        or row.get("credit", Decimal("0.00")) > 0
    )


def _row_matches_movement_bucket(row, bucket_key):
    movement_state = row.get("movement_state") or ClientTransaction.STATE_OPEN
    tx = row.get("tx")
    doc_category = row.get("doc_category")

    if bucket_key == "open":
        return movement_state == ClientTransaction.STATE_OPEN and _row_has_visible_amount(row)
    if bucket_key == "voided":
        return movement_state == ClientTransaction.STATE_VOIDED
    if bucket_key == "sales":
        if movement_state != ClientTransaction.STATE_CLOSED:
            return False
        return doc_category in {"invoice", "payment"} or (
            getattr(tx, "transaction_type", "") == ClientTransaction.TYPE_ADJUSTMENT
        )
    if bucket_key == "quotes":
        return movement_state == ClientTransaction.STATE_CLOSED and doc_category == "quote"
    if bucket_key == "remitos":
        return movement_state == ClientTransaction.STATE_CLOSED and doc_category == "remito"
    if bucket_key == "payments":
        return movement_state == ClientTransaction.STATE_CLOSED and doc_category == "payment"
    if bucket_key == "account":
        return _row_has_visible_amount(row) or movement_state == ClientTransaction.STATE_VOIDED
    return False


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
    for document in recent_internal_documents:
        movement_transaction = _resolve_internal_document_transaction(document)
        document.can_print = _movement_allows_print(movement_transaction)
    for document in recent_fiscal_documents:
        movement_transaction = _resolve_fiscal_document_transaction(document)
        document.can_print = _movement_allows_print(movement_transaction)
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
        ledger_movement_state_by_payment = {
            tx.payment_id: (tx.movement_state or ClientTransaction.STATE_OPEN)
            for tx in ledger_entries
            if tx.payment_id
        }

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
            movement_state = getattr(tx, "movement_state", ClientTransaction.STATE_OPEN) or ClientTransaction.STATE_OPEN
            if movement_state not in {
                ClientTransaction.STATE_OPEN,
                ClientTransaction.STATE_CLOSED,
                ClientTransaction.STATE_VOIDED,
            }:
                movement_state = ClientTransaction.STATE_OPEN
            effective_amount = (
                tx.amount
                if movement_state == ClientTransaction.STATE_CLOSED
                else Decimal("0.00")
            )
            movement_display_amount = (
                tx.amount
                if movement_state in {ClientTransaction.STATE_OPEN, ClientTransaction.STATE_CLOSED}
                else Decimal("0.00")
            )
            running_balance += effective_amount
            debit = movement_display_amount if movement_display_amount > 0 else Decimal('0.00')
            credit = abs(movement_display_amount) if movement_display_amount < 0 else Decimal('0.00')
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
            document_locked = False
            row_source_kind = ""
            if isinstance(source_document, InternalDocument):
                row_source_kind = "internal"
                if movement_state == ClientTransaction.STATE_CLOSED:
                    document_url = f"{reverse('admin_internal_document_print', args=[source_document.pk])}?copy=original"
                    document_action_label = "Documento"
                    document_target_blank = True
                else:
                    document_action_label = "Cerrar movimiento para imprimir"
                    document_locked = True
            elif isinstance(source_document, FiscalDocument):
                row_source_kind = "fiscal"
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

            related_order = None
            if tx.order_id:
                related_order = tx.order
            elif tx.payment_id and getattr(tx, "payment", None) and tx.payment.order_id:
                related_order = tx.payment.order
            related_order_id = getattr(related_order, "pk", None)
            can_relate = bool(related_order_id)
            can_void_movement, void_block_reason = can_transition_transaction_state(
                tx,
                ClientTransaction.STATE_VOIDED,
            )
            can_close_movement, close_block_reason = can_transition_transaction_state(
                tx,
                ClientTransaction.STATE_CLOSED,
            )
            can_reopen_movement, reopen_block_reason = can_transition_transaction_state(
                tx,
                ClientTransaction.STATE_OPEN,
            )
            if movement_state == ClientTransaction.STATE_VOIDED:
                can_void_movement = False
                void_block_reason = "Este movimiento ya esta anulado."
            actor_obj = getattr(tx, "created_by", None)
            if not actor_obj and tx.transaction_type == ClientTransaction.TYPE_PAYMENT:
                actor_obj = getattr(getattr(tx, "payment", None), "created_by", None)
            actor_label = "-"
            if actor_obj:
                actor_label = (
                    actor_obj.get_full_name().strip()
                    if hasattr(actor_obj, "get_full_name")
                    else ""
                ) or getattr(actor_obj, "username", "-")

            request_origin_parts = []
            if tx.order:
                if tx.order.source_request_id:
                    request_origin_parts.append(f"Solicitud web #{tx.order.source_request_id}")
                if tx.order.source_proposal_id:
                    proposal_version = getattr(getattr(tx.order, "source_proposal", None), "version_number", None)
                    if proposal_version:
                        request_origin_parts.append(f"Propuesta aceptada v{proposal_version}")
                    else:
                        request_origin_parts.append(f"Propuesta #{tx.order.source_proposal_id}")

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
                    reference_meta_parts.extend(request_origin_parts)
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
                    reference_meta_parts.extend(request_origin_parts)
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
                    reference_meta_parts.extend(request_origin_parts)
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
                elif doc_category == 'quote':
                    reference_title = "Cotizacion comercial"
                    reference_meta_parts = [f"Pedido #{tx.order_id}", getattr(tx.company, "name", "") or "-"]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta_parts.extend(request_origin_parts)
                    reference_meta = " | ".join(part for part in reference_meta_parts if part)
                else:
                    reference_title = f"Pedido #{tx.order_id}"
                    reference_meta_parts = [getattr(tx.company, "name", "") or "-"]
                    if tx.order:
                        reference_meta_parts.append(tx.order.get_status_display())
                    reference_meta_parts.extend(request_origin_parts)
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

            payment_action_url = ""
            payment_action_label = ""
            if related_order_id and movement_state != ClientTransaction.STATE_VOIDED:
                payment_action_url = (
                    f"{reverse('admin_payment_list')}?"
                    f"{urlencode({'client_id': client.pk, 'company_id': selected_company_id, 'order_id': related_order_id, 'suggested_action': 'create'})}"
                )
                payment_action_label = f"Registrar pago para pedido #{related_order_id}"

            edit_url = detail_url
            edit_label = "Modificar esta venta"
            if isinstance(source_document, FiscalDocument):
                edit_url = reverse("admin_fiscal_document_detail", args=[source_document.pk])
                edit_label = "Abrir comprobante"

            flag_actions = []
            copy_variants = (
                ("original", "Original"),
                ("duplicado", "Duplicado"),
                ("triplicado", "Triplicado"),
            )
            if isinstance(source_document, InternalDocument):
                can_print_internal = movement_state == ClientTransaction.STATE_CLOSED
                base_print_url = reverse("admin_internal_document_print", args=[source_document.pk])
                disabled_reason = "Cierra el movimiento para habilitar impresion y PDF."
                for copy_key, copy_label in copy_variants:
                    flag_actions.append(
                        {
                            "kind": "link",
                            "label": copy_label,
                            "url": f"{base_print_url}?copy={copy_key}",
                            "target_blank": True,
                            "disabled": not can_print_internal,
                            "disabled_reason": disabled_reason if not can_print_internal else "",
                        }
                    )
                flag_actions.append(
                    {
                        "kind": "link",
                        "label": "PDF",
                        "url": f"{base_print_url}?copy=original&format=pdf",
                        "target_blank": True,
                        "disabled": not can_print_internal,
                        "disabled_reason": disabled_reason if not can_print_internal else "",
                    }
                )
            elif isinstance(source_document, FiscalDocument):
                can_print_fiscal = movement_state == ClientTransaction.STATE_CLOSED
                base_print_url = reverse("admin_fiscal_document_print", args=[source_document.pk])
                disabled_reason = "Cierra el movimiento para habilitar impresion y PDF."
                for copy_key, copy_label in copy_variants:
                    flag_actions.append(
                        {
                            "kind": "link",
                            "label": copy_label,
                            "url": f"{base_print_url}?copy={copy_key}",
                            "target_blank": True,
                            "disabled": not can_print_fiscal,
                            "disabled_reason": disabled_reason if not can_print_fiscal else "",
                        }
                    )
                flag_actions.append(
                    {
                        "kind": "link",
                        "label": "PDF",
                        "url": f"{base_print_url}?copy=original&format=pdf",
                        "target_blank": True,
                        "disabled": not can_print_fiscal,
                        "disabled_reason": disabled_reason if not can_print_fiscal else "",
                    }
                )
                can_send_fiscal = source_document.status in {
                    FISCAL_STATUS_AUTHORIZED,
                    FISCAL_STATUS_EXTERNAL_RECORDED,
                }
                flag_actions.append(
                    {
                        "kind": "post",
                        "label": "Enviar",
                        "url": reverse("admin_fiscal_document_send_email", args=[source_document.pk]),
                        "target_blank": False,
                        "disabled": not can_send_fiscal,
                        "disabled_reason": (
                            "Solo comprobantes fiscales autorizados o externos pueden enviarse por email."
                            if not can_send_fiscal
                            else ""
                        ),
                    }
                )

            ledger_rows.append({
                'tx': tx,
                'movement_state': movement_state,
                'movement_state_label': dict(ClientTransaction.STATE_CHOICES).get(movement_state, "Abierto"),
                'movement_state_badge': (
                    'badge-warning' if movement_state == ClientTransaction.STATE_OPEN else (
                        'badge-success' if movement_state == ClientTransaction.STATE_CLOSED else 'badge-danger'
                    )
                ),
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
                'source_kind': row_source_kind,
                'document_url': document_url,
                'document_action_label': document_action_label,
                'document_target_blank': document_target_blank,
                'document_locked': document_locked,
                'detail_url': detail_url,
                'detail_label': detail_label,
                'edit_url': edit_url,
                'edit_label': edit_label,
                'payment_action_url': payment_action_url,
                'payment_action_label': payment_action_label,
                'related_order_id': related_order_id,
                'can_relate': can_relate,
                'can_close_movement': can_close_movement,
                'close_block_reason': close_block_reason,
                'can_reopen_movement': can_reopen_movement,
                'reopen_block_reason': reopen_block_reason,
                'can_void_movement': can_void_movement,
                'void_block_reason': void_block_reason,
                'actor_label': actor_label,
                'movement_total': abs(movement_display_amount),
                'flag_actions': flag_actions,
            })

        for payment in payments_recent:
            receipt_document = receipt_documents_by_payment.get(payment.pk)
            linked_invoice = fiscal_documents_by_order.get(payment.order_id) if payment.order_id else None
            payment_movement_state = ledger_movement_state_by_payment.get(
                payment.pk,
                ClientTransaction.STATE_OPEN,
            )
            if payment_movement_state not in {
                ClientTransaction.STATE_OPEN,
                ClientTransaction.STATE_CLOSED,
                ClientTransaction.STATE_VOIDED,
            }:
                payment_movement_state = ClientTransaction.STATE_OPEN
            can_print_receipt = payment_movement_state == ClientTransaction.STATE_CLOSED
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
                "movement_state": payment_movement_state,
                "movement_state_label": dict(ClientTransaction.STATE_CHOICES).get(payment_movement_state, "Abierto"),
                "movement_state_badge": (
                    'badge-warning' if payment_movement_state == ClientTransaction.STATE_OPEN else (
                        'badge-success' if payment_movement_state == ClientTransaction.STATE_CLOSED else 'badge-danger'
                    )
                ),
                "document_url": (
                    f"{reverse('admin_internal_document_print', args=[receipt_document.pk])}?copy=original"
                    if receipt_document and can_print_receipt
                    else ""
                ),
                "document_locked": bool(receipt_document) and not can_print_receipt,
            })
    except DatabaseError as exc:
        ledger_warning = "La cuenta corriente no pudo cargarse en este entorno."
        logger.warning(
            "Client history ledger unavailable for client %s and company %s: %s",
            client.pk,
            getattr(active_company, "pk", None),
            exc,
        )

    movement_tab = _resolve_client_history_movement_tab(request)
    open_state_tab = str(request.GET.get("open_state", "open")).strip().lower()
    if open_state_tab not in {"open", "voided"}:
        open_state_tab = "open"

    movement_rows_by_tab = {
        "open": [row for row in ledger_rows if _row_matches_movement_bucket(row, "open")],
        "voided": [row for row in ledger_rows if _row_matches_movement_bucket(row, "voided")],
        "sales": [row for row in ledger_rows if _row_matches_movement_bucket(row, "sales")],
        "quotes": [row for row in ledger_rows if _row_matches_movement_bucket(row, "quotes")],
        "remitos": [row for row in ledger_rows if _row_matches_movement_bucket(row, "remitos")],
        "payments": [row for row in ledger_rows if _row_matches_movement_bucket(row, "payments")],
        "account": [row for row in ledger_rows if _row_matches_movement_bucket(row, "account")],
    }

    ledger_tab = movement_tab
    ledger_rows_filtered = list(movement_rows_by_tab.get(movement_tab, movement_rows_by_tab["sales"]))

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
    all_open_movement_rows = movement_rows_by_tab["open"]
    voided_movement_rows = movement_rows_by_tab["voided"]
    open_movements_count = len(all_open_movement_rows)
    voided_movements_count = len(voided_movement_rows)
    open_movement_rows = (voided_movement_rows if open_state_tab == "voided" else all_open_movement_rows)[:12]
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
            or fiscal_documents_qs.filter(doc_type__in=INVOICE_FISCAL_DOC_TYPES).exclude(status="voided").exists()
            or official_docs_qs.exists()
        )
        quick_credit_note_available = (
            fiscal_documents_qs.filter(doc_type__in=INVOICE_FISCAL_DOC_TYPES).exclude(status="voided").exists()
            or official_docs_qs.exists()
        )
    else:
        quick_remito_available = False
        quick_invoice_available = False
        quick_credit_note_available = False

    quick_sales_document_actions = []
    related_sales_document_actions = []
    if active_company:
        quick_type_queryset = SalesDocumentType.objects.filter(
            company=active_company,
            enabled=True,
            document_behavior__in=[
                SALES_BEHAVIOR_RECIBO,
                SALES_BEHAVIOR_COTIZACION,
                SALES_BEHAVIOR_PRESUPUESTO,
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
                action_meta["label"] = "Presupuesto / Cotizacion"
                action_meta["help_text"] = "Crea un borrador comercial desde la ficha del cliente."
                action_meta["css_class"] = "is-quote"
            elif behavior == SALES_BEHAVIOR_REMITO:
                action_meta["action_value"] = "remito"
                action_meta["label"] = "Remito"
                action_meta["help_text"] = "Busca el pedido mas reciente listo para remito."
                action_meta["disabled"] = operations_locked or not quick_remito_available
                action_meta["css_class"] = "is-remito"
            elif behavior == SALES_BEHAVIOR_FACTURA:
                action_meta["action_value"] = "invoice"
                action_meta["label"] = "Factura electronica"
                action_meta["help_text"] = "Usa el pedido facturable mas reciente y aplica el tipo elegido."
                action_meta["disabled"] = operations_locked or not quick_invoice_available
                action_meta["css_class"] = "is-fiscal"
            elif behavior == SALES_BEHAVIOR_NOTA_CREDITO:
                action_meta["action_value"] = "credit_note"
                action_meta["label"] = "Nota de credito"
                action_meta["help_text"] = "Abre el comprobante base mas reciente para gestionar la nota."
                action_meta["disabled"] = operations_locked or not quick_credit_note_available
                action_meta["css_class"] = "is-credit-note"
            elif behavior == SALES_BEHAVIOR_RECIBO:
                action_meta["method"] = "get"
                action_meta["label"] = "Recibo"
                action_meta["url"] = (
                    f"{reverse('admin_payment_list')}?"
                    f"{urlencode({'client_id': client.pk, 'company_id': selected_company_id, 'sales_document_type_id': item.pk, 'suggested_action': 'create'})}"
                )
                action_meta["help_text"] = "Abre pagos con este tipo comercial preseleccionado."
                action_meta["css_class"] = "is-payment"
            elif behavior == SALES_BEHAVIOR_NOTA_DEBITO:
                action_meta["method"] = "get"
                action_meta["label"] = "Ajuste manual"
                action_meta["url"] = (
                    f"{reverse('admin_payment_list')}?"
                    f"{urlencode({'client_id': client.pk, 'company_id': selected_company_id, 'sales_document_type_id': item.pk, 'suggested_action': 'adjust'})}"
                )
                action_meta["help_text"] = "Abre ajustes de cuenta con este tipo comercial."
                action_meta["css_class"] = "is-adjustment"
            else:
                continue
            quick_sales_document_actions.append(action_meta)

        related_sales_document_actions = _build_related_sales_document_actions(
            company=active_company,
            operations_locked=operations_locked,
            quick_order_url=reverse("admin_client_quick_order", args=[client.pk]),
        )
        related_sales_document_actions = [
            item
            for item in related_sales_document_actions
            if item.get("action_value") != "order"
        ]

    client_tab = 'account'
    movement_tab_config = [
        {"key": key, "label": meta["label"]}
        for key, meta in CLIENT_HISTORY_MOVEMENT_TABS.items()
    ]
    client_tabs = [
        {
            'key': item['key'],
            'label': item['label'],
            'is_active': movement_tab == item['key'],
            'url': build_history_url(
                client_tab='account',
                movement_tab=item['key'],
                ledger_tab=None,
                page=None,
                status=None,
                more=None,
                show_all=None,
            ),
        }
        for item in movement_tab_config
    ]
    ledger_tabs_ui = client_tabs
    open_state_tabs = [
        {
            "key": "open",
            "label": "Abiertos",
            "is_active": open_state_tab == "open",
            "url": build_history_url(open_state="open", page=None),
        },
        {
            "key": "voided",
            "label": "Anulados",
            "is_active": open_state_tab == "voided",
            "url": build_history_url(open_state="voided", page=None),
        },
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
        movement_tab='sales',
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
    activity_timeline = build_client_activity_timeline(
        client,
        company=active_company,
        limit=12,
    )
    active_ledger_title_map = {
        key: meta["title"] for key, meta in CLIENT_HISTORY_MOVEMENT_TABS.items()
    }
    active_ledger_subtitle_map = {
        key: meta["subtitle"] for key, meta in CLIENT_HISTORY_MOVEMENT_TABS.items()
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
        'activity_timeline': activity_timeline,
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
        'movement_tab': movement_tab,
        'movement_rows_by_tab': movement_rows_by_tab,
        'ledger_warning': ledger_warning,
        'ledger_tabs': ledger_tabs_ui,
        'active_ledger_title': active_ledger_title_map.get(ledger_tab, 'Cuenta corriente'),
        'active_ledger_subtitle': active_ledger_subtitle_map.get(ledger_tab, ''),
        'ledger_more_url': ledger_more_url,
        'ledger_show_all_url': ledger_show_all_url,
        'history_current_url': request.get_full_path(),
        'open_movement_rows': open_movement_rows,
        'open_state_tab': open_state_tab,
        'open_state_tabs': open_state_tabs,
        'open_movements_count': open_movements_count,
        'voided_movements_count': voided_movements_count,
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
        'related_sales_document_actions': related_sales_document_actions,
        'manual_origin_channel_choices': [
            choice
            for choice in Order.ORIGIN_CHOICES
            if choice[0] != Order.ORIGIN_CATALOG
        ],
    })


@staff_member_required
@require_POST
def client_transaction_set_state(request, pk, tx_id):
    client = get_object_or_404(ClientProfile, pk=pk)
    transaction_obj = get_object_or_404(
        ClientTransaction.objects.select_related("company", "client_profile", "order"),
        pk=tx_id,
        client_profile=client,
    )
    active_company = get_active_company(request)
    if (
        active_company
        and transaction_obj.company_id
        and transaction_obj.company_id != active_company.id
    ):
        messages.error(request, "El movimiento no pertenece a la empresa activa.")
        return redirect(reverse("admin_client_order_history", args=[client.pk]))

    target_state = str(request.POST.get("state", "")).strip().lower()
    redirect_url = _resolve_safe_next_url(
        request,
        reverse("admin_client_order_history", args=[client.pk]),
    )
    current_state = transaction_obj.movement_state or ClientTransaction.STATE_OPEN

    tracked_tx_fields = ["movement_state", "closed_at", "voided_at"]
    before = model_snapshot(transaction_obj, tracked_tx_fields)
    linked_order_before = {}
    linked_order_after = {}
    linked_order = transaction_obj.order if transaction_obj.order_id else None
    if linked_order:
        linked_order_before = model_snapshot(linked_order, ["status", "status_updated_at"])

    try:
        transition_result = apply_transaction_state_transition(
            transaction_obj=transaction_obj,
            target_state=target_state,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, f"No se pudo actualizar el movimiento: {exc}")
        return redirect(redirect_url)

    if not transition_result.changed:
        messages.info(
            request,
            f"El movimiento ya estaba en estado {dict(ClientTransaction.STATE_CHOICES).get(target_state, target_state)}.",
        )
        return redirect(redirect_url)

    if linked_order:
        linked_order_after = model_snapshot(linked_order, ["status", "status_updated_at"])

    log_extra = {
        "client_profile_id": client.pk,
        "company_id": transaction_obj.company_id,
        "source_key": transaction_obj.source_key,
    }
    if linked_order:
        log_extra.update(
            {
                "order_id": linked_order.pk,
                "order_status_before": (linked_order_before or {}).get("status"),
                "order_status_after": (linked_order_after or {}).get("status"),
            }
        )
    log_admin_change(
        request,
        action="client_transaction_state_update",
        target_type="client_transaction",
        target_id=transaction_obj.pk,
        before=before,
        after=model_snapshot(transaction_obj, tracked_tx_fields),
        extra=log_extra,
    )
    success_action_label = {
        ClientTransaction.STATE_CLOSED: "Movimiento cerrado.",
        ClientTransaction.STATE_OPEN: "Movimiento dejado abierto.",
        ClientTransaction.STATE_VOIDED: "Movimiento anulado.",
    }.get(target_state, "Movimiento actualizado.")
    messages.success(
        request,
        f"{success_action_label}{transition_result.order_side_effect_note}",
    )
    return redirect(redirect_url)


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
@require_POST
def client_password_reset_email(request, pk):
    """Send password reset email to a client user."""
    client = get_object_or_404(ClientProfile.objects.select_related("user"), pk=pk)
    redirect_url = _resolve_safe_next_url(
        request,
        reverse("admin_client_order_history", kwargs={"pk": client.pk}),
    )

    if not can_manage_client_credentials(request.user):
        messages.error(
            request,
            f'Solo "{PRIMARY_SUPERADMIN_USERNAME}" puede enviar recuperacion de contrasena.',
        )
        return redirect(redirect_url)

    if not client.user:
        messages.error(request, "Este cliente no tiene usuario asociado.")
        return redirect(redirect_url)

    success, error_message = _send_password_reset_email_for_user(request, client.user)
    if not success:
        messages.error(request, error_message)
        return redirect(redirect_url)

    log_admin_action(
        request,
        action="client_password_reset_email_sent",
        target_type="user",
        target_id=client.user.pk,
        details={
            "client_profile_id": client.pk,
            "username": client.user.username,
            "email": client.user.email,
        },
    )
    messages.success(request, f'Se envio mail de recuperacion con link a "{client.user.email}".')
    return redirect(redirect_url)


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

__all__ = ['parse_optional_client_category', 'client_dashboard', 'client_tools_hub', 'client_export', 'client_reports_hub', 'client_report_list', 'client_report_ranking', 'client_report_debtors', 'client_list', 'client_category_list', 'client_category_create', 'client_category_edit', 'client_category_delete', 'client_create', 'client_edit', 'client_quick_order', 'client_cuit_lookup', 'client_order_history', 'client_transaction_set_state', 'client_password_change', 'client_password_reset_email', 'client_delete', 'request_list', 'request_approve', 'request_reject']
