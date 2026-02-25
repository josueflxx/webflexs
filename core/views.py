"""
Core app views.
"""
from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.utils import timezone

from accounts.models import ClientProfile, ClientPayment
from catalog.models import Category, ClampMeasureRequest, Product, Supplier
from orders.models import Order
from core.models import UserActivity
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
    return JsonResponse(
        {
            "admins": build_admin_presence_payload(),
            "refresh_seconds": config["refresh_seconds"],
            "online_window_seconds": config["online_window_seconds"],
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


SUGGESTION_LIMIT = 8


def _normalize_search_scope(raw_scope):
    return str(raw_scope or "").strip().lower()


def _append_suggestion(items, value, label=None, meta="", kind=""):
    value = str(value or "").strip()
    if not value:
        return
    items.append(
        {
            "value": value,
            "label": str(label or value).strip(),
            "meta": str(meta or "").strip(),
            "kind": str(kind or "").strip(),
        }
    )


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

    product_rows = (
        Product.catalog_visible()
        .filter(Q(sku__icontains=query) | Q(name__icontains=query))
        .values_list("sku", "name")
        .order_by("name")[:6]
    )
    for sku, name in product_rows:
        _append_suggestion(items, sku, name, meta=f"SKU {sku}", kind="product")

    category_names = (
        Category.objects.filter(is_active=True, name__icontains=query)
        .values_list("name", flat=True)
        .order_by("name")[:4]
    )
    for name in category_names:
        _append_suggestion(items, name, f"Categoria: {name}", meta="Categoria", kind="category")

    return _unique_trim_suggestions(items)


def _suggest_admin_products(query):
    items = []
    rows = (
        Product.objects.filter(
            Q(sku__icontains=query)
            | Q(name__icontains=query)
            | Q(supplier__icontains=query)
            | Q(supplier_ref__name__icontains=query)
        )
        .select_related("supplier_ref")
        .values_list("sku", "name", "supplier", "supplier_ref__name")
        .order_by("name")[:8]
    )
    for sku, name, supplier_text, supplier_ref_name in rows:
        supplier = supplier_ref_name or supplier_text or "-"
        _append_suggestion(items, sku, name, meta=f"{sku} · {supplier}", kind="product")
    return _unique_trim_suggestions(items)


def _suggest_admin_categories(query):
    items = []
    for cat in Category.objects.filter(name__icontains=query).values_list("name", flat=True).order_by("name")[:8]:
        _append_suggestion(items, cat, f"Categoria: {cat}", meta="Categorias", kind="category")
    return _unique_trim_suggestions(items)


def _suggest_admin_clients(query):
    items = []
    rows = (
        ClientProfile.objects.filter(
            Q(company_name__icontains=query)
            | Q(user__username__icontains=query)
            | Q(cuit_dni__icontains=query)
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

    if query.isdigit():
        for order_id in Order.objects.filter(pk=int(query)).values_list("pk", flat=True)[:3]:
            _append_suggestion(items, str(order_id), f"Pedido #{order_id}", meta="Pedido", kind="order")

    rows = (
        Order.objects.filter(
            Q(user__username__icontains=query)
            | Q(client_company__icontains=query)
            | Q(client_cuit__icontains=query)
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
    for name in Supplier.objects.filter(name__icontains=query).values_list("name", flat=True).order_by("name")[:8]:
        _append_suggestion(items, name, name, meta="Proveedor", kind="supplier")
    return _unique_trim_suggestions(items)


def _suggest_admin_payments(query):
    items = []
    items.extend(_suggest_admin_clients(query))

    if query.isdigit():
        for order_id in Order.objects.filter(pk=int(query)).values_list("pk", flat=True)[:4]:
            _append_suggestion(items, str(order_id), f"Pedido #{order_id}", meta="Pedido", kind="order")

    references = (
        ClientPayment.objects.filter(reference__icontains=query)
        .exclude(reference="")
        .values_list("reference", flat=True)
        .order_by("-paid_at")[:6]
    )
    for ref in references:
        _append_suggestion(items, ref, f"Ref: {ref}", meta="Pago", kind="payment")
    return _unique_trim_suggestions(items)


def _suggest_admin_clamp_requests(query):
    items = []
    rows = (
        ClampMeasureRequest.objects.filter(
            Q(client_name__icontains=query)
            | Q(client_email__icontains=query)
            | Q(generated_code__icontains=query)
            | Q(description__icontains=query)
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
    rows = (
        User.objects.filter(
            is_staff=True
        ).filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        ).values_list("username", "first_name", "last_name", "email").order_by("username")[:8]
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
    query = str(request.GET.get("q", "")).strip()
    if len(query) < 2:
        return JsonResponse({"suggestions": []})

    scope = _normalize_search_scope(request.GET.get("scope"))
    is_admin_scope = scope.startswith("admin_")

    if is_admin_scope and (not request.user.is_authenticated or not request.user.is_staff):
        return JsonResponse({"suggestions": []}, status=403)

    if scope == "catalog":
        suggestions = _suggest_catalog(query)
    elif scope in {"admin_products", "admin_supplier_products"}:
        suggestions = _suggest_admin_products(query)
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
            suggestions = _suggest_admin_products(query)
        else:
            suggestions = _suggest_catalog(query)

    return JsonResponse({"suggestions": suggestions})
