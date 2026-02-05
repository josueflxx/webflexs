"""
Admin Panel views - Custom admin interface.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
import json

from catalog.models import Product, Category, CategoryAttribute
from accounts.models import ClientProfile, AccountRequest
from orders.models import Order
from core.models import SiteSettings
from django.contrib.auth.models import User
from admin_panel.forms.import_forms import ProductImportForm, ClientImportForm, CategoryImportForm
from catalog.services.product_importer import ProductImporter
from accounts.services.client_importer import ClientImporter
from catalog.services.category_importer import CategoryImporter


@staff_member_required
def dashboard(request):
    """Admin dashboard with key metrics."""
    context = {
        'product_count': Product.objects.count(),
        'active_product_count': Product.objects.filter(is_active=True).count(),
        'client_count': ClientProfile.objects.count(),
        'pending_requests': AccountRequest.objects.filter(status='pending').count(),
        'pending_orders': Order.objects.filter(status='pending').count(),
        'recent_orders': Order.objects.order_by('-created_at')[:5],
        'recent_requests': AccountRequest.objects.filter(status='pending').order_by('-created_at')[:5],
    }
    return render(request, 'admin_panel/dashboard.html', context)


# ===================== PRODUCTS =====================

@staff_member_required
def product_list(request):
    """Product list with search, filters, and pagination."""
    products = Product.objects.select_related('category').all()
    
    # Search
    search = request.GET.get('q', '').strip()
    if search:
        products = products.filter(
            Q(sku__icontains=search) |
            Q(name__icontains=search)
        )
    
    # Category filter
    category_id = request.GET.get('category', '')
    if category_id:
        products = products.filter(category_id=category_id)
    
    # Active filter
    active_filter = request.GET.get('active', '')
    if active_filter == '1':
        products = products.filter(is_active=True)
    elif active_filter == '0':
        products = products.filter(is_active=False)
    
    # Ordering
    order = request.GET.get('order', '-updated_at')
    products = products.order_by(order)
    
    # Pagination
    paginator = Paginator(products, 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)
    
    categories = Category.objects.filter(is_active=True)
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        'search': search,
        'category_id': category_id,
        'active_filter': active_filter,
    }
    return render(request, 'admin_panel/products/list.html', context)


@staff_member_required
def product_create(request):
    """Create new product."""
    categories = Category.objects.filter(is_active=True)
    
    if request.method == 'POST':
        try:
            sku = request.POST.get('sku', '').strip()
            name = request.POST.get('name', '').strip()
            price = request.POST.get('price', '0')
            stock = request.POST.get('stock', '0')
            category_id = request.POST.get('category', '')
            description = request.POST.get('description', '').strip()
            
            if Product.objects.filter(sku=sku).exists():
                messages.error(request, f'Ya existe un producto con SKU "{sku}"')
            else:
                product = Product.objects.create(
                    sku=sku,
                    name=name,
                    price=float(price),
                    stock=int(stock),
                    category_id=category_id if category_id else None,
                    description=description,
                    attributes=json.loads(request.POST.get('attributes_json', '{}')),
                )
                messages.success(request, f'Producto "{sku}" creado exitosamente.')
                return redirect('admin_product_list')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
    
    return render(request, 'admin_panel/products/form.html', {
        'categories': categories,
        'action': 'Crear',
    })


@staff_member_required
def product_edit(request, pk):
    """Edit existing product."""
    product = get_object_or_404(Product, pk=pk)
    categories = Category.objects.filter(is_active=True)
    
    if request.method == 'POST':
        try:
            product.sku = request.POST.get('sku', '').strip()
            product.name = request.POST.get('name', '').strip()
            product.price = float(request.POST.get('price', '0'))
            product.stock = int(request.POST.get('stock', '0'))
            product.description = request.POST.get('description', '').strip()
            product.is_active = request.POST.get('is_active') == 'on'
            
            category_id = request.POST.get('category', '')
            product.category_id = category_id if category_id else None
            
            # Update attributes
            attributes_json = request.POST.get('attributes_json', '{}')
            if attributes_json:
                try:
                    product.attributes = json.loads(attributes_json)
                except json.JSONDecodeError:
                    pass
            
            product.save()
            messages.success(request, f'Producto "{product.sku}" actualizado.')
            return redirect('admin_product_list')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
    
    return render(request, 'admin_panel/products/form.html', {
        'product': product,
        'categories': categories,
        'action': 'Editar',
    })


@staff_member_required
@require_POST
def product_toggle_active(request):
    """Toggle product active status (AJAX)."""
    try:
        data = json.loads(request.body)
        product_ids = data.get('ids', [])
        active = data.get('active', True)
        
        Product.objects.filter(id__in=product_ids).update(is_active=active)
        
        return JsonResponse({
            'success': True,
            'message': f'{len(product_ids)} productos actualizados'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


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


# ===================== SETTINGS =====================

@staff_member_required
def settings_view(request):
    """Site settings management."""
    settings = SiteSettings.get_settings()
    
    if request.method == 'POST':
        settings.show_public_prices = request.POST.get('show_public_prices') == 'on'
        settings.public_prices_message = request.POST.get('public_prices_message', '').strip()
        settings.company_name = request.POST.get('company_name', '').strip()
        settings.company_email = request.POST.get('company_email', '').strip()
        settings.company_phone = request.POST.get('company_phone', '').strip()
        settings.company_phone_2 = request.POST.get('company_phone_2', '').strip()
        settings.company_address = request.POST.get('company_address', '').strip()
        settings.save()
        
        messages.success(request, 'Configuración guardada.')
    
    return render(request, 'admin_panel/settings.html', {'settings': settings})


# ===================== CATEGORIES =====================

@staff_member_required
def category_list(request):
    """Category list."""
    categories = Category.objects.filter(parent__isnull=True).prefetch_related('children')
    
    return render(request, 'admin_panel/categories/list.html', {
        'categories': categories,
    })


@staff_member_required
def category_create(request):
    """Create category."""
    parent_categories = Category.objects.filter(parent__isnull=True)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent', '')
        
        if Category.objects.filter(name=name).exists():
            messages.error(request, f'La categoría "{name}" ya existe.')
        else:
            Category.objects.create(
                name=name,
                parent_id=parent_id if parent_id else None,
            )
            messages.success(request, f'Categoría "{name}" creada.')
            return redirect('admin_category_list')
    
    return render(request, 'admin_panel/categories/form.html', {
        'parent_categories': parent_categories,
        'action': 'Crear',
    })


@staff_member_required
def category_edit(request, pk):
    """Edit category."""
    category = get_object_or_404(Category, pk=pk)
    parent_categories = Category.objects.filter(parent__isnull=True).exclude(pk=pk)
    
    if request.method == 'POST':
        category.name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent', '')
        category.parent_id = parent_id if parent_id else None
        category.is_active = request.POST.get('is_active') == 'on'
        category.save()
        
        messages.success(request, f'Categoría "{category.name}" actualizada.')
        return redirect('admin_category_list')
    
    return render(request, 'admin_panel/categories/form.html', {
        'category': category,
        'parent_categories': parent_categories,
        'action': 'Editar',
    })


@staff_member_required
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
def category_attribute_delete(request, category_id, attribute_id):
    """Delete a category attribute."""
    category = get_object_or_404(Category, pk=category_id)
    attribute = get_object_or_404(CategoryAttribute, pk=attribute_id, category=category)
    
    name = attribute.name
    attribute.delete()
    messages.success(request, f'Atributo "{name}" eliminado.')
    
    return redirect('admin_category_edit', pk=category.pk)

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
        return JsonResponse({'success': False, 'error': str(e)})


# ===================== IMPORTERS =====================

@staff_member_required
def import_dashboard(request):
    """Import dashboard / hub."""
    return render(request, 'admin_panel/importers/dashboard.html')

@staff_member_required
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
    else:
        messages.error(request, 'Tipo de importación no válido.')
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = FormClass(request.POST, request.FILES)
        if form.is_valid():
            try:
                file = request.FILES['file']
                dry_run = form.cleaned_data.get('dry_run', True)
                
                importer = ImporterClass(file)
                result = importer.run(dry_run=dry_run)
                
                if result.has_errors and not dry_run:
                     messages.warning(request, f'La importación finalizó con {result.errors} errores.')
                elif not dry_run:
                     messages.success(request, f'Importación completada. Creados: {result.created}, Actualizados: {result.updated}.')

                return render(request, 'admin_panel/importers/import_result.html', {
                    'result': result,
                    'dry_run': dry_run,
                    'import_type': import_type,
                })

            except Exception as e:
                messages.error(request, f'Error crítico en la importación: {str(e)}')
                return redirect('admin_import_process', import_type=import_type)
    else:
        form = FormClass()

    return render(request, template, {
        'form': form,
        'import_type': import_type
    })
