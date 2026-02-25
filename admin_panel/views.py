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
from django.core.paginator import Paginator
from django.db import transaction
from django.db import DatabaseError
from django.db.models import Q, Count, Sum, Max, Avg, F, DecimalField, ExpressionWrapper
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
import json
import os
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode, parse_qs
import csv
from openpyxl import Workbook

from catalog.models import Product, Category, CategoryAttribute, ClampMeasureRequest, Supplier
from accounts.models import AccountRequest, ClientPayment, ClientProfile
from orders.models import ClampQuotation, Order, OrderItem, OrderStatusHistory
from core.models import SiteSettings, CatalogAnalyticsEvent, AdminAuditLog, ImportExecution
from django.contrib.auth.models import User
from admin_panel.forms.import_forms import ProductImportForm, ClientImportForm, CategoryImportForm
from admin_panel.forms.category_forms import CategoryForm
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
    create_or_update_quote_product,
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
from core.services.audit import log_admin_action, log_admin_change, model_snapshot
import threading
import traceback
import logging
from core.decorators import superuser_required_for_modifications

logger = logging.getLogger(__name__)
PRIMARY_SUPERADMIN_USERNAME = "josueflexs"


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

    context = {
        'product_count': Product.objects.count(),
        'active_product_count': Product.objects.filter(is_active=True).count(),
        'category_count': Category.objects.count(),
        'supplier_count': Supplier.objects.count(),
        'client_count': ClientProfile.objects.count(),
        'pending_requests': AccountRequest.objects.filter(status='pending').count(),
        'pending_orders': Order.objects.filter(status__in=[Order.STATUS_DRAFT, Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]).count(),
        'recent_orders': Order.objects.order_by('-created_at')[:5],
        'recent_requests': AccountRequest.objects.filter(status='pending').order_by('-created_at')[:5],
        'top_zero_result_searches': top_zero_result_searches,
        'top_category_views': top_category_views,
        'top_filter_sets': top_filter_sets,
        'audit_count_last_30': AdminAuditLog.objects.filter(created_at__gte=last_30_days).count(),
        'recent_audit_logs': AdminAuditLog.objects.select_related('user').order_by('-created_at')[:8],
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
    search = data.get('q', '').strip()
    if search:
        products = products.filter(
            Q(sku__icontains=search) |
            Q(name__icontains=search) |
            Q(supplier__icontains=search) |
            Q(supplier_ref__name__icontains=search)
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
            settings = SiteSettings.get_settings()

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
            
            product.save()
            assign_categories_to_product(product, selected_category_ids, primary_category_id)
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


# ===================== SUPPLIERS =====================

@staff_member_required
def supplier_list(request):
    """Supplier directory with KPI summary."""
    search = request.GET.get('q', '').strip()
    only_active = request.GET.get('only_active') == '1'

    suppliers_qs = (
        Supplier.objects.all()
        .annotate(
            products_count=Count('products', distinct=True),
            active_products_count=Count('products', filter=Q(products__is_active=True), distinct=True),
            stock_total=Sum('products__stock'),
        )
        .order_by('name')
    )

    if only_active:
        suppliers_qs = suppliers_qs.filter(is_active=True)
    if search:
        suppliers_qs = suppliers_qs.filter(name__icontains=search)

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

    search = req_get.get('q', '').strip()
    if search:
        products = products.filter(
            Q(sku__icontains=search)
            | Q(name__icontains=search)
            | Q(description__icontains=search)
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


@staff_member_required
def supplier_unassigned(request):
    """
    Products without supplier assigned.
    """
    products = Product.objects.select_related('category').prefetch_related('categories').filter(
        Q(supplier='') | Q(supplier__isnull=True) | Q(supplier_ref__isnull=True)
    ).order_by('name')

    search = request.GET.get('q', '').strip()
    if search:
        products = products.filter(Q(sku__icontains=search) | Q(name__icontains=search))

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
    if request.method == 'POST':
        action = request.POST.get('action', 'create').strip()

        if action == 'cancel':
            payment_id = request.POST.get('payment_id', '').strip()
            cancel_reason = request.POST.get('cancel_reason', '').strip()
            payment = get_object_or_404(ClientPayment, pk=payment_id)
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

        payment = ClientPayment.objects.create(
            client_profile=client_profile,
            order=order,
            amount=amount,
            method=method,
            paid_at=paid_at,
            reference=reference,
            notes=notes,
            created_by=request.user if request.user.is_authenticated else None,
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

    q = request.GET.get('q', '').strip()
    client_id = request.GET.get('client_id', '').strip()
    order_id = request.GET.get('order_id', '').strip().replace('#', '')

    if order_id.isdigit() and not client_id:
        order_for_prefill = Order.objects.select_related('user').filter(pk=order_id).first()
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

    if client_id.isdigit():
        payments = payments.filter(client_profile_id=int(client_id))
    if order_id.isdigit():
        payments = payments.filter(order_id=int(order_id))
    if q:
        q_filter = (
            Q(client_profile__company_name__icontains=q)
            | Q(client_profile__user__username__icontains=q)
            | Q(client_profile__cuit_dni__icontains=q)
            | Q(reference__icontains=q)
            | Q(notes__icontains=q)
        )
        if q.isdigit():
            q_filter |= Q(order__id=int(q))
            q_filter |= Q(id=int(q))
        payments = payments.filter(q_filter)

    summary = payments.filter(is_cancelled=False).aggregate(
        total=Sum('amount'),
        count=Count('id'),
    )

    paginator = Paginator(payments.order_by('-paid_at'), 40)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    clients = ClientProfile.objects.select_related('user').order_by('company_name')
    selected_order = Order.objects.select_related('user').filter(pk=order_id).first() if order_id.isdigit() else None
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
        orders_total = selected_client.get_total_orders_for_balance()
        total_paid = selected_client.get_total_paid()
        selected_client_metrics = {
            'orders_total': orders_total,
            'total_paid': total_paid,
            'current_balance': orders_total - total_paid,
        }

    return render(request, 'admin_panel/payments/list.html', {
        'page_obj': page_obj,
        'search': q,
        'client_id': client_id,
        'selected_client_id': selected_client_id,
        'order_id': order_id,
        'clients': clients,
        'selected_client': selected_client,
        'selected_client_metrics': selected_client_metrics,
        'selected_order': selected_order,
        'payment_methods': ClientPayment.METHOD_CHOICES,
        'summary_total': summary.get('total') or Decimal('0.00'),
        'summary_count': summary.get('count') or 0,
    })


# ===================== CLAMP QUOTER =====================

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
        "profile_type": "PLANA",
        "product_stock": "1",
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
            "profile_type": str(request.POST.get("profile_type", "PLANA")).strip().upper(),
            "product_stock": str(request.POST.get("product_stock", "1")).strip() or "1",
        })
        if (
            form_values["clamp_type"] == "laminada"
            and form_values["diameter"] not in CLAMP_LAMINATED_ALLOWED_DIAMETERS
        ):
            form_values["diameter"] = CLAMP_LAMINATED_ALLOWED_DIAMETERS[0]

        action = str(request.POST.get("action", "save_quote")).strip().lower()
        if action in {"save_quote", "create_product"}:
            try:
                result = calculate_clamp_quote(request.POST)
                selected_key = str(request.POST.get("price_list_key", "")).strip()
                selected_map = {row["key"]: row for row in result["price_rows"]}
                selected_price = selected_map.get(selected_key)
                if not selected_price:
                    raise ValueError("Selecciona una lista valida para guardar.")
                if action == "save_quote":
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
                            "description": result["description"],
                            "generated_code": result.get("generated_code", ""),
                            "client_name": result["inputs"]["client_name"],
                        },
                    )
                    messages.success(request, f"Cotizacion guardada en {selected_price['label']}.")
                    return redirect("admin_clamp_quoter")

                product_stock = parse_int_value(
                    request.POST.get("product_stock", "1"),
                    "Stock inicial",
                    min_value=1,
                )
                product, created, price_row = create_or_update_quote_product(
                    quote_result=result,
                    price_list_key=selected_price["key"],
                    stock=product_stock,
                    activate_product=True,
                )
                log_admin_action(
                    request,
                    action="clamp_quote_publish_product",
                    target_type="product",
                    target_id=product.pk,
                    details={
                        "created": created,
                        "sku": product.sku,
                        "price_list": price_row.get("label", selected_price["label"]),
                        "final_price": f"{selected_price['final_price']:.2f}",
                        "base_cost": f"{result['base_cost']:.2f}",
                        "generated_code": result.get("generated_code", ""),
                    },
                )
                if created:
                    messages.success(
                        request,
                        f"Producto creado desde cotizador: {product.sku}.",
                    )
                else:
                    messages.success(
                        request,
                        f"Producto existente actualizado desde cotizador: {product.sku}.",
                    )
                return redirect("admin_product_edit", pk=product.pk)
            except ValueError as exc:
                messages.error(request, str(exc))

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
    search = str(request.GET.get("q", "")).strip()

    queryset = ClampMeasureRequest.objects.select_related("client_user", "processed_by")

    valid_statuses = {value for value, _ in ClampMeasureRequest.STATUS_CHOICES}
    if status_filter in valid_statuses:
        queryset = queryset.filter(status=status_filter)
    elif status_filter == "all":
        pass
    else:
        status_filter = "all"

    if search:
        queryset = queryset.filter(
            Q(client_name__icontains=search)
            | Q(client_email__icontains=search)
            | Q(client_phone__icontains=search)
            | Q(description__icontains=search)
            | Q(generated_code__icontains=search)
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
def client_list(request):
    """Client list with search."""
    clients = ClientProfile.objects.select_related('user').all()
    
    search = request.GET.get('q', '').strip()
    if search:
        clients = clients.filter(
            Q(company_name__icontains=search) |
            Q(user__username__icontains=search) |
            Q(cuit_dni__icontains=search)
        )
    
    paginator = Paginator(clients.order_by('-created_at'), 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)
    
    return render(request, 'admin_panel/clients/list.html', {
        'page_obj': page_obj,
        'search': search,
        'can_edit_client_profile': can_edit_client_profile(request.user),
        'can_manage_client_credentials': can_manage_client_credentials(request.user),
        'can_delete_client_record': can_delete_client_record(request.user),
    })


@staff_member_required
def client_edit(request, pk):
    """Edit client profile."""
    client = get_object_or_404(ClientProfile, pk=pk)

    if not can_edit_client_profile(request.user):
        messages.error(
            request,
            'No tienes permisos para editar clientes.',
        )
        return redirect('admin_client_order_history', pk=client.pk)
    
    if request.method == 'POST':
        try:
            discount_value = parse_admin_decimal_input(
                request.POST.get('discount', '0'),
                'Descuento (%)',
                min_value='0',
                max_value='100',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, 'admin_panel/clients/form.html', {'client': client})

        before = model_snapshot(
            client,
            ['company_name', 'cuit_dni', 'province', 'address', 'phone', 'discount', 'client_type', 'iva_condition'],
        )
        client.company_name = request.POST.get('company_name', '').strip()
        client.cuit_dni = request.POST.get('cuit_dni', '').strip()
        client.province = request.POST.get('province', '').strip()
        client.address = request.POST.get('address', '').strip()
        client.phone = request.POST.get('phone', '').strip()
        client.discount = discount_value
        client.client_type = request.POST.get('client_type', '')
        client.iva_condition = request.POST.get('iva_condition', '')
        client.save()
        after = model_snapshot(
            client,
            ['company_name', 'cuit_dni', 'province', 'address', 'phone', 'discount', 'client_type', 'iva_condition'],
        )
        log_admin_change(
            request,
            action='client_update',
            target_type='client_profile',
            target_id=client.pk,
            before=before,
            after=after,
            extra={
                'username': client.user.username if client.user_id else '',
            },
        )
        
        messages.success(request, f'Cliente "{client.company_name}" actualizado.')
        return redirect('admin_client_list')
    
    return render(request, 'admin_panel/clients/form.html', {'client': client})


@staff_member_required
def client_order_history(request, pk):
    """Show order history for one client profile."""
    client = get_object_or_404(ClientProfile.objects.select_related('user'), pk=pk)

    orders = (
        Order.objects.select_related('user')
        .prefetch_related('items')
        .filter(user=client.user)
    )

    status = request.GET.get('status', '').strip()
    if status:
        orders = orders.filter(status=status)

    summary = orders.aggregate(
        orders_count=Count('id'),
        total_amount=Sum('total'),
        avg_ticket=Avg('total'),
        last_order_at=Max('created_at'),
    )

    balance_orders_qs = client.get_orders_queryset_for_balance()
    balance_orders_summary = balance_orders_qs.aggregate(
        orders_count=Count('id'),
        total_amount=Sum('total'),
        last_order_at=Max('created_at'),
    )

    paginator = Paginator(orders.order_by('-created_at'), 30)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    payments_qs = ClientPayment.objects.select_related('order', 'created_by').filter(
        client_profile=client,
        is_cancelled=False,
    )
    payments_summary = payments_qs.aggregate(
        total_paid=Sum('amount'),
        payments_count=Count('id'),
        last_payment_at=Max('paid_at'),
    )

    return render(request, 'admin_panel/clients/order_history.html', {
        'client': client,
        'page_obj': page_obj,
        'status': status,
        'can_edit_client_profile': can_edit_client_profile(request.user),
        'can_manage_client_credentials': can_manage_client_credentials(request.user),
        'can_delete_client_record': can_delete_client_record(request.user),
        'status_choices': Order.STATUS_CHOICES,
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
            'current_balance': client.get_current_balance(),
        },
        'payments_recent': payments_qs.order_by('-paid_at')[:20],
        'payments_summary': {
            'total_paid': payments_summary.get('total_paid') or Decimal('0.00'),
            'payments_count': payments_summary.get('payments_count') or 0,
            'last_payment_at': payments_summary.get('last_payment_at'),
        },
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
            discount = parse_admin_decimal_input(
                request.POST.get('discount', '0'),
                'Descuento (%)',
                min_value='0',
                max_value='100',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, 'admin_panel/requests/approve.html', {
                'account_request': account_request,
            })
        before = model_snapshot(account_request, ['status', 'admin_notes', 'processed_at', 'created_user_id'])

        if not username:
            messages.error(request, 'El nombre de usuario es obligatorio.')
            return render(request, 'admin_panel/requests/approve.html', {
                'account_request': account_request,
            })

        if User.objects.filter(username=username).exists():
            messages.error(request, f'El usuario "{username}" ya existe.')
            return render(request, 'admin_panel/requests/approve.html', {
                'account_request': account_request,
            })

        if not password:
            messages.error(request, 'La contrasena es obligatoria.')
            return render(request, 'admin_panel/requests/approve.html', {
                'account_request': account_request,
            })

        try:
            validate_password(password)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return render(request, 'admin_panel/requests/approve.html', {
                'account_request': account_request,
            })

        else:
            # Create user
            user = User.objects.create_user(
                username=username,
                email=account_request.email,
                password=password,
                first_name=account_request.contact_name,
            )

            # Create client profile
            ClientProfile.objects.create(
                user=user,
                company_name=account_request.company_name,
                cuit_dni=account_request.cuit_dni,
                province=account_request.province,
                address=account_request.address,
                phone=account_request.phone,
                discount=discount,
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
                },
            )

            messages.success(
                request,
                f'Cuenta aprobada. Usuario "{username}" creado correctamente.'
            )
            return redirect('admin_request_list')
    
    return render(request, 'admin_panel/requests/approve.html', {
        'account_request': account_request,
    })


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
    orders = Order.objects.select_related('user').all()
    
    # Status filter
    status = request.GET.get('status', '')
    if status:
        orders = orders.filter(status=status)
    
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
        'client': client,
        'status_choices': Order.STATUS_CHOICES,
    })


@staff_member_required
def order_detail(request, pk):
    """Order detail and status management."""
    order = get_object_or_404(Order.objects.prefetch_related('status_history__changed_by'), pk=pk)
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
        if order_discount_percentage > 0:
            unit_discount_amount = (
                item.price_at_purchase * order_discount_percentage / Decimal('100')
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
    
    if request.method == 'POST':
        new_status = request.POST.get('status', '')
        if new_status:
            before = model_snapshot(order, ['status', 'admin_notes', 'status_updated_at'])
            order.admin_notes = request.POST.get('admin_notes', '')
            status_note = request.POST.get('status_note', '').strip()
            try:
                changed = order.change_status(
                    new_status=new_status,
                    changed_by=request.user,
                    note=status_note or f"Actualizacion desde panel por {request.user.username}",
                )
                order.save(update_fields=['admin_notes', 'updated_at'])
                if changed:
                    messages.success(request, f'Estado del pedido #{order.pk} actualizado.')
                    log_admin_change(
                        request,
                        action='order_status_change',
                        target_type='order',
                        target_id=order.pk,
                        before=before,
                        after=model_snapshot(order, ['status', 'admin_notes', 'status_updated_at']),
                        extra={
                            'status': order.status,
                            'note': status_note,
                        },
                    )
                else:
                    messages.info(request, f'El pedido #{order.pk} ya estaba en ese estado.')
            except ValueError as exc:
                messages.error(request, str(exc))
    
    return render(request, 'admin_panel/orders/detail.html', {
        'order': order,
        'order_items': order_items,
        'status_choices': Order.STATUS_CHOICES,
        'status_history': order.status_history.all()[:20],
        'order_paid_amount': order.get_paid_amount(),
        'order_pending_amount': order.get_pending_amount(),
        'order_is_paid': order.is_paid(),
        'order_client_profile_id': order_client_profile.pk if order_client_profile else '',
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
    order_item.save(update_fields=['product', 'product_sku', 'product_name'])

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
        before = model_snapshot(order, ['status', 'admin_notes', 'status_updated_at'])
        cancel_reason = request.POST.get('cancel_reason', '').strip()
        status_note = cancel_reason or f"Pedido cancelado desde panel por {request.user.username}"
        try:
            changed = order.change_status(
                new_status=Order.STATUS_CANCELLED,
                changed_by=request.user,
                note=status_note,
            )
            if cancel_reason:
                stamp = timezone.localtime().strftime('%d/%m/%Y %H:%M')
                reason_line = f"[{stamp}] Cancelacion: {cancel_reason}"
                order.admin_notes = f"{order.admin_notes}\n{reason_line}".strip() if order.admin_notes else reason_line
                order.save(update_fields=['admin_notes', 'updated_at'])
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
        
        messages.success(request, 'ConfiguraciÃ³n guardada.')
    
    return render(request, 'admin_panel/settings.html', {'settings': settings})


@user_passes_test(is_primary_superadmin)
def admin_user_list(request):
    """
    Superadmin-only list to manage admin accounts and permissions.
    """
    search = request.GET.get('q', '').strip()
    admins = User.objects.filter(is_staff=True).order_by('username')
    if search:
        admins = admins.filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )

    return render(
        request,
        'admin_panel/admin_users/list.html',
        {
            'admins': admins,
            'search': search,
            'total_admins': admins.count(),
        },
    )


@user_passes_test(is_primary_superadmin)
def admin_user_permissions(request, user_id):
    """
    Superadmin-only edit for core admin flags.
    """
    admin_user = get_object_or_404(User, pk=user_id)

    if request.method == 'POST':
        new_is_active = request.POST.get('is_active') == 'on'
        new_is_staff = request.POST.get('is_staff') == 'on'
        new_is_superuser = request.POST.get('is_superuser') == 'on'

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

        admin_user.is_active = new_is_active
        admin_user.is_staff = new_is_staff
        admin_user.is_superuser = new_is_superuser
        admin_user.save(update_fields=['is_active', 'is_staff', 'is_superuser'])

        log_admin_action(
            request,
            action='admin_user_permissions_update',
            target_type='auth_user',
            target_id=admin_user.pk,
            details={
                'username': admin_user.username,
                'is_active': admin_user.is_active,
                'is_staff': admin_user.is_staff,
                'is_superuser': admin_user.is_superuser,
            },
        )
        messages.success(request, f'Permisos actualizados para "{admin_user.username}".')
        return redirect('admin_user_list')

    return render(
        request,
        'admin_panel/admin_users/form.html',
        {
            'admin_user': admin_user,
        },
    )


# ===================== CATEGORIES =====================

@staff_member_required
def category_list(request):
    """Category list."""
    search = request.GET.get('q', '').strip()
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
        matched_ids = {c.id for c in filtered_categories if search.lower() in c.name.lower()}
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
            messages.success(request, f'CategorÃ­a "{category.name}" actualizada.')
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
        messages.success(request, f'CategorÃ­a "{name}" eliminada.')
        return redirect('admin_category_list')
        
    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"CategorÃ­a: {category.name}",
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
                messages.error(request, f'El slug "{slug}" ya existe en esta categorÃ­a.')
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

        search = req_data.get('q', '').strip()
        if search:
            qs = qs.filter(Q(sku__icontains=search) | Q(name__icontains=search))

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
        return JsonResponse({'success': False, 'error': 'No se pudo procesar la descripciÃ³n.'}, status=400)


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
    executions = ImportExecution.objects.select_related('user').order_by('-created_at')[:40]
    return render(
        request,
        'admin_panel/importers/dashboard.html',
        {'executions': executions},
    )


@user_passes_test(is_primary_superadmin)
def import_process(request, import_type):
    """Handle file upload and processing for imports."""
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
                task_id = ImportTaskManager.start_task()
                execution = ImportExecution.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    import_type=import_type,
                    file_name=file_basename,
                    dry_run=dry_run,
                    status=ImportExecution.STATUS_PROCESSING,
                )

                thread = threading.Thread(
                    target=run_background_import,
                    args=(task_id, execution.pk, import_type, ImporterClass, file_path, dry_run),
                )
                thread.daemon = True
                thread.start()

                log_admin_action(
                    request,
                    action='import_start',
                    target_type='import_execution',
                    target_id=execution.pk,
                    details={
                        'import_type': import_type,
                        'dry_run': dry_run,
                        'file_name': file_basename,
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
        messages.error(request, f'Frase de confirmaciÃ³n incorrecta. Debe escribir: "{expected}"')
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
        messages.error(request, f'Frase de confirmaciÃ³n incorrecta. Debe escribir: "{expected}"')
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
        messages.error(request, f'Frase de confirmaciÃ³n incorrecta. Debe escribir: "{expected}"')
        return redirect('admin_category_list')
    
    count, _ = Category.objects.all().delete()
    log_admin_action(
        request,
        action='category_delete_all',
        target_type='category_bulk',
        details={'deleted_count': count},
    )
    messages.success(request, f'Se eliminaron {count} categorÃ­as correctamente.')
    return redirect('admin_category_list')



