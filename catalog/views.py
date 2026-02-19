"""
Catalog app views - Product listing and detail.
"""
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.models import CatalogAnalyticsEvent, SiteSettings
from orders.models import ClientFavoriteProduct

from .models import Category, CategoryAttribute, Product


def build_category_tree_rows(categories):
    """Build flattened rows for tree rendering in templates."""
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
        rows.append(
            {
                "category": node,
                "depth": depth,
                "full_path": " > ".join(next_path),
                "has_children": bool(children),
                "children_count": len(children),
            }
        )

        for child in children:
            walk(child, depth + 1, next_path)

    for root in roots:
        walk(root, 0, [])

    remaining = sorted(
        (cat for cat in category_list if cat.id not in visited),
        key=lambda cat: (cat.order, cat.name.lower(), cat.id),
    )
    for category in remaining:
        walk(category, 0, [])

    return rows


def get_cached_category_tree_rows():
    """
    Cache category tree generation to avoid rebuilding on each request.
    """
    aggregate = Category.objects.filter(is_active=True).aggregate(
        total=Count("id"),
        max_updated=Max("updated_at"),
    )
    total = aggregate.get("total") or 0
    max_updated = aggregate.get("max_updated")
    stamp = int(max_updated.timestamp()) if max_updated else 0
    cache_key = f"catalog_tree_rows_v3:{total}:{stamp}"

    rows = cache.get(cache_key)
    if rows is not None:
        return rows

    categories = Category.objects.filter(is_active=True).select_related("parent").order_by("order", "name")
    rows = build_category_tree_rows(categories)
    cache.set(cache_key, rows, 300)
    return rows


def build_active_filter_chips(request, active_filters, category_attributes, field_labels):
    """
    Generate removable chips for active filters.
    """
    attribute_label_map = {attr.slug: attr.name for attr in category_attributes}
    chips = []

    for key, value in active_filters.items():
        label = attribute_label_map.get(key) or field_labels.get(key) or key
        params = request.GET.copy()
        params.pop(key, None)
        chips.append(
            {
                "label": label,
                "value": value,
                "remove_url": f"?{params.urlencode()}" if params else reverse("catalog"),
            }
        )

    if request.GET.get("q", "").strip():
        params = request.GET.copy()
        params.pop("q", None)
        chips.append(
            {
                "label": "Busqueda",
                "value": request.GET.get("q", "").strip(),
                "remove_url": f"?{params.urlencode()}" if params else reverse("catalog"),
            }
        )

    return chips


def build_category_breadcrumb(current_category):
    if not current_category:
        return []
    chain = []
    node = current_category
    while node:
        chain.append(node)
        node = node.parent
    chain.reverse()
    return [{"name": cat.name, "url": f"{reverse('catalog')}?category={cat.slug}"} for cat in chain]


def log_catalog_analytics(request, search_query, current_category, active_filters, results_count):
    try:
        user = request.user if request.user.is_authenticated else None
        category_slug = current_category.slug if current_category else ""
        if search_query:
            CatalogAnalyticsEvent.objects.create(
                event_type=CatalogAnalyticsEvent.EVENT_SEARCH,
                query=search_query,
                category_slug=category_slug,
                results_count=results_count,
                payload={"filters": active_filters},
                user=user,
            )

        if current_category:
            CatalogAnalyticsEvent.objects.create(
                event_type=CatalogAnalyticsEvent.EVENT_CATEGORY_VIEW,
                query=search_query,
                category_slug=category_slug,
                results_count=results_count,
                payload={"filters": active_filters},
                user=user,
            )

        if active_filters:
            CatalogAnalyticsEvent.objects.create(
                event_type=CatalogAnalyticsEvent.EVENT_FILTER,
                query=",".join(sorted(active_filters.keys())),
                category_slug=category_slug,
                results_count=results_count,
                payload=active_filters,
                user=user,
            )
    except Exception:
        # Analytics should never break the user flow.
        return


def catalog(request):
    """
    Public catalog view with search and filters.
    """
    products = Product.catalog_visible(
        Product.objects.select_related("category").prefetch_related("categories")
    )

    search_query = request.GET.get("q", "").strip()
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query)
            | Q(sku__icontains=search_query)
            | Q(description__icontains=search_query)
        )

    category_slug = request.GET.get("category", "")
    current_category = None
    category_attributes = []
    active_filters = {}
    clamp_options = {}

    if category_slug:
        current_category = Category.objects.filter(slug=category_slug, is_active=True).first()
        if current_category:
            category_ids = current_category.get_descendant_ids(include_self=True, only_active=True)
            products = products.filter(
                Q(category_id__in=category_ids) | Q(categories__id__in=category_ids)
            ).distinct()

            category_attributes = CategoryAttribute.objects.filter(category=current_category)

            for attr in category_attributes:
                value = request.GET.get(attr.slug, "").strip()
                if value:
                    products = products.filter(**{f"attributes__{attr.slug}": value})
                    active_filters[attr.slug] = value

            if "ABRAZADERA" in current_category.name.upper():
                spec_fields = ["fabrication", "diameter", "width", "length", "shape"]
                products_before_specs = products

                for field in spec_fields:
                    value = request.GET.get(field, "").strip()
                    if value:
                        active_filters[field] = value
                        if field in ["width", "length"]:
                            try:
                                products = products.filter(**{f"clamp_specs__{field}": int(value)})
                            except ValueError:
                                pass
                        else:
                            products = products.filter(**{f"clamp_specs__{field}": value})

                for field in spec_fields:
                    facet_qs = products_before_specs
                    for other_field in spec_fields:
                        if other_field == field:
                            continue
                        value = request.GET.get(other_field, "").strip()
                        if value:
                            if other_field in ["width", "length"]:
                                try:
                                    facet_qs = facet_qs.filter(
                                        **{f"clamp_specs__{other_field}": int(value)}
                                    )
                                except ValueError:
                                    pass
                            else:
                                facet_qs = facet_qs.filter(
                                    **{f"clamp_specs__{other_field}": value}
                                )
                    field_lookup = f"clamp_specs__{field}"
                    options = (
                        facet_qs.values_list(field_lookup, flat=True)
                        .distinct()
                        .order_by(field_lookup)
                    )
                    clamp_options[field] = [option for option in options if option]

    order_by = request.GET.get("order", "name")
    valid_orders = ["name", "-name", "price", "-price", "sku"]
    if order_by in valid_orders:
        products = products.order_by(order_by)

    paginator = Paginator(products, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    category_tree_rows = get_cached_category_tree_rows()

    settings = SiteSettings.get_settings()
    show_prices = settings.show_public_prices or request.user.is_authenticated

    discount = 0
    favorite_product_ids = set()
    if request.user.is_authenticated and hasattr(request.user, "client_profile"):
        discount = request.user.client_profile.get_discount_decimal()
        favorite_product_ids = set(
            ClientFavoriteProduct.objects.filter(user=request.user).values_list("product_id", flat=True)
        )

    for product in page_obj.object_list:
        linked_categories = [cat for cat in product.get_linked_categories() if cat.is_active]
        product.display_categories = linked_categories[:3]
        product.is_favorite = product.id in favorite_product_ids
        if discount > 0:
            fixed_discount = discount / 100 if discount > 1 else discount
            product.final_price = product.price * (1 - fixed_discount)
        else:
            product.final_price = product.price

    expanded_category_ids = []
    current_category_has_descendants = False
    if current_category:
        expanded_category_ids.append(current_category.id)
        current_category_has_descendants = bool(
            current_category.get_descendant_ids(include_self=False, only_active=True)
        )
        parent = current_category.parent
        while parent:
            expanded_category_ids.append(parent.id)
            parent = parent.parent

    field_labels = {
        "fabrication": "Fabricacion",
        "diameter": "Diametro",
        "width": "Ancho",
        "length": "Largo",
        "shape": "Forma",
    }

    active_filter_chips = build_active_filter_chips(
        request=request,
        active_filters=active_filters,
        category_attributes=category_attributes,
        field_labels=field_labels,
    )

    breadcrumb_categories = build_category_breadcrumb(current_category)
    canonical_url = request.build_absolute_uri(reverse("catalog"))
    if current_category:
        canonical_url = request.build_absolute_uri(f"{reverse('catalog')}?category={current_category.slug}")

    if current_category:
        seo_title = current_category.seo_title or f"{current_category.name} | Catalogo FLEXS"
        seo_description = (
            current_category.seo_description
            or f"Explora productos de {current_category.name} en FLEXS."
        )
    else:
        seo_title = "Catalogo FLEXS - Repuestos y Autopartes"
        seo_description = "Catalogo de repuestos FLEXS con filtros por categoria y atributos tecnicos."

    log_catalog_analytics(
        request=request,
        search_query=search_query,
        current_category=current_category,
        active_filters=active_filters,
        results_count=paginator.count,
    )

    context = {
        "field_labels": field_labels,
        "page_obj": page_obj,
        "category_tree_rows": category_tree_rows,
        "search_query": search_query,
        "category_slug": category_slug,
        "current_category": current_category,
        "current_category_has_descendants": current_category_has_descendants,
        "expanded_category_ids": expanded_category_ids,
        "category_attributes": category_attributes,
        "active_filters": active_filters,
        "active_filter_chips": active_filter_chips,
        "breadcrumb_categories": breadcrumb_categories,
        "order_by": order_by,
        "show_prices": show_prices,
        "discount": discount,
        "price_message": settings.public_prices_message,
        "request_get": request.GET,
        "canonical_url": canonical_url,
        "seo_title": seo_title,
        "seo_description": seo_description,
    }

    if clamp_options:
        context["clamp_options"] = clamp_options

    return render(request, "catalog/catalog_v3.html", context)


def product_detail(request, sku):
    """Product detail view."""
    product = get_object_or_404(
        Product.catalog_visible(
            Product.objects.select_related("category").prefetch_related("categories")
        ),
        sku=sku,
    )

    settings = SiteSettings.get_settings()
    show_prices = settings.show_public_prices or request.user.is_authenticated

    discount = 0
    if request.user.is_authenticated and hasattr(request.user, "client_profile"):
        discount = request.user.client_profile.get_discount_decimal()
    is_favorite = False
    if request.user.is_authenticated:
        is_favorite = ClientFavoriteProduct.objects.filter(
            user=request.user,
            product_id=product.id,
        ).exists()

    final_price = product.price * (1 - discount) if discount else product.price
    linked_categories = [cat for cat in product.get_linked_categories() if cat.is_active]
    primary_category = product.get_primary_category()
    category_breadcrumb = build_category_breadcrumb(primary_category)

    description = (product.description or "").strip()
    seo_description = (
        description[:155] + "..." if len(description) > 158 else description
    ) or f"Detalle del producto {product.name} ({product.sku}) en FLEXS."

    context = {
        "product": product,
        "display_categories": linked_categories[:6],
        "show_prices": show_prices,
        "discount": discount,
        "discount_percentage": discount * 100,
        "final_price": final_price,
        "price_message": settings.public_prices_message,
        "primary_category": primary_category,
        "category_breadcrumb": category_breadcrumb,
        "canonical_url": request.build_absolute_uri(request.path),
        "seo_title": f"{product.name} | FLEXS",
        "seo_description": seo_description,
        "is_favorite": is_favorite,
    }

    return render(request, "catalog/product_detail.html", context)
