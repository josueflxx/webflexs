"""
Catalog app views - Product listing and detail.
"""
from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Product, Category, CategoryAttribute
from core.models import SiteSettings


def catalog(request):
    """
    Public catalog view with search and filters.
    Prices shown based on site settings.
    """
    # Get all active products
    products = Product.objects.filter(is_active=True).select_related('category')
    
    # Search
    search_query = request.GET.get('q', '').strip()
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    
    # Category filter
    category_slug = request.GET.get('category', '')
    current_category = None
    category_attributes = []
    active_filters = {}

    if category_slug:
        # Use filter().first() to avoid 404 if category doesn't exist (just show empty or all)
        # But logically if selecting a category, we want that context.
        current_category = Category.objects.filter(slug=category_slug).first()
        if current_category:
            products = products.filter(category=current_category)
            
            # Get attributes for this category
            category_attributes = CategoryAttribute.objects.filter(category=current_category)
            
            # Apply dynamic filters
            for attr in category_attributes:
                val = request.GET.get(attr.slug, '').strip()
                if val:
                    filter_kwargs = {f"attributes__{attr.slug}": val}
                    products = products.filter(**filter_kwargs)
                    active_filters[attr.slug] = val
                    
            # --- PHASE 4: Abrazaderas Special Filters ---
            if 'ABRAZADERA' in current_category.name.upper():
                clamp_filter_fields = ['fabrication', 'diameter', 'shape']
                for field in clamp_filter_fields:
                    val = request.GET.get(field, '').strip()
                    if val:
                        filter_kwargs = {f"clamp_specs__{field}": val}
                        products = products.filter(**filter_kwargs)
                        active_filters[field] = val
                
                # Numeric filters (width, length) - optional, for now handles exact
                for field in ['width', 'length']:
                    val = request.GET.get(field, '').strip()
                    if val:
                        try:
                            filter_kwargs = {f"clamp_specs__{field}": int(val)}
                            products = products.filter(**filter_kwargs)
                            active_filters[field] = val
                        except ValueError:
                            pass
                
                # Get available options for these filters to show in UI
                from .models import ClampSpecs
                context_extra = {
                    'clamp_options': {
                        'fabrication': ClampSpecs.objects.filter(product__category=current_category).values_list('fabrication', flat=True).distinct().exclude(fabrication__isnull=True),
                        'diameter': ClampSpecs.objects.filter(product__category=current_category).values_list('diameter', flat=True).distinct().exclude(diameter__isnull=True),
                        'shape': ClampSpecs.objects.filter(product__category=current_category).values_list('shape', flat=True).distinct().exclude(shape__isnull=True),
                    }
                }
                # I'll merge this into context later in the view logic
            else:
                context_extra = {}
    
    # Ordering
    order_by = request.GET.get('order', 'name')
    valid_orders = ['name', '-name', 'price', '-price', 'sku']
    if order_by in valid_orders:
        products = products.order_by(order_by)
    
    # Pagination (server-side)
    paginator = Paginator(products, 20)  # 20 products per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Get categories for filter sidebar
    categories = Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related('children')
    
    # Get site settings for price visibility
    settings = SiteSettings.get_settings()
    
    # Check if user can see prices
    show_prices = settings.show_public_prices or request.user.is_authenticated
    
    # Get client discount if logged in
    discount = 0
    if request.user.is_authenticated and hasattr(request.user, 'client_profile'):
        discount = request.user.client_profile.get_discount_decimal()
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        'search_query': search_query,
        'category_slug': category_slug,
        'current_category': current_category,
        'category_attributes': category_attributes,
        'active_filters': active_filters,
        'order_by': order_by,
        'show_prices': show_prices,
        'discount': discount,
        'price_message': settings.public_prices_message,
        'request_get': request.GET, # Useful for keeping other params in links
    }
    
    if 'context_extra' in locals():
        context.update(context_extra)
    
    return render(request, 'catalog/catalog_v2.html', context)


def product_detail(request, sku):
    """Product detail view."""
    product = get_object_or_404(Product, sku=sku, is_active=True)
    
    settings = SiteSettings.get_settings()
    show_prices = settings.show_public_prices or request.user.is_authenticated
    
    discount = 0
    if request.user.is_authenticated and hasattr(request.user, 'client_profile'):
        discount = request.user.client_profile.get_discount_decimal()
    
    context = {
        'product': product,
        'show_prices': show_prices,
        'discount': discount,
        'price_message': settings.public_prices_message,
    }
    
    return render(request, 'catalog/product_detail.html', context)
