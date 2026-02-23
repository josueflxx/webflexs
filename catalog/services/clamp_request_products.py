"""
Helpers to materialize and publish custom clamp measure requests as products.
"""
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from catalog.models import Category, ClampMeasureRequest, ClampSpecs, Product
from catalog.services.clamp_quoter import calculate_clamp_quote


ABRAZADERAS_CATEGORY_NAME = "ABRAZADERAS"
GENERATED_SOURCE_KEY = "clamp_request"


def _safe_decimal(value, fallback="0.00"):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(fallback))


def _build_base_sku(clamp_request):
    raw_code = str(clamp_request.generated_code or "").strip().upper()
    if not raw_code:
        prefix = "ABT" if clamp_request.clamp_type == "trefilada" else "ABL"
        raw_code = f"{prefix}REQ{clamp_request.pk}"

    compact = re.sub(r"[^A-Z0-9/_-]", "", raw_code)
    if not compact:
        compact = f"REQ{clamp_request.pk}"
    return compact[:50]


def _build_unique_sku(base_sku, exclude_product_id=None):
    base = str(base_sku or "").strip()[:50] or "REQ"
    if not Product.objects.filter(sku=base).exclude(pk=exclude_product_id).exists():
        return base

    counter = 1
    while counter < 10000:
        suffix = f"-R{counter}"
        candidate = f"{base[: max(1, 50 - len(suffix))]}{suffix}"
        if not Product.objects.filter(sku=candidate).exclude(pk=exclude_product_id).exists():
            return candidate
        counter += 1

    return f"REQ-{timezone.now().strftime('%Y%m%d%H%M%S')}"[:50]


def _is_generated_from_request(product, clamp_request_id):
    attrs = product.attributes if isinstance(product.attributes, dict) else {}
    return (
        attrs.get("source") == GENERATED_SOURCE_KEY
        and int(attrs.get("clamp_request_id") or 0) == int(clamp_request_id)
    )


def _ensure_clamp_specs_values(
    product,
    *,
    clamp_type,
    diameter,
    width_mm,
    length_mm,
    profile_type,
):
    specs, created = ClampSpecs.objects.get_or_create(
        product=product,
        defaults={
            "fabrication": str(clamp_type or "").upper(),
            "diameter": diameter,
            "width": width_mm,
            "length": length_mm,
            "shape": profile_type,
            "parse_confidence": 100,
            "parse_warnings": [],
            "manual_override": True,
        },
    )

    if created:
        return specs

    changed = False
    target_values = {
        "fabrication": str(clamp_type or "").upper(),
        "diameter": diameter,
        "width": width_mm,
        "length": length_mm,
        "shape": profile_type,
        "manual_override": True,
    }
    for field, expected in target_values.items():
        if getattr(specs, field) != expected:
            setattr(specs, field, expected)
            changed = True

    if changed:
        specs.save(update_fields=list(target_values.keys()) + ["updated_at"])
    return specs


def _ensure_clamp_specs(product, clamp_request):
    return _ensure_clamp_specs_values(
        product,
        clamp_type=clamp_request.clamp_type,
        diameter=clamp_request.diameter,
        width_mm=clamp_request.width_mm,
        length_mm=clamp_request.length_mm,
        profile_type=clamp_request.profile_type,
    )


def _ensure_abrazaderas_category():
    root_exact = (
        Category.objects.filter(name__iexact=ABRAZADERAS_CATEGORY_NAME, parent__isnull=True)
        .order_by("order", "name", "id")
        .first()
    )
    if root_exact:
        category = root_exact
    else:
        category = (
            Category.objects.filter(name__icontains="ABRAZADERA", parent__isnull=True)
            .order_by("order", "name", "id")
            .first()
        )
        if not category:
            category = Category.objects.create(
                name=ABRAZADERAS_CATEGORY_NAME,
                is_active=True,
                order=10,
            )

    if not category.is_active:
        category.is_active = True
        category.save(update_fields=["is_active", "updated_at"])
    return category


def _find_exact_match_by_specs(clamp_request):
    return (
        Product.objects.select_related("category")
        .prefetch_related("categories")
        .filter(
            clamp_specs__fabrication=clamp_request.clamp_type.upper(),
            clamp_specs__diameter=clamp_request.diameter,
            clamp_specs__width=clamp_request.width_mm,
            clamp_specs__length=clamp_request.length_mm,
            clamp_specs__shape=clamp_request.profile_type,
        )
        .order_by("-is_active", "name", "id")
        .first()
    )


def _find_exact_match_by_values(
    *,
    clamp_type,
    diameter,
    width_mm,
    length_mm,
    profile_type,
):
    return (
        Product.objects.select_related("category")
        .prefetch_related("categories")
        .filter(
            clamp_specs__fabrication=str(clamp_type or "").upper(),
            clamp_specs__diameter=diameter,
            clamp_specs__width=width_mm,
            clamp_specs__length=length_mm,
            clamp_specs__shape=profile_type,
        )
        .order_by("-is_active", "name", "id")
        .first()
    )


def _build_generated_product_payload(clamp_request):
    base_cost = _safe_decimal(clamp_request.base_cost).quantize(Decimal("0.01"))
    confirmed_price = _safe_decimal(
        clamp_request.confirmed_price if clamp_request.confirmed_price is not None else clamp_request.estimated_final_price
    ).quantize(Decimal("0.01"))
    quantity = max(int(clamp_request.quantity or 1), 1)
    attrs = {
        "source": GENERATED_SOURCE_KEY,
        "clamp_request_id": clamp_request.pk,
        "generated_code": clamp_request.generated_code or "",
        "created_from_status": clamp_request.status,
    }
    return {
        "name": (clamp_request.description or "ABRAZADERA A MEDIDA")[:255],
        "cost": base_cost,
        "price": confirmed_price,
        "stock": quantity,
        "description": (
            f"Generado desde solicitud de abrazadera a medida #{clamp_request.pk}. "
            f"Cliente: {clamp_request.client_name or '-'}."
        ),
        "attributes": attrs,
    }


def _build_quote_product_payload(
    *,
    quote_result,
    final_price,
    stock,
):
    inputs = quote_result.get("inputs") or {}
    generated_code = str(quote_result.get("generated_code") or "").strip().upper()
    return {
        "name": str(quote_result.get("description") or "ABRAZADERA A MEDIDA")[:255],
        "cost": _safe_decimal(quote_result.get("base_cost")).quantize(Decimal("0.01")),
        "price": _safe_decimal(final_price).quantize(Decimal("0.01")),
        "stock": max(int(stock or 1), 1),
        "description": (
            "Generado desde cotizador de abrazaderas. "
            f"Tipo: {str(inputs.get('clamp_type') or '').upper()} "
            f"{inputs.get('diameter')} x {inputs.get('width_mm')} x {inputs.get('length_mm')} {inputs.get('profile_type')}."
        ),
        "attributes": {
            "source": "clamp_quoter",
            "generated_code": generated_code,
            "clamp_type": inputs.get("clamp_type"),
            "diameter": inputs.get("diameter"),
            "width_mm": inputs.get("width_mm"),
            "length_mm": inputs.get("length_mm"),
            "profile_type": inputs.get("profile_type"),
            "is_zincated": bool(inputs.get("is_zincated")),
        },
    }


def _build_quote_payload(clamp_request):
    return {
        "client_name": clamp_request.client_name,
        "dollar_rate": clamp_request.dollar_rate,
        "steel_price_usd": clamp_request.steel_price_usd,
        "supplier_discount_pct": clamp_request.supplier_discount_pct,
        "general_increase_pct": clamp_request.general_increase_pct,
        "clamp_type": clamp_request.clamp_type,
        "is_zincated": clamp_request.is_zincated,
        "diameter": clamp_request.diameter,
        "width_mm": clamp_request.width_mm,
        "length_mm": clamp_request.length_mm,
        "profile_type": clamp_request.profile_type,
    }


def _calculate_facturacion_price(clamp_request):
    """
    Return (base_cost, facturacion_price) for a request.
    Falls back to stored values if the quote cannot be recalculated.
    """
    fallback_base = _safe_decimal(clamp_request.base_cost).quantize(Decimal("0.01"))
    fallback_price = _safe_decimal(
        clamp_request.confirmed_price if clamp_request.confirmed_price is not None else clamp_request.estimated_final_price
    ).quantize(Decimal("0.01"))
    # If admin already confirmed "Facturacion", keep that exact confirmed value.
    if (
        str(clamp_request.confirmed_price_list or "").strip().lower() == "facturacion"
        and clamp_request.confirmed_price is not None
        and _safe_decimal(clamp_request.confirmed_price) > 0
    ):
        return fallback_base, _safe_decimal(clamp_request.confirmed_price).quantize(Decimal("0.01"))

    try:
        quote = calculate_clamp_quote(_build_quote_payload(clamp_request))
        row_map = {row["key"]: row for row in quote.get("price_rows", [])}
        facturacion_row = row_map.get("facturacion")
        facturacion_price = _safe_decimal(
            facturacion_row["final_price"] if facturacion_row else fallback_price
        ).quantize(Decimal("0.01"))
        return _safe_decimal(quote.get("base_cost", fallback_base)).quantize(Decimal("0.01")), facturacion_price
    except Exception:
        return fallback_base, fallback_price


@transaction.atomic
def create_or_update_quote_product(
    *,
    quote_result,
    price_list_key="facturacion",
    supplier_name="COTIZADOR",
    stock=1,
    activate_product=True,
):
    """
    Create/update a catalog product directly from cotizador output.
    Returns (product, created, selected_price_row).
    """
    if not isinstance(quote_result, dict):
        raise ValueError("No se pudo leer el resultado del cotizador.")

    inputs = quote_result.get("inputs") or {}
    clamp_type = str(inputs.get("clamp_type", "")).strip().lower()
    diameter = str(inputs.get("diameter", "")).strip()
    profile_type = str(inputs.get("profile_type", "")).strip().upper()
    try:
        width_mm = int(inputs.get("width_mm"))
        length_mm = int(inputs.get("length_mm"))
    except (TypeError, ValueError):
        raise ValueError("No se pudieron leer las medidas de la abrazadera.")

    if clamp_type not in {"trefilada", "laminada"}:
        raise ValueError("Tipo de abrazadera invalido para crear producto.")
    if not diameter:
        raise ValueError("Diametro invalido para crear producto.")
    if profile_type not in {"PLANA", "SEMICURVA", "CURVA"}:
        raise ValueError("Forma invalida para crear producto.")

    row_map = {str(row.get("key", "")).strip(): row for row in quote_result.get("price_rows", [])}
    selected_row = row_map.get(str(price_list_key or "").strip()) or row_map.get("facturacion")
    if not selected_row:
        raise ValueError("No se encontro la lista seleccionada para crear el producto.")

    payload = _build_quote_product_payload(
        quote_result=quote_result,
        final_price=selected_row.get("final_price"),
        stock=stock,
    )

    category = _ensure_abrazaderas_category()
    product = _find_exact_match_by_values(
        clamp_type=clamp_type,
        diameter=diameter,
        width_mm=width_mm,
        length_mm=length_mm,
        profile_type=profile_type,
    )
    created = False

    if not product:
        generated_code = str(quote_result.get("generated_code", "")).strip().upper()
        fallback_code = f"{'ABT' if clamp_type == 'trefilada' else 'ABL'}Q{width_mm}{length_mm}{profile_type[:1]}"
        sku = _build_unique_sku(generated_code or fallback_code)
        product = Product.objects.create(
            sku=sku,
            name=payload["name"],
            supplier=supplier_name,
            description=payload["description"],
            cost=payload["cost"],
            price=payload["price"],
            stock=payload["stock"],
            is_active=bool(activate_product),
            category=category,
            attributes=payload["attributes"],
        )
        created = True
    else:
        update_fields = []
        existing_attrs = product.attributes if isinstance(product.attributes, dict) else {}
        merged_attrs = dict(existing_attrs)
        merged_attrs.update(payload["attributes"])
        if product.name != payload["name"]:
            product.name = payload["name"]
            update_fields.append("name")
        if product.supplier != supplier_name:
            product.supplier = supplier_name
            update_fields.append("supplier")
        if product.description != payload["description"]:
            product.description = payload["description"]
            update_fields.append("description")
        if product.cost != payload["cost"]:
            product.cost = payload["cost"]
            update_fields.append("cost")
        if product.price != payload["price"]:
            product.price = payload["price"]
            update_fields.append("price")
        if product.stock != payload["stock"]:
            product.stock = payload["stock"]
            update_fields.append("stock")
        if activate_product and not product.is_active:
            product.is_active = True
            update_fields.append("is_active")
        if not product.category_id:
            product.category = category
            update_fields.append("category")
        if product.attributes != merged_attrs:
            product.attributes = merged_attrs
            update_fields.append("attributes")
        if update_fields:
            product.save(update_fields=update_fields + ["updated_at"])

    if not product.categories.filter(pk=category.pk).exists():
        product.categories.add(category)

    _ensure_clamp_specs_values(
        product,
        clamp_type=clamp_type,
        diameter=diameter,
        width_mm=width_mm,
        length_mm=length_mm,
        profile_type=profile_type,
    )
    return product, created, selected_row


@transaction.atomic
def get_or_create_request_product(clamp_request):
    """
    Return product linked to a measure request.
    If needed, create an internal product (hidden from catalog by default).
    """
    if clamp_request.linked_product_id:
        product = Product.objects.filter(pk=clamp_request.linked_product_id).first()
        if product:
            if _is_generated_from_request(product, clamp_request.pk):
                # If already published to catalog, keep catalog pricing untouched.
                published = bool(
                    clamp_request.published_to_catalog_at
                    and product.is_visible_in_catalog(include_uncategorized=False)
                )
                if not published:
                    payload = _build_generated_product_payload(clamp_request)
                    updates = []
                    for field in ("name", "cost", "price", "stock", "description", "attributes"):
                        value = payload[field]
                        if getattr(product, field) != value:
                            setattr(product, field, value)
                            updates.append(field)
                    if updates:
                        product.save(update_fields=updates + ["updated_at"])
            _ensure_clamp_specs(product, clamp_request)
            return product, False

    matching_product = _find_exact_match_by_specs(clamp_request)
    if matching_product:
        clamp_request.linked_product = matching_product
        update_fields = ["linked_product", "updated_at"]
        if matching_product.is_visible_in_catalog(include_uncategorized=False):
            clamp_request.exists_in_catalog = True
            if not clamp_request.published_to_catalog_at:
                clamp_request.published_to_catalog_at = timezone.now()
            update_fields.extend(["exists_in_catalog", "published_to_catalog_at"])
        clamp_request.save(update_fields=update_fields)
        return matching_product, False

    payload = _build_generated_product_payload(clamp_request)
    sku = _build_unique_sku(_build_base_sku(clamp_request))
    product = Product.objects.create(
        sku=sku,
        name=payload["name"],
        supplier="COTIZADOR",
        description=payload["description"],
        cost=payload["cost"],
        price=payload["price"],
        stock=payload["stock"],
        is_active=False,
        attributes=payload["attributes"],
    )
    _ensure_clamp_specs(product, clamp_request)

    clamp_request.linked_product = product
    clamp_request.save(update_fields=["linked_product", "updated_at"])
    return product, True


@transaction.atomic
def publish_clamp_request_product(clamp_request):
    """
    Publish a request-linked clamp product into main catalog under ABRAZADERAS.
    """
    product, created = get_or_create_request_product(clamp_request)
    was_visible = product.is_visible_in_catalog(include_uncategorized=False)
    category = _ensure_abrazaderas_category()
    recalculated_base_cost, facturacion_price = _calculate_facturacion_price(clamp_request)

    update_fields = []
    if not product.is_active:
        product.is_active = True
        update_fields.append("is_active")
    if not product.category_id:
        product.category = category
        update_fields.append("category")
    if product.price != facturacion_price:
        product.price = facturacion_price
        update_fields.append("price")
    if product.cost != recalculated_base_cost:
        product.cost = recalculated_base_cost
        update_fields.append("cost")
    if update_fields:
        product.save(update_fields=update_fields + ["updated_at"])

    if not product.categories.filter(pk=category.pk).exists():
        product.categories.add(category)

    _ensure_clamp_specs(product, clamp_request)

    now_visible = product.is_visible_in_catalog(include_uncategorized=False)
    request_updates = []
    if clamp_request.linked_product_id != product.pk:
        clamp_request.linked_product = product
        request_updates.append("linked_product")
    if now_visible and not clamp_request.published_to_catalog_at:
        clamp_request.published_to_catalog_at = timezone.now()
        request_updates.append("published_to_catalog_at")
    if now_visible and not clamp_request.exists_in_catalog:
        clamp_request.exists_in_catalog = True
        request_updates.append("exists_in_catalog")

    if request_updates:
        clamp_request.save(update_fields=request_updates + ["updated_at"])

    published_now = (not was_visible) and now_visible
    return product, created, published_now
