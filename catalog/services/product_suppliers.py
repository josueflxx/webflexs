"""Transactional product/supplier offers with immutable cost history."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from catalog.models import (
    ProductDuplicateReview,
    ProductSupplier,
    SupplierCostHistory,
)


MONEY_QUANTUM = Decimal("0.0001")
PERCENT_BASE = Decimal("100")


def _decimal(value, field_name, default="0"):
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Ingresa un numero decimal valido."}) from exc
    if result < 0:
        raise ValidationError({field_name: "El valor no puede ser negativo."})
    return result


def calculate_final_cost(cost, discount_percentage=0, bonus_percentage=0, tax_percentage=0):
    cost = _decimal(cost, "current_cost")
    discount = _decimal(discount_percentage, "discount_percentage")
    bonus = _decimal(bonus_percentage, "bonus_percentage")
    tax = _decimal(tax_percentage, "tax_percentage")
    for field_name, value in (
        ("discount_percentage", discount),
        ("bonus_percentage", bonus),
        ("tax_percentage", tax),
    ):
        if value > PERCENT_BASE:
            raise ValidationError({field_name: "El porcentaje no puede superar 100."})
    result = cost
    result *= (PERCENT_BASE - discount) / PERCENT_BASE
    result *= (PERCENT_BASE - bonus) / PERCENT_BASE
    result *= (PERCENT_BASE + tax) / PERCENT_BASE
    return result.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _queue_supplier_code_conflict(product, conflicting_product, supplier, supplier_code):
    first_id, second_id = sorted((product.pk, conflicting_product.pk))
    review, created = ProductDuplicateReview.objects.get_or_create(
        primary_product_id=first_id,
        candidate_product_id=second_id,
        reason=ProductDuplicateReview.REASON_SUPPLIER_CODE,
        defaults={
            "confidence": 100,
            "evidence": {
                "supplier_id": supplier.pk,
                "supplier": supplier.name,
                "supplier_code": supplier_code,
            },
        },
    )
    if not created and review.status == ProductDuplicateReview.STATUS_PENDING:
        review.confidence = 100
        review.evidence = {
            "supplier_id": supplier.pk,
            "supplier": supplier.name,
            "supplier_code": supplier_code,
        }
        review.save(update_fields=["confidence", "evidence", "updated_at"])
    return review


def upsert_product_supplier_offer(
    *,
    product,
    supplier,
    current_cost=None,
    currency=ProductSupplier.CURRENCY_ARS,
    supplier_code="",
    supplier_description="",
    discount_percentage=0,
    bonus_percentage=0,
    tax_percentage=0,
    minimum_purchase_quantity=1,
    is_available=True,
    lead_time_days=0,
    price_list_date=None,
    source="manual",
    source_file="",
    source_row=None,
    import_execution=None,
    changed_by=None,
    reason="",
    is_preferred=None,
    status=ProductSupplier.STATUS_ACTIVE,
    match_confidence=100,
    match_method="",
    notes="",
):
    """Create/update an offer and append history only when its cost changes."""
    if not product or not getattr(product, "pk", None):
        raise ValidationError("El producto debe estar guardado.")
    if not supplier or not getattr(supplier, "pk", None):
        raise ValidationError("El proveedor debe estar guardado.")

    normalized_code = ProductSupplier.normalize_supplier_code(supplier_code)
    if normalized_code:
        conflict = (
            ProductSupplier.objects.filter(
                supplier=supplier,
                normalized_supplier_code=normalized_code,
            )
            .exclude(product=product)
            .select_related("product")
            .first()
        )
        if conflict:
            _queue_supplier_code_conflict(product, conflict.product, supplier, supplier_code)
            raise ValidationError(
                f'El codigo "{supplier_code}" ya identifica al producto {conflict.product.sku}; '
                "se creo una revision de duplicado y no se modificaron productos."
            )

    cost = _decimal(product.cost if current_cost is None else current_cost, "current_cost")
    discount = _decimal(discount_percentage, "discount_percentage")
    bonus = _decimal(bonus_percentage, "bonus_percentage")
    tax = _decimal(tax_percentage, "tax_percentage")
    final_cost = calculate_final_cost(cost, discount, bonus, tax)
    currency = str(currency or ProductSupplier.CURRENCY_ARS).strip().upper()
    if currency not in dict(ProductSupplier.CURRENCY_CHOICES):
        raise ValidationError({"currency": "Moneda no soportada."})

    with transaction.atomic():
        existing = (
            ProductSupplier.objects.select_for_update()
            .filter(product=product, supplier=supplier)
            .first()
        )
        previous_cost = existing.current_cost if existing else None
        if is_preferred is None:
            is_preferred = bool(
                (existing and existing.is_preferred)
                or product.supplier_ref_id == supplier.pk
                or not ProductSupplier.objects.filter(product=product, is_preferred=True).exists()
            )
        if is_preferred:
            ProductSupplier.objects.filter(product=product, is_preferred=True).exclude(
                pk=getattr(existing, "pk", None)
            ).update(is_preferred=False)

        offer = existing or ProductSupplier(product=product, supplier=supplier)
        offer.supplier_code = supplier_code
        offer.supplier_description = str(supplier_description or "").strip()
        offer.current_cost = cost
        offer.currency = currency
        offer.discount_percentage = discount
        offer.bonus_percentage = bonus
        offer.tax_percentage = tax
        offer.final_cost = final_cost
        offer.minimum_purchase_quantity = max(1, int(minimum_purchase_quantity or 1))
        offer.is_available = bool(is_available)
        offer.lead_time_days = max(0, int(lead_time_days or 0))
        offer.price_list_date = price_list_date
        offer.source_file = str(source_file or "")[:255]
        offer.source_row = source_row
        offer.status = status
        offer.is_preferred = bool(is_preferred)
        offer.match_confidence = int(match_confidence)
        offer.match_method = str(match_method or "")[:50]
        offer.notes = str(notes or "")
        offer.save()

        cost_changed = previous_cost is None or previous_cost != cost
        history = None
        if cost_changed:
            difference_amount = cost - previous_cost if previous_cost is not None else Decimal("0")
            difference_percentage = (
                (difference_amount / previous_cost * PERCENT_BASE).quantize(
                    MONEY_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
                if previous_cost not in (None, Decimal("0"))
                else None
            )
            history = SupplierCostHistory.objects.create(
                product_supplier=offer,
                previous_cost=previous_cost,
                new_cost=cost,
                difference_amount=difference_amount,
                difference_percentage=difference_percentage,
                currency=currency,
                source=str(source or "manual")[:50],
                source_file=str(source_file or "")[:255],
                source_row=source_row,
                import_execution=import_execution,
                changed_by=changed_by,
                reason=str(reason or "")[:255],
            )

        if offer.is_preferred:
            legacy_updates = []
            if product.supplier_ref_id != supplier.pk:
                product.supplier_ref = supplier
                legacy_updates.append("supplier_ref")
            if product.supplier != supplier.name:
                product.supplier = supplier.name
                legacy_updates.append("supplier")
            if product.cost != cost:
                product.cost = cost
                legacy_updates.append("cost")
            if legacy_updates:
                legacy_updates.append("updated_at")
                product.save(update_fields=legacy_updates)

    return offer, history


def get_preferred_supplier_offer(product):
    return (
        ProductSupplier.objects.filter(product=product, is_preferred=True)
        .select_related("supplier")
        .first()
    )


def set_preferred_supplier_preserving_terms(
    *,
    product,
    supplier,
    current_cost,
    supplier_code=None,
    source="manual",
    changed_by=None,
    reason="",
    match_method="manual",
    source_file="",
    source_row=None,
):
    """Promote a supplier without erasing commercial terms already recorded for it."""
    offer = ProductSupplier.objects.filter(product=product, supplier=supplier).first()
    return upsert_product_supplier_offer(
        product=product,
        supplier=supplier,
        current_cost=current_cost,
        currency=offer.currency if offer else ProductSupplier.CURRENCY_ARS,
        supplier_code=(
            offer.supplier_code
            if supplier_code is None and offer
            else str(supplier_code or "")
        ),
        supplier_description=offer.supplier_description if offer else "",
        discount_percentage=offer.discount_percentage if offer else 0,
        bonus_percentage=offer.bonus_percentage if offer else 0,
        tax_percentage=offer.tax_percentage if offer else 0,
        minimum_purchase_quantity=offer.minimum_purchase_quantity if offer else 1,
        is_available=offer.is_available if offer else True,
        lead_time_days=offer.lead_time_days if offer else 0,
        price_list_date=offer.price_list_date if offer else None,
        source=source,
        source_file=source_file,
        source_row=source_row,
        changed_by=changed_by,
        reason=reason,
        is_preferred=True,
        status=offer.status if offer else ProductSupplier.STATUS_ACTIVE,
        match_confidence=offer.match_confidence if offer else 100,
        match_method=offer.match_method if offer else match_method,
        notes=offer.notes if offer else "",
    )


def sync_preferred_supplier_cost(
    product,
    new_cost,
    *,
    source="manual",
    changed_by=None,
    reason="",
):
    """Record a legacy Product.cost edit against its preferred supplier when available."""
    offer = get_preferred_supplier_offer(product)
    supplier = offer.supplier if offer else product.supplier_ref
    if not supplier:
        return None, None
    return set_preferred_supplier_preserving_terms(
        product=product,
        supplier=supplier,
        current_cost=new_cost,
        source=source,
        changed_by=changed_by,
        reason=reason,
        match_method=offer.match_method if offer else "legacy_supplier_ref",
    )
