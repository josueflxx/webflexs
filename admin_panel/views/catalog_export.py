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

__all__ = ['_export_template_detail_url', '_build_excel_sheet_base_name', '_build_unique_excel_sheet_name', '_resolve_auto_columns_from_template', '_replace_sheet_columns', 'catalog_excel_template_list', 'catalog_excel_template_detail', 'catalog_excel_template_create', 'catalog_excel_template_edit', 'catalog_excel_template_delete', 'catalog_excel_template_download', 'catalog_excel_template_autogenerate_main_category_sheets', 'catalog_excel_sheet_create', 'catalog_excel_sheet_edit', 'catalog_excel_sheet_delete', 'catalog_excel_column_create', 'catalog_excel_column_edit', 'catalog_excel_column_delete']
