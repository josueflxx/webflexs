"""
Admin Panel views - Custom admin interface.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.forms import SetPasswordForm
from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db import DatabaseError
from django.db.models import Q, Count, Sum, Max, Avg, F, DecimalField, ExpressionWrapper
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
import json
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import csv
from openpyxl import Workbook

from catalog.models import Product, Category, CategoryAttribute, Supplier
from accounts.models import ClientProfile, AccountRequest
from orders.models import Order
from core.models import SiteSettings, CatalogAnalyticsEvent, AdminAuditLog, ImportExecution
from django.contrib.auth.models import User
from admin_panel.forms.import_forms import ProductImportForm, ClientImportForm, CategoryImportForm
from admin_panel.forms.category_forms import CategoryForm
from catalog.services.product_importer import ProductImporter
from accounts.services.client_importer import ClientImporter
from catalog.services.category_importer import CategoryImporter
from catalog.services.abrazadera_importer import AbrazaderaImporter
from catalog.services.supplier_sync import ensure_supplier, clean_supplier_name
from catalog.services.category_assignment import (
    normalize_category_ids,
    assign_categories_to_product,
    add_category_to_products,
    replace_categories_for_products,
    remove_category_from_products,
)
from core.services.import_manager import ImportTaskManager
from core.services.audit import log_admin_action
import threading
import traceback
import logging
from core.decorators import superuser_required_for_modifications

logger = logging.getLogger(__name__)


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
    Simulate how many products would become hidden if this category is deactivated.
    """
    linked_products = Product.objects.filter(
        Q(category_id=category.id) | Q(categories__id=category.id)
    ).distinct().prefetch_related("categories").select_related("category")

    would_hide = 0
    for product in linked_products:
        linked_categories = product.get_linked_categories()
        has_other_active = any(cat.is_active and cat.id != category.id for cat in linked_categories)
        if product.is_active and not has_other_active:
            would_hide += 1

    return {
        "linked_products": linked_products.count(),
        "would_hide": would_hide,
        "would_remain_visible": max(linked_products.count() - would_hide, 0),
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
        'client_count': ClientProfile.objects.count(),
        'pending_requests': AccountRequest.objects.filter(status='pending').count(),
        'pending_orders': Order.objects.filter(status='pending').count(),
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
    
    context = {
        'page_obj': page_obj,
        'category_options': category_options,
        'search': search,
        'current_category_id': current_category_id,
        'active_filter': active_filter,
        'total_count': products.count(),
        'pagination_count': page_obj.paginator.count,
    }
    return render(request, 'admin_panel/products/list.html', context)


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
    category_id = data.get('category', '')
    current_category_id = None
    if category_id:
        try:
            current_category_id = int(category_id)
            selected_category = Category.objects.filter(pk=current_category_id).first()
            if selected_category:
                descendant_ids = selected_category.get_descendant_ids(include_self=True)
                products = products.filter(
                    Q(category_id__in=descendant_ids) | Q(categories__id__in=descendant_ids)
                ).distinct()
        except (ValueError, TypeError):
            pass
        
    # Active filter
    active_filter = data.get('active', '')
    if active_filter:
        is_active = active_filter == '1'
        products = products.filter(is_active=is_active)
        
    return products, search, current_category_id, active_filter


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
            primary_category_id = request.POST.get('category', '')
            selected_category_ids = normalize_category_ids(request.POST.getlist('categories'))
            description = request.POST.get('description', '').strip()
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
                    'category_options': category_options,
                    'selected_category_ids': selected_category_ids,
                    'supplier_suggestions': supplier_suggestions,
                    'action': 'Crear',
                })
            
            if Product.objects.filter(sku=sku).exists():
                messages.error(request, f'Ya existe un producto con SKU "{sku}"')
            else:
                product = Product.objects.create(
                    sku=sku,
                    name=name,
                    supplier=supplier_name,
                    supplier_ref=supplier_obj,
                    price=float(price),
                    stock=int(stock),
                    category_id=int(primary_category_id) if str(primary_category_id).isdigit() else None,
                    description=description,
                    attributes=json.loads(request.POST.get('attributes_json', '{}')),
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
            product.price = float(request.POST.get('price', '0'))
            product.stock = int(request.POST.get('stock', '0'))
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
            attributes_json = request.POST.get('attributes_json', '{}')
            if attributes_json:
                try:
                    product.attributes = json.loads(attributes_json)
                except json.JSONDecodeError:
                    pass
            
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
        category_id = request.POST.get('category_id')
        mode = request.POST.get('mode', 'append')
        select_all_pages = request.POST.get('select_all_pages') == 'true'

        if not category_id:
            messages.warning(request, 'No se selecciono una categoria.')
            return redirect('admin_product_list')

        if select_all_pages:
            products_to_update, _, _, _ = get_product_queryset(request.POST)
        else:
            product_ids = request.POST.getlist('product_ids')
            if not product_ids:
                messages.warning(request, 'No se seleccionaron productos.')
                return redirect('admin_product_list')
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

    return redirect('admin_product_list')


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
    })


@staff_member_required
@superuser_required_for_modifications
def client_edit(request, pk):
    """Edit client profile."""
    client = get_object_or_404(ClientProfile, pk=pk)
    
    if request.method == 'POST':
        client.company_name = request.POST.get('company_name', '').strip()
        client.cuit_dni = request.POST.get('cuit_dni', '').strip()
        client.province = request.POST.get('province', '').strip()
        client.address = request.POST.get('address', '').strip()
        client.phone = request.POST.get('phone', '').strip()
        client.discount = float(request.POST.get('discount', '0'))
        client.client_type = request.POST.get('client_type', '')
        client.iva_condition = request.POST.get('iva_condition', '')
        client.save()
        
        messages.success(request, f'Cliente "{client.company_name}" actualizado.')
        return redirect('admin_client_list')
    
    return render(request, 'admin_panel/clients/form.html', {'client': client})


@staff_member_required
@superuser_required_for_modifications
def client_password_change(request, pk):
    """Change client password."""
    client = get_object_or_404(ClientProfile, pk=pk)
    if not client.user:
        messages.error(request, 'Este cliente no tiene un usuario asociado.')
        return redirect('admin_client_edit', pk=pk)
        
    if request.method == 'POST':
        form = SetPasswordForm(client.user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f'Contraseña actualizada para el usuario "{client.user.username}".')
            return redirect('admin_client_list')
    else:
        form = SetPasswordForm(client.user)
        
    return render(request, 'admin_panel/clients/password_form.html', {
        'form': form,
        'client': client
    })


@staff_member_required
@superuser_required_for_modifications
def client_delete(request, pk):
    """Delete single client."""
    client = get_object_or_404(ClientProfile, pk=pk)
    
    if request.method == 'POST':
        name = client.company_name
        # Delete user reference will cascade delete profile usually, but here profile is main view
        user = client.user
        user.delete() # This deletes the client profile too via cascade
        messages.success(request, f'Cliente "{name}" eliminado.')
        return redirect('admin_client_list')
        
    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"{client.company_name} (Usuario: {client.user.username if client.user else 'Sin usuario'})",
        'cancel_url': reverse('admin_client_list')
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
@superuser_required_for_modifications
def request_approve(request, pk):
    """Approve account request and create user."""
    account_request = get_object_or_404(AccountRequest, pk=pk)
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        discount = float(request.POST.get('discount', '0'))
        
        if User.objects.filter(username=username).exists():
            messages.error(request, f'El usuario "{username}" ya existe.')
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
            
            messages.success(
                request, 
                f'Cuenta aprobada. Usuario "{username}" creado con contraseña: {password}'
            )
            return redirect('admin_request_list')
    
    return render(request, 'admin_panel/requests/approve.html', {
        'account_request': account_request,
    })


@staff_member_required
@require_POST
@superuser_required_for_modifications
def request_reject(request, pk):
    """Reject account request."""
    account_request = get_object_or_404(AccountRequest, pk=pk)
    account_request.status = 'rejected'
    account_request.processed_at = timezone.now()
    account_request.admin_notes = request.POST.get('notes', '')
    account_request.save()
    
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
@superuser_required_for_modifications
def order_detail(request, pk):
    """Order detail and status management."""
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=pk)
    
    if request.method == 'POST':
        new_status = request.POST.get('status', '')
        if new_status:
            order.status = new_status
            order.admin_notes = request.POST.get('admin_notes', '')
            order.save()
            messages.success(request, f'Estado del pedido #{order.pk} actualizado.')
    
    return render(request, 'admin_panel/orders/detail.html', {
        'order': order,
        'status_choices': Order.STATUS_CHOICES,
    })


@staff_member_required
@superuser_required_for_modifications
def order_delete(request, pk):
    """Delete single order."""
    order = get_object_or_404(Order, pk=pk)
    
    if request.method == 'POST':
        pk_display = order.pk
        order.delete()
        messages.success(request, f'Pedido #{pk_display} eliminado.')
        return redirect('admin_order_list')
        
    return render(request, 'admin_panel/delete_confirm.html', {
        'object': f"Pedido #{order.pk} (Cliente: {order.user.username if order.user else 'Anonimo'})",
        'cancel_url': reverse('admin_order_detail', args=[pk])
    })


# ===================== SETTINGS =====================

@user_passes_test(lambda u: u.is_superuser)
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


# ===================== CATEGORIES =====================

@staff_member_required
def category_list(request):
    """Category list."""
    search = request.GET.get('q', '').strip()
    status = request.GET.get('status', 'all').strip().lower()

    all_categories = list(
        Category.objects.select_related('parent').order_by('order', 'name')
    )

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

    return render(request, 'admin_panel/categories/list.html', {
        'tree_rows': tree_rows,
        'visible_total': len(tree_rows),
        'search': search,
        'status': status,
        'integrity_issues': integrity_issues,
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
        action = request.POST.get('action')
        select_all_pages = request.POST.get('select_all_pages') == 'true'

        if select_all_pages:
            products_to_update, _, _, _ = get_filtered_queryset(request.POST)
            target_ids = list(products_to_update.values_list('id', flat=True))
        else:
            target_ids = normalize_category_ids(request.POST.getlist('product_ids'))
            if not target_ids:
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
        'pagination_count': page_obj.paginator.count,
    })

# ===================== API =====================

@staff_member_required
def get_category_attributes(request, category_id):
    """API: Get attributes for a category."""
    attributes = CategoryAttribute.objects.filter(category_id=category_id).values(
        'name', 'slug', 'type', 'options', 'required', 'regex_pattern'
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


# ===================== IMPORTERS =====================

def run_background_import(task_id, execution_id, import_type, ImporterClass, file_path, dry_run):
    """Function to run in a separate thread."""
    execution = ImportExecution.objects.filter(pk=execution_id).first()
    try:
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


@user_passes_test(lambda u: u.is_superuser)
def import_dashboard(request):
    """Import dashboard / hub."""
    executions = ImportExecution.objects.select_related('user').order_by('-created_at')[:40]
    return render(
        request,
        'admin_panel/importers/dashboard.html',
        {'executions': executions},
    )


@user_passes_test(lambda u: u.is_superuser)
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
                temp_dir = os.path.join(settings.BASE_DIR, 'media', 'temp_imports')
                os.makedirs(temp_dir, exist_ok=True)

                file_basename = os.path.basename(uploaded_file.name)
                stamp = timezone.now().strftime('%Y%m%d%H%M%S%f')
                file_path = os.path.join(temp_dir, f'import_{stamp}_{file_basename}')
                with open(file_path, 'wb+') as destination:
                    for chunk in uploaded_file.chunks():
                        destination.write(chunk)

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


@user_passes_test(lambda u: u.is_superuser)
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
    """Deletes ALL clients if confirmation is correct."""
    confirmation = request.POST.get('confirmation', '').strip().lower()
    expected = "delete clientes"
    
    if confirmation != expected:
        messages.error(request, f'Frase de confirmación incorrecta. Debe escribir: "{expected}"')
        return redirect('admin_client_list')
    
    # Safe fallback: Delete ClientProfile objects.
    count, _ = ClientProfile.objects.all().delete()
    log_admin_action(
        request,
        action='client_delete_all',
        target_type='client_bulk',
        details={'deleted_count': count},
    )
    messages.success(request, f'Se eliminaron {count} perfiles de cliente.')
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


