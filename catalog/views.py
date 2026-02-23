"""
Catalog app views - Product listing and detail.
"""
import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Max, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import CatalogAnalyticsEvent, SiteSettings
from orders.models import Cart, CartItem, ClientFavoriteProduct

from catalog.services.clamp_request_products import get_or_create_request_product
from catalog.services.clamp_quoter import (
    CLAMP_LAMINATED_ALLOWED_DIAMETERS,
    calculate_clamp_quote,
    get_allowed_diameter_options,
)
from .models import Category, CategoryAttribute, ClampMeasureRequest, Product


CLAMP_REQUEST_DEFAULT_DOLLAR_RATE = Decimal("1450")
CLAMP_REQUEST_DEFAULT_STEEL_PRICE_USD = Decimal("1.45")
CLAMP_REQUEST_DEFAULT_SUPPLIER_DISCOUNT = Decimal("0")
CLAMP_REQUEST_DEFAULT_GENERAL_INCREASE = Decimal("40")


def get_catalog_product_queryset():
    """
    Build a lean queryset for catalog listing with active categories prefetched.
    """
    active_category_prefetch = Prefetch(
        "categories",
        queryset=Category.objects.filter(is_active=True).only("id", "name", "is_active", "slug"),
        to_attr="prefetched_active_categories",
    )
    return Product.catalog_visible(
        Product.objects.select_related("category")
        .prefetch_related(active_category_prefetch)
        .only(
            "id",
            "sku",
            "name",
            "description",
            "price",
            "stock",
            "image",
            "category_id",
            "category__id",
            "category__name",
            "category__is_active",
            "updated_at",
        )
    )


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
    products = get_catalog_product_queryset()

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

            category_attributes = list(
                CategoryAttribute.objects.filter(category=current_category).only(
                    "id",
                    "name",
                    "slug",
                    "type",
                    "options",
                    "required",
                    "is_recommended",
                )
            )

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
        linked_categories = list(getattr(product, "prefetched_active_categories", []))
        if (
            product.category_id
            and product.category
            and product.category.is_active
            and all(cat.id != product.category_id for cat in linked_categories)
        ):
            linked_categories.append(product.category)
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
    active_category_prefetch = Prefetch(
        "categories",
        queryset=Category.objects.filter(is_active=True).only("id", "name", "is_active", "slug"),
        to_attr="prefetched_active_categories",
    )
    product = get_object_or_404(
        Product.catalog_visible(
            Product.objects.select_related("category").prefetch_related(active_category_prefetch).only(
                "id",
                "sku",
                "name",
                "description",
                "supplier",
                "price",
                "stock",
                "image",
                "attributes",
                "category_id",
                "category__id",
                "category__name",
                "category__slug",
                "category__is_active",
            )
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
    linked_categories = list(getattr(product, "prefetched_active_categories", []))
    if (
        product.category_id
        and product.category
        and product.category.is_active
        and all(cat.id != product.category_id for cat in linked_categories)
    ):
        linked_categories.append(product.category)
    if product.category_id and product.category and product.category.is_active:
        primary_category = product.category
    else:
        primary_category = linked_categories[0] if linked_categories else None
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


def _find_matching_clamp_products(inputs, limit=12):
    """Find existing catalog products with same clamp technical dimensions."""
    clamp_type = str(inputs.get("clamp_type", "")).strip().upper()
    diameter = str(inputs.get("diameter", "")).strip()
    width_mm = inputs.get("width_mm")
    length_mm = inputs.get("length_mm")
    profile_type = str(inputs.get("profile_type", "")).strip().upper()

    if not all([clamp_type, diameter, width_mm, length_mm, profile_type]):
        return []

    queryset = Product.catalog_visible(
        Product.objects.select_related("category")
        .prefetch_related("categories")
        .filter(
            clamp_specs__fabrication=clamp_type,
            clamp_specs__diameter=diameter,
            clamp_specs__width=width_mm,
            clamp_specs__length=length_mm,
            clamp_specs__shape=profile_type,
            is_active=True,
        )
        .distinct()
        .order_by("name")
    )
    return list(queryset[:limit])


def clamp_measure_request(request):
    """
    Client flow:
    1) Search whether the measure already exists.
    2) If not found, submit "consultar precio" request.
    3) Admin confirms a price and client can later see it in this page.
    """
    default_form = {
        "client_name": "",
        "client_email": "",
        "client_phone": "",
        "client_note": "",
        "quantity": "1",
        "clamp_type": "trefilada",
        "is_zincated": False,
        "diameter": "7/16",
        "width_mm": "",
        "length_mm": "",
        "profile_type": "PLANA",
    }

    if request.user.is_authenticated:
        profile = getattr(request.user, "client_profile", None)
        default_form["client_name"] = (
            getattr(profile, "company_name", "")
            or request.user.get_full_name()
            or request.user.username
        )
        default_form["client_email"] = request.user.email or ""
        default_form["client_phone"] = getattr(profile, "phone", "")

    form_values = default_form.copy()
    check_performed = False
    matching_products = []
    has_matches = False
    generated_description = ""
    generated_code = ""
    created_request = None

    feedback_ids = request.session.get("clamp_request_feedback_ids", [])
    allowed_feedback_ids = {int(item) for item in feedback_ids if str(item).isdigit()}
    request_id = str(request.GET.get("request_id", "")).strip()
    if request_id.isdigit():
        candidate = ClampMeasureRequest.objects.filter(pk=int(request_id)).first()
        if candidate:
            is_allowed = bool(request.user.is_authenticated and request.user.is_staff)
            if not is_allowed and request.user.is_authenticated and candidate.client_user_id == request.user.pk:
                is_allowed = True
            if not is_allowed and int(request_id) in allowed_feedback_ids:
                is_allowed = True
            if is_allowed:
                created_request = candidate

    if request.user.is_authenticated:
        client_requests = ClampMeasureRequest.objects.filter(client_user=request.user).order_by("-created_at")[:25]
    else:
        client_requests = (
            ClampMeasureRequest.objects.filter(pk__in=allowed_feedback_ids).order_by("-created_at")[:25]
            if allowed_feedback_ids
            else []
        )

    if request.method == "POST":
        form_values.update(
            {
                "client_name": str(request.POST.get("client_name", "")).strip(),
                "client_email": str(request.POST.get("client_email", "")).strip(),
                "client_phone": str(request.POST.get("client_phone", "")).strip(),
                "client_note": str(request.POST.get("client_note", "")).strip(),
                "quantity": str(request.POST.get("quantity", "1")).strip() or "1",
                "clamp_type": str(request.POST.get("clamp_type", "trefilada")).strip().lower(),
                "is_zincated": str(request.POST.get("is_zincated", "")).strip().lower()
                in {"1", "true", "on", "yes"},
                "diameter": str(request.POST.get("diameter", "7/16")).strip(),
                "width_mm": str(request.POST.get("width_mm", "")).strip(),
                "length_mm": str(request.POST.get("length_mm", "")).strip(),
                "profile_type": str(request.POST.get("profile_type", "PLANA")).strip().upper(),
            }
        )
        if (
            form_values["clamp_type"] == "laminada"
            and form_values["diameter"] not in CLAMP_LAMINATED_ALLOWED_DIAMETERS
        ):
            form_values["diameter"] = CLAMP_LAMINATED_ALLOWED_DIAMETERS[0]
        action = str(request.POST.get("action", "check_exists")).strip().lower()
        check_performed = True

        try:
            internal_quote_payload = {
                "client_name": form_values["client_name"],
                "dollar_rate": CLAMP_REQUEST_DEFAULT_DOLLAR_RATE,
                "steel_price_usd": CLAMP_REQUEST_DEFAULT_STEEL_PRICE_USD,
                "supplier_discount_pct": CLAMP_REQUEST_DEFAULT_SUPPLIER_DISCOUNT,
                "general_increase_pct": CLAMP_REQUEST_DEFAULT_GENERAL_INCREASE,
                "clamp_type": form_values["clamp_type"],
                "is_zincated": "1" if form_values["is_zincated"] else "0",
                "diameter": form_values["diameter"],
                "width_mm": form_values["width_mm"],
                "length_mm": form_values["length_mm"],
                "profile_type": form_values["profile_type"],
            }
            quote_result = calculate_clamp_quote(internal_quote_payload)
            generated_description = quote_result["description"]
            generated_code = quote_result.get("generated_code", "")

            matching_products = _find_matching_clamp_products(quote_result["inputs"])
            has_matches = bool(matching_products)

            if action == "submit_request":
                if has_matches:
                    messages.info(
                        request,
                        "La medida ya existe en el catalogo. No se envio solicitud de precio.",
                    )
                else:
                    try:
                        quantity = int(form_values.get("quantity", "1"))
                    except (TypeError, ValueError):
                        quantity = 0
                    if quantity <= 0:
                        raise ValueError("La cantidad debe ser mayor a cero.")

                    client_name = form_values["client_name"]
                    if not client_name and request.user.is_authenticated:
                        profile = getattr(request.user, "client_profile", None)
                        client_name = (
                            getattr(profile, "company_name", "")
                            or request.user.get_full_name()
                            or request.user.username
                        )
                    if not client_name:
                        raise ValueError("Ingresa un nombre de cliente para consultar precio.")

                    profile = getattr(request.user, "client_profile", None) if request.user.is_authenticated else None
                    client_email = form_values["client_email"] or (request.user.email if request.user.is_authenticated else "")
                    client_phone = form_values["client_phone"] or (getattr(profile, "phone", "") if profile else "")
                    price_map = {row["key"]: row for row in quote_result["price_rows"]}
                    default_row = price_map.get("lista_1") or quote_result["price_rows"][0]

                    created = ClampMeasureRequest.objects.create(
                        client_user=request.user if request.user.is_authenticated else None,
                        client_name=client_name,
                        client_email=client_email,
                        client_phone=client_phone,
                        clamp_type=quote_result["inputs"]["clamp_type"],
                        is_zincated=quote_result["inputs"]["is_zincated"],
                        diameter=quote_result["inputs"]["diameter"],
                        width_mm=quote_result["inputs"]["width_mm"],
                        length_mm=quote_result["inputs"]["length_mm"],
                        profile_type=quote_result["inputs"]["profile_type"],
                        quantity=quantity,
                        description=quote_result["description"],
                        generated_code=generated_code,
                        dollar_rate=quote_result["inputs"]["dollar_rate"],
                        steel_price_usd=quote_result["inputs"]["steel_price_usd"],
                        supplier_discount_pct=quote_result["inputs"]["supplier_discount_pct"],
                        general_increase_pct=quote_result["inputs"]["general_increase_pct"],
                        base_cost=quote_result["base_cost"],
                        selected_price_list=default_row["key"],
                        estimated_final_price=default_row["final_price"],
                        exists_in_catalog=False,
                        client_note=form_values["client_note"],
                    )
                    messages.success(
                        request,
                        f"Solicitud enviada. Numero de seguimiento: #{created.pk}.",
                    )
                    feedback_ids = request.session.get("clamp_request_feedback_ids", [])
                    feedback_ids.append(created.pk)
                    request.session["clamp_request_feedback_ids"] = feedback_ids[-20:]
                    return redirect(f"{reverse('catalog_clamp_request')}?request_id={created.pk}")
        except ValueError as exc:
            messages.error(request, str(exc))

    context = {
        "form_values": form_values,
        "diameter_options": get_allowed_diameter_options(form_values.get("clamp_type")),
        "profile_options": ["PLANA", "SEMICURVA", "CURVA"],
        "check_performed": check_performed,
        "matching_products": matching_products,
        "has_matches": has_matches,
        "generated_description": generated_description,
        "generated_code": generated_code,
        "created_request": created_request,
        "client_requests": client_requests,
        "all_diameter_options_json": json.dumps(get_allowed_diameter_options()),
        "laminated_diameter_options_json": json.dumps(list(CLAMP_LAMINATED_ALLOWED_DIAMETERS)),
        "canonical_url": request.build_absolute_uri(reverse("catalog_clamp_request")),
        "seo_title": "Abrazaderas a Medida | FLEXS",
        "seo_description": "Consulta existencia y solicita cotizacion de abrazaderas a medida.",
    }
    return render(request, "catalog/clamp_request.html", context)


@login_required
@require_POST
def clamp_request_add_to_cart(request, pk):
    """Add a completed custom clamp request into client cart."""
    clamp_request = get_object_or_404(
        ClampMeasureRequest.objects.select_related("linked_product"),
        pk=pk,
        client_user=request.user,
    )

    if clamp_request.status != ClampMeasureRequest.STATUS_COMPLETED:
        messages.error(
            request,
            "Solo puedes agregar al carrito solicitudes con estado Completada.",
        )
        return redirect(f"{reverse('catalog_clamp_request')}?request_id={clamp_request.pk}")

    if not clamp_request.confirmed_price or clamp_request.confirmed_price <= 0:
        messages.error(
            request,
            "Esta solicitud todavia no tiene un precio confirmado valido.",
        )
        return redirect(f"{reverse('catalog_clamp_request')}?request_id={clamp_request.pk}")

    try:
        quantity = int(str(request.POST.get("quantity", "")).strip() or clamp_request.quantity or 1)
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(quantity, 1)

    product, _ = get_or_create_request_product(clamp_request)
    if product.price != clamp_request.confirmed_price:
        product.price = clamp_request.confirmed_price
        product.save(update_fields=["price", "updated_at"])

    cart, _ = Cart.objects.get_or_create(user=request.user)
    cart_item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        defaults={
            "quantity": quantity,
            "clamp_request": clamp_request,
        },
    )
    if not created:
        cart_item.quantity += quantity
        if cart_item.clamp_request_id != clamp_request.pk:
            cart_item.clamp_request = clamp_request
            cart_item.save(update_fields=["quantity", "clamp_request"])
        else:
            cart_item.save(update_fields=["quantity"])

    if not clamp_request.added_to_cart_at:
        clamp_request.added_to_cart_at = timezone.now()
        clamp_request.save(update_fields=["added_to_cart_at", "updated_at"])

    messages.success(
        request,
        f"Se agrego la medida solicitada (#{clamp_request.pk}) al carrito.",
    )
    return redirect("cart")
