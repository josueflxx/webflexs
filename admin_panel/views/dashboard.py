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

from core.services.operational_timeline import (
    build_company_activity_timeline,
    build_operational_snapshot,
)

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



@staff_member_required
def dashboard(request):
    """Admin dashboard hub with queue metrics, recent activity and commercial rankings."""
    active_company = get_active_company(request)
    last_30_days = timezone.now() - timedelta(days=30)

    billable_documents_qs = FiscalDocument.objects.filter(
        doc_type__in=BILLABLE_FISCAL_DOC_TYPES,
        status__in=[FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED],
        issued_at__gte=last_30_days,
    )
    if active_company:
        billable_documents_qs = billable_documents_qs.filter(company=active_company)

    top_clients_raw = (
        billable_documents_qs
        .values(
            'client_profile__id',
            'client_profile__company_name',
            'client_profile__user__username',
            'client_company_ref__client_profile__id',
            'client_company_ref__client_profile__company_name',
        )
        .annotate(
            total_billed=Sum('total'),
            documents_count=Count('id'),
        )
        .order_by('-total_billed', '-documents_count')[:5]
    )
    top_clients_rank = [
        {
            'client_id': item.get('client_profile__id') or item.get('client_company_ref__client_profile__id'),
            'client_name': item.get('client_profile__company_name')
            or item.get('client_company_ref__client_profile__company_name')
            or 'Cliente sin nombre',
            'username': item.get('client_profile__user__username') or '-',
            'total_billed': item.get('total_billed') or Decimal('0.00'),
            'documents_count': item.get('documents_count') or 0,
            'detail_url': (
                reverse('admin_client_order_history', args=[item.get('client_profile__id') or item.get('client_company_ref__client_profile__id')])
                if (item.get('client_profile__id') or item.get('client_company_ref__client_profile__id'))
                else ''
            ),
        }
        for item in top_clients_raw
    ]

    top_products_raw = (
        FiscalDocument.objects.filter(pk__in=billable_documents_qs.values('pk'))
        .values('items__product_id', 'items__sku', 'items__description')
        .annotate(
            total_qty=Sum('items__quantity'),
            total_amount=Sum('items__total_amount'),
            documents_count=Count('id', distinct=True),
        )
        .order_by('-total_qty', '-total_amount')[:5]
    )
    top_products_rank = [
        {
            'product_id': item.get('items__product_id'),
            'sku': item.get('items__sku') or '-',
            'description': item.get('items__description') or 'Producto sin descripcion',
            'total_qty': item.get('total_qty') or Decimal('0.00'),
            'total_amount': item.get('total_amount') or Decimal('0.00'),
            'documents_count': item.get('documents_count') or 0,
            'detail_url': (
                reverse('admin_product_edit', args=[item.get('items__product_id')])
                if item.get('items__product_id')
                else (
                    f"{reverse('admin_product_list')}?{urlencode({'q': item.get('items__sku') or ''})}"
                    if item.get('items__sku')
                    else reverse('admin_product_list')
                )
            ),
        }
        for item in top_products_raw
    ]

    debt_qs = ClientTransaction.objects.filter(client_profile__isnull=False)
    if active_company:
        debt_qs = debt_qs.filter(company=active_company)
    top_debtors_raw = (
        debt_qs
        .values(
            'client_profile__id',
            'client_profile__company_name',
            'client_profile__user__username',
        )
        .annotate(balance=Sum('amount'))
        .filter(balance__gt=0)
        .order_by('-balance')[:5]
    )
    top_debtors_rank = [
        {
            'client_id': item.get('client_profile__id'),
            'client_name': item.get('client_profile__company_name') or 'Cliente sin nombre',
            'username': item.get('client_profile__user__username') or '-',
            'balance': item.get('balance') or Decimal('0.00'),
            'detail_url': (
                reverse('admin_client_order_history', args=[item.get('client_profile__id')])
                if item.get('client_profile__id')
                else ''
            ),
        }
        for item in top_debtors_raw
    ]

    operational_snapshot_cards = build_operational_snapshot(company=active_company)
    recent_activity = build_company_activity_timeline(company=active_company, limit=10)

    context = {
        'active_company': active_company,
        'operational_snapshot_cards': operational_snapshot_cards,
        'recent_activity': recent_activity,
        'top_clients_rank': top_clients_rank,
        'top_products_rank': top_products_rank,
        'top_debtors_rank': top_debtors_rank,
    }
    return render(request, 'admin_panel/dashboard.html', context)

__all__ = ['dashboard']
