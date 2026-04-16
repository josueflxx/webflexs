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
from core.services.operational_timeline import (
    build_order_flow_steps,
    build_sales_pipeline_rows,
    build_sales_workspace,
    resolve_sales_workspace_active_keys,
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
        .filter(order=order, doc_type__in=INVOICE_FISCAL_DOC_TYPES)
        .exclude(status=FISCAL_STATUS_VOIDED)
        .order_by("-created_at", "-id")
        .first()
    )


def _parse_order_item_manual_price(raw_value):
    normalized = str(raw_value or "").strip().replace(",", ".")
    if not normalized:
        return None
    try:
        value = Decimal(normalized)
    except (ValueError, InvalidOperation):
        return None
    if value < 0:
        return None
    return value.quantize(Decimal("0.01"))


def _build_order_detail_items(order):
    order_items = list(
        order.items.select_related(
            "product",
            "clamp_request",
            "clamp_request__linked_product",
        ).order_by("-id")
    )
    order_discount_percentage = (order.discount_percentage or Decimal("0")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    for item in order_items:
        unit_discount_amount = Decimal("0.00")
        item_discount_percentage = (
            item.discount_percentage_used
            if getattr(item, "discount_percentage_used", None) not in (None, 0)
            else order_discount_percentage
        )
        base_price = item.unit_price_base if getattr(item, "unit_price_base", None) else item.price_at_purchase
        if item_discount_percentage and item_discount_percentage > 0:
            unit_discount_amount = (
                base_price * item_discount_percentage / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        item.unit_discount_amount = unit_discount_amount

        clamp_request = getattr(item, "clamp_request", None)
        linked_product = getattr(clamp_request, "linked_product", None) if clamp_request else None
        published_to_catalog = bool(
            clamp_request
            and linked_product
            and linked_product.is_visible_in_catalog(include_uncategorized=False)
        )
        item.published_to_catalog = published_to_catalog
        item.can_publish_to_catalog = bool(clamp_request) and not published_to_catalog

    return order_items


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
                        "total_price": f"{saved_quote.final_price:.2f}",
                        "description": result["description"],
                        "generated_code": result.get("generated_code", ""),
                        "client_name": result["inputs"]["client_name"],
                    },
                )
                messages.success(
                    request,
                    f"Cotizacion guardada en {selected_price['label']} por ${_format_currency_ars(saved_quote.final_price)}.",
                )
                return redirect("admin_clamp_quoter")
            except ValueError as exc:
                messages.error(request, str(exc))
        elif action == "create_product":
            try:
                result = calculate_clamp_quote(request.POST)
                selected_key = str(request.POST.get("price_list_key", "")).strip()
                selected_map = {row["key"]: row for row in result["price_rows"]}
                selected_price = selected_map.get(selected_key)
                if not selected_price:
                    raise ValueError("Selecciona una lista valida para crear el producto.")

                stock_value = parse_int_value(request.POST.get("product_stock", "0"), "Stock inicial", min_value=0)
                sku = result["generated_code"]
                
                if Product.objects.filter(sku=sku).exists():
                    raise ValueError(f"Ya existe un producto con el SKU {sku}.")

                category_name = "ABRAZADERAS"
                if result["inputs"]["clamp_type"] == "laminada":
                    category_name = "ABRAZADERAS LAMINADAS"
                else:
                    category_name = "ABRAZADERAS TREFILADAS"
                
                category, _ = Category.objects.get_or_create(name=category_name)
                
                with transaction.atomic():
                    product = Product.objects.create(
                        sku=sku,
                        name=result["description"],
                        supplier="COTIZADOR",
                        cost=result["base_cost"],
                        price=selected_price["final_price"],
                        stock=stock_value,
                        category=category,
                        description=result["description"],
                    )
                    product.categories.add(category)
                    
                    from catalog.models import ClampSpecs
                    ClampSpecs.objects.create(
                        product=product,
                        fabrication=result["inputs"]["clamp_type"].upper(),
                        diameter=result["inputs"]["diameter"],
                        width=result["inputs"]["width_mm"],
                        length=result["inputs"]["length_mm"],
                        shape=result["inputs"]["profile_type"],
                    )
                
                log_admin_action(
                    request,
                    action="clamp_product_created",
                    target_type="product",
                    target_id=product.pk,
                    details={
                        "sku": sku,
                        "price": f"{product.price:.2f}",
                    }
                )
                messages.success(request, f"Producto {sku} creado exitosamente.")
                return redirect("admin_clamp_quoter")
            except ValueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Accion no reconocida por el cotizador.")

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


# ===================== ORDERS =====================

def _get_order_request_admin_queryset():
    return OrderRequest.objects.select_related(
        'user',
        'company',
        'client_company_ref',
        'client_company_ref__client_profile',
    ).prefetch_related(
        Prefetch(
            'items',
            queryset=OrderRequestItem.objects.select_related('product', 'clamp_request', 'price_list'),
        ),
        Prefetch(
            'proposals',
            queryset=OrderProposal.objects.select_related('created_by', 'responded_by')
            .prefetch_related(
                Prefetch(
                    'items',
                    queryset=OrderProposalItem.objects.select_related('product', 'clamp_request', 'price_list'),
                )
            )
            .order_by('-version_number', '-id'),
        ),
        Prefetch(
            'events',
            queryset=OrderRequestEvent.objects.select_related('actor').order_by('-created_at', '-id'),
        ),
        'generated_orders',
    )


def _get_order_request_for_admin(request, pk):
    active_company = get_active_company(request)
    order_request = get_object_or_404(_get_order_request_admin_queryset(), pk=pk)
    if active_company and order_request.company_id != active_company.id:
        raise ValidationError('La solicitud no pertenece a la empresa activa.')
    return order_request


def _parse_order_request_money(raw_value, field_label):
    normalized = str(raw_value or '').strip().replace(',', '.')
    if not normalized:
        raise ValidationError(f'Completa {field_label}.')
    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValidationError(f'{field_label} no es un numero valido.') from exc
    if value < 0:
        raise ValidationError(f'{field_label} no puede ser negativo.')
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _parse_order_request_quantity(raw_value):
    try:
        quantity = int(str(raw_value or '').strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError('La cantidad de la propuesta debe ser un entero valido.') from exc
    if quantity <= 0:
        raise ValidationError('La cantidad de la propuesta debe ser mayor a cero.')
    return quantity


def _get_order_request_proposal_source_rows(order_request, current_proposal=None):
    if current_proposal:
        return list(current_proposal.items.all())
    return list(order_request.items.all())


def _get_order_request_quote_document_types(company, *, origin_channel=""):
    if not company:
        return []
    origin_channel = str(origin_channel or "").strip().lower()
    queryset = (
        SalesDocumentType.objects.filter(
            company=company,
            enabled=True,
            billing_mode=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
        )
        .filter(
            Q(document_behavior=SALES_BEHAVIOR_COTIZACION)
            | Q(document_behavior=SALES_BEHAVIOR_PRESUPUESTO)
            | Q(internal_doc_type=DocumentSeries.DOC_COT)
        )
        .exclude(internal_doc_type="")
    )
    if origin_channel:
        queryset = queryset.annotate(
            origin_priority=Case(
                When(is_default=True, default_origin_channel=origin_channel, then=Value(0)),
                When(is_default=True, default_origin_channel="", then=Value(1)),
                When(default_origin_channel=origin_channel, then=Value(2)),
                When(default_origin_channel="", then=Value(3)),
                default=Value(9),
                output_field=IntegerField(),
            )
        ).order_by("origin_priority", "display_order", "name")
    else:
        queryset = queryset.order_by("-is_default", "display_order", "name")
    return list(queryset)


def _get_order_request_invoice_document_types(company, *, origin_channel=""):
    if not company:
        return []
    origin_channel = str(origin_channel or "").strip().lower()
    queryset = (
        SalesDocumentType.objects.filter(
            company=company,
            enabled=True,
            document_behavior=SALES_BEHAVIOR_FACTURA,
        )
        .exclude(fiscal_doc_type="")
    )
    if origin_channel:
        queryset = queryset.annotate(
            origin_priority=Case(
                When(is_default=True, default_origin_channel=origin_channel, then=Value(0)),
                When(is_default=True, default_origin_channel="", then=Value(1)),
                When(default_origin_channel=origin_channel, then=Value(2)),
                When(default_origin_channel="", then=Value(3)),
                default=Value(9),
                output_field=IntegerField(),
            )
        ).order_by("origin_priority", "display_order", "name")
    else:
        queryset = queryset.order_by("-is_default", "display_order", "name")
    return list(queryset)


def _count_legacy_client_account_documents_for_order(order):
    if not order:
        return 0
    table_names = connection.introspection.table_names()
    if "accounts_clientaccountdocument" not in table_names:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(1) FROM accounts_clientaccountdocument WHERE order_id = %s",
            [order.pk],
        )
        row = cursor.fetchone()
    return int(row[0] or 0) if row else 0


def _clear_legacy_client_account_documents_for_order(order):
    if not order:
        return 0
    table_names = connection.introspection.table_names()
    if "accounts_clientaccountdocument" not in table_names:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE accounts_clientaccountdocument SET order_id = NULL WHERE order_id = %s",
            [order.pk],
        )
        return int(cursor.rowcount or 0)


def _get_order_request_delete_blockers(order_request):
    blockers = []
    if order_request.converted_order:
        blockers.append(
            "La solicitud ya tiene un pedido operativo generado. Elimina primero ese pedido."
        )
    return blockers


def _get_internal_document_delete_blockers(document):
    blockers = []
    if document.payment_id:
        blockers.append("El documento interno esta vinculado a un pago.")
    if document.transaction_id:
        blockers.append("El documento interno esta vinculado a un ajuste de cuenta corriente.")
    if document.fiscal_documents.exists():
        blockers.append("El documento interno ya esta vinculado a comprobantes fiscales.")
    if document.stock_movements.exists():
        blockers.append("El documento interno genero movimientos de stock.")
    return blockers


def _get_fiscal_document_delete_blockers(document):
    from .fiscal import _get_fiscal_document_delete_blockers as fiscal_delete_blockers

    return fiscal_delete_blockers(document)


def _get_order_hard_delete_blockers(order):
    blockers = []
    if order.status not in {Order.STATUS_DRAFT, Order.STATUS_CANCELLED}:
        blockers.append("Solo se pueden eliminar pedidos en borrador o cancelados.")
    if order.payments.filter(is_cancelled=False).exists():
        blockers.append("El pedido tiene pagos aplicados.")
    internal_docs = list(order.documents.all())
    if any(_get_internal_document_delete_blockers(doc) for doc in internal_docs):
        blockers.append("El pedido tiene documentos internos no eliminables automaticamente.")
    fiscal_docs = list(order.fiscal_documents.all())
    if any(_get_fiscal_document_delete_blockers(doc) for doc in fiscal_docs):
        blockers.append("El pedido tiene comprobantes fiscales no eliminables automaticamente.")
    if order.stock_movements.exists():
        blockers.append("El pedido conserva movimientos de stock propios.")
    if order.client_transactions.exclude(
        transaction_type=ClientTransaction.TYPE_ORDER_CHARGE
    ).exists():
        blockers.append("El pedido tiene movimientos contables adicionales vinculados.")
    return blockers


def _ensure_request_operational_order(order_request, *, actor, target_status=Order.STATUS_DRAFT):
    order = order_request.converted_order
    if not order:
        order, _ = convert_request_to_order(
            order_request=order_request,
            actor=actor,
            status=target_status,
        )
        return order

    if (
        target_status == Order.STATUS_CONFIRMED
        and order.normalized_status() == Order.STATUS_DRAFT
    ):
        order.change_status(
            Order.STATUS_CONFIRMED,
            changed_by=actor,
            note=f"Pedido confirmado al emitir factura desde solicitud #{order_request.pk}",
        )
        order.refresh_from_db()
    return order


def _build_order_request_proposal_payloads(source_rows, post_data):
    item_payloads = []
    for row in source_rows:
        row_key = str(row.line_number)
        if post_data.get(f'row_enabled_{row_key}') != 'on':
            continue
        replacement_product = None
        replacement_product_id = str(post_data.get(f'replacement_product_id_{row_key}', '')).strip()
        if replacement_product_id:
            if not replacement_product_id.isdigit():
                raise ValidationError(
                    f'El producto alternativo de la linea {row.line_number} no es valido.'
                )
            replacement_product = Product.objects.filter(pk=int(replacement_product_id), is_active=True).first()
            if not replacement_product:
                raise ValidationError(
                    f'No se encontro el producto alternativo seleccionado para la linea {row.line_number}.'
                )
        quantity = _parse_order_request_quantity(post_data.get(f'quantity_{row_key}'))
        unit_price_base = _parse_order_request_money(
            post_data.get(f'unit_price_base_{row_key}'),
            f'precio base de la linea {row.line_number}',
        )
        price_at_snapshot = _parse_order_request_money(
            post_data.get(f'price_at_snapshot_{row_key}'),
            f'precio final de la linea {row.line_number}',
        )
        selected_product = replacement_product or row.product
        selected_clamp_request = row.clamp_request if not replacement_product else None
        selected_sku = row.product_sku
        selected_name = row.product_name
        if replacement_product:
            selected_sku = replacement_product.sku
            selected_name = replacement_product.name
        item_payloads.append(
            {
                'product': selected_product,
                'clamp_request': selected_clamp_request,
                'product_sku': selected_sku,
                'product_name': selected_name,
                'quantity': quantity,
                'unit_price_base': unit_price_base,
                'discount_percentage_used': row.discount_percentage_used,
                'price_list': row.price_list,
                'price_at_snapshot': price_at_snapshot,
            }
        )
    if not item_payloads:
        raise ValidationError('Selecciona al menos una linea para enviar la propuesta.')
    return item_payloads

@staff_member_required
def sales_workspace(request):
    """Cross-flow sales view joining requests and operational orders."""
    companies = Company.objects.filter(is_active=True).order_by("name")
    company_map = {company.pk: company for company in companies}
    active_company = get_admin_company_filter(request)
    selected_company_id = "all" if active_company is None else str(active_company.pk)
    stage = request.GET.get("stage", "").strip().lower()
    client = request.GET.get("client", "").strip()

    rows = build_sales_pipeline_rows(
        company=active_company,
        stage=stage,
        client_query=client,
    )
    actions_by_company_id = {}
    if active_company:
        actions_by_company_id[active_company.pk] = _build_related_sales_document_actions(
            company=active_company,
            operations_locked=False,
            quick_order_url="",
        )
    for row in rows:
        row["quick_payment_url"] = ""
        row["quick_related_actions"] = []
        row["quick_action_url"] = ""
        if row.get("kind") != "order":
            continue
        client_profile_id = row.get("client_profile_id")
        company_id = row.get("company_id")
        order_id = row.get("order_id")
        if not client_profile_id or not company_id or not order_id:
            continue
        if company_id not in actions_by_company_id:
            company_obj = company_map.get(company_id)
            actions_by_company_id[company_id] = _build_related_sales_document_actions(
                company=company_obj,
                operations_locked=False,
                quick_order_url="",
            ) if company_obj else []
        quick_url = reverse("admin_client_quick_order", args=[client_profile_id])
        row["quick_action_url"] = quick_url
        row["quick_payment_url"] = (
            f"{reverse('admin_payment_list')}?"
            f"{urlencode({'order_id': order_id, 'company_id': company_id, 'client_id': client_profile_id})}"
        )
        row["quick_related_actions"] = [
            {
                **item,
                "url": quick_url,
            }
            for item in actions_by_company_id.get(company_id, [])
        ]
    paginator = Paginator(rows, 50)
    page = request.GET.get("page", 1)
    page_obj = paginator.get_page(page)

    return render(request, "admin_panel/orders/sales_workspace.html", {
        "page_obj": page_obj,
        "stage": stage,
        "client": client,
        "companies": companies,
        "selected_company_id": selected_company_id,
        "sales_workspace_cards": build_sales_workspace(
            company=active_company,
            hub_url_name="admin_sales_workspace",
        ),
        "sales_workspace_active_keys": resolve_sales_workspace_active_keys(stage),
    })


@staff_member_required
def order_request_list(request):
    """Commercial requests submitted from the catalog before operational confirmation."""
    order_requests = _get_order_request_admin_queryset()
    companies = Company.objects.filter(is_active=True).order_by("name")
    active_company = get_admin_company_filter(request)
    selected_company_id = "all" if active_company is None else str(active_company.pk)
    if active_company:
        order_requests = order_requests.filter(company=active_company)

    status = request.GET.get('status', '').strip()
    stage = request.GET.get('stage', '').strip().lower()
    if stage == 'inbox':
        order_requests = order_requests.filter(
            status__in=[OrderRequest.STATUS_SUBMITTED, OrderRequest.STATUS_IN_REVIEW]
        )
    elif stage == 'waiting':
        order_requests = order_requests.filter(
            status__in=[OrderRequest.STATUS_PROPOSAL_SENT, OrderRequest.STATUS_WAITING_CLIENT]
        )
    elif stage == 'resolved':
        order_requests = order_requests.filter(
            status__in=[OrderRequest.STATUS_CONFIRMED, OrderRequest.STATUS_CONVERTED]
        )
    if status:
        order_requests = order_requests.filter(status=status)

    client = request.GET.get('client', '').strip()
    if client:
        order_requests = order_requests.filter(
            Q(user__username__icontains=client)
            | Q(client_company_ref__client_profile__company_name__icontains=client)
        )

    paginator = Paginator(order_requests.order_by('-created_at'), 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    return render(request, 'admin_panel/order_requests/list.html', {
        'page_obj': page_obj,
        'stage': stage,
        'status': status,
        'client': client,
        'status_choices': OrderRequest.STATUS_CHOICES,
        'companies': companies,
        'selected_company_id': selected_company_id,
        'sales_workspace_cards': build_sales_workspace(company=active_company),
        'sales_workspace_active_keys': resolve_sales_workspace_active_keys(stage),
    })


@staff_member_required
def order_request_detail(request, pk):
    """Detailed admin inbox view for one commercial request."""
    try:
        order_request = _get_order_request_for_admin(request, pk)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect('admin_order_request_list')
    proposals = list(order_request.proposals.all())
    current_proposal = next((proposal for proposal in proposals if proposal.is_current), None)
    proposal_source_rows = _get_order_request_proposal_source_rows(order_request, current_proposal=current_proposal)
    request_order = order_request.converted_order
    request_order_charge = None
    request_order_charge_is_billable = False
    request_order_documents = []
    request_order_fiscal_documents = []
    request_events = list(order_request.events.all()[:20])
    request_delete_blockers = _get_order_request_delete_blockers(order_request)
    quote_document_types = _get_order_request_quote_document_types(
        order_request.company,
        origin_channel=order_request.origin_channel,
    )
    invoice_document_types = _get_order_request_invoice_document_types(
        order_request.company,
        origin_channel=order_request.origin_channel,
    )
    if request_order:
        request_order_charge = (
            ClientTransaction.objects.filter(
                order=request_order,
                transaction_type=ClientTransaction.TYPE_ORDER_CHARGE,
            )
            .order_by("-occurred_at", "-id")
            .first()
        )
        if request_order_charge and Decimal(request_order_charge.amount or 0) > 0:
            request_order_charge_is_billable = True
        request_order_documents = list(
            InternalDocument.objects.select_related('sales_document_type')
            .filter(order=request_order)
            .order_by('issued_at', 'id')
        )
        for doc in request_order_documents:
            doc.can_safe_delete = not _get_internal_document_delete_blockers(doc)
        request_order_fiscal_documents = list(
            FiscalDocument.objects.select_related('sales_document_type', 'point_of_sale')
            .filter(order=request_order)
            .order_by('-created_at', '-id')
        )
        for doc in request_order_fiscal_documents:
            doc.can_safe_delete = not _get_fiscal_document_delete_blockers(doc)
    return render(request, 'admin_panel/order_requests/detail.html', {
        'order_request': order_request,
        'request_order': request_order,
        'request_order_charge': request_order_charge,
        'request_order_charge_is_billable': request_order_charge_is_billable,
        'request_order_documents': request_order_documents,
        'request_order_fiscal_documents': request_order_fiscal_documents,
        'current_proposal': current_proposal,
        'proposal_source_rows': proposal_source_rows,
        'can_confirm_request': order_request.status in {
            OrderRequest.STATUS_SUBMITTED,
            OrderRequest.STATUS_IN_REVIEW,
            OrderRequest.STATUS_PROPOSAL_SENT,
            OrderRequest.STATUS_WAITING_CLIENT,
        },
        'can_reject_request': order_request.status not in {
            OrderRequest.STATUS_REJECTED,
            OrderRequest.STATUS_CANCELLED,
            OrderRequest.STATUS_CONVERTED,
        },
        'can_send_proposal': (
            order_request.status
            not in {
                OrderRequest.STATUS_REJECTED,
                OrderRequest.STATUS_CANCELLED,
                OrderRequest.STATUS_CONVERTED,
            }
            and not request_order
        ),
        'can_convert_request': order_request.status == OrderRequest.STATUS_CONFIRMED and not order_request.converted_order,
        'can_generate_documents': bool(
            order_request.status == OrderRequest.STATUS_CONFIRMED or request_order
        ),
        'can_delete_request': not request_delete_blockers,
        'request_delete_blockers': request_delete_blockers,
        'quote_document_types': quote_document_types,
        'invoice_document_types': invoice_document_types,
        'proposals': proposals,
        'request_events': request_events,
    })


@staff_member_required
@require_POST
def order_request_confirm_view(request, pk):
    """Confirm one request without sending a counterproposal."""
    try:
        order_request = _get_order_request_for_admin(request, pk)
        admin_note = request.POST.get('admin_note', '').strip()
        if admin_note:
            order_request.admin_note = admin_note
            order_request.save(update_fields=['admin_note', 'updated_at'])
        confirm_order_request(order_request=order_request, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'Solicitud #{order_request.pk} confirmada sin cambios.')
    return redirect('admin_order_request_detail', pk=pk)


@staff_member_required
@require_POST
def order_request_reject_view(request, pk):
    """Reject one request with a visible commercial reason."""
    reason = request.POST.get('rejection_reason', '').strip()
    try:
        order_request = _get_order_request_for_admin(request, pk)
        reject_order_request(order_request=order_request, reason=reason, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, f'Solicitud #{order_request.pk} rechazada.')
    return redirect('admin_order_request_detail', pk=pk)


@staff_member_required
@require_POST
def order_request_propose_view(request, pk):
    """Create or replace the current commercial proposal for the client."""
    try:
        order_request = _get_order_request_for_admin(request, pk)
        current_proposal = order_request.current_proposal
        proposal_source_rows = _get_order_request_proposal_source_rows(
            order_request,
            current_proposal=current_proposal,
        )
        item_payloads = _build_order_request_proposal_payloads(proposal_source_rows, request.POST)
        proposal = create_order_proposal(
            order_request=order_request,
            created_by=request.user,
            item_payloads=item_payloads,
            message_to_client=request.POST.get('message_to_client', '').strip(),
            internal_note=request.POST.get('internal_note', '').strip(),
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f'Propuesta v{proposal.version_number} enviada al cliente para la solicitud #{order_request.pk}.',
        )
    return redirect('admin_order_request_detail', pk=pk)


@staff_member_required
@require_POST
def order_request_convert_view(request, pk):
    """Create the operational order draft from a confirmed request."""
    try:
        order_request = _get_order_request_for_admin(request, pk)
        order, created = convert_request_to_order(order_request=order_request, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect('admin_order_request_detail', pk=pk)
    if created:
        messages.success(
            request,
            f'Solicitud #{order_request.pk} convertida al pedido operativo #{order.pk} en borrador.',
        )
    else:
        messages.info(request, f'La solicitud ya estaba convertida en el pedido #{order.pk}.')
    return redirect('admin_order_detail', pk=order.pk)


@staff_member_required
@require_POST
def order_request_generate_quote_view(request, pk):
    """Generate or reuse one non-fiscal quote from a confirmed/commercially approved request."""
    try:
        order_request = _get_order_request_for_admin(request, pk)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect('admin_order_request_list')

    if order_request.status not in {OrderRequest.STATUS_CONFIRMED, OrderRequest.STATUS_CONVERTED}:
        messages.error(request, 'La solicitud debe estar confirmada para generar una cotizacion.')
        return redirect('admin_order_request_detail', pk=pk)

    order = _ensure_request_operational_order(
        order_request,
        actor=request.user,
        target_status=Order.STATUS_DRAFT,
    )
    sales_document_type = None
    sales_document_type_id = str(request.POST.get('sales_document_type_id', '')).strip()
    if sales_document_type_id.isdigit():
        sales_document_type = SalesDocumentType.objects.filter(
            pk=int(sales_document_type_id),
            company=order.company,
            enabled=True,
            billing_mode=SALES_BILLING_MODE_INTERNAL_DOCUMENT,
        ).first()

    try:
        if sales_document_type:
            document, created = create_internal_document_from_sales_type(
                order=order,
                sales_document_type=sales_document_type,
                actor=request.user,
            )
        else:
            existing_document = InternalDocument.objects.filter(
                order=order,
                doc_type=DocumentSeries.DOC_COT,
            ).first()
            document = ensure_document_for_order(
                order,
                doc_type=DocumentSeries.DOC_COT,
                actor=request.user,
            )
            if not document:
                raise ValidationError('No se encontro una configuracion valida para la cotizacion.')
            created = existing_document is None
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('admin_order_request_detail', pk=pk)

    if created:
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_QUOTE_GENERATED,
            actor=request.user,
            from_status=order_request.status,
            to_status=order_request.status,
            message=f"Se genero la cotizacion {document.commercial_type_label} {document.display_number}.",
            metadata={
                "document_id": document.pk,
                "document_number": document.display_number,
                "order_id": order.pk,
            },
        )
        messages.success(
            request,
            f'Cotizacion generada para la solicitud #{order_request.pk}.',
        )
    else:
        messages.info(
            request,
            f'La cotizacion ya existia y se reutilizo para la solicitud #{order_request.pk}.',
        )
    return redirect('admin_order_request_detail', pk=pk)


@staff_member_required
@require_POST
def order_request_generate_invoice_view(request, pk):
    """Generate or reuse one fiscal invoice from the approved request snapshot."""
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_order_request_list"),
        action_label="generar facturas desde solicitudes",
    )
    if denied_response:
        return denied_response

    try:
        order_request = _get_order_request_for_admin(request, pk)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect('admin_order_request_list')

    if order_request.status not in {OrderRequest.STATUS_CONFIRMED, OrderRequest.STATUS_CONVERTED}:
        messages.error(request, 'La solicitud debe estar confirmada para generar factura.')
        return redirect('admin_order_request_detail', pk=pk)

    order = _ensure_request_operational_order(
        order_request,
        actor=request.user,
        target_status=Order.STATUS_DRAFT,
    )
    invoice_ready, invoice_errors = is_invoice_ready(order)

    sales_document_type = None
    sales_document_type_id = str(request.POST.get('sales_document_type_id', '')).strip()
    if sales_document_type_id.isdigit():
        sales_document_type = SalesDocumentType.objects.filter(
            pk=int(sales_document_type_id),
            company=order.company,
            enabled=True,
            document_behavior=SALES_BEHAVIOR_FACTURA,
        ).first()

    try:
        if sales_document_type:
            fiscal_doc, created = create_fiscal_document_from_sales_type(
                order=order,
                sales_document_type=sales_document_type,
                actor=request.user,
                require_invoice_ready=invoice_ready,
            )
        else:
            point_of_sale = _resolve_default_point_of_sale(order.company)
            fiscal_doc, created = create_local_fiscal_document_from_order(
                order=order,
                company=order.company,
                doc_type=_resolve_preferred_invoice_doc_type(order),
                point_of_sale=point_of_sale,
                issue_mode=FISCAL_ISSUE_MODE_MANUAL,
                actor=request.user,
                require_invoice_ready=invoice_ready,
            )
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('admin_order_request_detail', pk=pk)

    if order.normalized_status() == Order.STATUS_DRAFT:
        try:
            order.change_status(
                Order.STATUS_CONFIRMED,
                changed_by=request.user,
                note=f'Confirmado al generar factura desde solicitud #{order_request.pk}',
            )
        except ValueError as exc:
            messages.warning(
                request,
                f'La factura se genero, pero el pedido no pudo confirmarse automaticamente: {exc}',
            )

    if created:
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_INVOICE_GENERATED,
            actor=request.user,
            from_status=order_request.status,
            to_status=order_request.status,
            message=(
                f"Se genero la factura {fiscal_doc.commercial_type_label} "
                f"{fiscal_doc.display_number}."
            ),
            metadata={
                "document_id": fiscal_doc.pk,
                "document_number": fiscal_doc.display_number,
                "order_id": order.pk,
                "issue_mode": fiscal_doc.issue_mode,
                "status": fiscal_doc.status,
            },
        )
        if invoice_ready:
            messages.success(
                request,
                f'Factura abierta/generada para la solicitud #{order_request.pk}.',
            )
        else:
            messages.warning(
                request,
                'Se abrio el comprobante fiscal, pero faltan datos para cerrarlo o emitirlo. '
                + '; '.join(invoice_errors[:3]),
            )
    else:
        messages.info(request, 'La factura ya existia y se reutilizo.')
    return redirect('admin_fiscal_document_detail', pk=fiscal_doc.pk)


@staff_member_required
def order_request_delete_view(request, pk):
    """Hard delete one commercial request only when it has no operational order linked."""
    try:
        order_request = _get_order_request_for_admin(request, pk)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect('admin_order_request_list')

    blockers = _get_order_request_delete_blockers(order_request)
    if blockers:
        messages.error(request, "No se puede eliminar la solicitud: " + " ".join(blockers))
        return redirect('admin_order_request_detail', pk=pk)

    if request.method == 'POST':
        company_id = order_request.company_id
        request_id = order_request.pk
        order_request.delete()
        messages.success(request, f'Solicitud #{request_id} eliminada correctamente.')
        if get_active_company(request) and get_active_company(request).id != company_id:
            return redirect('admin_order_request_list')
        return redirect('admin_order_request_list')

    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"Solicitud #{order_request.pk}",
        'cancel_url': reverse('admin_order_request_detail', args=[pk]),
        'title': 'Eliminar Solicitud',
        'question': 'Estas por eliminar esta solicitud comercial.',
        'warning': 'Se borraran items, propuestas y trazabilidad de la solicitud. Esta accion no se puede deshacer.',
        'confirm_label': 'Eliminar solicitud',
    })


@staff_member_required
def order_list(request):
    """Order list with filters."""
    orders = Order.objects.select_related('user', 'company').all()
    companies = Company.objects.filter(is_active=True).order_by("name")
    active_company = get_admin_company_filter(request)
    selected_company_id = "all" if active_company is None else str(active_company.pk)
    if active_company:
        orders = orders.filter(company=active_company)
    stage = request.GET.get('stage', '').strip().lower()
    invoice_order_ids = FiscalDocument.objects.exclude(status=FISCAL_STATUS_VOIDED)
    remito_order_ids = InternalDocument.objects.filter(doc_type=DocumentSeries.DOC_REM)
    if active_company:
        invoice_order_ids = invoice_order_ids.filter(company=active_company)
        remito_order_ids = remito_order_ids.filter(company=active_company)
    invoice_order_ids = invoice_order_ids.filter(doc_type__in=INVOICE_FISCAL_DOC_TYPES).values_list('order_id', flat=True)
    remito_order_ids = remito_order_ids.values_list('order_id', flat=True)

    if stage == 'drafts':
        orders = orders.filter(status=Order.STATUS_DRAFT)
    elif stage == 'remito':
        orders = orders.filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]).exclude(pk__in=remito_order_ids)
    elif stage == 'invoice':
        orders = (
            orders
            .filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED])
            .exclude(pk__in=invoice_order_ids)
            .filter(Q(saas_document_number='') | Q(saas_document_number__isnull=True))
        )
    elif stage == 'collections':
        orders = (
            orders
            .exclude(status=Order.STATUS_CANCELLED)
            .annotate(
                total_paid=Coalesce(
                    Sum('payments__amount', filter=Q(payments__is_cancelled=False)),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
            .filter(total__gt=F('total_paid'))
        )
    elif stage == 'active':
        orders = orders.filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED])

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
    page_orders = list(page_obj.object_list)

    user_ids = {order.user_id for order in page_orders if order.user_id}
    profile_by_user_id = {}
    if user_ids:
        for profile in ClientProfile.objects.only("pk", "user_id").filter(user_id__in=user_ids):
            profile_by_user_id[profile.user_id] = profile.pk

    actions_by_company_id = {}
    if active_company:
        actions_by_company_id[active_company.pk] = _build_related_sales_document_actions(
            company=active_company,
            operations_locked=False,
            quick_order_url="",
        )

    for order in page_orders:
        order.quick_client_profile_id = profile_by_user_id.get(order.user_id) or ""
        if order.company_id not in actions_by_company_id:
            company_obj = order.company if getattr(order, "company_id", None) else None
            actions_by_company_id[order.company_id] = _build_related_sales_document_actions(
                company=company_obj,
                operations_locked=False,
                quick_order_url="",
            ) if company_obj else []
        order.quick_related_actions = actions_by_company_id.get(order.company_id, [])
        if order.quick_client_profile_id:
            quick_url = reverse("admin_client_quick_order", args=[order.quick_client_profile_id])
            order.quick_related_actions = [
                {
                    **item,
                    "url": quick_url,
                }
                for item in order.quick_related_actions
            ]
    
    return render(request, 'admin_panel/orders/list.html', {
        'page_obj': page_obj,
        'stage': stage,
        'status': status,
        'sync_status': sync_status,
        'client': client,
        'status_choices': Order.STATUS_CHOICES,
        'sync_status_choices': Order.SYNC_STATUS_CHOICES,
        'origin_channel_choices': Order.ORIGIN_CHOICES,
        'companies': companies,
        'selected_company_id': selected_company_id,
        'sales_workspace_cards': build_sales_workspace(company=active_company),
        'sales_workspace_active_keys': resolve_sales_workspace_active_keys(stage),
    })


@staff_member_required
@require_POST
def order_create_from_panel(request):
    """Create a draft order from Orders panel selecting client + company."""
    raw_client_id = str(request.POST.get("client_id", "")).strip()
    if not raw_client_id.isdigit():
        messages.error(request, "Selecciona un cliente valido para crear el pedido.")
        return redirect("admin_order_list")

    client = get_object_or_404(
        ClientProfile.objects.select_related("user"),
        pk=int(raw_client_id),
    )
    if not getattr(client, "user_id", None):
        messages.error(request, "El cliente seleccionado no tiene usuario vinculado.")
        return redirect("admin_order_list")

    selected_origin_channel = str(
        request.POST.get("origin_channel", Order.ORIGIN_ADMIN)
    ).strip().lower()
    if selected_origin_channel not in dict(Order.ORIGIN_CHOICES):
        selected_origin_channel = Order.ORIGIN_ADMIN

    active_company = get_admin_selected_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa valida para crear el pedido.")
        return redirect("admin_order_list")

    client_company = client.get_company_link(active_company)
    if not client_company:
        messages.error(request, "El cliente no tiene relacion comercial activa con esta empresa.")
        return redirect("admin_order_list")

    try:
        order = _create_draft_order_for_client(
            client=client,
            client_company=client_company,
            company=active_company,
            origin_channel=selected_origin_channel,
            actor=request.user,
            created_label="Pedido",
            admin_note="Pedido creado desde panel de pedidos.",
            history_note="Pedido creado desde panel de pedidos",
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_list")

    messages.success(
        request,
        (
            f"Pedido #{order.pk} creado para {client.company_name or client.user.username}. "
            "Ahora podes cargar productos."
        ),
    )
    return redirect("admin_order_detail", pk=order.pk)


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
    order_items = _build_order_detail_items(order)

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
            admin_notes_input = request.POST.get('admin_notes', None)
            try:
                with transaction.atomic():
                    locked_order = Order.objects.select_for_update().get(pk=order.pk)
                    before = model_snapshot(locked_order, ['status', 'admin_notes', 'status_updated_at'])
                    if admin_notes_input is not None:
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
    hard_delete_blockers = _get_order_hard_delete_blockers(order)
    order_movement_transaction = _resolve_order_charge_transaction(order)
    order_movement_closed = _movement_allows_print(order_movement_transaction)
    order_movement_state = (
        (order_movement_transaction.movement_state or ClientTransaction.STATE_OPEN)
        if order_movement_transaction
        else ClientTransaction.STATE_OPEN
    )
    order_movement_state_label = dict(ClientTransaction.STATE_CHOICES).get(order_movement_state, "Abierto")
    order_items_edit_locked = _is_order_items_edit_locked(order)
    order_items_edit_lock_reason = (
        "La venta tiene registracion final cerrada; no se pueden editar productos."
        if order_items_edit_locked
        else ""
    )
    order_movement_can_reopen = (
        bool(order_movement_transaction)
        and not _is_transaction_reopen_locked(order_movement_transaction)
    )
    order_movement_can_manage = bool(order_movement_transaction and order_client_profile)
    order_movement_can_close, order_movement_close_block_reason = can_transition_transaction_state(
        order_movement_transaction,
        ClientTransaction.STATE_CLOSED,
    )
    order_movement_can_void, order_movement_void_block_reason = can_transition_transaction_state(
        order_movement_transaction,
        ClientTransaction.STATE_VOIDED,
    )
    order_movement_can_open, order_movement_open_block_reason = can_transition_transaction_state(
        order_movement_transaction,
        ClientTransaction.STATE_OPEN,
    )
    order_documents = list(
        InternalDocument.objects.select_related('sales_document_type').filter(order=order).order_by('issued_at')
    )
    for doc in order_documents:
        doc.can_safe_delete = not _get_internal_document_delete_blockers(doc)
        doc.can_print = order_movement_closed
    order_fiscal_documents = list(order_fiscal_documents)
    for doc in order_fiscal_documents:
        doc.can_safe_delete = not _get_fiscal_document_delete_blockers(doc)
        doc.can_print = order_movement_closed
    related_sales_document_actions = _build_related_sales_document_actions(
        company=order.company,
        operations_locked=False,
        quick_order_url=(
            reverse("admin_client_quick_order", args=[order_client_profile.pk])
            if order_client_profile
            else ""
        ),
    )
    order_flow_steps = build_order_flow_steps(
        order,
        order_documents=order_documents,
        order_invoice_document=order_invoice_document,
        order_has_external_invoice=order_has_external_invoice,
        order_paid_amount=order.get_paid_amount(),
        order_pending_amount=order.get_pending_amount(),
        client_profile_id=order_client_profile.pk if order_client_profile else None,
    )

    return render(request, 'admin_panel/orders/detail.html', {
        'order': order,
        'order_items': order_items,
        'status_choices': Order.STATUS_CHOICES,
        'status_history': order.status_history.all()[:20],
        'order_paid_amount': order.get_paid_amount(),
        'order_pending_amount': order.get_pending_amount(),
        'order_is_paid': order.is_paid(),
        'order_client_profile_id': order_client_profile.pk if order_client_profile else '',
        'order_documents': order_documents,
        'document_company': order.company,
        'pricing_snapshot': pricing_snapshot,
        'sales_internal_document_types': sales_internal_document_types,
        'order_fiscal_documents': order_fiscal_documents,
        'order_invoice_document': order_invoice_document,
        'order_invoice_state': order_invoice_state,
        'order_can_invoice': order_can_invoice,
        'order_has_external_invoice': order_has_external_invoice,
        'order_invoice_action_label': order_invoice_action_label,
        'order_movement_transaction': order_movement_transaction,
        'order_movement_closed': order_movement_closed,
        'order_movement_state': order_movement_state,
        'order_movement_state_label': order_movement_state_label,
        'order_movement_can_reopen': order_movement_can_reopen,
        'order_movement_can_manage': order_movement_can_manage,
        'order_movement_can_close': order_movement_can_close,
        'order_movement_can_open': order_movement_can_open,
        'order_movement_can_void': order_movement_can_void,
        'order_movement_close_block_reason': order_movement_close_block_reason,
        'order_movement_open_block_reason': order_movement_open_block_reason,
        'order_movement_void_block_reason': order_movement_void_block_reason,
        'order_items_edit_locked': order_items_edit_locked,
        'order_items_edit_lock_reason': order_items_edit_lock_reason,
        'order_detail_next_url': request.get_full_path(),
        'can_hard_delete_order': not hard_delete_blockers,
        'hard_delete_blockers': hard_delete_blockers,
        'order_flow_steps': order_flow_steps,
        'related_sales_document_actions': related_sales_document_actions,
        'order_related_source_tx_id': order_movement_transaction.pk if order_movement_transaction else '',
        'order_related_source_order_id': order.pk,
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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_order_detail", args=[order.pk]),
        action_label="abrir/generar facturas",
    )
    if denied_response:
        return denied_response

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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_order_detail", args=[order.pk]),
        action_label="crear comprobantes fiscales",
    )
    if denied_response:
        return denied_response

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
    denied_response = _deny_fiscal_operation_if_needed(
        request,
        redirect_url=reverse("admin_order_detail", args=[order.pk]),
        action_label="registrar comprobantes externos",
    )
    if denied_response:
        return denied_response

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
@require_POST
def order_item_add(request, pk):
    order = get_object_or_404(Order.objects.select_related('company', 'user'), pk=pk)
    detail_anchor_url = f"{reverse('admin_order_detail', args=[order.pk])}#order-item-add-form"
    is_ajax = _is_ajax_request(request)

    def _error_response(message, *, status=400):
        if is_ajax:
            return JsonResponse({"ok": False, "error": message}, status=status)
        messages.error(request, message)
        return redirect(detail_anchor_url)

    def _success_response(message):
        if not is_ajax:
            messages.success(request, message)
            return redirect(detail_anchor_url)

        order.refresh_from_db(fields=["subtotal", "discount_percentage", "discount_amount", "total"])
        order_items = _build_order_detail_items(order)
        items_tbody_html = render_to_string(
            "admin_panel/orders/_order_items_rows.html",
            {
                "order": order,
                "order_items": order_items,
                "order_items_edit_locked": _is_order_items_edit_locked(order),
            },
            request=request,
        )
        items_count = len(order_items)
        payload = {
            "ok": True,
            "message": message,
            "items_count": items_count,
            "items_label": f"{items_count} item" + ("" if items_count == 1 else "s"),
            "items_tbody_html": items_tbody_html,
            "totals": {
                "subtotal": str(order.subtotal or Decimal("0.00")),
                "discount_amount": str(order.discount_amount or Decimal("0.00")),
                "total": str(order.total or Decimal("0.00")),
                "paid": str(order.get_paid_amount() or Decimal("0.00")),
                "pending": str(order.get_pending_amount() or Decimal("0.00")),
            },
        }
        return JsonResponse(payload)

    if not order.is_mutable_for_items():
        return _error_response("Solo podes editar items en pedidos borrador.")
    if _is_order_items_edit_locked(order):
        return _error_response("La venta tiene registracion final cerrada; no se pueden editar productos.")

    sku = request.POST.get("sku", "").strip()
    product_id = request.POST.get("product_id", "").strip()
    qty_raw = request.POST.get("quantity", "1").strip()
    try:
        quantity = int(qty_raw)
    except ValueError:
        quantity = 0
    if quantity <= 0:
        return _error_response("Cantidad invalida.")

    product = None
    if product_id.isdigit():
        product = Product.objects.filter(pk=int(product_id)).first()
    if not product and sku:
        product_matches = _find_products_for_order_query(sku, limit=5)
        if len(product_matches) == 1:
            product = product_matches[0]
        elif len(product_matches) > 1:
            matches_label = ", ".join(match.sku for match in product_matches[:3])
            return _error_response(
                f'Hay varias coincidencias para "{sku}". Elegi una sugerencia mas precisa ({matches_label}).',
            )
    if not product:
        return _error_response("Producto no encontrado.")

    price_raw = request.POST.get("price", "").strip()
    manual_price = _parse_order_item_manual_price(price_raw)
    if price_raw and manual_price is None:
        return _error_response("Precio invalido.")

    unit_price_base, auto_final_price, discount_used, price_list = _resolve_order_item_pricing(order, product)
    final_price = manual_price if manual_price is not None else auto_final_price
    if manual_price is not None:
        unit_price_base = manual_price

    # If manual price is used, we might want to recalculate the discount or just use it as is.
    # For now, we use the manual price as the final price at purchase.
    
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

    _recalculate_order_totals_from_items(order, discount_percentage=discount_used)
    return _success_response("Item agregado al documento.")


@staff_member_required
def order_item_edit(request, pk, item_id):
    order = get_object_or_404(Order.objects.select_related('company', 'user'), pk=pk)
    if not order.is_mutable_for_items():
        messages.error(request, "Solo podes editar items en pedidos borrador.")
        return redirect("admin_order_detail", pk=order.pk)
    if _is_order_items_edit_locked(order):
        messages.error(request, "La venta tiene registracion final cerrada; no se pueden editar productos.")
        return redirect("admin_order_detail", pk=order.pk)

    order_item = get_object_or_404(OrderItem.objects.select_related("product"), pk=item_id, order=order)

    form_sku = request.POST.get("sku", order_item.product_sku).strip() if request.method == "POST" else order_item.product_sku
    form_product_id = request.POST.get("product_id", str(order_item.product_id or "")).strip() if request.method == "POST" else str(order_item.product_id or "")
    form_quantity = request.POST.get("quantity", str(order_item.quantity)).strip() if request.method == "POST" else str(order_item.quantity)
    form_price = request.POST.get("price", f"{order_item.price_at_purchase:.2f}").strip() if request.method == "POST" else f"{order_item.price_at_purchase:.2f}"

    if request.method == "POST":
        try:
            quantity = int(form_quantity)
        except ValueError:
            quantity = 0
        if quantity <= 0:
            messages.error(request, "Cantidad invalida.")
            return render(
                request,
                "admin_panel/orders/item_edit.html",
                {
                    "order": order,
                    "order_item": order_item,
                    "form_sku": form_sku,
                    "form_product_id": form_product_id,
                    "form_quantity": form_quantity,
                    "form_price": form_price,
                },
            )

        selected_product = None
        if form_product_id.isdigit():
            selected_product = Product.objects.filter(pk=int(form_product_id), is_active=True).first()
        if not selected_product and form_sku:
            product_matches = _find_products_for_order_query(form_sku, limit=5)
            if len(product_matches) == 1:
                selected_product = product_matches[0]
            elif len(product_matches) > 1:
                matches_label = ", ".join(match.sku for match in product_matches[:3])
                messages.error(
                    request,
                    f'Hay varias coincidencias para "{form_sku}". Elegi una sugerencia mas precisa ({matches_label}).',
                )
                return render(
                    request,
                    "admin_panel/orders/item_edit.html",
                    {
                        "order": order,
                        "order_item": order_item,
                        "form_sku": form_sku,
                        "form_product_id": form_product_id,
                        "form_quantity": form_quantity,
                        "form_price": form_price,
                    },
                )

        if not selected_product:
            selected_product = order_item.product
        if not selected_product:
            messages.error(request, "Producto no encontrado.")
            return render(
                request,
                "admin_panel/orders/item_edit.html",
                {
                    "order": order,
                    "order_item": order_item,
                    "form_sku": form_sku,
                    "form_product_id": form_product_id,
                    "form_quantity": form_quantity,
                    "form_price": form_price,
                },
            )

        manual_price = _parse_order_item_manual_price(form_price)
        if manual_price is None:
            messages.error(request, "Precio invalido.")
            return render(
                request,
                "admin_panel/orders/item_edit.html",
                {
                    "order": order,
                    "order_item": order_item,
                    "form_sku": form_sku,
                    "form_product_id": form_product_id,
                    "form_quantity": form_quantity,
                    "form_price": form_price,
                },
            )

        _, _, _, price_list = _resolve_order_item_pricing(order, selected_product)

        previous_product_id = order_item.product_id
        order_item.product = selected_product
        if previous_product_id != selected_product.pk:
            order_item.clamp_request = None
        order_item.product_sku = selected_product.sku
        order_item.product_name = selected_product.name
        order_item.quantity = quantity
        order_item.unit_price_base = manual_price
        order_item.discount_percentage_used = Decimal("0.00")
        order_item.price_list = price_list
        order_item.price_at_purchase = manual_price
        order_item.save()

        _recalculate_order_totals_from_items(order)

        messages.success(request, "Item actualizado correctamente.")
        return redirect("admin_order_detail", pk=order.pk)

    return render(
        request,
        "admin_panel/orders/item_edit.html",
        {
            "order": order,
            "order_item": order_item,
            "form_sku": form_sku,
            "form_product_id": form_product_id,
            "form_quantity": form_quantity,
            "form_price": form_price,
        },
    )


@staff_member_required
@require_POST
def order_item_delete(request, pk, item_id):
    order = get_object_or_404(Order.objects.select_related('company', 'user'), pk=pk)
    if not order.is_mutable_for_items():
        messages.error(request, "Solo podes eliminar items en pedidos borrador.")
        return redirect("admin_order_detail", pk=order.pk)
    if _is_order_items_edit_locked(order):
        messages.error(request, "La venta tiene registracion final cerrada; no se pueden editar productos.")
        return redirect("admin_order_detail", pk=order.pk)

    order_item = get_object_or_404(OrderItem, pk=item_id, order=order)
    try:
        order_item.delete()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("admin_order_detail", pk=order.pk)

    _recalculate_order_totals_from_items(order)

    messages.success(request, "Item eliminado.")
    return redirect("admin_order_detail", pk=order.pk)


@staff_member_required
def order_hard_delete(request, pk):
    """Safely hard delete one order only when no downstream business artifacts remain."""
    order = get_object_or_404(
        Order.objects.select_related("company", "source_request", "source_proposal"),
        pk=pk,
    )
    active_company = get_active_company(request)
    if active_company and order.company_id != active_company.id:
        messages.error(request, "No podes eliminar pedidos de otra empresa.")
        return redirect('admin_order_list')

    blockers = _get_order_hard_delete_blockers(order)
    if blockers:
        messages.error(request, "No se puede eliminar el pedido: " + " ".join(blockers))
        return redirect('admin_order_detail', pk=pk)

    if request.method == 'POST':
        source_request = order.source_request
        order_id = order.pk
        with transaction.atomic():
            _clear_legacy_client_account_documents_for_order(order)
            for fiscal_document in list(order.fiscal_documents.all()):
                fiscal_document.delete()
            for internal_document in list(order.documents.all()):
                internal_document.delete()
            order.client_transactions.filter(
                transaction_type=ClientTransaction.TYPE_ORDER_CHARGE
            ).delete()
            if source_request and source_request.status == OrderRequest.STATUS_CONVERTED:
                source_request.status = OrderRequest.STATUS_CONFIRMED
                source_request.converted_at = None
                source_request.save(update_fields=["status", "converted_at", "updated_at"])
            order.delete()
        messages.success(request, f'Pedido #{order_id} eliminado definitivamente.')
        if source_request:
            return redirect('admin_order_request_detail', pk=source_request.pk)
        return redirect('admin_order_list')

    warning_bits = [
        "Se borraran items, historial de estados y cargos tecnicos del pedido.",
    ]
    legacy_count = _count_legacy_client_account_documents_for_order(order)
    if legacy_count:
        warning_bits.append(
            f"Tambien se desvincularan {legacy_count} registros legacy de cuenta corriente."
        )
    if order.source_request_id:
        warning_bits.append(
            f"La solicitud comercial #{order.source_request_id} volvera a estado confirmado."
        )
    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"Pedido #{order.pk}",
        'cancel_url': reverse('admin_order_detail', args=[pk]),
        'title': 'Eliminar Pedido',
        'question': 'Estas por eliminar definitivamente este pedido.',
        'warning': " ".join(warning_bits),
        'confirm_label': 'Eliminar pedido',
    })


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
    movement_transaction = _resolve_internal_document_transaction(document)
    if movement_transaction and not _movement_allows_print(movement_transaction):
        messages.warning(
            request,
            "Primero cerra el movimiento en cuenta corriente para imprimir o descargar este documento.",
        )
        if document.order_id:
            return redirect("admin_order_detail", pk=document.order_id)
        if document.client_profile_id:
            return redirect("admin_client_order_history", pk=document.client_profile_id)
        return redirect("admin_order_list")

    copy_key = str(request.GET.get("copy", "original")).strip().lower()
    copy_labels = {
        "original": "ORIGINAL",
        "duplicado": "DUPLICADO",
        "triplicado": "TRIPLICADO",
    }
    if copy_key not in copy_labels:
        copy_key = "original"
    copy_label = copy_labels.get(copy_key, "ORIGINAL")
    order_items = []
    if document.order_id:
        order_items = list(
            document.order.items.select_related("product").all()
        )

    context = {
        "document": document,
        "copy_key": copy_key,
        "copy_label": copy_label,
        "order_items": order_items,
    }
    if request.GET.get("format") == "pdf":
        try:
            from core.services.pdf_generator import generate_document_pdf

            html_string = render_to_string("admin_panel/documents/print.html", context, request=request)
            pdf_bytes = generate_document_pdf(html_string, base_url=request.build_absolute_uri("/"))
            filename_label = slugify(document.commercial_type_label or document.doc_type) or "documento"
            filename_number = slugify(str(document.display_number)) or str(document.number or "s-n")
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="{filename_label}_{filename_number}_{copy_key}.pdf"'
            )
            return response
        except ImportError:
            messages.error(request, "El generador de PDF no esta instalado correctamente.")
        except Exception as exc:
            messages.error(request, f"Error al generar PDF: {exc}")

    response = render(
        request,
        "admin_panel/documents/print.html",
        context,
    )
    if request.GET.get("download") == "1":
        filename = f"{document.doc_type}_{document.number:07d}.html"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_member_required
def internal_document_delete(request, doc_id):
    """Safely delete one internal document that has no fiscal/account/stock impact."""
    document = get_object_or_404(
        InternalDocument.objects.select_related("company", "order", "payment", "transaction", "sales_document_type"),
        pk=doc_id,
    )
    active_company = get_active_company(request)
    if active_company and document.company_id != active_company.id:
        messages.error(request, "No podes eliminar documentos de otra empresa.")
        return redirect('admin_order_list')

    blockers = _get_internal_document_delete_blockers(document)
    if blockers:
        messages.error(request, "No se puede eliminar el documento interno: " + " ".join(blockers))
        if document.order_id:
            return redirect('admin_order_detail', pk=document.order_id)
        return redirect('admin_order_list')

    if request.method == 'POST':
        order_id = document.order_id
        label = f"{document.commercial_type_label} {document.display_number}"
        document.delete()
        if order_id:
            try:
                linked_order = Order.objects.filter(pk=order_id).first()
                if linked_order:
                    sync_order_charge_transaction(order=linked_order, actor=request.user)
            except Exception:
                pass
        messages.success(request, f'Documento interno eliminado: {label}.')
        if order_id:
            return redirect('admin_order_detail', pk=order_id)
        return redirect('admin_order_list')

    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"{document.commercial_type_label} {document.display_number}",
        'cancel_url': reverse('admin_order_detail', args=[document.order_id]) if document.order_id else reverse('admin_order_list'),
        'title': 'Eliminar Documento Interno',
        'question': 'Estas por eliminar definitivamente este documento interno.',
        'warning': 'Solo se permite cuando no hay pagos, fiscalidad ni stock vinculados.',
        'confirm_label': 'Eliminar documento',
    })


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
        before = model_snapshot(order, ['status', 'admin_notes', 'status_updated_at'])
        changed = False
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
                order = locked_order
        except ValueError as exc:
            messages.error(
                request,
                str(exc),
            )
            return redirect('admin_order_detail', pk=order.pk)
        except DatabaseError as exc:
            if "database is locked" in str(exc).lower():
                messages.warning(
                    request,
                    "La base de datos esta ocupada en este momento. Reintenta en unos segundos.",
                )
            else:
                messages.error(request, f"No se pudo cancelar el pedido: {exc}")
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

__all__ = ['_get_order_active_invoice', '_parse_order_item_manual_price', '_build_order_detail_items', '_parse_payment_amount', '_parse_adjustment_amount', '_parse_paid_at', 'payment_list', 'payment_export_saas', '_build_clamp_quote_download_response', 'clamp_quoter', 'clamp_quote_close', 'clamp_quote_download', '_find_admin_clamp_request_matches', 'clamp_request_list', 'clamp_request_detail', '_get_order_request_admin_queryset', '_get_order_request_for_admin', '_parse_order_request_money', '_parse_order_request_quantity', '_get_order_request_proposal_source_rows', '_get_order_request_quote_document_types', '_get_order_request_invoice_document_types', '_count_legacy_client_account_documents_for_order', '_clear_legacy_client_account_documents_for_order', '_get_order_request_delete_blockers', '_get_internal_document_delete_blockers', '_get_order_hard_delete_blockers', '_ensure_request_operational_order', '_build_order_request_proposal_payloads', 'sales_workspace', 'order_request_list', 'order_request_detail', 'order_request_confirm_view', 'order_request_reject_view', 'order_request_propose_view', 'order_request_convert_view', 'order_request_generate_quote_view', 'order_request_generate_invoice_view', 'order_request_delete_view', 'order_list', 'order_create_from_panel', 'order_export_saas', 'order_detail', 'order_invoice_open', 'order_internal_document_create', 'order_fiscal_create_local', 'order_fiscal_register_external', 'order_item_add', 'order_item_edit', 'order_item_delete', 'order_hard_delete', 'internal_document_print', 'internal_document_delete', 'order_item_publish_clamp', 'order_delete']
