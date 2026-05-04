"""
Catalog app views - Product listing and detail.
"""
import hashlib
import json
import re
from decimal import Decimal
from functools import lru_cache

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Max, Prefetch, Q, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import CatalogAnalyticsEvent, CatalogExcelTemplate, SiteSettings
from core.services.advanced_search import apply_text_search, build_text_query
from core.services.catalog_excel_exporter import build_catalog_workbook, build_export_filename
from core.services.company_context import get_active_company
from core.services.pricing import (
    build_price_list_item_map,
    resolve_effective_discount_percentage,
    resolve_effective_price_list,
    resolve_pricing_context,
    get_product_pricing,
)
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

SEARCH_TOKEN_PATTERN = re.compile(r'"([^"]+)"|(\S+)')
DIMENSIONS_PATTERN = re.compile(
    r"(?P<diam>\d+(?:\s+\d+/\d+|/\d+)?)\s*(?:x|X|×|\*)\s*(?P<width>\d{1,4})\s*(?:x|X|×|\*)\s*(?P<length>\d{1,4})"
)
SKU_CODE_PATTERN = re.compile(r"^AB[LT][A-Z0-9/\-]+$", re.IGNORECASE)
TOKEN_EDGE_TRIM_PATTERN = re.compile(r"^[,;|]+|[,;|]+$")
SEARCH_ACTION_LABEL_PATTERN = re.compile(r'(?i)^buscar\s+"(.+)"$')

CLAMP_TYPE_ALIASES = {
    "t": "TREFILADA",
    "tref": "TREFILADA",
    "trefilada": "TREFILADA",
    "l": "LAMINADA",
    "lam": "LAMINADA",
    "laminada": "LAMINADA",
}

CLAMP_SHAPE_ALIASES = {
    "p": "PLANA",
    "plana": "PLANA",
    "plano": "PLANA",
    "s": "SEMICURVA",
    "semi": "SEMICURVA",
    "semicurva": "SEMICURVA",
    "sc": "SEMICURVA",
    "c": "CURVA",
    "curva": "CURVA",
}

ADVANCED_KEY_ALIASES = {
    "sku": "sku",
    "codigo": "sku",
    "code": "sku",
    "proveedor": "supplier",
    "supplier": "supplier",
    "marca": "supplier",
    "prov": "supplier",
    "cat": "category",
    "categoria": "category",
    "tipo": "type",
    "t": "type",
    "diam": "diameter",
    "diametro": "diameter",
    "d": "diameter",
    "ancho": "width",
    "width": "width",
    "w": "width",
    "largo": "length",
    "length": "length",
    "l": "length",
    "forma": "shape",
    "perfil": "shape",
    "shape": "shape",
    "f": "shape",
}


def can_use_clamp_measure_feature(user, company=None):
    """
    Clamp custom measure feature is available only for:
    - Admin users
    - Approved client accounts
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    profile = getattr(user, "client_profile", None)
    if not profile:
        return False
    if not getattr(profile, "is_approved", False):
        return False
    if company:
        return profile.can_operate_in_company(company)
    return True


@lru_cache(maxsize=1)
def get_compact_diameter_lookup():
    """
    Build compact->human lookup once to support searches like 716x80x220.
    """
    mapping = {}
    for option in get_allowed_diameter_options():
        compact = re.sub(r"[^\d]", "", str(option or ""))
        if compact:
            mapping.setdefault(compact, option)
    return mapping


def sanitize_search_token(value):
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    search_action_match = SEARCH_ACTION_LABEL_PATTERN.fullmatch(cleaned)
    if search_action_match:
        cleaned = search_action_match.group(1).strip()
        if not cleaned:
            return ""
    cleaned = TOKEN_EDGE_TRIM_PATTERN.sub("", cleaned).strip()
    cleaned = cleaned.replace("×", "x")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def normalize_diameter_value(value):
    raw = sanitize_search_token(value).lower()
    if not raw:
        return ""

    raw = raw.replace("-", "/")
    raw = re.sub(r"\s+", "", raw)
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            return f"{int(parts[0])}/{int(parts[1])}"
    if raw.isdigit():
        compact = raw.lstrip("0") or "0"
        compact_lookup = get_compact_diameter_lookup()
        if compact in compact_lookup:
            return str(compact_lookup[compact])
        return str(int(raw))
    return str(value or "").strip()


def normalize_shape_value(value):
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return CLAMP_SHAPE_ALIASES.get(token, token.upper())


def normalize_type_value(value):
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return CLAMP_TYPE_ALIASES.get(token, token.upper())


def extract_search_tokens(raw_query):
    tokens = []
    for match in SEARCH_TOKEN_PATTERN.finditer(str(raw_query or "")):
        token = (match.group(1) or match.group(2) or "").strip()
        if token:
            tokens.append((token, bool(match.group(1))))
    return tokens


def parse_catalog_search_query(raw_query):
    """
    Parse user search text into structured, reusable filters.
    Supports:
    - quoted phrases: "buje trasero"
    - exclusions: -ford
    - key:value expressions: sku:ABT..., tipo:trefilada, diametro:7/16, ancho:80...
    - compact dimensions: 7/16x80x220
    """
    parsed = {
        "raw": sanitize_search_token(raw_query),
        "phrases": [],
        "include_terms": [],
        "exclude_terms": [],
        "sku": "",
        "supplier_terms": [],
        "category_terms": [],
        "clamp_type": "",
        "clamp_shape": "",
        "clamp_diameter": "",
        "clamp_width": None,
        "clamp_length": None,
    }
    if not parsed["raw"]:
        return parsed

    for token, is_phrase in extract_search_tokens(parsed["raw"]):
        cleaned = sanitize_search_token(token)
        if not cleaned:
            continue

        is_exclusion = cleaned.startswith("-") and len(cleaned) > 1
        if is_exclusion:
            cleaned = sanitize_search_token(cleaned[1:])
            if not cleaned:
                continue

        key = ""
        value = cleaned
        if ":" in cleaned:
            maybe_key, maybe_value = cleaned.split(":", 1)
            alias = ADVANCED_KEY_ALIASES.get(maybe_key.strip().lower())
            if alias:
                key = alias
                value = sanitize_search_token(maybe_value)

        if key:
            if key == "sku" and value:
                parsed["sku"] = value
                continue
            if key == "supplier" and value:
                parsed["supplier_terms"].append(value)
                continue
            if key == "category" and value:
                parsed["category_terms"].append(value)
                continue
            if key == "type" and value:
                parsed["clamp_type"] = normalize_type_value(value)
                continue
            if key == "shape" and value:
                parsed["clamp_shape"] = normalize_shape_value(value)
                continue
            if key == "diameter" and value:
                parsed["clamp_diameter"] = normalize_diameter_value(value)
                continue
            if key == "width" and value.isdigit():
                parsed["clamp_width"] = int(value)
                continue
            if key == "length" and value.isdigit():
                parsed["clamp_length"] = int(value)
                continue

        dimensions_match = DIMENSIONS_PATTERN.search(cleaned)
        if dimensions_match:
            parsed["clamp_diameter"] = normalize_diameter_value(dimensions_match.group("diam"))
            parsed["clamp_width"] = int(dimensions_match.group("width"))
            parsed["clamp_length"] = int(dimensions_match.group("length"))
            continue

        maybe_type = normalize_type_value(cleaned)
        if maybe_type in {"TREFILADA", "LAMINADA"}:
            parsed["clamp_type"] = maybe_type
            continue

        maybe_shape = normalize_shape_value(cleaned)
        if maybe_shape in {"PLANA", "SEMICURVA", "CURVA"}:
            parsed["clamp_shape"] = maybe_shape
            continue

        normalized_diameter = normalize_diameter_value(cleaned)
        if normalized_diameter in get_allowed_diameter_options():
            parsed["clamp_diameter"] = normalized_diameter
            continue

        if not parsed["sku"] and SKU_CODE_PATTERN.match(cleaned):
            parsed["sku"] = cleaned
            continue

        if is_phrase:
            parsed["phrases"].append(cleaned)
        elif is_exclusion:
            parsed["exclude_terms"].append(cleaned)
        else:
            parsed["include_terms"].append(cleaned)

    parsed["include_terms"] = parsed["include_terms"][:8]
    parsed["exclude_terms"] = parsed["exclude_terms"][:8]
    parsed["phrases"] = parsed["phrases"][:4]
    parsed["supplier_terms"] = parsed["supplier_terms"][:4]
    parsed["category_terms"] = parsed["category_terms"][:4]
    return parsed


def apply_catalog_text_search(products, parsed_search):
    search_fields = ["name", "sku", "description", "supplier", "supplier_ref__name"]

    if parsed_search["sku"]:
        products = products.filter(sku__icontains=parsed_search["sku"])

    for phrase in parsed_search["phrases"]:
        products = apply_text_search(
            products,
            phrase,
            search_fields,
            order_by_similarity=False,
        )

    for term in parsed_search["include_terms"]:
        products = apply_text_search(
            products,
            term,
            search_fields,
            order_by_similarity=False,
        )

    for term in parsed_search["exclude_terms"]:
        products = products.exclude(build_text_query(search_fields, term))

    for term in parsed_search["supplier_terms"]:
        products = apply_text_search(
            products,
            term,
            ["supplier", "supplier_ref__name"],
            order_by_similarity=False,
        )

    for term in parsed_search["category_terms"]:
        products = products.filter(
            Q(category__name__icontains=term)
            | Q(category__slug__icontains=term)
            | Q(categories__name__icontains=term)
            | Q(categories__slug__icontains=term)
        ).distinct()

    clamp_filters = {}
    if parsed_search["clamp_type"] in {"TREFILADA", "LAMINADA"}:
        clamp_filters["clamp_specs__fabrication"] = parsed_search["clamp_type"]
    if parsed_search["clamp_shape"] in {"PLANA", "SEMICURVA", "CURVA"}:
        clamp_filters["clamp_specs__shape"] = parsed_search["clamp_shape"]
    if parsed_search["clamp_diameter"]:
        clamp_filters["clamp_specs__diameter"] = parsed_search["clamp_diameter"]
    if parsed_search["clamp_width"] is not None:
        clamp_filters["clamp_specs__width"] = parsed_search["clamp_width"]
    if parsed_search["clamp_length"] is not None:
        clamp_filters["clamp_specs__length"] = parsed_search["clamp_length"]
    if clamp_filters:
        products = products.filter(**clamp_filters)

    return products


def annotate_catalog_search_rank(products, parsed_search):
    if not parsed_search["raw"]:
        return products

    rank_expr = Value(0, output_field=IntegerField())
    rank_expr += Case(
        When(sku__iexact=parsed_search["raw"], then=Value(400)),
        default=Value(0),
        output_field=IntegerField(),
    )
    rank_expr += Case(
        When(sku__istartswith=parsed_search["raw"], then=Value(180)),
        default=Value(0),
        output_field=IntegerField(),
    )
    rank_expr += Case(
        When(name__icontains=parsed_search["raw"], then=Value(120)),
        default=Value(0),
        output_field=IntegerField(),
    )
    rank_expr += Case(
        When(description__icontains=parsed_search["raw"], then=Value(45)),
        default=Value(0),
        output_field=IntegerField(),
    )

    for phrase in parsed_search["phrases"]:
        rank_expr += Case(
            When(name__icontains=phrase, then=Value(70)),
            default=Value(0),
            output_field=IntegerField(),
        )
        rank_expr += Case(
            When(description__icontains=phrase, then=Value(25)),
            default=Value(0),
            output_field=IntegerField(),
        )

    for term in parsed_search["include_terms"]:
        rank_expr += Case(
            When(sku__iexact=term, then=Value(90)),
            default=Value(0),
            output_field=IntegerField(),
        )
        rank_expr += Case(
            When(sku__icontains=term, then=Value(45)),
            default=Value(0),
            output_field=IntegerField(),
        )
        rank_expr += Case(
            When(name__icontains=term, then=Value(28)),
            default=Value(0),
            output_field=IntegerField(),
        )
        rank_expr += Case(
            When(description__icontains=term, then=Value(12)),
            default=Value(0),
            output_field=IntegerField(),
        )
        rank_expr += Case(
            When(supplier__icontains=term, then=Value(8)),
            default=Value(0),
            output_field=IntegerField(),
        )
        rank_expr += Case(
            When(supplier_ref__name__icontains=term, then=Value(8)),
            default=Value(0),
            output_field=IntegerField(),
        )

    if parsed_search["sku"]:
        rank_expr += Case(
            When(sku__iexact=parsed_search["sku"], then=Value(300)),
            default=Value(0),
            output_field=IntegerField(),
        )

    return products.annotate(search_rank=rank_expr)


def build_search_summary(parsed_search):
    summary = []
    if parsed_search.get("sku"):
        summary.append(f"SKU: {parsed_search['sku']}")
    if parsed_search.get("clamp_type"):
        summary.append(f"Tipo: {parsed_search['clamp_type'].title()}")
    if parsed_search.get("clamp_diameter"):
        summary.append(f"Diametro: {parsed_search['clamp_diameter']}")
    if parsed_search.get("clamp_width") is not None:
        summary.append(f"Ancho: {parsed_search['clamp_width']}")
    if parsed_search.get("clamp_length") is not None:
        summary.append(f"Largo: {parsed_search['clamp_length']}")
    if parsed_search.get("clamp_shape"):
        summary.append(f"Forma: {parsed_search['clamp_shape'].title()}")
    for term in parsed_search.get("supplier_terms", []):
        summary.append(f"Proveedor: {term}")
    for term in parsed_search.get("category_terms", []):
        summary.append(f"Categoria: {term}")
    for phrase in parsed_search.get("phrases", []):
        summary.append(f'"{phrase}"')
    for term in parsed_search.get("exclude_terms", []):
        summary.append(f"Excluye: {term}")
    return summary[:10]


def get_catalog_product_queryset():
    """
    Build a lean queryset for catalog listing with public categories prefetched.
    """
    public_category_prefetch = Prefetch(
        "categories",
        queryset=Category.objects.filter(is_active=True, visible_in_catalog=True).only(
            "id",
            "name",
            "public_name",
            "is_active",
            "visible_in_catalog",
            "slug",
        ),
        to_attr="prefetched_public_categories",
    )
    return Product.catalog_visible(
        Product.objects.select_related("category")
        .prefetch_related(public_category_prefetch)
        .only(
            "id",
            "sku",
            "name",
            "supplier",
            "description",
            "price",
            "stock",
            "image",
            "category_id",
            "category__id",
            "category__name",
            "category__public_name",
            "category__is_active",
            "category__visible_in_catalog",
            "updated_at",
        )
    )


def get_public_category_ids_with_products():
    """
    Return public category IDs that should appear in the client catalog.

    A category appears if it has at least one catalog-visible product assigned to
    itself or to a visible descendant. Empty public categories remain hidden from
    the customer-facing tree but still exist in admin.
    """
    all_categories = {
        category.id: category
        for category in Category.objects.select_related("parent")
    }
    visible_categories = {
        category.id: category
        for category in all_categories.values()
        if category.is_active and category.visible_in_catalog
    }
    if not visible_categories:
        return set()

    products = Product.catalog_visible(
        Product.objects.all(),
        include_uncategorized=False,
    )
    linked_ids = set(
        products.exclude(category_id__isnull=True).values_list("category_id", flat=True)
    )
    linked_ids.update(
        products.exclude(categories__id__isnull=True).values_list("categories__id", flat=True)
    )

    public_ids = set()
    for category_id in linked_ids:
        node = visible_categories.get(category_id)
        if not node:
            continue
        chain = []
        cursor = node
        is_public_path = True
        while cursor:
            if not cursor.is_active or not cursor.visible_in_catalog:
                is_public_path = False
                break
            chain.append(cursor)
            cursor = all_categories.get(cursor.parent_id)
        if is_public_path:
            public_ids.update(category.id for category in chain)
    return public_ids


def build_category_tree_rows(categories):
    """Build flattened rows for tree rendering in templates."""
    category_list = list(categories)
    category_map = {category.id: category for category in category_list}
    children_map = {}

    for category in category_list:
        children_map.setdefault(category.parent_id, []).append(category)

    def category_sort_key(cat):
        return (cat.public_order, cat.order, cat.display_name.lower(), cat.id)

    for siblings in children_map.values():
        siblings.sort(key=category_sort_key)

    roots = [cat for cat in category_list if cat.parent_id not in category_map]
    roots.sort(key=category_sort_key)

    rows = []
    visited = set()

    def walk(node, depth, path_names):
        if node.id in visited:
            return
        visited.add(node.id)

        next_path = [*path_names, node.display_name]
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
        key=category_sort_key,
    )
    for category in remaining:
        walk(category, 0, [])

    return rows


def get_cached_category_tree_rows():
    """
    Cache category tree generation to avoid rebuilding on each request.
    """
    aggregate = Category.objects.filter(is_active=True, visible_in_catalog=True).aggregate(
        total=Count("id"),
        max_updated=Max("updated_at"),
    )
    total = aggregate.get("total") or 0
    max_updated = aggregate.get("max_updated")
    stamp = int(max_updated.timestamp()) if max_updated else 0
    public_ids = get_public_category_ids_with_products()
    ids_stamp = hashlib.sha1(
        ",".join(str(category_id) for category_id in sorted(public_ids)).encode("utf-8")
    ).hexdigest()[:16]
    cache_key = f"catalog_tree_rows_v4:{total}:{stamp}:{ids_stamp}"

    rows = cache.get(cache_key)
    if rows is not None:
        return rows

    categories = (
        Category.objects.filter(
            id__in=public_ids,
            is_active=True,
            visible_in_catalog=True,
        )
        .select_related("parent")
        .order_by("public_order", "order", "name")
    )
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
    return [{"name": cat.display_name, "url": f"{reverse('catalog')}?category={cat.slug}"} for cat in chain]


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

    search_query = sanitize_search_token(request.GET.get("q", ""))
    parsed_search = parse_catalog_search_query(search_query)
    if search_query:
        products = apply_catalog_text_search(products, parsed_search)

    category_slug = request.GET.get("category", "")
    current_category = None
    category_attributes = []
    active_filters = {}
    clamp_options = {}

    if category_slug:
        current_category = Category.objects.filter(
            slug=category_slug,
            is_active=True,
            visible_in_catalog=True,
        ).first()
        if current_category:
            raw_category_ids = current_category.get_descendant_ids(include_self=True, only_active=True)
            category_ids = list(
                Category.objects.filter(
                    id__in=raw_category_ids,
                    visible_in_catalog=True,
                ).values_list("id", flat=True)
            )
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

    order_by_default = "relevance" if search_query else "name"
    order_by = request.GET.get("order", order_by_default)
    valid_orders = {"name", "-name", "price", "-price", "sku", "relevance"}

    if order_by not in valid_orders:
        order_by = order_by_default

    view_mode = request.GET.get("view", "grid").strip().lower()
    if view_mode not in {"grid", "list"}:
        view_mode = "grid"

    if search_query:
        products = annotate_catalog_search_rank(products, parsed_search)
        if order_by == "relevance":
            products = products.order_by("-search_rank", "name")
        else:
            products = products.order_by(order_by)
    else:
        if order_by == "relevance":
            order_by = "name"
        products = products.order_by(order_by)

    paginator = Paginator(products, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    category_tree_rows = get_cached_category_tree_rows()

    settings = SiteSettings.get_settings()
    show_prices = settings.show_public_prices or request.user.is_authenticated

    company = get_active_company(request)
    discount_percentage = Decimal("0")
    discount = Decimal("0")
    pricing_context = None
    price_list = None
    favorite_product_ids = set()
    if request.user.is_authenticated and hasattr(request.user, "client_profile"):
        pricing_context = resolve_pricing_context(request.user, company)
        price_list = resolve_effective_price_list(
            company=company,
            client_company=pricing_context[1],
            client_category=pricing_context[2],
        )
        discount_percentage = resolve_effective_discount_percentage(
            client_profile=pricing_context[0],
            company=company,
            client_company=pricing_context[1],
            client_category=pricing_context[2],
        )
        discount = discount_percentage / 100 if discount_percentage else Decimal("0")
        favorite_product_ids = set(
            ClientFavoriteProduct.objects.filter(user=request.user).values_list("product_id", flat=True)
        )
    else:
        price_list = resolve_effective_price_list(company=company)

    product_ids = [product.id for product in page_obj.object_list]
    price_item_map = build_price_list_item_map(price_list, product_ids)
    for product in page_obj.object_list:
        linked_categories = list(getattr(product, "prefetched_public_categories", []))
        if (
            product.category_id
            and product.category
            and product.category.is_active
            and product.category.visible_in_catalog
            and all(cat.id != product.category_id for cat in linked_categories)
        ):
            linked_categories.append(product.category)
        product.display_categories = linked_categories[:3]
        product.is_favorite = product.id in favorite_product_ids
        pricing = get_product_pricing(
            product,
            company=company,
            price_list=price_list,
            item_map=price_item_map,
            context=pricing_context,
        )
        product.base_price = pricing.base_price
        product.final_price = pricing.final_price

    expanded_category_ids = []
    current_category_has_descendants = False
    if current_category:
        expanded_category_ids.append(current_category.id)
        current_category_has_descendants = bool(
            Category.objects.filter(
                id__in=current_category.get_descendant_ids(include_self=False, only_active=True),
                visible_in_catalog=True,
            ).exists()
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
        seo_title = current_category.seo_title or f"{current_category.display_name} | Catalogo FLEXS"
        seo_description = (
            current_category.seo_description
            or current_category.public_description
            or f"Explora productos de {current_category.display_name} en FLEXS."
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
        "view_mode": view_mode,
        "can_view_catalog_supplier": bool(request.user.is_authenticated and request.user.is_staff),
        "show_prices": show_prices,
        "discount": discount,
        "price_message": settings.public_prices_message,
        "request_get": request.GET,
        "canonical_url": canonical_url,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "search_summary": build_search_summary(parsed_search),
        "parsed_search": parsed_search,
    }

    if clamp_options:
        context["clamp_options"] = clamp_options

    return render(request, "catalog/catalog_v3.html", context)


def product_detail(request, sku):
    """Product detail view."""
    public_category_prefetch = Prefetch(
        "categories",
        queryset=Category.objects.filter(is_active=True, visible_in_catalog=True).only(
            "id",
            "name",
            "public_name",
            "is_active",
            "visible_in_catalog",
            "slug",
        ),
        to_attr="prefetched_public_categories",
    )
    product = get_object_or_404(
        Product.catalog_visible(
            Product.objects.select_related("category").prefetch_related(public_category_prefetch).only(
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
                "category__public_name",
                "category__slug",
                "category__is_active",
                "category__visible_in_catalog",
            )
        ),
        sku=sku,
    )

    settings = SiteSettings.get_settings()
    show_prices = settings.show_public_prices or request.user.is_authenticated

    company = get_active_company(request)
    discount_percentage = Decimal("0")
    discount = Decimal("0")
    pricing_context = None
    price_list = None
    if request.user.is_authenticated and hasattr(request.user, "client_profile"):
        pricing_context = resolve_pricing_context(request.user, company)
        price_list = resolve_effective_price_list(
            company=company,
            client_company=pricing_context[1],
            client_category=pricing_context[2],
        )
        discount_percentage = resolve_effective_discount_percentage(
            client_profile=pricing_context[0],
            company=company,
            client_company=pricing_context[1],
            client_category=pricing_context[2],
        )
        discount = discount_percentage / 100 if discount_percentage else Decimal("0")
    else:
        price_list = resolve_effective_price_list(company=company)
    is_favorite = False
    if request.user.is_authenticated:
        is_favorite = ClientFavoriteProduct.objects.filter(
            user=request.user,
            product_id=product.id,
        ).exists()

    pricing = get_product_pricing(
        product,
        company=company,
        price_list=price_list,
        context=pricing_context,
    )
    final_price = pricing.final_price
    base_price = pricing.base_price
    linked_categories = list(getattr(product, "prefetched_public_categories", []))
    if (
        product.category_id
        and product.category
        and product.category.is_active
        and product.category.visible_in_catalog
        and all(cat.id != product.category_id for cat in linked_categories)
    ):
        linked_categories.append(product.category)
    if (
        product.category_id
        and product.category
        and product.category.is_active
        and product.category.visible_in_catalog
    ):
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
        "discount_percentage": discount_percentage,
        "final_price": final_price,
        "base_price": base_price,
        "price_message": settings.public_prices_message,
        "primary_category": primary_category,
        "category_breadcrumb": category_breadcrumb,
        "canonical_url": request.build_absolute_uri(request.path),
        "seo_title": f"{product.name} | FLEXS",
        "seo_description": seo_description,
        "is_favorite": is_favorite,
    }

    return render(request, "catalog/product_detail.html", context)


@login_required
def client_catalog_excel_download(request):
    """Download the published catalog Excel template for approved clients/admins."""
    if not request.user.is_staff:
        profile = getattr(request.user, "client_profile", None)
        company = get_active_company(request)
        if not profile or not getattr(profile, "is_approved", False):
            messages.warning(
                request,
                "La descarga de Excel esta disponible solo para clientes aprobados.",
            )
            return redirect("catalog")
        if company and not profile.can_operate_in_company(company):
            messages.warning(
                request,
                "Tu cuenta no esta habilitada para descargar el catalogo en esta empresa.",
            )
            return redirect("catalog")

    template = (
        CatalogExcelTemplate.objects.prefetch_related(
            "sheets__columns",
            "sheets__categories",
            "sheets__suppliers",
        )
        .filter(is_active=True, is_client_download_enabled=True)
        .order_by("-updated_at", "id")
        .first()
    )

    if not template:
        messages.warning(
            request,
            "No hay una plantilla de Excel publicada para clientes en este momento.",
        )
        return redirect("catalog")

    company = get_active_company(request)
    pricing_context = resolve_pricing_context(request.user, company) if request.user.is_authenticated else None
    price_list = resolve_effective_price_list(
        company=company,
        client_company=pricing_context[1] if pricing_context else None,
        client_category=pricing_context[2] if pricing_context else None,
    )
    discount_percentage = resolve_effective_discount_percentage(
        client_profile=pricing_context[0] if pricing_context else None,
        company=company,
        client_company=pricing_context[1] if pricing_context else None,
        client_category=pricing_context[2] if pricing_context else None,
    )
    workbook, _stats = build_catalog_workbook(
        template,
        price_list=price_list,
        discount_percentage=discount_percentage,
    )
    file_name = build_export_filename(template)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    workbook.save(response)
    return response


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


@login_required
def clamp_measure_request(request):
    """
    Client flow:
    1) Search whether the measure already exists.
    2) If not found, submit "consultar precio" request.
    3) Admin confirms a price and client can later see it in this page.
    """
    company = get_active_company(request)
    if not can_use_clamp_measure_feature(request.user, company=company):
        messages.error(
            request,
            "Esta opcion esta disponible solo para clientes aprobados o administradores.",
        )
        return redirect("catalog")

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
    generated_base_cost = None
    generated_total_weight_kg = None
    generated_development_meters = None
    created_request = None

    feedback_ids = request.session.get("clamp_request_feedback_ids", [])
    allowed_feedback_ids = {int(item) for item in feedback_ids if str(item).isdigit()}
    request_id = str(request.GET.get("request_id", "")).strip()
    if request_id.isdigit():
        candidate_qs = ClampMeasureRequest.objects.filter(pk=int(request_id))
        if company:
            candidate_qs = candidate_qs.filter(company=company)
        candidate = candidate_qs.first()
        if candidate:
            is_allowed = bool(request.user.is_authenticated and request.user.is_staff)
            if not is_allowed and request.user.is_authenticated and candidate.client_user_id == request.user.pk:
                is_allowed = True
            if not is_allowed and int(request_id) in allowed_feedback_ids:
                is_allowed = True
            if is_allowed:
                created_request = candidate

    if request.user.is_authenticated:
        client_requests = ClampMeasureRequest.objects.filter(client_user=request.user)
        if company:
            client_requests = client_requests.filter(company=company)
        client_requests = client_requests.order_by("-created_at")[:25]
    else:
        if allowed_feedback_ids:
            client_requests = ClampMeasureRequest.objects.filter(pk__in=allowed_feedback_ids)
            if company:
                client_requests = client_requests.filter(company=company)
            client_requests = client_requests.order_by("-created_at")[:25]
        else:
            client_requests = []

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
            generated_base_cost = quote_result.get("base_cost")
            generated_total_weight_kg = quote_result.get("total_weight_kg")
            generated_development_meters = quote_result.get("development_meters")

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
                        company=company,
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
        "generated_base_cost": generated_base_cost,
        "generated_total_weight_kg": generated_total_weight_kg,
        "generated_development_meters": generated_development_meters,
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
    company = get_active_company(request)
    if not can_use_clamp_measure_feature(request.user, company=company):
        messages.error(
            request,
            "Esta opcion esta disponible solo para clientes aprobados o administradores.",
        )
        return redirect("catalog")

    clamp_qs = ClampMeasureRequest.objects.select_related("linked_product")
    if company:
        clamp_qs = clamp_qs.filter(company=company)
    clamp_request = get_object_or_404(
        clamp_qs,
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

    cart, _ = Cart.objects.get_or_create(user=request.user, defaults={"company": company})
    if not cart.company_id and company:
        cart.company = company
        cart.save(update_fields=["company"])
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
