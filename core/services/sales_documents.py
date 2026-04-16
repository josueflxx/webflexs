"""Configurable sales-document helpers built on top of existing internal/fiscal flows."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Value, When

from accounts.services.account_movement_service import (
    sync_fiscal_document_account_movement,
    sync_internal_document_account_movement,
)
from core.models import (
    DocumentSeries,
    SALES_BEHAVIOR_COTIZACION,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BEHAVIOR_NOTA_DEBITO,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_PRESUPUESTO,
    SALES_BEHAVIOR_RECIBO,
    SALES_BEHAVIOR_REMITO,
    SALES_BILLING_MODE_AFIP_ONLINE,
    SALES_BILLING_MODE_AFIP_WSFE,
    SALES_BILLING_MODE_INTERNAL_DOCUMENT,
    SALES_BILLING_MODE_MANUAL_FISCAL,
    STOCK_MOVEMENT_IN,
    STOCK_MOVEMENT_OUT,
    STOCK_MOVEMENT_RELEASE,
    STOCK_MOVEMENT_RESERVE,
    FiscalDocument,
    InternalDocument,
    SalesDocumentType,
    StockMovement,
)
INTERNAL_DOC_BEHAVIOR_MAP = {
    "COT": SALES_BEHAVIOR_COTIZACION,
    "PED": SALES_BEHAVIOR_PEDIDO,
    "REM": SALES_BEHAVIOR_REMITO,
    "REC": SALES_BEHAVIOR_RECIBO,
    "AJU": SALES_BEHAVIOR_NOTA_DEBITO,
}

FISCAL_DOC_BEHAVIOR_MAP = {
    "FA": SALES_BEHAVIOR_FACTURA,
    "FB": SALES_BEHAVIOR_FACTURA,
    "FC": SALES_BEHAVIOR_FACTURA,
    "NCA": SALES_BEHAVIOR_NOTA_CREDITO,
    "NCB": SALES_BEHAVIOR_NOTA_CREDITO,
    "NCC": SALES_BEHAVIOR_NOTA_CREDITO,
    "NDA": SALES_BEHAVIOR_NOTA_DEBITO,
    "NDB": SALES_BEHAVIOR_NOTA_DEBITO,
    "NDC": SALES_BEHAVIOR_NOTA_DEBITO,
}

BEHAVIOR_STOCK_RULES = {
    SALES_BEHAVIOR_FACTURA: (STOCK_MOVEMENT_OUT, -1, True),
    SALES_BEHAVIOR_REMITO: (STOCK_MOVEMENT_OUT, -1, True),
    SALES_BEHAVIOR_NOTA_CREDITO: (STOCK_MOVEMENT_IN, 1, True),
    SALES_BEHAVIOR_PEDIDO: (STOCK_MOVEMENT_RESERVE, 0, False),
    SALES_BEHAVIOR_PRESUPUESTO: (None, 0, False),
    SALES_BEHAVIOR_COTIZACION: (None, 0, False),
    SALES_BEHAVIOR_RECIBO: (None, 0, False),
    SALES_BEHAVIOR_NOTA_DEBITO: (None, 0, False),
}

ORDER_INTERNAL_ALLOWED_STATUSES = {
    DocumentSeries.DOC_COT: {"draft", "confirmed", "preparing", "shipped", "delivered"},
    DocumentSeries.DOC_PED: {"confirmed", "preparing", "shipped", "delivered"},
    DocumentSeries.DOC_REM: {"shipped", "delivered"},
}


def resolve_sales_document_type(
    *,
    company,
    behavior=None,
    explicit_id=None,
    internal_doc_type="",
    fiscal_doc_type="",
    billing_mode="",
    origin_channel="",
    enabled_only=True,
):
    """Resolve configured document type for a company without duplicating business rules."""
    if not company:
        return None

    queryset = SalesDocumentType.objects.filter(company=company)
    if enabled_only:
        queryset = queryset.filter(enabled=True)

    if explicit_id:
        return queryset.filter(pk=explicit_id).first()

    if behavior:
        queryset = queryset.filter(document_behavior=behavior)
    if internal_doc_type:
        queryset = queryset.filter(internal_doc_type=internal_doc_type)
    if fiscal_doc_type:
        queryset = queryset.filter(fiscal_doc_type=fiscal_doc_type)
    if billing_mode:
        queryset = queryset.filter(billing_mode=billing_mode)
    origin_channel = str(origin_channel or "").strip().lower()
    if not origin_channel:
        return queryset.order_by("-is_default", "display_order", "name").first()

    prioritized_queryset = queryset.filter(
        Q(default_origin_channel="") | Q(default_origin_channel=origin_channel)
    ).annotate(
        _origin_priority=Case(
            When(is_default=True, default_origin_channel=origin_channel, then=Value(0)),
            When(is_default=True, default_origin_channel="", then=Value(1)),
            When(default_origin_channel=origin_channel, then=Value(2)),
            When(default_origin_channel="", then=Value(3)),
            default=Value(9),
            output_field=IntegerField(),
        )
    )
    resolved = prioritized_queryset.order_by("_origin_priority", "display_order", "name").first()
    if resolved:
        return resolved
    return queryset.order_by("-is_default", "display_order", "name").first()


def resolve_sales_document_type_for_internal_doc(*, company, doc_type, origin_channel=""):
    behavior = INTERNAL_DOC_BEHAVIOR_MAP.get(doc_type)
    if not behavior:
        return None
    return resolve_sales_document_type(
        company=company,
        behavior=behavior,
        internal_doc_type=doc_type,
        origin_channel=origin_channel,
    )


def resolve_sales_document_type_for_fiscal_doc(*, company, doc_type, billing_mode="", origin_channel=""):
    behavior = FISCAL_DOC_BEHAVIOR_MAP.get(doc_type)
    if not behavior:
        return None
    return resolve_sales_document_type(
        company=company,
        behavior=behavior,
        fiscal_doc_type=doc_type,
        billing_mode=billing_mode,
        origin_channel=origin_channel,
    )


def reserve_sales_document_number(*, sales_document_type):
    """Reserve and persist one sequential number inside SalesDocumentType."""
    if not sales_document_type:
        raise ValidationError("Tipo de documento invalido.")

    with transaction.atomic():
        locked = SalesDocumentType.objects.select_for_update().get(pk=sales_document_type.pk)
        next_number = int(locked.last_number or 0) + 1
        locked.last_number = next_number
        locked.save(update_fields=["last_number", "updated_at"])
        return next_number


def sync_sales_document_type_counter(*, sales_document_type=None, sales_document_type_id=None, number=None):
    """Keep mirror counter aligned with the real internal/fiscal series."""
    if number in (None, ""):
        return None
    if not sales_document_type_id and sales_document_type is not None:
        sales_document_type_id = sales_document_type.pk
    if not sales_document_type_id:
        return None

    with transaction.atomic():
        locked = SalesDocumentType.objects.select_for_update().filter(pk=sales_document_type_id).first()
        if not locked:
            return None
        normalized_number = int(number or 0)
        if normalized_number > int(locked.last_number or 0):
            locked.last_number = normalized_number
            locked.save(update_fields=["last_number", "updated_at"])
        return locked


def format_sales_document_number(*, sales_document_type, number=None):
    if not sales_document_type:
        return ""
    return sales_document_type.format_number(number=number)


def _collect_order_quantities(order, *, group_equal_products):
    grouped = defaultdict(int)
    rows = []
    products = {}
    for item in order.items.select_related("product").all():
        if not item.product_id:
            continue
        products[item.product_id] = item.product
        quantity = int(item.quantity or 0)
        if quantity <= 0:
            continue
        if group_equal_products:
            grouped[item.product_id] += quantity
        else:
            rows.append((item.product, quantity))
    if group_equal_products:
        rows = [
            (products[product_id], quantity)
            for product_id, quantity in grouped.items()
            if product_id in products
        ]
    return rows


def _apply_stock_delta(*, product_id, delta):
    if not delta:
        return
    StockMovement._meta.get_field("product").remote_field.model.objects.filter(pk=product_id).update(
        stock=F("stock") + delta
    )


def ensure_stock_movements_for_order_document(
    *,
    order,
    company,
    sales_document_type,
    actor=None,
    internal_document=None,
    fiscal_document=None,
):
    """Idempotently create stock movements from one configured document."""
    if not order or not company or not sales_document_type or not sales_document_type.generate_stock_movement:
        return []

    movement_type, direction_sign, mutates_stock = BEHAVIOR_STOCK_RULES.get(
        sales_document_type.document_behavior,
        (None, 0, False),
    )
    if not movement_type:
        return []

    warehouse = sales_document_type.default_warehouse
    rows = _collect_order_quantities(
        order,
        group_equal_products=bool(sales_document_type.group_equal_products),
    )
    movements = []

    with transaction.atomic():
        for idx, (product, quantity) in enumerate(rows, start=1):
            source_prefix = "internal" if internal_document else "fiscal"
            source_doc = internal_document or fiscal_document
            source_key = f"{source_prefix}:{source_doc.pk}:stock:{product.pk}:{idx}"
            defaults = {
                "company": company,
                "warehouse": warehouse,
                "product": product,
                "sales_document_type": sales_document_type,
                "order": order,
                "internal_document": internal_document,
                "fiscal_document": fiscal_document,
                "movement_type": movement_type,
                "quantity": Decimal(quantity).quantize(Decimal("0.001")),
                "notes": f"{sales_document_type.name} generado desde pedido #{order.pk}",
                "created_by": actor if getattr(actor, "is_authenticated", False) else None,
            }
            existing = StockMovement.objects.select_for_update().filter(source_key=source_key).first()
            old_effect = 0
            if existing and mutates_stock:
                old_effect = int(existing.quantity) * direction_sign

            movement, _ = StockMovement.objects.update_or_create(
                source_key=source_key,
                defaults=defaults,
            )
            if mutates_stock:
                new_effect = int(movement.quantity) * direction_sign
                delta = new_effect - old_effect
                _apply_stock_delta(product_id=product.pk, delta=delta)
            movements.append(movement)
    return movements


def ensure_account_adjustment_for_fiscal_document(*, fiscal_document, sales_document_type, actor=None):
    """Backward-compatible wrapper for the unified account-movement service."""
    if not fiscal_document or not sales_document_type:
        return None
    return sync_fiscal_document_account_movement(
        fiscal_document=fiscal_document,
        actor=actor,
    )


def apply_sales_document_type_to_internal_document(*, document, sales_document_type=None, actor=None):
    if not document:
        return None
    if sales_document_type is None and getattr(document, "company_id", None):
        origin_channel = getattr(getattr(document, "order", None), "origin_channel", "")
        sales_document_type = resolve_sales_document_type_for_internal_doc(
            company=document.company,
            doc_type=document.doc_type,
            origin_channel=origin_channel,
        )
    if not sales_document_type:
        return document

    update_fields = []
    if document.sales_document_type_id != sales_document_type.id:
        document.sales_document_type = sales_document_type
        update_fields.append("sales_document_type")
    if update_fields:
        document.save(update_fields=update_fields + ["updated_at"])
    sync_sales_document_type_counter(sales_document_type=document.sales_document_type, number=document.number)

    if document.order_id:
        ensure_stock_movements_for_order_document(
            order=document.order,
            company=document.company,
            sales_document_type=sales_document_type,
            actor=actor,
            internal_document=document,
        )
        try:
            sync_internal_document_account_movement(
                internal_document=document,
                actor=actor,
            )
        except Exception:
            pass
    return document


def apply_sales_document_type_to_fiscal_document(*, document, sales_document_type=None, actor=None):
    if not document:
        return None
    if sales_document_type is None and getattr(document, "company_id", None):
        origin_channel = getattr(getattr(document, "order", None), "origin_channel", "")
        sales_document_type = resolve_sales_document_type_for_fiscal_doc(
            company=document.company,
            doc_type=document.doc_type,
            origin_channel=origin_channel,
        )
    if not sales_document_type:
        return document

    update_fields = []
    if document.sales_document_type_id != sales_document_type.id:
        document.sales_document_type = sales_document_type
        update_fields.append("sales_document_type")
    if update_fields:
        document.save(update_fields=update_fields + ["updated_at"])
    if document.number:
        sync_sales_document_type_counter(sales_document_type=document.sales_document_type, number=document.number)

    if document.order_id:
        ensure_stock_movements_for_order_document(
            order=document.order,
            company=document.company,
            sales_document_type=sales_document_type,
            actor=actor,
            fiscal_document=document,
        )
    try:
        sync_fiscal_document_account_movement(
            fiscal_document=document,
            actor=actor,
        )
    except Exception:
        pass
    return document


def create_fiscal_document_from_sales_type(
    *,
    order,
    sales_document_type,
    actor=None,
    external_system="",
    external_id="",
    external_number="",
    require_invoice_ready=True,
):
    """Bridge configurable document types to the existing fiscal services."""
    from core.services.fiscal_documents import (
        create_local_fiscal_document_from_order,
        register_external_fiscal_document_for_order,
    )

    if not sales_document_type:
        raise ValidationError("Debes seleccionar un tipo de documento comercial.")
    if sales_document_type.billing_mode == SALES_BILLING_MODE_INTERNAL_DOCUMENT:
        raise ValidationError("El tipo seleccionado no genera comprobantes fiscales.")
    if sales_document_type.billing_mode == SALES_BILLING_MODE_AFIP_ONLINE:
        raise ValidationError("AFIP online todavia no esta implementado en este sistema.")
    if not sales_document_type.fiscal_doc_type:
        raise ValidationError("El tipo configurado no tiene tipo fiscal asociado.")
    if not sales_document_type.point_of_sale_id:
        raise ValidationError("El tipo configurado no tiene punto de venta asociado.")

    if sales_document_type.billing_mode == SALES_BILLING_MODE_AFIP_WSFE:
        document, created = create_local_fiscal_document_from_order(
            order=order,
            company=order.company,
            doc_type=sales_document_type.fiscal_doc_type,
            point_of_sale=sales_document_type.point_of_sale,
            issue_mode="arca_wsfe",
            sales_document_type=sales_document_type,
            actor=actor,
            require_invoice_ready=require_invoice_ready,
        )
    elif sales_document_type.billing_mode == SALES_BILLING_MODE_MANUAL_FISCAL:
        if external_system or external_id or external_number:
            document, created = register_external_fiscal_document_for_order(
                order=order,
                company=order.company,
                doc_type=sales_document_type.fiscal_doc_type,
                point_of_sale=sales_document_type.point_of_sale,
                external_system=external_system or "manual_fiscal",
                external_id=external_id,
                external_number=external_number,
                sales_document_type=sales_document_type,
                actor=actor,
            )
        else:
            document, created = create_local_fiscal_document_from_order(
                order=order,
                company=order.company,
                doc_type=sales_document_type.fiscal_doc_type,
                point_of_sale=sales_document_type.point_of_sale,
                issue_mode="manual",
                sales_document_type=sales_document_type,
                actor=actor,
                require_invoice_ready=require_invoice_ready,
            )
    else:
        raise ValidationError("Modo de facturacion no soportado.")

    apply_sales_document_type_to_fiscal_document(
        document=document,
        sales_document_type=sales_document_type,
        actor=actor,
    )
    return document, created


def create_internal_document_from_sales_type(*, order, sales_document_type, actor=None):
    from core.services.documents import ensure_document_for_order

    if not order:
        raise ValidationError("Pedido invalido.")
    if not sales_document_type:
        raise ValidationError("Debes seleccionar un tipo de documento comercial.")
    if sales_document_type.company_id != order.company_id:
        raise ValidationError("El tipo seleccionado no pertenece a la empresa del pedido.")
    if not sales_document_type.enabled:
        raise ValidationError("El tipo de documento seleccionado esta deshabilitado.")
    if sales_document_type.billing_mode != SALES_BILLING_MODE_INTERNAL_DOCUMENT:
        raise ValidationError("El tipo seleccionado no corresponde a un documento interno.")
    if not sales_document_type.internal_doc_type:
        raise ValidationError("El tipo configurado no tiene documento interno asociado.")

    allowed_statuses = ORDER_INTERNAL_ALLOWED_STATUSES.get(sales_document_type.internal_doc_type)
    if allowed_statuses and order.status not in allowed_statuses:
        raise ValidationError(
            "El estado actual del pedido no permite generar este documento interno."
        )

    source_key = f"order:{order.pk}:{sales_document_type.internal_doc_type}"
    existing = InternalDocument.objects.filter(source_key=source_key).first()
    document = ensure_document_for_order(
        order,
        doc_type=sales_document_type.internal_doc_type,
        sales_document_type=sales_document_type,
        actor=actor,
    )
    if not document:
        raise ValidationError("No se pudo generar el documento interno para este pedido.")
    created = existing is None
    return document, created
