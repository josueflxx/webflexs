"""
Core app views.
"""
import hashlib
import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.utils import timezone

from accounts.models import AccountRequest, ClientProfile, ClientPayment
from catalog.models import Category, ClampMeasureRequest, Product, Supplier
from orders.models import Order
from core.models import UserActivity
from core.services.advanced_search import (
    apply_compact_text_search,
    apply_parsed_text_search,
    compact_search_token,
    parse_text_search_query,
    sanitize_search_token,
)
from core.services.presence import build_admin_presence_payload, get_presence_config


def home(request):
    """Home page view."""
    return render(request, 'core/home.html')


@require_GET
def admin_presence(request):
    """Live presence payload for admin sidebar."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"detail": "forbidden"}, status=403)

    config = get_presence_config()
    admins = build_admin_presence_payload()
    digest_base = [
        {
            "user_id": row.get("user_id"),
            "status": row.get("status"),
            "last_activity": row.get("last_activity"),
        }
        for row in admins
    ]
    digest = hashlib.sha1(
        json.dumps(digest_base, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    client_digest = (request.GET.get("digest") or "").strip()
    if client_digest and client_digest == digest:
        return JsonResponse(
            {
                "changed": False,
                "digest": digest,
                "refresh_seconds": config["refresh_seconds"],
                "online_window_seconds": config["online_window_seconds"],
                "idle_window_seconds": config["idle_window_seconds"],
            }
        )

    return JsonResponse(
        {
            "changed": True,
            "admins": admins,
            "digest": digest,
            "refresh_seconds": config["refresh_seconds"],
            "online_window_seconds": config["online_window_seconds"],
            "idle_window_seconds": config["idle_window_seconds"],
        }
    )


@require_GET
def admin_alerts(request):
    """Lightweight admin alerts payload (new client account requests)."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"detail": "forbidden"}, status=403)

    pending_qs = AccountRequest.objects.filter(status="pending")
    latest_pending = (
        pending_qs.order_by("-created_at")
        .values("id", "company_name", "created_at")
        .first()
    )

    return JsonResponse(
        {
            "account_requests": {
                "pending_count": pending_qs.count(),
                "latest_id": (latest_pending or {}).get("id") or 0,
                "latest_company_name": (latest_pending or {}).get("company_name") or "",
                "latest_created_at": (
                    latest_pending.get("created_at").isoformat()
                    if latest_pending and latest_pending.get("created_at")
                    else ""
                ),
            }
        }
    )


@require_POST
def go_offline(request):
    """Mark user as offline (called via beacon on page close)."""
    if request.user.is_authenticated and request.user.is_staff:
        UserActivity.objects.update_or_create(
            user=request.user,
            defaults={
                "is_online": False,
                "last_activity": timezone.now(),
            },
        )
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'ignored'})


@require_POST
def admin_presence_touch(request):
    """Update admin presence using explicit UI interaction heartbeat."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"detail": "forbidden"}, status=403)
    UserActivity.objects.update_or_create(
        user=request.user,
        defaults={
            "is_online": True,
            "last_activity": timezone.now(),
        },
    )
    return JsonResponse({"status": "ok"})


SUGGESTION_LIMIT = 12
PRODUCT_SUGGESTION_LIMIT = 300


def _normalize_search_scope(raw_scope):
    return str(raw_scope or "").strip().lower()


def _parse_suggestion_query(raw_query):
    return parse_text_search_query(
        raw_query,
        max_include=6,
        max_exclude=4,
        max_phrases=3,
    )


def _append_suggestion(items, value, label=None, meta="", kind="", **extra):
    value = str(value or "").strip()
    if not value:
        return
    payload = {
        "value": value,
        "label": str(label or value).strip(),
        "meta": str(meta or "").strip(),
        "kind": str(kind or "").strip(),
    }
    for key, extra_value in extra.items():
        if extra_value in (None, ""):
            continue
        payload[str(key)] = str(extra_value).strip()
    items.append(payload)


def _unique_trim_suggestions(items, limit=SUGGESTION_LIMIT):
    seen = set()
    clean = []
    for item in items:
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
        if len(clean) >= limit:
            break
    return clean


def _suggest_catalog(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items

    product_rows = (
        apply_parsed_text_search(
            Product.catalog_visible(),
            parsed_query,
            ["sku", "name", "supplier", "description"],
            order_by_similarity=False,
        )
        .values_list("sku", "name", "supplier")
        .order_by("name")[:6]
    )
    for sku, name, supplier in product_rows:
        meta = f"SKU {sku}"
        if supplier:
            meta = f"{meta} | {supplier}"
        _append_suggestion(items, sku, name, meta=meta, kind="product")

    category_rows = (
        apply_parsed_text_search(
            Category.objects.filter(is_active=True),
            parsed_query,
            ["name", "slug"],
            order_by_similarity=False,
        )
        .values_list("name", "slug")
        .order_by("name")[:4]
    )
    for name, slug in category_rows:
        _append_suggestion(
            items,
            f"cat:{slug}",
            f"Categoria: {name}",
            meta="Filtro categoria",
            kind="category",
        )

    return _unique_trim_suggestions(items)


def _suggest_admin_products_legacy(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    rows = (
        apply_parsed_text_search(
            Product.objects.all(),
            parsed_query,
            ["sku", "name", "supplier", "supplier_ref__name"],
            order_by_similarity=False,
        )
        .select_related("supplier_ref")
        .values_list("sku", "name", "supplier", "supplier_ref__name")
        .order_by("name")[:8]
    )
    for sku, name, supplier_text, supplier_ref_name in rows:
        supplier = supplier_ref_name or supplier_text or "-"
        _append_suggestion(items, sku, name, meta=f"{sku} · {supplier}", kind="product")
    return _unique_trim_suggestions(items)


def _suggest_admin_products(query, request=None):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items

    base_queryset = Product.objects.filter(is_active=True)
    search_fields = ["sku", "name", "supplier", "supplier_ref__name", "description"]
    raw_query = parsed_query["raw"]
    compact_query = compact_search_token(raw_query)

    exact_rows = list(
        base_queryset.filter(Q(sku__iexact=raw_query) | Q(name__iexact=raw_query))
        .values_list("pk", "sku", "name", "supplier", "supplier_ref__name")
        .order_by("name")[:24]
    )
    prefix_rows = list(
        base_queryset.filter(Q(sku__istartswith=raw_query) | Q(name__istartswith=raw_query))
        .values_list("pk", "sku", "name", "supplier", "supplier_ref__name")
        .order_by("name")[:80]
    )
    parsed_rows = list(
        apply_parsed_text_search(
            base_queryset,
            parsed_query,
            search_fields,
            order_by_similarity=False,
        )
        .values_list("pk", "sku", "name", "supplier", "supplier_ref__name")
        .order_by("name")[:PRODUCT_SUGGESTION_LIMIT]
    )
    compact_rows = []
    if compact_query:
        compact_rows = list(
            apply_compact_text_search(base_queryset, compact_query, ["sku", "name"])
            .values_list("pk", "sku", "name", "supplier", "supplier_ref__name")
            .order_by("name")[:PRODUCT_SUGGESTION_LIMIT]
        )

    # For pricing resolution if request/company is available
    company = None
    if request:
        from core.services.company_context import get_active_company
        company = get_active_company(request)

    from core.services import pricing

    # Pre-fetch all product IDs for a single bulk query
    all_product_ids = set()
    for product_id, sku, name, supplier_text, supplier_ref_name in [
        *exact_rows, *prefix_rows, *parsed_rows, *compact_rows,
    ]:
        all_product_ids.add(product_id)

    # Bulk fetch product prices
    product_price_map = {}
    if all_product_ids:
        for p in base_queryset.filter(pk__in=all_product_ids).only("id", "price"):
            product_price_map[p.id] = p.price

    # Resolve price list if company context is available
    price_list = None
    price_list_item_map = {}
    if company:
        try:
            price_list = pricing.resolve_effective_price_list(company=company)
            if price_list:
                price_list_item_map = pricing.build_price_list_item_map(
                    price_list, list(all_product_ids)
                )
        except Exception:
            pass

    for product_id, sku, name, supplier_text, supplier_ref_name in [
        *exact_rows,
        *prefix_rows,
        *parsed_rows,
        *compact_rows,
    ]:
        supplier = supplier_ref_name or supplier_text or "-"

        # Determine price: price list > product default
        product_price = product_price_map.get(product_id)
        if price_list_item_map and product_id in price_list_item_map:
            product_price = price_list_item_map[product_id].price

        extra_payload = {
            "target_value": product_id,
            "input_value": f"{sku} - {name}",
        }
        if product_price is not None:
            extra_payload["price"] = str(product_price)

        _append_suggestion(
            items,
            sku,
            name,
            meta=f"SKU {sku} · {supplier}",
            kind="product",
            **extra_payload
        )
    return _unique_trim_suggestions(items, limit=PRODUCT_SUGGESTION_LIMIT)



def _suggest_admin_categories(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    for cat in apply_parsed_text_search(
        Category.objects.all(),
        parsed_query,
        ["name", "slug"],
        order_by_similarity=False,
    ).values_list("name", flat=True).order_by("name")[:8]:
        _append_suggestion(items, cat, f"Categoria: {cat}", meta="Categorias", kind="category")
    return _unique_trim_suggestions(items)


def _suggest_admin_clients(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    rows = (
        apply_parsed_text_search(
            ClientProfile.objects.all(),
            parsed_query,
            ["company_name", "user__username", "cuit_dni", "user__email"],
            order_by_similarity=False,
        )
        .select_related("user")
        .values_list("company_name", "user__username", "cuit_dni")
        .order_by("company_name")[:8]
    )
    for company_name, username, cuit in rows:
        value = company_name or username or cuit
        meta_bits = [bit for bit in [username, cuit] if bit]
        _append_suggestion(items, value, company_name or username, meta=" · ".join(meta_bits), kind="client")
    return _unique_trim_suggestions(items)


def _suggest_admin_orders(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items

    numeric_terms = set()
    if parsed_query.get("raw", "").isdigit():
        numeric_terms.add(int(parsed_query["raw"]))
    for term in [*parsed_query.get("phrases", []), *parsed_query.get("include_terms", [])]:
        if str(term).isdigit():
            numeric_terms.add(int(term))
    for numeric in list(numeric_terms)[:3]:
        for order_id in Order.objects.filter(pk=numeric).values_list("pk", flat=True)[:1]:
            _append_suggestion(items, str(order_id), f"Pedido #{order_id}", meta="Pedido", kind="order")

    rows = (
        apply_parsed_text_search(
            Order.objects.all(),
            parsed_query,
            ["user__username", "client_company", "client_cuit"],
            order_by_similarity=False,
        )
        .select_related("user")
        .values_list("pk", "user__username", "client_company", "client_cuit")
        .order_by("-created_at")[:8]
    )
    for order_id, username, company, cuit in rows:
        display_name = company or username or f"Pedido #{order_id}"
        meta = " · ".join(bit for bit in [f"#{order_id}", username, cuit] if bit)
        _append_suggestion(items, display_name, display_name, meta=meta, kind="order")
    return _unique_trim_suggestions(items)


def _suggest_admin_suppliers(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    for name in apply_parsed_text_search(
        Supplier.objects.all(),
        parsed_query,
        ["name", "normalized_name", "slug"],
        order_by_similarity=False,
    ).values_list("name", flat=True).order_by("name")[:8]:
        _append_suggestion(items, name, name, meta="Proveedor", kind="supplier")
    return _unique_trim_suggestions(items)


def _suggest_admin_payments(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    items.extend(_suggest_admin_clients(query))

    numeric_terms = set()
    if parsed_query.get("raw", "").isdigit():
        numeric_terms.add(int(parsed_query["raw"]))
    for term in [*parsed_query.get("phrases", []), *parsed_query.get("include_terms", [])]:
        if str(term).isdigit():
            numeric_terms.add(int(term))
    for numeric in list(numeric_terms)[:4]:
        for order_id in Order.objects.filter(pk=numeric).values_list("pk", flat=True)[:1]:
            _append_suggestion(items, str(order_id), f"Pedido #{order_id}", meta="Pedido", kind="order")

    references = (
        apply_parsed_text_search(
            ClientPayment.objects.all(),
            parsed_query,
            ["reference", "notes", "client_profile__company_name", "client_profile__user__username"],
            order_by_similarity=False,
        )
        .exclude(reference="")
        .values_list("reference", flat=True)
        .order_by("-paid_at")[:6]
    )
    for ref in references:
        _append_suggestion(items, ref, f"Ref: {ref}", meta="Pago", kind="payment")
    return _unique_trim_suggestions(items)


def _suggest_admin_clamp_requests(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    rows = (
        apply_parsed_text_search(
            ClampMeasureRequest.objects.all(),
            parsed_query,
            ["client_name", "client_email", "generated_code", "description"],
            order_by_similarity=False,
        )
        .values_list("client_name", "generated_code", "description")
        .order_by("-created_at")[:8]
    )
    for client_name, code, description in rows:
        value = code or description or client_name
        meta = " · ".join(bit for bit in [client_name, code] if bit)
        _append_suggestion(items, value, description or code or client_name, meta=meta, kind="clamp_request")
    return _unique_trim_suggestions(items)


def _suggest_admin_users(query):
    items = []
    parsed_query = _parse_suggestion_query(query)
    if not parsed_query.get("raw"):
        return items
    rows = (
        apply_parsed_text_search(
            User.objects.filter(is_staff=True),
            parsed_query,
            ["username", "first_name", "last_name", "email"],
            order_by_similarity=False,
        )
        .values_list("username", "first_name", "last_name", "email")
        .order_by("username")[:8]
    )
    for username, first_name, last_name, email in rows:
        full_name = " ".join(part for part in [first_name, last_name] if part).strip()
        _append_suggestion(items, username, username, meta=full_name or email, kind="admin_user")
    return _unique_trim_suggestions(items)


@require_GET
def search_suggestions(request):
    """
    Shared search suggestions endpoint for catalog and admin panel.
    """
    query = sanitize_search_token(request.GET.get("q", ""))
    if len(query) < 2:
        return JsonResponse({"suggestions": []})

    scope = _normalize_search_scope(request.GET.get("scope"))
    is_admin_scope = scope.startswith("admin_")

    if is_admin_scope and (not request.user.is_authenticated or not request.user.is_staff):
        return JsonResponse({"suggestions": []}, status=403)

    cache_scope = scope or ("admin_fallback" if is_admin_scope else "catalog_fallback")
    cache_key = f"search_suggest:{cache_scope}:{query.lower()[:80]}"
    suggestions = cache.get(cache_key)

    if suggestions is None:
        if scope == "catalog":
            suggestions = _suggest_catalog(query)
        elif scope in {"admin_products", "admin_supplier_products"}:
            suggestions = _suggest_admin_products(query, request=request)
        elif scope == "admin_categories":
            suggestions = _suggest_admin_categories(query)
        elif scope == "admin_clients":
            suggestions = _suggest_admin_clients(query)
        elif scope == "admin_orders":
            suggestions = _suggest_admin_orders(query)
        elif scope == "admin_suppliers":
            suggestions = _suggest_admin_suppliers(query)
        elif scope == "admin_payments":
            suggestions = _suggest_admin_payments(query)
        elif scope == "admin_clamp_requests":
            suggestions = _suggest_admin_clamp_requests(query)
        elif scope == "admin_admins":
            suggestions = _suggest_admin_users(query)
        else:
            # Safe fallback by context.
            if request.user.is_authenticated and request.user.is_staff:
                suggestions = _suggest_admin_products(query, request=request)
            else:
                suggestions = _suggest_catalog(query)
        cache.set(cache_key, suggestions, 90)

    return JsonResponse({"suggestions": suggestions})
