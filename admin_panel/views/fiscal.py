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



def _get_fiscal_snapshot(document):
    payload = getattr(document, "request_payload", None)
    if not isinstance(payload, dict):
        return {}
    snapshot = payload.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _get_fiscal_document_number_display(document):
    return document.display_number


def _get_fiscal_document_delete_blockers(document):
    blockers = []
    if document.status in {
        FISCAL_STATUS_AUTHORIZED,
        FISCAL_STATUS_EXTERNAL_RECORDED,
        FISCAL_STATUS_SUBMITTING,
    }:
        blockers.append(
            "Solo se pueden eliminar comprobantes fiscales no emitidos ni cerrados oficialmente."
        )
    if document.credit_notes.exclude(status=FISCAL_STATUS_VOIDED).exists():
        blockers.append("El comprobante tiene notas de credito vinculadas.")
    if document.stock_movements.exists():
        blockers.append("El comprobante genero movimientos de stock.")
    if ClientTransaction.objects.filter(
        source_key=f"fiscal:{document.pk}:account-adjustment"
    ).exists():
        blockers.append("El comprobante genero un movimiento de cuenta corriente.")
    return blockers


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

    today = timezone.localdate()
    summary = {
        "documents_count": documents.count(),
        "total_amount": documents.aggregate(total=Coalesce(Sum("total"), Decimal("0.00"))).get("total")
        or Decimal("0.00"),
        "authorized_count": documents.filter(status=FISCAL_STATUS_AUTHORIZED).count(),
        "pending_retry_count": documents.filter(status=FISCAL_STATUS_PENDING_RETRY).count(),
        "rejected_count": documents.filter(status=FISCAL_STATUS_REJECTED).count(),
        "overdue_count": documents.filter(
            status__in=[FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED],
            payment_due_date__lt=today,
        ).count(),
    }

    paginator = Paginator(documents, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    for doc in page_obj.object_list:
        doc.can_safe_delete = not _get_fiscal_document_delete_blockers(doc)
        doc.collection_snapshot = _build_fiscal_collection_snapshot(doc)
        doc.movement_transaction = _resolve_fiscal_document_transaction(doc)
        doc.can_print = _movement_allows_print(doc.movement_transaction)
        doc.can_email = (
            doc.status in {FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED}
            and can_manage_fiscal_operations(request.user)
        )

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

    can_operate_fiscal = can_manage_fiscal_operations(request.user)
    can_emit = (
        fiscal_document.issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE
        and fiscal_document.doc_type in EMITTABLE_FISCAL_DOC_TYPES
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
    snapshot = _get_fiscal_snapshot(fiscal_document)
    client_snapshot = snapshot.get("client", {}) if isinstance(snapshot, dict) else {}
    can_emit = can_emit and document_invoice_ready and can_operate_fiscal
    workflow_state = _get_fiscal_workflow_state(fiscal_document)
    can_close_document = (
        can_operate_fiscal
        and
        fiscal_document.issue_mode == FISCAL_ISSUE_MODE_MANUAL
        and fiscal_document.status in {
            FISCAL_STATUS_READY_TO_ISSUE,
            FISCAL_STATUS_PENDING_RETRY,
            FISCAL_STATUS_REJECTED,
        }
        and document_invoice_ready
    )
    can_reopen_document = (
        can_operate_fiscal
        and
        fiscal_document.issue_mode == FISCAL_ISSUE_MODE_MANUAL
        and fiscal_document.status == FISCAL_STATUS_EXTERNAL_RECORDED
    )
    can_void_document = can_operate_fiscal and fiscal_document.status in {
        FISCAL_STATUS_READY_TO_ISSUE,
        FISCAL_STATUS_PENDING_RETRY,
        FISCAL_STATUS_REJECTED,
        FISCAL_STATUS_EXTERNAL_RECORDED,
    }
    fiscal_delete_blockers = _get_fiscal_document_delete_blockers(fiscal_document)
    collection_snapshot = _build_fiscal_collection_snapshot(fiscal_document)
    can_send_email = (
        can_operate_fiscal
        and fiscal_document.status in {FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED}
    )
    movement_transaction = _resolve_fiscal_document_transaction(fiscal_document)
    can_print_document = _movement_allows_print(movement_transaction)
    movement_state = (
        (movement_transaction.movement_state or ClientTransaction.STATE_OPEN)
        if movement_transaction
        else ""
    )
    movement_can_close, movement_close_reason = can_transition_transaction_state(
        movement_transaction,
        ClientTransaction.STATE_CLOSED,
    )
    movement_can_open, movement_open_reason = can_transition_transaction_state(
        movement_transaction,
        ClientTransaction.STATE_OPEN,
    )
    movement_can_void, movement_void_reason = can_transition_transaction_state(
        movement_transaction,
        ClientTransaction.STATE_VOIDED,
    )
    can_close_movement_from_fiscal = can_operate_fiscal and bool(movement_transaction) and movement_can_close
    can_open_movement_from_fiscal = can_operate_fiscal and bool(movement_transaction) and movement_can_open
    can_void_movement_from_fiscal = can_operate_fiscal and bool(movement_transaction) and movement_can_void
    related_client_profile = fiscal_document.client_profile
    if not related_client_profile and getattr(fiscal_document, "client_company_ref", None):
        related_client_profile = fiscal_document.client_company_ref.client_profile
    client_name_display = (
        str(client_snapshot.get("name", "") or "").strip()
        or getattr(related_client_profile, "company_name", "")
        or "-"
    )
    client_document_value = (
        str(client_snapshot.get("document_number", "") or "").strip()
        or getattr(related_client_profile, "document_number", "")
        or getattr(related_client_profile, "cuit_dni", "")
        or "-"
    )
    client_document_label = str(client_snapshot.get("document_type_label", "") or "").strip()
    if not client_document_label:
        if getattr(related_client_profile, "document_type", ""):
            try:
                client_document_label = related_client_profile.get_document_type_display()
            except Exception:
                client_document_label = str(related_client_profile.document_type or "").strip()
        elif str(client_document_value).strip() not in {"", "-"}:
            client_document_label = "CUIT/DNI"
        else:
            client_document_label = "-"
    client_tax_condition_display = str(client_snapshot.get("tax_condition_label", "") or "").strip()
    if not client_tax_condition_display and getattr(related_client_profile, "iva_condition", ""):
        try:
            client_tax_condition_display = related_client_profile.get_iva_condition_display()
        except Exception:
            client_tax_condition_display = str(related_client_profile.iva_condition or "").strip()
    client_address_display = (
        str(client_snapshot.get("fiscal_address", "") or "").strip()
        or getattr(related_client_profile, "fiscal_address", "")
        or getattr(related_client_profile, "address", "")
        or "-"
    )
    client_city_display = (
        str(client_snapshot.get("fiscal_city", "") or "").strip()
        or getattr(related_client_profile, "fiscal_city", "")
        or getattr(related_client_profile, "province", "")
        or "-"
    )
    related_sales_document_actions = _build_related_sales_document_actions(
        company=fiscal_document.company,
        operations_locked=False,
        quick_order_url=(
            reverse("admin_client_quick_order", args=[related_client_profile.pk])
            if related_client_profile
            else ""
        ),
    )
    subtotal_amount = Decimal(fiscal_document.subtotal_net or 0).quantize(Decimal("0.01"))
    discount_amount = Decimal(fiscal_document.discount_total or 0).quantize(Decimal("0.01"))
    net_amount = (subtotal_amount - discount_amount).quantize(Decimal("0.01"))
    if net_amount < 0:
        net_amount = Decimal("0.00")

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
            "can_delete_document": can_operate_fiscal and not fiscal_delete_blockers,
            "can_send_email": can_send_email,
            "can_print_document": can_print_document,
            "movement_transaction": movement_transaction,
            "movement_state": movement_state,
            "movement_state_label": (
                movement_transaction.get_movement_state_display()
                if movement_transaction
                else "Sin movimiento"
            ),
            "can_close_movement_from_fiscal": can_close_movement_from_fiscal,
            "can_open_movement_from_fiscal": can_open_movement_from_fiscal,
            "can_void_movement_from_fiscal": can_void_movement_from_fiscal,
            "movement_close_reason": movement_close_reason,
            "movement_open_reason": movement_open_reason,
            "movement_void_reason": movement_void_reason,
            "fiscal_delete_blockers": fiscal_delete_blockers,
            "document_invoice_ready": document_invoice_ready,
            "document_invoice_errors": document_invoice_errors,
            "document_number_display": _get_fiscal_document_number_display(fiscal_document),
            "client_name_display": client_name_display,
            "client_document_label": client_document_label,
            "client_document_value": client_document_value,
            "client_tax_condition_display": client_tax_condition_display or "-",
            "client_address_display": client_address_display,
            "client_city_display": client_city_display,
            "can_operate_fiscal": can_operate_fiscal,
            "collection_snapshot": collection_snapshot,
            "subtotal_amount": subtotal_amount,
            "discount_amount": discount_amount,
            "net_amount": net_amount,
            "related_client_profile_id": related_client_profile.pk if related_client_profile else "",
            "related_sales_document_actions": related_sales_document_actions,
            "related_source_tx_id": movement_transaction.pk if movement_transaction else "",
            "related_source_order_id": fiscal_document.order_id or "",
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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
        action_label="emitir comprobantes fiscales",
    )
    if denied_response:
        return denied_response
    try:
        from core.tasks import emit_fiscal_document_async_task

        # We pre-validate to fail fast before queueing if something is obviously wrong
        from core.services.fiscal_emission import _validate_before_submit
        _validate_before_submit(fiscal_document)

        emit_fiscal_document_async_task.delay(document_id=fiscal_document.pk, actor_id=request.user.pk)

        messages.info(request, "Comprobante encolado para emision fiscal en AFIP. Esto puede demorar unos segundos.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    except Exception as exc:
        messages.error(request, f"Error al encolar emision: {str(exc)}")

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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
        action_label="cerrar comprobantes fiscales",
    )
    if denied_response:
        return denied_response

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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
        action_label="reabrir comprobantes fiscales",
    )
    if denied_response:
        return denied_response

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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
        action_label="anular comprobantes fiscales",
    )
    if denied_response:
        return denied_response

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
def fiscal_document_delete(request, pk):
    """Safely delete one fiscal document only if it has not become an official/billable record."""
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "company",
            "order",
            "point_of_sale",
            "sales_document_type",
        ),
        pk=pk,
    )
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "No podes eliminar comprobantes de otra empresa.")
        return redirect("admin_fiscal_document_list")
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
        action_label="eliminar comprobantes fiscales",
    )
    if denied_response:
        return denied_response

    blockers = _get_fiscal_document_delete_blockers(fiscal_document)
    if blockers:
        messages.error(request, "No se puede eliminar el comprobante fiscal: " + " ".join(blockers))
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)

    if request.method == 'POST':
        order = fiscal_document.order
        label = f"{fiscal_document.commercial_type_label} {fiscal_document.display_number}"
        fiscal_document.delete()
        if order:
            try:
                sync_order_charge_transaction(order=order, actor=request.user)
            except Exception:
                pass
        messages.success(request, f'Comprobante fiscal eliminado: {label}.')
        if order:
            return redirect("admin_order_detail", pk=order.pk)
        return redirect("admin_fiscal_document_list")

    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"{fiscal_document.commercial_type_label} {fiscal_document.display_number}",
        'cancel_url': reverse('admin_fiscal_document_detail', args=[fiscal_document.pk]),
        'title': 'Eliminar Comprobante Fiscal',
        'question': 'Estas por eliminar definitivamente este comprobante fiscal.',
        'warning': 'Solo se permite para comprobantes no emitidos ni cerrados oficialmente.',
        'confirm_label': 'Eliminar comprobante',
    })


@staff_member_required
@require_POST
def fiscal_document_send_email(request, pk):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    fiscal_document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "company",
            "client_profile__user",
            "client_company_ref__client_profile__user",
            "order",
        ),
        pk=pk,
    )
    if fiscal_document.company_id != active_company.id:
        messages.error(request, "No podes operar comprobantes de otra empresa.")
        return redirect("admin_fiscal_document_list")

    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
        action_label="enviar comprobantes por email",
    )
    if denied_response:
        return denied_response

    force_send = request.POST.get("force") == "1"
    redirect_url = _resolve_safe_next_url(
        request,
        reverse("admin_fiscal_document_detail", args=[fiscal_document.pk]),
    )

    success, message = send_fiscal_document_email(
        fiscal_document=fiscal_document,
        actor=request.user,
        force=force_send,
    )
    if success:
        log_admin_action(
            request,
            action="fiscal_document_email_send",
            target_type="fiscal_document",
            target_id=fiscal_document.pk,
            details={
                "document_number": fiscal_document.display_number,
                "recipient": fiscal_document.email_last_recipient,
                "forced": force_send,
            },
        )
        if message.startswith("El comprobante ya fue enviado"):
            messages.info(request, message)
        else:
            messages.success(request, message)
    else:
        messages.error(request, f"No se pudo enviar el comprobante: {message}")
    return redirect(redirect_url)


@staff_member_required
def fiscal_report(request):
    return fiscal_report_view(
        request,
        get_active_company_fn=get_active_company,
        deny_if_needed_fn=_deny_fiscal_operation_if_needed,
        build_collection_snapshot_fn=_build_fiscal_collection_snapshot,
        can_manage_fiscal_operations_fn=can_manage_fiscal_operations,
    )


@staff_member_required
def fiscal_health(request):
    return fiscal_health_view(
        request,
        get_active_company_fn=get_active_company,
        deny_if_needed_fn=_deny_fiscal_operation_if_needed,
    )


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
    movement_transaction = _resolve_fiscal_document_transaction(fiscal_document)
    if movement_transaction and not _movement_allows_print(movement_transaction):
        messages.warning(
            request,
            "Primero cerra el movimiento en cuenta corriente para imprimir o descargar este comprobante.",
        )
        return redirect("admin_fiscal_document_detail", pk=fiscal_document.pk)

    copy_type = str(request.GET.get("copy", "original")).strip().lower()
    if copy_type not in {"original", "duplicado", "triplicado"}:
        copy_type = "original"

    site_settings = SiteSettings.get_settings()
    client_profile = fiscal_document.client_profile
    if not client_profile and getattr(fiscal_document, "client_company_ref", None):
        client_profile = fiscal_document.client_company_ref.client_profile
    snapshot = _get_fiscal_snapshot(fiscal_document)
    emitter_snapshot = snapshot.get("emitter", {}) if isinstance(snapshot, dict) else {}
    client_snapshot = snapshot.get("client", {}) if isinstance(snapshot, dict) else {}

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

    company_legal_name = (
        str(emitter_snapshot.get("legal_name", "") or "").strip()
        or company.legal_name
        or company.name
    )
    emitter_cuit = (
        str(emitter_snapshot.get("cuit", "") or "").strip()
        or str(company.cuit or "").strip()
    )
    emitter_tax_condition_label = str(emitter_snapshot.get("tax_condition_label", "") or "").strip()
    if not emitter_tax_condition_label and company.tax_condition:
        try:
            emitter_tax_condition_label = company.get_tax_condition_display()
        except Exception:
            emitter_tax_condition_label = str(company.tax_condition or "")
    point_of_sale_display = (
        str(emitter_snapshot.get("point_of_sale", "") or "").strip()
        or str(getattr(fiscal_document.point_of_sale, "number", "") or "").strip()
    )

    company_address_bits = [
        str(emitter_snapshot.get("fiscal_address", "") or "").strip() or company.fiscal_address,
        str(emitter_snapshot.get("fiscal_city", "") or "").strip() or company.fiscal_city,
        str(emitter_snapshot.get("fiscal_province", "") or "").strip() or company.fiscal_province,
        str(emitter_snapshot.get("postal_code", "") or "").strip() or company.postal_code,
    ]

    client_name_display = (
        str(client_snapshot.get("name", "") or "").strip()
        or (client_profile.company_name if client_profile else "")
    )
    client_document_label = str(client_snapshot.get("document_type_label", "") or "").strip()
    if not client_document_label:
        client_document_label = (
            client_profile.get_document_type_display() if client_profile and client_profile.document_type else "CUIT/DNI"
        ) if client_profile else "CUIT/DNI"
    client_document_value = (
        str(client_snapshot.get("document_number", "") or "").strip()
        or ((client_profile.document_number or client_profile.cuit_dni) if client_profile else "")
    )
    client_tax_condition_display = str(client_snapshot.get("tax_condition_label", "") or "").strip()
    if not client_tax_condition_display and client_profile and client_profile.iva_condition:
        try:
            client_tax_condition_display = client_profile.get_iva_condition_display()
        except Exception:
            client_tax_condition_display = str(client_profile.iva_condition or "")
    client_address_bits = [
        str(client_snapshot.get("fiscal_address", "") or "").strip()
        or ((client_profile.fiscal_address or client_profile.address) if client_profile else ""),
        str(client_snapshot.get("fiscal_city", "") or "").strip()
        or ((client_profile.fiscal_city or client_profile.province) if client_profile else ""),
        str(client_snapshot.get("fiscal_province", "") or "").strip()
        or (client_profile.fiscal_province if client_profile else ""),
        str(client_snapshot.get("postal_code", "") or "").strip()
        or (client_profile.postal_code if client_profile else ""),
    ]

    subtotal_before_discount = Decimal(fiscal_document.subtotal_net or 0)
    discount_total = Decimal(fiscal_document.discount_total or 0)
    taxable_net = subtotal_before_discount - discount_total
    if taxable_net < 0:
        taxable_net = Decimal("0.00")

    order_total_discount_percentage = Decimal("0.00")
    if order and getattr(order, "discount_percentage", None):
        order_total_discount_percentage = Decimal(order.discount_percentage or 0)

    # Generate QR Code
    qr_base64 = ""
    qr_url = ""
    try:
        from core.services.pdf_generator import generate_afip_qr_data, generate_qr_image_base64
        qr_url = generate_afip_qr_data(fiscal_document)
        qr_base64 = generate_qr_image_base64(qr_url)
    except Exception as exc:
        pass

    context = {
        "fiscal_document": fiscal_document,
        "items": fiscal_document.items.all().order_by("line_number"),
        "copy_type": copy_type,
        "copy_label": copy_label,
        "document_letter": company_meta["letter"],
        "document_code": company_meta["code"],
        "document_number_display": (
            _get_fiscal_document_number_display(fiscal_document)
        ),
        "company_legal_name": company_legal_name,
        "company_address_line": " / ".join(bit for bit in company_address_bits if bit),
        "company_contact_line": " / ".join(
            bit for bit in [site_settings.company_phone, site_settings.company_phone_2, company.email or site_settings.company_email] if bit
        ),
        "company_contact_site": site_settings.company_address,
        "emitter_cuit": emitter_cuit,
        "emitter_tax_condition_label": emitter_tax_condition_label,
        "point_of_sale_display": point_of_sale_display,
        "client_profile": client_profile,
        "client_name_display": client_name_display,
        "client_address_line": " / ".join(bit for bit in client_address_bits if bit),
        "client_document_label": client_document_label,
        "client_document_value": client_document_value,
        "client_tax_condition_display": client_tax_condition_display,
        "sale_condition_label": sale_condition_label,
        "operator_label": operator_label,
        "observations_text": "\n".join(bit for bit in observations if bit).strip(),
        "subtotal_before_discount": subtotal_before_discount,
        "taxable_net": taxable_net,
        "order_total_discount_percentage": order_total_discount_percentage,
        "qr_base64": qr_base64,
        "qr_url": qr_url,
    }

    if request.GET.get('format') == 'pdf':
        try:
            from core.services.pdf_generator import generate_document_pdf

            html_string = render_to_string("admin_panel/fiscal/print.html", context)
            pdf_bytes = generate_document_pdf(html_string, base_url=request.build_absolute_uri("/"))
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{fiscal_document.commercial_type_label}_{fiscal_document.display_number}_{copy_type}.pdf"'
            return response
        except ImportError:
             messages.error(request, "El generador de PDF no esta instalado correctamente.")
        except Exception as exc:
            messages.error(request, f"Error al generar PDF: {str(exc)}")

    return render(request, "admin_panel/fiscal/print.html", context)


def _get_fiscal_active_company(request):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para configurar factura electronica.")
        return None
    return active_company


def _run_fiscal_pos_preflight(*, company, point_of_sale):
    result = {
        "ok": False,
        "message": "",
        "details": {},
    }
    try:
        client = ArcaWsfeClient(company=company, point_of_sale=point_of_sale)
        details = client.run_preflight()
    except ArcaConfigurationError as exc:
        result["message"] = f"Configuracion ARCA incompleta: {exc}"
        return result
    except ArcaTemporaryError as exc:
        result["message"] = f"Conectividad ARCA temporalmente no disponible: {exc}"
        return result
    except Exception as exc:
        result["message"] = f"Error inesperado en preflight ARCA: {exc}"
        return result

    result["ok"] = True
    result["message"] = "Conexion ARCA verificada correctamente."
    result["details"] = details
    return result


@user_passes_test(is_primary_superadmin)
def fiscal_config(request):
    """Fiscal configuration dashboard scoped to active company."""
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")

    points = FiscalPointOfSale.objects.filter(company=active_company).order_by("number")
    is_ready, readiness_errors = is_company_fiscal_ready(active_company)
    default_point = points.filter(is_default=True).first()
    active_points = points.filter(is_active=True)
    active_points_count = active_points.count()
    active_homologation_count = active_points.filter(
        environment=FiscalPointOfSale.ENV_HOMOLOGATION
    ).count()
    active_production_count = active_points.filter(
        environment=FiscalPointOfSale.ENV_PRODUCTION
    ).count()

    readiness_errors = readiness_errors or []

    def _errors_by_keywords(keywords):
        return [
            err
            for err in readiness_errors
            if any(keyword in str(err).lower() for keyword in keywords)
        ]

    company_errors = _errors_by_keywords(
        [
            "razon social",
            "cuit",
            "condicion fiscal",
            "domicilio fiscal",
            "localidad fiscal",
            "provincia fiscal",
            "codigo postal",
        ]
    )
    pos_errors = _errors_by_keywords(
        [
            "punto de venta",
            "pos default",
            "default",
            "inactivo",
            "activo",
        ]
    )
    arca_errors = _errors_by_keywords(
        [
            "arca",
            "cert_path",
            "key_path",
            "servidor",
        ]
    )

    company_snapshot = [
        ("Razon social", getattr(active_company, "legal_name", "") or "-"),
        ("CUIT", getattr(active_company, "cuit", "") or "-"),
        ("Condicion fiscal", active_company.get_tax_condition_display() or "-"),
        ("Domicilio fiscal", getattr(active_company, "fiscal_address", "") or "-"),
        (
            "Ciudad / Provincia",
            f"{getattr(active_company, 'fiscal_city', '') or '-'} / {getattr(active_company, 'fiscal_province', '') or '-'}",
        ),
        ("Codigo postal", getattr(active_company, "postal_code", "") or "-"),
    ]

    pos_snapshot = [
        ("POS activos", str(active_points_count)),
        ("Homologacion", str(active_homologation_count)),
        ("Produccion", str(active_production_count)),
        ("POS default empresa", getattr(active_company, "point_of_sale_default", "") or "-"),
        ("POS default modelo", getattr(default_point, "number", "") if default_point else "-"),
    ]

    arca_snapshot = [
        (
            "Entorno default",
            default_point.get_environment_display() if default_point else "-",
        ),
        (
            "Preflight",
            "Disponible para ejecutar" if default_point else "Definir POS default primero",
        ),
    ]

    fiscal_wizard_steps = [
        {
            "number": 1,
            "title": "Datos fiscales de empresa",
            "description": "Completa razon social, CUIT, condicion fiscal y domicilio para habilitar emision.",
            "is_ready": len(company_errors) == 0,
            "errors": company_errors,
            "snapshot": company_snapshot,
            "cta_label": "Editar empresa",
            "cta_url": reverse("admin_company_edit", args=[active_company.pk]),
            "cta_is_form": False,
        },
        {
            "number": 2,
            "title": "Punto de venta fiscal",
            "description": "Configura al menos un POS activo y define uno como default para operar.",
            "is_ready": len(pos_errors) == 0 and active_points_count > 0 and bool(default_point),
            "errors": pos_errors,
            "snapshot": pos_snapshot,
            "cta_label": "Agregar punto de venta",
            "cta_url": reverse("admin_fiscal_point_create"),
            "cta_is_form": False,
        },
        {
            "number": 3,
            "title": "Conexion ARCA",
            "description": "Valida certificado, clave y conectividad WSAA/WSFE con preflight de ARCA.",
            "is_ready": len(arca_errors) == 0 and bool(default_point),
            "errors": arca_errors,
            "snapshot": arca_snapshot,
            "cta_label": "Probar ARCA",
            "cta_url": reverse("admin_fiscal_point_preflight", args=[default_point.pk]) if default_point else "",
            "cta_is_form": True if default_point else False,
            "secondary_label": "Ver comprobantes",
            "secondary_url": reverse("admin_fiscal_document_list"),
        },
    ]

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
            "fiscal_wizard_steps": fiscal_wizard_steps,
        },
    )


@user_passes_test(is_primary_superadmin)
@require_POST
def fiscal_point_preflight(request, pk):
    """Run ARCA preflight (credentials + WSAA/WSFE connectivity) for one POS."""
    active_company = _get_fiscal_active_company(request)
    if not active_company:
        return redirect("select_company")

    point = get_object_or_404(FiscalPointOfSale, pk=pk, company=active_company)
    preflight = _run_fiscal_pos_preflight(company=active_company, point_of_sale=point)
    if preflight.get("ok"):
        details = preflight.get("details", {}) or {}
        last_auth = details.get("last_authorized_numbers", {}) or {}
        short_last = ", ".join(
            f"{doc}: {num if num is not None else '-'}"
            for doc, num in last_auth.items()
        )
        messages.success(
            request,
            f"Preflight OK para PV {point.number} ({point.get_environment_display()}). Ultimos autorizados: {short_last}.",
        )
        log_admin_action(
            request,
            action="fiscal_pos_preflight_ok",
            target_type="fiscal_point_of_sale",
            target_id=point.pk,
            details={
                "company_id": active_company.pk,
                "point_of_sale": point.number,
                "environment": point.environment,
                "last_authorized_numbers": last_auth,
            },
        )
    else:
        messages.error(
            request,
            f"Preflight ARCA fallido en PV {point.number}: {preflight.get('message')}",
        )
        log_admin_action(
            request,
            action="fiscal_pos_preflight_error",
            target_type="fiscal_point_of_sale",
            target_id=point.pk,
            details={
                "company_id": active_company.pk,
                "point_of_sale": point.number,
                "environment": point.environment,
                "error": preflight.get("message", ""),
            },
        )
    return redirect("admin_fiscal_config")


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

__all__ = ['_get_fiscal_snapshot', '_get_fiscal_document_number_display', '_get_fiscal_document_delete_blockers', 'fiscal_document_list', 'fiscal_document_detail', 'fiscal_document_emit', 'fiscal_document_close', 'fiscal_document_reopen', 'fiscal_document_void', 'fiscal_document_delete', 'fiscal_document_send_email', 'fiscal_report', 'fiscal_health', 'fiscal_document_print', '_get_fiscal_active_company', '_run_fiscal_pos_preflight', 'fiscal_config', 'fiscal_point_preflight', 'fiscal_point_create', 'fiscal_point_edit', 'fiscal_point_toggle_active', 'fiscal_point_set_default']
