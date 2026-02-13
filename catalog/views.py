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
            # --- PHASE 4: Abrazaderas Special Filters (Faceted) ---
            if 'ABRAZADERA' in current_category.name.upper():
                # Define managed fields
                spec_fields = ['fabrication', 'diameter', 'width', 'length', 'shape']
                
                # Snapshot of products before specialized filters (but after generic category filters)
                products_before_specs = products

                # 1. Apply ALL active filters to the main 'products' queryset (for display)
                for field in spec_fields:
                    val = request.GET.get(field, '').strip()
                    if val:
                        active_filters[field] = val
                        if field in ['width', 'length']:
                            try:
                                products = products.filter(**{f"clamp_specs__{field}": int(val)})
                            except ValueError:
                                pass
                        else:
                            products = products.filter(**{f"clamp_specs__{field}": val})
                
                # 2. Calculate available options (Facets)
                # Logic: For each field, valid options are those available in products 
                # filtered by ALL OTHER active filters (excluding itself).
                clamp_options = {}
                
                for field in spec_fields:
                    # Start with base
                    facet_qs = products_before_specs
                    
                    # Apply other filters
                    for other_field in spec_fields:
                        if other_field == field:
                            continue # Skip self
                        
                        val = request.GET.get(other_field, '').strip()
                        if val:
                            if other_field in ['width', 'length']:
                                try:
                                    facet_qs = facet_qs.filter(**{f"clamp_specs__{other_field}": int(val)})
                                except ValueError:
                                    pass
                            else:
                                facet_qs = facet_qs.filter(**{f"clamp_specs__{other_field}": val})
                    
                    # Extract distinct values for this field from the faceted queryset
                    # Use values_list traversing the relation
                    field_lookup = f"clamp_specs__{field}"
                    opts = facet_qs.values_list(field_lookup, flat=True).distinct().order_by(field_lookup)
                    
                    # Clean None/Empty
                    clamp_options[field] = [o for o in opts if o]

                context_extra = {'clamp_options': clamp_options}
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

    # Calculate final price for each product in the current page
    # This avoids using complex template filters that might fail parsing
    for product in page_obj.object_list:
        if discount > 0:
            # discount is decimal (e.g. 0.10)
            if discount > 1: 
                # Safety check if it's percentage (e.g. 10)
                # But get_discount_decimal should return 0.xx
                # core_extras check: if discount_percentage > 1: discount_percentage / 100
                # Let's assume get_discount_decimal is correct, but safe math:
                d = discount
                if d > 1: d = d / 100
                product.final_price = product.price * (1 - d)
            else:
                 product.final_price = product.price * (1 - discount)
        else:
            product.final_price = product.price
    
    # Calculate expanded categories for sidebar accordion
    expanded_category_ids = []
    if current_category:
        # Always expand the current category (to show its children if any)
        expanded_category_ids.append(current_category.id)
        
        # Walk up the tree to expand all parents
        parent = current_category.parent
        while parent:
            expanded_category_ids.append(parent.id)
            parent = parent.parent
            
    # Field labels for translation
    field_labels = {
        'fabrication': 'Fabricación',
        'diameter': 'Diámetro',
        'width': 'Ancho',
        'length': 'Largo',
        'shape': 'Forma',
    }

    context = {
        'field_labels': field_labels,
        'page_obj': page_obj,
        'categories': categories,
        'search_query': search_query,
        'category_slug': category_slug,
        'current_category': current_category,
        'expanded_category_ids': expanded_category_ids,
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
    
    return render(request, 'catalog/catalog_v3.html', context)


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
