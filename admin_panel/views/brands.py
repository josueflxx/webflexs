from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_POST
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.db.models import Q, Max
from django.utils import timezone
import json

from catalog.models import Category, Brand, BrandRubro, BrandSubrubro, BrandSubrubroProductOrder, BrandRubroProductOrder, Product
from admin_panel.forms.brand_forms import BrandForm, BrandRubroForm, BrandSubrubroForm
from admin_panel.views.helpers import get_cached_category_options
from core.services.audit import log_admin_action
from core.decorators import superuser_required_for_modifications
from core.services.advanced_search import sanitize_search_token


@staff_member_required
def brand_list(request):
    """View to list brands, rubros, and subrubros in a tree hierarchy."""
    search = sanitize_search_token(request.GET.get('q', ''))
    status = request.GET.get('status', 'all').strip().lower()

    # Pre-fetch the hierarchy
    brands_qs = Brand.objects.all()
    if status == 'active':
        brands_qs = brands_qs.filter(is_active=True)
    elif status == 'inactive':
        brands_qs = brands_qs.filter(is_active=False)

    if search:
        brands_qs = brands_qs.filter(name__icontains=search)

    brands = brands_qs.prefetch_related("rubros__subrubros").order_by("order", "name")

    return render(request, 'admin_panel/brands/brand_list.html', {
        'brands': brands,
        'search': search,
        'status': status,
    })


@staff_member_required
@superuser_required_for_modifications
def brand_create(request):
    """Create brand view."""
    if request.method == 'POST':
        form = BrandForm(request.POST, request.FILES)
        if form.is_valid():
            brand = form.save()
            log_admin_action(
                request,
                action="brand_create",
                target_type="brand",
                target_id=brand.pk,
                details={"name": brand.name},
            )
            messages.success(request, f'Marca "{brand.name}" creada con éxito.')
            return redirect('admin_brand_list')
    else:
        form = BrandForm()

    return render(request, 'admin_panel/brands/brand_form.html', {
        'form': form,
        'action': 'Crear',
        'title': 'Crear Nueva Marca',
    })


@staff_member_required
@superuser_required_for_modifications
def brand_edit(request, pk):
    """Edit brand view."""
    brand = get_object_or_404(Brand, pk=pk)
    if request.method == 'POST':
        form = BrandForm(request.POST, request.FILES, instance=brand)
        if form.is_valid():
            brand = form.save()
            log_admin_action(
                request,
                action="brand_edit",
                target_type="brand",
                target_id=brand.pk,
                details={"name": brand.name},
            )
            messages.success(request, f'Marca "{brand.name}" actualizada con éxito.')
            return redirect('admin_brand_list')
    else:
        form = BrandForm(instance=brand)

    return render(request, 'admin_panel/brands/brand_form.html', {
        'form': form,
        'action': 'Editar',
        'title': f'Editar Marca: {brand.name}',
    })


@staff_member_required
@superuser_required_for_modifications
@require_POST
def brand_delete(request, pk):
    """Delete brand view."""
    brand = get_object_or_404(Brand, pk=pk)
    name = brand.name
    brand.delete()
    log_admin_action(
        request,
        action="brand_delete",
        target_type="brand",
        target_id=pk,
        details={"name": name},
    )
    messages.success(request, f'Marca "{name}" eliminada.')
    return redirect('admin_brand_list')


@staff_member_required
@superuser_required_for_modifications
def brand_rubro_create(request):
    """Create BrandRubro view."""
    brand_id = request.GET.get('brand', '').strip()
    initial = {}
    if brand_id.isdigit():
        initial['brand'] = int(brand_id)

    if request.method == 'POST':
        form = BrandRubroForm(request.POST, request.FILES)
        if form.is_valid():
            rubro = form.save()
            log_admin_action(
                request,
                action="brand_rubro_create",
                target_type="brand_rubro",
                target_id=rubro.pk,
                details={"name": rubro.name, "brand_id": rubro.brand_id},
            )
            messages.success(request, f'Rubro de marca "{rubro.name}" creado.')
            return redirect('admin_brand_list')
    else:
        form = BrandRubroForm(initial=initial)

    return render(request, 'admin_panel/brands/brand_form.html', {
        'form': form,
        'action': 'Crear Rubro',
        'title': 'Crear Rubro de Marca',
    })


@staff_member_required
@superuser_required_for_modifications
def brand_rubro_edit(request, pk):
    """Edit BrandRubro view."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    if request.method == 'POST':
        form = BrandRubroForm(request.POST, request.FILES, instance=rubro)
        if form.is_valid():
            rubro = form.save()
            log_admin_action(
                request,
                action="brand_rubro_edit",
                target_type="brand_rubro",
                target_id=rubro.pk,
                details={"name": rubro.name, "brand_id": rubro.brand_id},
            )
            messages.success(request, f'Rubro de marca "{rubro.name}" actualizado.')
            return redirect('admin_brand_list')
    else:
        form = BrandRubroForm(instance=rubro)

    return render(request, 'admin_panel/brands/brand_form.html', {
        'form': form,
        'action': 'Editar Rubro',
        'title': f'Editar Rubro: {rubro.name}',
    })


@staff_member_required
@superuser_required_for_modifications
@require_POST
def brand_rubro_delete(request, pk):
    """Delete BrandRubro view."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    name = rubro.name
    rubro.delete()
    log_admin_action(
        request,
        action="brand_rubro_delete",
        target_type="brand_rubro",
        target_id=pk,
        details={"name": name},
    )
    messages.success(request, f'Rubro "{name}" eliminado.')
    return redirect('admin_brand_list')


@staff_member_required
@superuser_required_for_modifications
def brand_subrubro_create(request):
    """Create BrandSubrubro view."""
    rubro_id = request.GET.get('rubro', '').strip()
    initial = {}
    if rubro_id.isdigit():
        initial['brand_rubro'] = int(rubro_id)

    if request.method == 'POST':
        form = BrandSubrubroForm(request.POST, request.FILES)
        if form.is_valid():
            subrubro = form.save()
            log_admin_action(
                request,
                action="brand_subrubro_create",
                target_type="brand_subrubro",
                target_id=subrubro.pk,
                details={"name": subrubro.name, "brand_rubro_id": subrubro.brand_rubro_id},
            )
            messages.success(request, f'Subrubro de marca "{subrubro.name}" creado.')
            return redirect('admin_brand_list')
    else:
        form = BrandSubrubroForm(initial=initial)

    return render(request, 'admin_panel/brands/brand_form.html', {
        'form': form,
        'action': 'Crear Subrubro',
        'title': 'Crear Subrubro de Marca',
    })


@staff_member_required
@superuser_required_for_modifications
def brand_subrubro_edit(request, pk):
    """Edit BrandSubrubro view."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    if request.method == 'POST':
        form = BrandSubrubroForm(request.POST, request.FILES, instance=subrubro)
        if form.is_valid():
            subrubro = form.save()
            log_admin_action(
                request,
                action="brand_subrubro_edit",
                target_type="brand_subrubro",
                target_id=subrubro.pk,
                details={"name": subrubro.name, "brand_rubro_id": subrubro.brand_rubro_id},
            )
            messages.success(request, f'Subrubro de marca "{subrubro.name}" actualizado.')
            return redirect('admin_brand_list')
    else:
        form = BrandSubrubroForm(instance=subrubro)

    return render(request, 'admin_panel/brands/brand_form.html', {
        'form': form,
        'action': 'Editar Subrubro',
        'title': f'Editar Subrubro: {subrubro.name}',
    })


@staff_member_required
@superuser_required_for_modifications
@require_POST
def brand_subrubro_delete(request, pk):
    """Delete BrandSubrubro view."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    name = subrubro.name
    subrubro.delete()
    log_admin_action(
        request,
        action="brand_subrubro_delete",
        target_type="brand_subrubro",
        target_id=pk,
        details={"name": name},
    )
    messages.success(request, f'Subrubro "{name}" eliminado.')
    return redirect('admin_brand_list')


@staff_member_required
def brand_subrubro_products(request, pk):
    """View to manage and reorder products inside a brand subrubro."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    
    order_rows = BrandSubrubroProductOrder.objects.filter(
        brand_subrubro=subrubro
    ).select_related("product").order_by("sort_order", "product__name")
    
    category_id_str = request.GET.get('category_id', '').strip()
    category_id = int(category_id_str) if category_id_str.isdigit() else None
    
    q = sanitize_search_token(request.GET.get('q', ''))
    search_results = []
    
    if q or category_id:
        products_qs = Product.objects.all()
        if category_id:
            try:
                cat = Category.objects.get(pk=category_id)
                descendant_ids = cat.get_descendant_ids(include_self=True)
                products_qs = products_qs.filter(category_id__in=descendant_ids)
            except Category.DoesNotExist:
                pass
        if q:
            products_qs = products_qs.filter(
                Q(sku__icontains=q) | Q(name__icontains=q)
            )
        search_results = products_qs.exclude(
            id__in=order_rows.values_list("product_id", flat=True)
        ).distinct()[:50]
        
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        results = []
        for prod in search_results:
            results.append({
                "id": prod.id,
                "sku": prod.sku,
                "name": prod.name,
            })
        return JsonResponse({"success": True, "results": results})
        
    category_options = get_cached_category_options(only_active=True, include_inactive_suffix=False)
    
    return render(request, 'admin_panel/brands/brand_subrubro_products.html', {
        'subrubro': subrubro,
        'order_rows': order_rows,
        'search_results': search_results,
        'q': q,
        'category_id': category_id,
        'category_options': category_options,
    })


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_subrubro_add_product(request, pk):
    """Manually add a product to a brand subrubro."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    product_id = request.POST.get('product_id', '').strip()
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1'
    
    if product_id.isdigit():
        product = get_object_or_404(Product, pk=int(product_id))
        
        max_order = BrandSubrubroProductOrder.objects.filter(
            brand_subrubro=subrubro
        ).aggregate(Max("sort_order"))["sort_order__max"] or 0
        
        row, created = BrandSubrubroProductOrder.objects.get_or_create(
            brand_subrubro=subrubro,
            product=product,
            defaults={"sort_order": max_order + 10}
        )
        if is_ajax:
            return JsonResponse({
                "success": True,
                "created": created,
                "product": {
                    "id": product.id,
                    "sku": product.sku,
                    "name": product.name
                }
            })
            
        if created:
            messages.success(request, f'Producto "{product.name}" agregado.')
        else:
            messages.info(request, f'El producto "{product.name}" ya existe en este subrubro.')
            
    elif is_ajax:
        return JsonResponse({"success": False, "error": "ID de producto inválido."}, status=400)
        
    return redirect('admin_brand_subrubro_products', pk=subrubro.pk)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_subrubro_remove_product(request, pk):
    """Remove a product from a brand subrubro."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    product_id = request.POST.get('product_id', '').strip()
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1'
    
    if product_id.isdigit():
        product = get_object_or_404(Product, pk=int(product_id))
        BrandSubrubroProductOrder.objects.filter(
            brand_subrubro=subrubro,
            product=product
        ).delete()
        
        if is_ajax:
            return JsonResponse({
                "success": True,
                "product_id": product.id
            })
            
        messages.success(request, f'Producto "{product.name}" removido.')
        
    elif is_ajax:
        return JsonResponse({"success": False, "error": "ID de producto inválido."}, status=400)
        
    return redirect('admin_brand_subrubro_products', pk=subrubro.pk)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_subrubro_products_reorder(request, pk):
    """AJAX endpoint to save manual product order in a brand subrubro."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "JSON invalido."}, status=400)

    ordered_ids = payload.get("ordered_ids", [])
    if not ordered_ids:
        return JsonResponse({"success": False, "error": "No hay productos para ordenar."}, status=400)

    ordered_ids = [int(x) for x in ordered_ids if str(x).isdigit()]

    existing_rows = {
        row.product_id: row
        for row in BrandSubrubroProductOrder.objects.filter(
            brand_subrubro=subrubro,
            product_id__in=ordered_ids
        )
    }

    updates = []
    creates = []

    for index, product_id in enumerate(ordered_ids, start=1):
        sort_order = index * 10
        row = existing_rows.get(product_id)
        if row:
            row.sort_order = sort_order
            updates.append(row)
        else:
            creates.append(
                BrandSubrubroProductOrder(
                    brand_subrubro=subrubro,
                    product_id=product_id,
                    sort_order=sort_order
                )
            )

    if creates:
        BrandSubrubroProductOrder.objects.bulk_create(creates, ignore_conflicts=True)
    if updates:
        BrandSubrubroProductOrder.objects.bulk_update(updates, ["sort_order"])

    log_admin_action(
        request,
        action="brand_subrubro_products_reorder",
        target_type="brand_subrubro",
        target_id=subrubro.id,
        details={"ordered_ids": ordered_ids[:100], "count": len(ordered_ids)}
    )

    return JsonResponse({"success": True, "count": len(ordered_ids)})


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_subrubro_sync(request, pk):
    """AJAX endpoint to auto-populate brand subrubro using helper categories and brand keyword."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    brand_name = subrubro.brand_rubro.brand.name.upper()
    categories = subrubro.helper_categories.all()
    if not categories:
        return JsonResponse({"success": False, "error": "No hay categorías ayudantes configuradas para este subrubro."})

    # Collect helper categories and all their active descendant category IDs
    all_cat_ids = []
    for cat in categories:
        all_cat_ids.extend(cat.get_descendant_ids(include_self=True, only_active=True))
    all_cat_ids = list(set(all_cat_ids))

    # Query active products in these categories matching the brand name
    products = Product.objects.filter(
        is_active=True
    ).filter(
        Q(category_id__in=all_cat_ids) | Q(categories__id__in=all_cat_ids)
    ).filter(
        Q(name__icontains=brand_name) | Q(sku__icontains=brand_name)
    ).distinct()

    existing_product_ids = set(
        subrubro.product_order_rows.values_list("product_id", flat=True)
    )

    max_order = subrubro.product_order_rows.aggregate(
        Max("sort_order")
    )["sort_order__max"] or 0

    created_count = 0
    creates = []
    for prod in products:
        if prod.id not in existing_product_ids:
            max_order += 10
            creates.append(
                BrandSubrubroProductOrder(
                    brand_subrubro=subrubro,
                    product=prod,
                    sort_order=max_order
                )
            )
            created_count += 1

    if creates:
        BrandSubrubroProductOrder.objects.bulk_create(creates, ignore_conflicts=True)

    log_admin_action(
        request,
        action="brand_subrubro_sync",
        target_type="brand_subrubro",
        target_id=subrubro.id,
        details={"brand": brand_name, "added_count": created_count}
    )

    return JsonResponse({"success": True, "added_count": created_count})


@staff_member_required
def brand_rubro_products(request, pk):
    """View to manage and reorder products inside a brand rubro."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    
    order_rows = BrandRubroProductOrder.objects.filter(
        brand_rubro=rubro
    ).select_related("product").order_by("sort_order", "product__name")
    
    category_id_str = request.GET.get('category_id', '').strip()
    category_id = int(category_id_str) if category_id_str.isdigit() else None
    
    q = sanitize_search_token(request.GET.get('q', ''))
    search_results = []
    
    if q or category_id:
        products_qs = Product.objects.all()
        if category_id:
            try:
                cat = Category.objects.get(pk=category_id)
                descendant_ids = cat.get_descendant_ids(include_self=True)
                products_qs = products_qs.filter(category_id__in=descendant_ids)
            except Category.DoesNotExist:
                pass
        if q:
            products_qs = products_qs.filter(
                Q(sku__icontains=q) | Q(name__icontains=q)
            )
        search_results = products_qs.exclude(
            id__in=order_rows.values_list("product_id", flat=True)
        ).distinct()[:50]
        
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        results = []
        for prod in search_results:
            results.append({
                "id": prod.id,
                "sku": prod.sku,
                "name": prod.name,
            })
        return JsonResponse({"success": True, "results": results})
        
    category_options = get_cached_category_options(only_active=True, include_inactive_suffix=False)
    
    return render(request, 'admin_panel/brands/brand_rubro_products.html', {
        'rubro': rubro,
        'order_rows': order_rows,
        'search_results': search_results,
        'q': q,
        'category_id': category_id,
        'category_options': category_options,
    })


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_rubro_add_product(request, pk):
    """Manually add a product to a brand rubro."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    product_id = request.POST.get('product_id', '').strip()
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1'
    
    if product_id.isdigit():
        product = get_object_or_404(Product, pk=int(product_id))
        
        max_order = BrandRubroProductOrder.objects.filter(
            brand_rubro=rubro
        ).aggregate(Max("sort_order"))["sort_order__max"] or 0
        
        row, created = BrandRubroProductOrder.objects.get_or_create(
            brand_rubro=rubro,
            product=product,
            defaults={"sort_order": max_order + 10}
        )
        if is_ajax:
            return JsonResponse({
                "success": True,
                "created": created,
                "product": {
                    "id": product.id,
                    "sku": product.sku,
                    "name": product.name
                }
            })
            
        if created:
            messages.success(request, f'Producto "{product.name}" agregado.')
        else:
            messages.info(request, f'El producto "{product.name}" ya existe en este rubro.')
            
    elif is_ajax:
        return JsonResponse({"success": False, "error": "ID de producto inválido."}, status=400)
        
    return redirect('admin_brand_rubro_products', pk=rubro.pk)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_rubro_remove_product(request, pk):
    """Remove a product from a brand rubro."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    product_id = request.POST.get('product_id', '').strip()
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1'
    
    if product_id.isdigit():
        product = get_object_or_404(Product, pk=int(product_id))
        BrandRubroProductOrder.objects.filter(
            brand_rubro=rubro,
            product=product
        ).delete()
        
        if is_ajax:
            return JsonResponse({
                "success": True,
                "product_id": product.id
            })
            
        messages.success(request, f'Producto "{product.name}" removido.')
        
    elif is_ajax:
        return JsonResponse({"success": False, "error": "ID de producto inválido."}, status=400)
        
    return redirect('admin_brand_rubro_products', pk=rubro.pk)


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_rubro_products_reorder(request, pk):
    """AJAX endpoint to save manual product order in a brand rubro."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "JSON invalido."}, status=400)

    ordered_ids = payload.get("ordered_ids", [])
    if not ordered_ids:
        return JsonResponse({"success": False, "error": "No hay productos para ordenar."}, status=400)

    ordered_ids = [int(x) for x in ordered_ids if str(x).isdigit()]

    existing_rows = {
        row.product_id: row
        for row in BrandRubroProductOrder.objects.filter(
            brand_rubro=rubro,
            product_id__in=ordered_ids
        )
    }

    updates = []
    creates = []

    for index, product_id in enumerate(ordered_ids, start=1):
        sort_order = index * 10
        row = existing_rows.get(product_id)
        if row:
            row.sort_order = sort_order
            updates.append(row)
        else:
            creates.append(
                BrandRubroProductOrder(
                    brand_rubro=rubro,
                    product_id=product_id,
                    sort_order=sort_order
                )
            )

    if creates:
        BrandRubroProductOrder.objects.bulk_create(creates, ignore_conflicts=True)
    if updates:
        BrandRubroProductOrder.objects.bulk_update(updates, ["sort_order"])

    log_admin_action(
        request,
        action="brand_rubro_products_reorder",
        target_type="brand_rubro",
        target_id=rubro.id,
        details={"ordered_ids": ordered_ids[:100], "count": len(ordered_ids)}
    )

    return JsonResponse({"success": True, "count": len(ordered_ids)})


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_rubro_sync(request, pk):
    """AJAX endpoint to auto-populate brand rubro using helper categories of its subrubros and brand keyword."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    brand_name = rubro.brand.name.upper()
    
    # Collect all helper categories from all active subrubros
    subrubros = rubro.subrubros.filter(is_active=True)
    all_cat_ids = []
    for sub in subrubros:
        for cat in sub.helper_categories.all():
            all_cat_ids.extend(cat.get_descendant_ids(include_self=True, only_active=True))
            
    all_cat_ids = list(set(all_cat_ids))
    if not all_cat_ids:
        return JsonResponse({"success": False, "error": "No hay categorías de subrubros configuradas para auto-poblar este rubro."})

    # Query active products in these categories matching the brand name
    products = Product.objects.filter(
        is_active=True
    ).filter(
        Q(category_id__in=all_cat_ids) | Q(categories__id__in=all_cat_ids)
    ).filter(
        Q(name__icontains=brand_name) | Q(sku__icontains=brand_name)
    ).distinct()

    existing_product_ids = set(
        rubro.product_order_rows.values_list("product_id", flat=True)
    )

    max_order = rubro.product_order_rows.aggregate(
        Max("sort_order")
    )["sort_order__max"] or 0

    created_count = 0
    creates = []
    for prod in products:
        if prod.id not in existing_product_ids:
            max_order += 10
            creates.append(
                BrandRubroProductOrder(
                    brand_rubro=rubro,
                    product=prod,
                    sort_order=max_order
                )
            )
            created_count += 1

    if creates:
        BrandRubroProductOrder.objects.bulk_create(creates, ignore_conflicts=True)

    log_admin_action(
        request,
        action="brand_rubro_sync",
        target_type="brand_rubro",
        target_id=rubro.id,
        details={"brand": brand_name, "added_count": created_count}
    )

    return JsonResponse({"success": True, "added_count": created_count})


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_rubro_bulk_add_category(request, pk):
    """AJAX endpoint to bulk associate all products of a main catalog category to a BrandRubro."""
    rubro = get_object_or_404(BrandRubro, pk=pk)
    category_id_str = request.POST.get('category_id', '').strip()
    
    if not category_id_str.isdigit():
        return JsonResponse({"success": False, "error": "ID de categoría inválido."}, status=400)
        
    category_id = int(category_id_str)
    category = get_object_or_404(Category, pk=category_id)
    
    # Get all active descendant category IDs
    descendant_ids = category.get_descendant_ids(include_self=True, only_active=True)
    
    # Query all active products in these categories that are NOT already associated with the rubro
    existing_product_ids = set(
        rubro.product_order_rows.values_list("product_id", flat=True)
    )
    
    products = Product.objects.filter(
        is_active=True,
        category_id__in=descendant_ids
    ).exclude(
        id__in=existing_product_ids
    ).distinct()
    
    max_order = rubro.product_order_rows.aggregate(
        Max("sort_order")
    )["sort_order__max"] or 0
    
    creates = []
    created_count = 0
    for prod in products:
        max_order += 10
        creates.append(
            BrandRubroProductOrder(
                brand_rubro=rubro,
                product=prod,
                sort_order=max_order
            )
        )
        created_count += 1
        
    if creates:
        BrandRubroProductOrder.objects.bulk_create(creates, ignore_conflicts=True)
        
    log_admin_action(
        request,
        action="brand_rubro_bulk_add_category",
        target_type="brand_rubro",
        target_id=rubro.id,
        details={"category_id": category_id, "category_name": category.name, "added_count": created_count}
    )
    
    return JsonResponse({"success": True, "added_count": created_count})


@staff_member_required
@require_POST
@superuser_required_for_modifications
def brand_subrubro_bulk_add_category(request, pk):
    """AJAX endpoint to bulk associate all products of a main catalog category to a BrandSubrubro."""
    subrubro = get_object_or_404(BrandSubrubro, pk=pk)
    category_id_str = request.POST.get('category_id', '').strip()
    
    if not category_id_str.isdigit():
        return JsonResponse({"success": False, "error": "ID de categoría inválido."}, status=400)
        
    category_id = int(category_id_str)
    category = get_object_or_404(Category, pk=category_id)
    
    # Get all active descendant category IDs
    descendant_ids = category.get_descendant_ids(include_self=True, only_active=True)
    
    # Query all active products in these categories that are NOT already associated with the subrubro
    existing_product_ids = set(
        subrubro.product_order_rows.values_list("product_id", flat=True)
    )
    
    products = Product.objects.filter(
        is_active=True,
        category_id__in=descendant_ids
    ).exclude(
        id__in=existing_product_ids
    ).distinct()
    
    max_order = subrubro.product_order_rows.aggregate(
        Max("sort_order")
    )["sort_order__max"] or 0
    
    creates = []
    created_count = 0
    for prod in products:
        max_order += 10
        creates.append(
            BrandSubrubroProductOrder(
                brand_subrubro=subrubro,
                product=prod,
                sort_order=max_order
            )
        )
        created_count += 1
        
    if creates:
        BrandSubrubroProductOrder.objects.bulk_create(creates, ignore_conflicts=True)
        
    # Also associate to parent rubro if not already present
    rubro = subrubro.brand_rubro
    existing_rub_product_ids = set(
        rubro.product_order_rows.values_list("product_id", flat=True)
    )
    rub_max_order = rubro.product_order_rows.aggregate(Max("sort_order"))["sort_order__max"] or 0
    rub_creates = []
    for prod in products:
        if prod.id not in existing_rub_product_ids:
            rub_max_order += 10
            rub_creates.append(
                BrandRubroProductOrder(
                    brand_rubro=rubro,
                    product=prod,
                    sort_order=rub_max_order
                )
            )
            
    if rub_creates:
        BrandRubroProductOrder.objects.bulk_create(rub_creates, ignore_conflicts=True)
        
    log_admin_action(
        request,
        action="brand_subrubro_bulk_add_category",
        target_type="brand_subrubro",
        target_id=subrubro.id,
        details={"category_id": category_id, "category_name": category.name, "added_count": created_count}
    )
    
    return JsonResponse({"success": True, "added_count": created_count})
