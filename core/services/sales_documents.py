"""Configurable sales-document helpers built on top of existing internal/fiscal flows."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils import timezone

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
    SALES_DEFAULT_USER_CURRENT,
    SALES_DEFAULT_USER_NONE,
    SALES_DEFAULT_USER_SPECIFIC,
    DOCUMENT_SITUATION_NOT_APPLICABLE,
    DOCUMENT_SITUATION_PENDING,
    DOCUMENT_SITUATION_APPROVED,
    DOCUMENT_SITUATION_OBSERVED,
    DOCUMENT_SITUATION_REJECTED,
    FISCAL_AUTHORIZED_STATUSES,
    FISCAL_STATUS_DRAFT,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    STOCK_MOVEMENT_IN,
    STOCK_MOVEMENT_OUT,
    STOCK_MOVEMENT_RELEASE,
    STOCK_MOVEMENT_RESERVE,
    FiscalDocument,
    InternalDocument,
    SalesDocumentType,
    StockMovement,
)
from core.services.warehouse_stock import apply_movement_to_warehouse_balance
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


def build_sales_document_rule_snapshot(sales_document_type):
    """Freeze every operational rule used by a generated document."""
    if not sales_document_type:
        return {}
    return {
        "schema_version": 1,
        "type_id": sales_document_type.pk,
        "type_code": sales_document_type.code,
        "type_name": sales_document_type.name,
        "rules_version": int(sales_document_type.rules_version or 1),
        "letter": sales_document_type.letter or "",
        "point_of_sale_id": sales_document_type.point_of_sale_id,
        "point_of_sale_number": sales_document_type.point_of_sale_number,
        "document_behavior": sales_document_type.document_behavior,
        "billing_mode": sales_document_type.billing_mode,
        "generate_stock_movement": bool(sales_document_type.generate_stock_movement),
        "generate_account_movement": bool(sales_document_type.generate_account_movement),
        "group_equal_products": bool(sales_document_type.group_equal_products),
        "default_warehouse_id": sales_document_type.default_warehouse_id,
        "default_warehouse_name": getattr(sales_document_type.default_warehouse, "name", "") or "",
        "prioritize_default_warehouse": bool(sales_document_type.prioritize_default_warehouse),
        "default_sales_user_mode": sales_document_type.default_sales_user_mode,
        "default_sales_user_id": sales_document_type.default_sales_user_id,
        "use_document_situation": bool(sales_document_type.use_document_situation),
        "currency_code": sales_document_type.currency_code,
        "default_exchange_rate": str(sales_document_type.default_exchange_rate or Decimal("1")),
    }


def get_document_rule(document, field_name, default=None):
    snapshot = getattr(document, "sales_rules_snapshot", None) or {}
    if field_name in snapshot:
        return snapshot[field_name]
    sales_document_type = getattr(document, "sales_document_type", None)
    if sales_document_type is not None and hasattr(sales_document_type, field_name):
        return getattr(sales_document_type, field_name)
    return default


def build_internal_document_display_items(document):
    """Return printable order rows honoring the rules frozen on the document."""
    if not document or not getattr(document, "order_id", None):
        return []

    items = list(document.order.items.select_related("product", "price_list").all())
    if not bool(get_document_rule(document, "group_equal_products", True)):
        return items

    grouped = {}
    for item in items:
        unit_price = Decimal(item.price_at_purchase or 0)
        discount = Decimal(item.discount_percentage_used or 0)
        key = (
            item.product_id,
            item.product_sku or "",
            item.product_name or "",
            unit_price,
            discount,
            item.price_list_id,
        )
        if key not in grouped:
            grouped[key] = {
                "product_sku": item.product_sku or "",
                "product_name": item.product_name or "",
                "quantity": Decimal(item.quantity or 0),
                "price_at_purchase": unit_price,
                "subtotal": Decimal(item.subtotal or 0),
            }
            continue
        grouped[key]["quantity"] += Decimal(item.quantity or 0)
        grouped[key]["subtotal"] += Decimal(item.subtotal or 0)
    return list(grouped.values())


def resolve_sales_document_seller(*, sales_document_type, actor=None, fallback=None):
    """Resolve seller using the configured mode at the moment the movement is created."""
    if not sales_document_type:
        return fallback
    mode = sales_document_type.default_sales_user_mode
    if mode == SALES_DEFAULT_USER_SPECIFIC:
        return sales_document_type.default_sales_user
    if mode == SALES_DEFAULT_USER_NONE:
        return None
    if mode == SALES_DEFAULT_USER_CURRENT:
        if getattr(actor, "is_authenticated", False) and getattr(actor, "is_staff", False):
            return actor
        return fallback
    return fallback


def resolve_sales_document_warehouse(*, order, sales_document_type, document=None):
    configured_id = get_document_rule(
        document,
        "default_warehouse_id",
        getattr(sales_document_type, "default_warehouse_id", None),
    ) if document else getattr(sales_document_type, "default_warehouse_id", None)
    prioritize = bool(get_document_rule(
        document,
        "prioritize_default_warehouse",
        getattr(sales_document_type, "prioritize_default_warehouse", True),
    )) if document else bool(getattr(sales_document_type, "prioritize_default_warehouse", True))
    if prioritize and configured_id:
        from core.models import Warehouse

        return Warehouse.objects.filter(pk=configured_id, company=order.company).first()
    inherited = (
        StockMovement.objects.filter(order=order, warehouse__isnull=False)
        .select_related("warehouse")
        .order_by("-created_at", "-id")
        .first()
    )
    if inherited:
        return inherited.warehouse
    if configured_id:
        from core.models import Warehouse

        return Warehouse.objects.filter(pk=configured_id, company=order.company).first()
    return None


def update_document_situation(*, document, situation, note="", actor=None):
    """Update the optional commercial-review state without changing fiscal evidence."""
    allowed = {
        DOCUMENT_SITUATION_PENDING,
        DOCUMENT_SITUATION_APPROVED,
        DOCUMENT_SITUATION_OBSERVED,
        DOCUMENT_SITUATION_REJECTED,
    }
    if not bool(get_document_rule(document, "use_document_situation", False)):
        raise ValidationError("Este tipo de movimiento no utiliza situacion comercial.")
    if situation not in allowed:
        raise ValidationError("Situacion comercial invalida.")
    normalized_note = str(note or "").strip()
    if situation in {DOCUMENT_SITUATION_OBSERVED, DOCUMENT_SITUATION_REJECTED} and not normalized_note:
        raise ValidationError("Debes indicar una observacion para observar o rechazar el movimiento.")
    document.commercial_situation = situation
    document.commercial_situation_note = normalized_note
    document.commercial_situation_updated_at = timezone.now()
    document.commercial_situation_updated_by = (
        actor if getattr(actor, "is_authenticated", False) else None
    )
    document.save(update_fields=[
        "commercial_situation",
        "commercial_situation_note",
        "commercial_situation_updated_at",
        "commercial_situation_updated_by",
        "updated_at",
    ])
    return document


def _collect_order_quantities(order, *, group_equal_products):
    grouped = defaultdict(int)
    rows = []
    products = {}
    for item in order.items.select_related("product").all():
        if not item.product_id:
            continue
        if not bool(getattr(item.product, "tracks_stock", False)):
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


def _collect_fiscal_document_quantities(fiscal_document, *, group_equal_products):
    """Use the immutable fiscal lines, including partial credit notes."""
    grouped = defaultdict(int)
    rows = []
    products = {}
    for item in fiscal_document.items.select_related("product").all():
        if not item.product_id or not bool(getattr(item.product, "tracks_stock", False)):
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
    if not order or not company or not sales_document_type:
        return []
    document = fiscal_document or internal_document
    if not bool(get_document_rule(document, "generate_stock_movement", False)):
        return []
    if fiscal_document:
        if fiscal_document.status not in FISCAL_AUTHORIZED_STATUSES:
            return []
        if (
            fiscal_document.issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE
            and not str(fiscal_document.cae or "").strip()
        ):
            return []

    movement_type, direction_sign, mutates_stock = BEHAVIOR_STOCK_RULES.get(
        get_document_rule(document, "document_behavior", sales_document_type.document_behavior),
        (None, 0, False),
    )
    if not movement_type:
        return []

    warehouse = resolve_sales_document_warehouse(
        order=order,
        sales_document_type=sales_document_type,
        document=document,
    )
    group_equal = bool(get_document_rule(document, "group_equal_products", True))
    rows = []
    if fiscal_document:
        rows = _collect_fiscal_document_quantities(
            fiscal_document,
            group_equal_products=group_equal,
        )
    if not rows and (not fiscal_document or not fiscal_document.items.exists()):
        rows = _collect_order_quantities(
            order,
            group_equal_products=group_equal,
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
                apply_movement_to_warehouse_balance(
                    movement=movement,
                    signed_effect=new_effect,
                    previous_signed_effect=old_effect,
                )
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
    if not document.sales_rules_snapshot:
        document.sales_rules_snapshot = build_sales_document_rule_snapshot(sales_document_type)
        document.sales_rules_version = int(sales_document_type.rules_version or 1)
        update_fields.extend(["sales_rules_snapshot", "sales_rules_version"])
        if sales_document_type.use_document_situation:
            document.commercial_situation = DOCUMENT_SITUATION_PENDING
            update_fields.append("commercial_situation")
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
    # The fiscal payload is frozen as soon as the document leaves draft.  Old
    # rows are backfilled by the data migration, but callers may still pass a
    # legacy/finalized object created outside the normal factory (imports,
    # fixtures or old integrations).  In that case the service can apply the
    # effective rules without trying to rewrite protected history.
    can_freeze_rules = document.status == FISCAL_STATUS_DRAFT
    if can_freeze_rules and document.sales_document_type_id != sales_document_type.id:
        document.sales_document_type = sales_document_type
        update_fields.append("sales_document_type")
    if can_freeze_rules and not document.sales_rules_snapshot:
        document.sales_rules_snapshot = build_sales_document_rule_snapshot(sales_document_type)
        document.sales_rules_version = int(sales_document_type.rules_version or 1)
        update_fields.extend(["sales_rules_snapshot", "sales_rules_version"])
        if sales_document_type.use_document_situation:
            document.commercial_situation = DOCUMENT_SITUATION_PENDING
            update_fields.append("commercial_situation")
    if update_fields:
        document.save(update_fields=update_fields + ["updated_at"])
    if document.number:
        sync_sales_document_type_counter(sales_document_type=sales_document_type, number=document.number)

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
