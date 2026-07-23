"""Product contract and mutations for the external mass editor."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalog.models import Category, Product, ProductSupplier, Supplier
from catalog.services.product_suppliers import (
    set_preferred_supplier_preserving_terms,
    sync_preferred_supplier_cost,
)
from catalog.services.supplier_sync import clean_supplier_name, ensure_supplier


MONEY_QUANTUM = Decimal("0.01")
MAX_SELECTION_IDS = 50000
PRICE_FIELDS = {"cost", "salePrice", "price", "margin"}


class ExternalEditorConflict(Exception):
    """The product changed after the editor loaded it."""


def _bool_param(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "active"}:
        return True
    if normalized in {"0", "false", "no", "off", "inactive"}:
        return False
    return None


def _decimal(value, field_name):
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Ingresa un numero decimal valido."}) from exc
    if parsed < 0:
        raise ValidationError({field_name: "El valor no puede ser negativo."})
    return parsed.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _integer(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Ingresa un numero entero valido."}) from exc
    if parsed < 0:
        raise ValidationError({field_name: "El valor no puede ser negativo."})
    return parsed


def build_editor_product_queryset(params):
    """Apply server-side filters shared by list, selection and bulk operations."""
    queryset = Product.objects.select_related(
        "category",
        "category__parent",
        "supplier_ref",
    ).prefetch_related("categories").order_by("sku", "id")

    trash = _bool_param(params.get("trash"))
    if trash is True:
        queryset = queryset.filter(attributes__has_key="editor_deleted_at")
    else:
        queryset = queryset.exclude(attributes__has_key="editor_deleted_at")

    query = str(params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(sku__icontains=query)
            | Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(supplier__icontains=query)
            | Q(supplier_ref__name__icontains=query)
            | Q(filter_1__icontains=query)
        ).distinct()

    code = str(params.get("code") or params.get("sku") or "").strip()
    if code:
        queryset = queryset.filter(sku__icontains=code)

    name = str(params.get("name") or "").strip()
    if name:
        queryset = queryset.filter(name__icontains=name)

    supplier_id = str(params.get("supplierId") or params.get("supplier_id") or "").strip()
    if supplier_id.isdigit():
        queryset = queryset.filter(supplier_ref_id=int(supplier_id))

    supplier = str(params.get("supplier") or "").strip()
    if supplier:
        queryset = queryset.filter(
            Q(supplier__icontains=supplier)
            | Q(supplier_ref__name__icontains=supplier)
            | Q(supplier_offers__supplier__name__icontains=supplier)
        ).distinct()

    category_id = str(params.get("categoryId") or params.get("category_id") or "").strip()
    subcategory_id = str(params.get("subcategoryId") or params.get("subcategory_id") or "").strip()
    selected_category_id = subcategory_id if subcategory_id.isdigit() else category_id
    if selected_category_id.isdigit():
        category = Category.objects.filter(pk=int(selected_category_id)).first()
        if category:
            category_ids = category.get_descendant_ids(include_self=True, only_active=False)
            queryset = queryset.filter(
                Q(category_id__in=category_ids) | Q(categories__id__in=category_ids)
            ).distinct()

    uncategorized = _bool_param(params.get("uncategorized"))
    if uncategorized is True:
        queryset = queryset.filter(category__isnull=True, categories__isnull=True)

    without_supplier = _bool_param(params.get("withoutSupplier") or params.get("without_supplier"))
    if without_supplier is True:
        queryset = queryset.filter(supplier_ref__isnull=True, supplier="")

    stock = str(params.get("stock") or "").strip().lower()
    if stock in {"in", "positive", "with", "available"}:
        queryset = queryset.filter(stock__gt=0)
    elif stock in {"out", "zero", "without"}:
        queryset = queryset.filter(stock=0)
    elif stock in {"negative", "below_zero"}:
        queryset = queryset.filter(stock__lt=0)

    status = str(params.get("status") or params.get("active") or "").strip().lower()
    active = _bool_param(status)
    if active is not None:
        queryset = queryset.filter(is_active=active)

    return queryset


def serialize_editor_product(product):
    primary = product.category
    root_category = primary.parent if primary and primary.parent_id else primary
    subcategory = primary if primary and primary.parent_id else None
    cost = Decimal(product.cost or 0)
    price = Decimal(product.price or 0)
    margin = (
        ((price - cost) / cost * Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if cost > 0
        else Decimal("0")
    )
    attributes = product.attributes if isinstance(product.attributes, dict) else {}
    return {
        "id": product.pk,
        "internalCode": product.sku,
        "name": product.name,
        "description": product.description,
        "cost": product.cost,
        "salePrice": product.price,
        "margin": margin,
        "stock": product.stock,
        "status": "active" if product.is_active else "inactive",
        "categoryId": root_category.pk if root_category else None,
        "categoryName": root_category.name if root_category else "",
        "subcategoryId": subcategory.pk if subcategory else None,
        "subcategoryName": subcategory.name if subcategory else "",
        "categoryIds": [category.pk for category in product.get_linked_categories()],
        "supplierId": product.supplier_ref_id,
        "supplier": product.supplier_ref.name if product.supplier_ref_id else product.supplier,
        "reference": product.filter_1,
        "filter1": product.filter_1,
        "filter2": product.filter_2,
        "filter3": product.filter_3,
        "filter4": product.filter_4,
        "filter5": product.filter_5,
        "vatRate": None,
        "notes": str(attributes.get("editor_notes") or ""),
        "tags": [str(tag) for tag in (attributes.get("editor_tags") or []) if str(tag).strip()],
        "imageUrl": product.image.url if product.image else "",
        "isDeleted": bool(attributes.get("editor_deleted_at")),
        "deletedAt": attributes.get("editor_deleted_at"),
        "updatedAt": product.updated_at.isoformat() if product.updated_at else None,
    }


def _resolve_category(payload):
    has_category = "categoryId" in payload or "category_id" in payload
    has_subcategory = "subcategoryId" in payload or "subcategory_id" in payload
    if not has_category and not has_subcategory:
        return False, None

    raw_category = payload.get("categoryId", payload.get("category_id"))
    raw_subcategory = payload.get("subcategoryId", payload.get("subcategory_id"))
    if raw_category in (None, "") and raw_subcategory in (None, ""):
        return True, None

    selected_raw = raw_subcategory if raw_subcategory not in (None, "") else raw_category
    try:
        selected_id = int(selected_raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"categoryId": "Categoria invalida."}) from exc

    selected = Category.objects.filter(pk=selected_id, is_active=True).select_related("parent").first()
    if not selected:
        raise ValidationError({"categoryId": "La categoria no existe o esta inactiva."})

    if raw_subcategory not in (None, ""):
        if not selected.parent_id:
            raise ValidationError({"subcategoryId": "La subcategoria seleccionada no tiene categoria padre."})
        if raw_category not in (None, "") and selected.parent_id != int(raw_category):
            raise ValidationError({"subcategoryId": "La subcategoria no pertenece a la categoria indicada."})

    return True, selected


def _resolve_supplier(payload):
    has_supplier = "supplierId" in payload or "supplier_id" in payload or "supplier" in payload
    if not has_supplier:
        return False, None

    raw_id = payload.get("supplierId", payload.get("supplier_id"))
    raw_name = clean_supplier_name(payload.get("supplier"))
    if raw_id in (None, "") and not raw_name:
        return True, None
    if raw_id not in (None, ""):
        try:
            supplier_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"supplierId": "Proveedor invalido."}) from exc
        supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
        if not supplier:
            raise ValidationError({"supplierId": "El proveedor no existe o esta inactivo."})
        return True, supplier
    return True, ensure_supplier(raw_name)


def apply_editor_product_patch(*, product, payload, user):
    """Validate and apply one editor patch. Caller owns the transaction and lock."""
    payload = dict(payload)
    if payload.pop("clearCategory", False):
        payload["categoryId"] = None
        payload["subcategoryId"] = None
    if payload.pop("clearSupplier", False):
        payload["supplierId"] = None
        payload["supplier"] = ""

    expected = payload.get("expectedUpdatedAt", payload.get("expected_updated_at"))
    if expected:
        expected_at = parse_datetime(str(expected))
        if not expected_at:
            raise ValidationError({"expectedUpdatedAt": "Fecha de version invalida."})
        if product.updated_at and abs((product.updated_at - expected_at).total_seconds()) > 0.001:
            raise ExternalEditorConflict("El producto fue modificado por otro usuario.")

    update_fields = []
    cost_changed = False

    if "internalCode" in payload or "sku" in payload:
        sku = str(payload.get("internalCode", payload.get("sku")) or "").strip()
        if not sku:
            raise ValidationError({"internalCode": "El SKU no puede estar vacio."})
        if Product.objects.filter(sku__iexact=sku).exclude(pk=product.pk).exists():
            raise ValidationError({"internalCode": "El SKU ya pertenece a otro producto."})
        if product.sku != sku:
            product.sku = sku
            update_fields.append("sku")

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "El nombre no puede estar vacio."})
        if product.name != name:
            product.name = name
            update_fields.append("name")

    if "description" in payload:
        description = str(payload.get("description") or "").strip()
        if product.description != description:
            product.description = description
            update_fields.append("description")

    if "cost" in payload:
        cost = _decimal(payload.get("cost"), "cost")
        if product.cost != cost:
            product.cost = cost
            update_fields.append("cost")
            cost_changed = True

    if "salePrice" in payload or "price" in payload:
        price = _decimal(payload.get("salePrice", payload.get("price")), "salePrice")
        if product.price != price:
            product.price = price
            update_fields.append("price")

    if "margin" in payload and "salePrice" not in payload and "price" not in payload:
        margin = _decimal(payload.get("margin"), "margin")
        price = (Decimal(product.cost or 0) * (Decimal("1") + margin / Decimal("100"))).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if product.price != price:
            product.price = price
            update_fields.append("price")

    if "stock" in payload:
        stock = _integer(payload.get("stock"), "stock")
        if product.stock != stock:
            product.stock = stock
            update_fields.append("stock")

    if "status" in payload or "isActive" in payload or "is_active" in payload:
        raw_status = payload.get("status", payload.get("isActive", payload.get("is_active")))
        active = _bool_param(raw_status)
        if active is None:
            raise ValidationError({"status": "Estado invalido; usa active o inactive."})
        if product.is_active != active:
            product.is_active = active
            update_fields.append("is_active")

    reference_fields = {
        "reference": "filter_1",
        "filter1": "filter_1",
        "filter2": "filter_2",
        "filter3": "filter_3",
        "filter4": "filter_4",
        "filter5": "filter_5",
    }
    for source, target in reference_fields.items():
        if source in payload:
            value = str(payload.get(source) or "").strip()
            if getattr(product, target) != value:
                setattr(product, target, value)
                if target not in update_fields:
                    update_fields.append(target)

    attributes = dict(product.attributes or {})
    attributes_changed = False
    if "isDeleted" in payload:
        if bool(payload.get("isDeleted")):
            attributes["editor_deleted_at"] = timezone.now().isoformat()
            attributes["editor_deleted_by"] = getattr(user, "username", "")
            if product.is_active:
                product.is_active = False
                update_fields.append("is_active")
        else:
            attributes.pop("editor_deleted_at", None)
            attributes.pop("editor_deleted_by", None)
        attributes_changed = True
    if "notes" in payload:
        notes = str(payload.get("notes") or "").strip()
        if attributes.get("editor_notes", "") != notes:
            if notes:
                attributes["editor_notes"] = notes
            else:
                attributes.pop("editor_notes", None)
            attributes_changed = True

    if "tags" in payload:
        raw_tags = payload.get("tags")
        if not isinstance(raw_tags, list):
            raise ValidationError({"tags": "Las etiquetas deben enviarse como una lista."})
        tags = []
        for raw_tag in raw_tags:
            tag = str(raw_tag or "").strip()[:40]
            if tag and tag.casefold() not in {existing.casefold() for existing in tags}:
                tags.append(tag)
        if len(tags) > 20:
            raise ValidationError({"tags": "No puedes asignar mas de 20 etiquetas."})
        if attributes.get("editor_tags", []) != tags:
            if tags:
                attributes["editor_tags"] = tags
            else:
                attributes.pop("editor_tags", None)
            attributes_changed = True

    if attributes_changed:
        product.attributes = attributes
        update_fields.append("attributes")

    category_supplied, category = _resolve_category(payload)
    if category_supplied and product.category_id != getattr(category, "pk", None):
        product.category = category
        update_fields.append("category")

    supplier_supplied, supplier = _resolve_supplier(payload)
    if supplier_supplied:
        supplier_name = supplier.name if supplier else ""
        if product.supplier_ref_id != getattr(supplier, "pk", None):
            product.supplier_ref = supplier
            update_fields.append("supplier_ref")
        if product.supplier != supplier_name:
            product.supplier = supplier_name
            update_fields.append("supplier")

    if update_fields:
        product.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])

    if category_supplied:
        if category:
            product.categories.set([category])
        else:
            product.categories.clear()

    if supplier_supplied:
        if supplier:
            set_preferred_supplier_preserving_terms(
                product=product,
                supplier=supplier,
                current_cost=product.cost,
                source="external_editor",
                changed_by=user,
                reason="Proveedor actualizado desde el editor externo.",
                match_method="external_editor",
            )
        else:
            ProductSupplier.objects.filter(product=product, is_preferred=True).update(is_preferred=False)
    elif cost_changed:
        sync_preferred_supplier_cost(
            product,
            product.cost,
            source="external_editor",
            changed_by=user,
            reason="Costo actualizado desde el editor externo.",
        )

    product.refresh_from_db()
    return product
