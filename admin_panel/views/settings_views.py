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
    "default_sales_user_mode",
    "billing_mode",
    "use_document_situation",
    "internal_doc_type",
    "fiscal_doc_type",
    "print_address",
    "print_email",
    "print_phones",
    "print_locality",
    "print_signature",
    "base_design",
    "notes",
    "is_default",
    "default_origin_channel",
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
        message="Selecciona una empresa activa para gestionar tipos de venta.",
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
        message="Selecciona una empresa activa para crear tipos de venta.",
    )
    if not active_company:
        return redirect("select_company")

    form = SalesDocumentTypeForm(request.POST or None, company=active_company)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                document_type = form.save()
        except IntegrityError:
            form.add_error(
                "is_default",
                "Ya existe un tipo de venta predeterminado para este comportamiento y canal.",
            )
            messages.error(
                request,
                "No se pudo guardar porque ya existe un predeterminado para ese comportamiento y canal.",
            )
        else:
            log_admin_action(
                request,
                action="sales_document_type_create",
                target_type="sales_document_type",
                target_id=document_type.pk,
                details=model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS),
            )
            messages.success(request, "Tipo de venta guardado.")
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
        message="Selecciona una empresa activa para editar tipos de venta.",
    )
    if not active_company:
        return redirect("select_company")

    document_type = get_object_or_404(SalesDocumentType, pk=pk)
    if document_type.company_id != active_company.id:
        messages.error(request, "El tipo de venta no pertenece a la empresa activa.")
        return redirect("admin_sales_document_type_list")

    before = model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS)
    form = SalesDocumentTypeForm(request.POST or None, instance=document_type, company=active_company)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                document_type = form.save()
        except IntegrityError:
            form.add_error(
                "is_default",
                "Ya existe un tipo de venta predeterminado para este comportamiento y canal.",
            )
            messages.error(
                request,
                "No se pudo guardar porque ya existe un predeterminado para ese comportamiento y canal.",
            )
        else:
            synced_orders = _resync_order_charges_for_sales_document_type(
                document_type,
                actor=request.user,
            )
            log_admin_change(
                request,
                action="sales_document_type_update",
                target_type="sales_document_type",
                target_id=document_type.pk,
                before=before,
                after=model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS),
            )
            if synced_orders:
                messages.success(
                    request,
                    f"Tipo de venta actualizado. Cuenta corriente resincronizada en {synced_orders} pedido(s).",
                )
            else:
                messages.success(request, "Tipo de venta actualizado.")
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
        message="Selecciona una empresa activa para editar tipos de venta.",
    )
    if not active_company:
        return redirect("select_company")

    document_type = get_object_or_404(SalesDocumentType, pk=pk)
    if document_type.company_id != active_company.id:
        messages.error(request, "El tipo de venta no pertenece a la empresa activa.")
        return redirect("admin_sales_document_type_list")

    before = model_snapshot(document_type, ["enabled"])
    document_type.enabled = not document_type.enabled
    document_type.save(update_fields=["enabled", "updated_at"])
    synced_orders = _resync_order_charges_for_sales_document_type(
        document_type,
        actor=request.user,
    )
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
        "Tipo de venta habilitado." if document_type.enabled else "Tipo de venta deshabilitado.",
    )
    if synced_orders:
        messages.info(
            request,
            f"Cuenta corriente resincronizada en {synced_orders} pedido(s).",
        )
    return redirect("admin_sales_document_type_list")


def _sales_document_type_usage(document_type):
    return {
        "internal_documents": InternalDocument.objects.filter(sales_document_type=document_type).count(),
        "fiscal_documents": FiscalDocument.objects.filter(sales_document_type=document_type).count(),
        "stock_movements": StockMovement.objects.filter(sales_document_type=document_type).count(),
    }


def _resync_order_charges_for_sales_document_type(document_type, *, actor=None):
    """Recalculate current-account charge rows for orders linked to one sales document type."""
    if not document_type:
        return 0
    order_ids = list(
        InternalDocument.objects.filter(
            sales_document_type=document_type,
            order_id__isnull=False,
        )
        .values_list("order_id", flat=True)
        .distinct()
    )
    if not order_ids:
        return 0
    synced = 0
    for order in Order.objects.filter(pk__in=order_ids).select_related("company"):
        try:
            sync_order_charge_transaction(order=order, actor=actor)
            synced += 1
        except Exception:
            continue
    return synced


@user_passes_test(is_primary_superadmin)
def sales_document_type_delete(request, pk):
    active_company = _get_settings_active_company(
        request,
        message="Selecciona una empresa activa para eliminar tipos de venta.",
    )
    if not active_company:
        return redirect("select_company")

    document_type = get_object_or_404(SalesDocumentType, pk=pk)
    if document_type.company_id != active_company.id:
        messages.error(request, "El tipo de venta no pertenece a la empresa activa.")
        return redirect("admin_sales_document_type_list")

    usage = _sales_document_type_usage(document_type)
    has_usage = any(usage.values())

    if request.method == "POST":
        affected_order_ids = list(
            InternalDocument.objects.filter(
                sales_document_type=document_type,
                order_id__isnull=False,
            )
            .values_list("order_id", flat=True)
            .distinct()
        )
        snapshot = model_snapshot(document_type, SALES_DOCUMENT_TYPE_SNAPSHOT_FIELDS)
        name = document_type.name
        document_type.delete()
        synced_orders = 0
        for order in Order.objects.filter(pk__in=affected_order_ids).select_related("company"):
            try:
                sync_order_charge_transaction(order=order, actor=request.user)
                synced_orders += 1
            except Exception:
                continue
        log_admin_action(
            request,
            action="sales_document_type_delete",
            target_type="sales_document_type",
            target_id=pk,
            details={
                "deleted": snapshot,
                "usage": usage,
            },
        )
        if has_usage:
            if synced_orders:
                messages.success(
                    request,
                    f"Tipo de venta '{name}' eliminado. Se mantuvo el historial, desvinculando referencias previas. Cuenta corriente resincronizada en {synced_orders} pedido(s).",
                )
            else:
                messages.success(
                    request,
                    f"Tipo de venta '{name}' eliminado. Se mantuvo el historial, desvinculando referencias previas.",
                )
        else:
            if synced_orders:
                messages.success(
                    request,
                    f"Tipo de venta '{name}' eliminado. Cuenta corriente resincronizada en {synced_orders} pedido(s).",
                )
            else:
                messages.success(request, f"Tipo de venta '{name}' eliminado.")
        return redirect("admin_sales_document_type_list")

    usage_chunks = []
    if usage["internal_documents"]:
        usage_chunks.append(f"{usage['internal_documents']} documentos internos")
    if usage["fiscal_documents"]:
        usage_chunks.append(f"{usage['fiscal_documents']} documentos fiscales")
    if usage["stock_movements"]:
        usage_chunks.append(f"{usage['stock_movements']} movimientos de stock")
    usage_text = ", ".join(usage_chunks)
    if usage_text:
        warning = (
            f"Hay historial asociado ({usage_text}). "
            "Al eliminarlo, esos registros conservaran datos pero quedaran sin este tipo asignado."
        )
    else:
        warning = "Esta accion no se puede deshacer."

    return render(
        request,
        "admin_panel/delete_confirm.html",
        {
            "object": f"{document_type.name} ({document_type.code})",
            "cancel_url": reverse("admin_sales_document_type_list"),
            "title": "Eliminar tipo de venta",
            "question": "Estas por eliminar este tipo de venta.",
            "warning": warning,
            "confirm_label": "Confirmar eliminacion",
        },
    )


def _sync_company_default_pos(company, point_of_sale):
    if not company or not point_of_sale:
        return
    if company.point_of_sale_default != point_of_sale.number:
        company.point_of_sale_default = point_of_sale.number
        company.save(update_fields=["point_of_sale_default", "updated_at"])


@user_passes_test(is_primary_superadmin)
def admin_user_list(request):
    """
    Superadmin-only list to manage admin accounts and permissions.
    """
    search = sanitize_search_token(request.GET.get('q', ''))
    admins = get_managed_admin_users_queryset()
    missing_email_filter = Q(email__isnull=True) | Q(email__exact="")
    missing_email_total = admins.filter(missing_email_filter).count()
    missing_email_admins = list(admins.filter(missing_email_filter).order_by("username")[:8])
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
            'missing_email_total': missing_email_total,
            'missing_email_admins': missing_email_admins,
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
@require_POST
def admin_user_send_password_reset_email(request, user_id):
    """Send password reset email to an admin/operator user."""
    admin_user = get_object_or_404(get_managed_admin_users_queryset(), pk=user_id)
    redirect_url = _resolve_safe_next_url(request, reverse("admin_user_list"))

    success, error_message = _send_password_reset_email_for_user(request, admin_user)
    if not success:
        messages.error(request, error_message)
        return redirect(redirect_url)

    log_admin_action(
        request,
        action="admin_user_password_reset_email_sent",
        target_type="auth_user",
        target_id=admin_user.pk,
        details={
            "username": admin_user.username,
            "email": admin_user.email,
        },
    )
    messages.success(request, f'Se envio mail de recuperacion con link a "{admin_user.email}".')
    return redirect(redirect_url)


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
        if admin_company_access_table_available():
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
    current_scope_links = []
    if admin_company_access_table_available():
        current_scope_links = list(
            AdminCompanyAccess.objects.filter(
                user=admin_user,
                is_active=True,
                company__is_active=True,
            ).select_related("company")
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

        # Any superuser can theoretically make others superusers now, but the views check superuser_required_for_modifications

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
            if admin_company_access_table_available():
                AdminCompanyAccess.objects.filter(user=admin_user).update(is_active=False)
            if selected_scope_mode == "limited" and admin_company_access_table_available():
                for company in available_companies:
                    if company.pk not in selected_company_ids:
                        continue
                    AdminCompanyAccess.objects.update_or_create(
                        user=admin_user,
                        company=company,
                        defaults={"is_active": True},
                    )
        else:
            if admin_company_access_table_available():
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

__all__ = ['company_list', 'company_edit', '_get_settings_active_company', 'warehouse_list', 'warehouse_create', 'warehouse_edit', 'sales_document_type_list', 'sales_document_type_create', 'sales_document_type_edit', 'sales_document_type_toggle_enabled', '_sales_document_type_usage', '_resync_order_charges_for_sales_document_type', 'sales_document_type_delete', '_sync_company_default_pos', 'admin_user_list', 'admin_user_edit', 'admin_user_password_change', 'admin_user_send_password_reset_email', 'admin_user_delete', 'admin_user_permissions']
