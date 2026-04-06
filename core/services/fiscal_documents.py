"""Fiscal document local flow helpers (without ARCA emission)."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
    FISCAL_ISSUE_MODE_MANUAL,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_VOIDED,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalPointOfSale,
)
from core.services.fiscal import (
    is_invoice_ready,
    resolve_payment_due_date,
    validate_credit_note_relationship,
)
from core.services.sales_documents import (
    apply_sales_document_type_to_fiscal_document,
    resolve_sales_document_type_for_fiscal_doc,
)


ALLOWED_DOC_TYPES_FOR_PHASE3 = {code for code, _label in FiscalDocument.DOC_TYPE_CHOICES}
LOCAL_ISSUE_MODES_FOR_PHASE3 = {
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_ISSUE_MODE_MANUAL,
}


def build_local_source_key(*, order_id, company_id, point_of_sale_id, doc_type):
    return f"local:order:{order_id}:company:{company_id}:pos:{point_of_sale_id}:doc:{doc_type}"


def build_external_source_key(
    *,
    order_id,
    company_id,
    point_of_sale_id,
    doc_type,
    external_system,
    external_id,
    external_number,
):
    external_ref = str(external_id or external_number or "").strip()
    return (
        "external:"
        f"order:{order_id}:company:{company_id}:pos:{point_of_sale_id}:doc:{doc_type}:"
        f"system:{external_system}:ref:{external_ref}"
    )


def _validate_order_and_point(*, order, company, point_of_sale, doc_type):
    if not order:
        raise ValidationError("Pedido invalido.")
    if not company:
        raise ValidationError("Empresa invalida.")
    if order.company_id != company.id:
        raise ValidationError("El pedido no pertenece a la empresa activa.")
    if not point_of_sale:
        raise ValidationError("Debes seleccionar un punto de venta.")
    if point_of_sale.company_id != company.id:
        raise ValidationError("El punto de venta no pertenece a la empresa activa.")
    if not point_of_sale.is_active:
        raise ValidationError("El punto de venta seleccionado esta inactivo.")
    if doc_type not in ALLOWED_DOC_TYPES_FOR_PHASE3:
        raise ValidationError("Tipo de comprobante no permitido en esta fase.")


def _get_discount_percentage(item, order):
    item_discount = getattr(item, "discount_percentage_used", None)
    if item_discount is None:
        return Decimal(order.discount_percentage or 0)
    return Decimal(item_discount or 0)


def _to_decimal(value, default="0.00"):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(default)


def _resolve_default_iva_rate_for_doc(doc_type):
    raw_map = getattr(settings, "FISCAL_DOC_TYPE_DEFAULT_IVA_RATES", {}) or {}
    if isinstance(raw_map, dict):
        raw_rate = raw_map.get(str(doc_type or "").strip().upper(), "0.00")
    else:
        raw_rate = "0.00"
    rate = _to_decimal(raw_rate, "0.00").quantize(Decimal("0.01"))
    if rate < 0:
        return Decimal("0.00")
    return rate


def _should_apply_item_tax(issue_mode):
    if not bool(getattr(settings, "FISCAL_AUTO_ITEM_TAX_ENABLED", True)):
        return False
    if issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE:
        return True
    return bool(getattr(settings, "FISCAL_APPLY_TAX_TO_MANUAL_DOCS", False))


def _apply_item_tax_breakdown(*, base_amount, iva_rate):
    iva_rate = _to_decimal(iva_rate, "0.00").quantize(Decimal("0.01"))
    amount = _to_decimal(base_amount, "0.00").quantize(Decimal("0.01"))
    if iva_rate <= 0:
        return amount, Decimal("0.00"), amount

    mode = str(
        getattr(settings, "FISCAL_ITEM_TAX_CALCULATION_MODE", "gross") or "gross"
    ).strip().lower()
    if mode == "net":
        net_amount = amount
        iva_amount = (net_amount * iva_rate / Decimal("100")).quantize(Decimal("0.01"))
        total_amount = (net_amount + iva_amount).quantize(Decimal("0.01"))
        return net_amount, iva_amount, total_amount

    # gross (default): amount already includes IVA and gets split out.
    divisor = Decimal("1.00") + (iva_rate / Decimal("100"))
    if divisor <= 0:
        return amount, Decimal("0.00"), amount
    net_amount = (amount / divisor).quantize(Decimal("0.01"))
    iva_amount = (amount - net_amount).quantize(Decimal("0.01"))
    total_amount = amount
    return net_amount, iva_amount, total_amount


def _build_order_items_payload(order, *, doc_type, issue_mode):
    payload = []
    line_number = 1
    apply_tax = _should_apply_item_tax(issue_mode)
    default_iva_rate = _resolve_default_iva_rate_for_doc(doc_type) if apply_tax else Decimal("0.00")

    for item in order.items.select_related("product").all():
        quantity = Decimal(item.quantity or 0)
        unit_price_base = Decimal(getattr(item, "unit_price_base", None) or item.price_at_purchase or 0)
        line_gross = (unit_price_base * quantity).quantize(Decimal("0.01"))
        line_amount = Decimal(getattr(item, "subtotal", None) or 0).quantize(Decimal("0.01"))
        discount_amount = (line_gross - line_amount).quantize(Decimal("0.01"))
        if discount_amount < 0:
            discount_amount = Decimal("0.00")

        net_amount, iva_amount, total_amount = _apply_item_tax_breakdown(
            base_amount=line_amount,
            iva_rate=default_iva_rate,
        )
        if not apply_tax:
            net_amount = line_amount
            iva_amount = Decimal("0.00")
            total_amount = line_amount

        payload.append(
            {
                "line_number": line_number,
                "product_id": item.product_id,
                "sku": item.product_sku or "",
                "description": item.product_name or "",
                "quantity": quantity.quantize(Decimal("0.001")),
                "unit_price_net": unit_price_base.quantize(Decimal("0.01")),
                "discount_percentage": _get_discount_percentage(item, order).quantize(Decimal("0.01")),
                "discount_amount": discount_amount,
                "net_amount": net_amount,
                "iva_rate": default_iva_rate,
                "iva_amount": iva_amount,
                "total_amount": total_amount,
            }
        )
        line_number += 1
    return payload


def _compute_totals_from_payload(payload):
    subtotal_net = Decimal("0.00")
    discount_total = Decimal("0.00")
    tax_total = Decimal("0.00")
    total = Decimal("0.00")
    for row in payload:
        subtotal_net += _to_decimal(row.get("net_amount", 0))
        discount_total += _to_decimal(row.get("discount_amount", 0))
        tax_total += _to_decimal(row.get("iva_amount", 0))
        total += _to_decimal(row.get("total_amount", 0))
    return {
        "subtotal_net": subtotal_net.quantize(Decimal("0.01")),
        "discount_total": discount_total.quantize(Decimal("0.01")),
        "tax_total": tax_total.quantize(Decimal("0.01")),
        "total": total.quantize(Decimal("0.01")),
    }


def _build_fiscal_snapshot_payload(
    *,
    order,
    company,
    point_of_sale,
    doc_type,
    issue_mode,
    sales_document_type=None,
    actor=None,
    client_company_ref=None,
    external_system="",
    external_id="",
    external_number="",
):
    client_profile = None
    if client_company_ref is not None:
        client_profile = getattr(client_company_ref, "client_profile", None)
    if not client_profile and getattr(order, "user_id", None):
        client_profile = getattr(order.user, "client_profile", None)

    company_tax_label = ""
    if company and getattr(company, "tax_condition", ""):
        try:
            company_tax_label = company.get_tax_condition_display()
        except Exception:
            company_tax_label = str(getattr(company, "tax_condition", "") or "")

    client_doc_label = ""
    client_tax_label = ""
    if client_profile and getattr(client_profile, "document_type", ""):
        try:
            client_doc_label = client_profile.get_document_type_display()
        except Exception:
            client_doc_label = str(getattr(client_profile, "document_type", "") or "")
    if client_profile and getattr(client_profile, "iva_condition", ""):
        try:
            client_tax_label = client_profile.get_iva_condition_display()
        except Exception:
            client_tax_label = str(getattr(client_profile, "iva_condition", "") or "")

    return {
        "version": 1,
        "captured_at": timezone.now().isoformat(),
        "emitter": {
            "company_id": getattr(company, "id", None),
            "name": str(getattr(company, "name", "") or ""),
            "legal_name": str(getattr(company, "legal_name", "") or ""),
            "cuit": str(getattr(company, "cuit", "") or ""),
            "tax_condition": str(getattr(company, "tax_condition", "") or ""),
            "tax_condition_label": str(company_tax_label or ""),
            "fiscal_address": str(getattr(company, "fiscal_address", "") or ""),
            "fiscal_city": str(getattr(company, "fiscal_city", "") or ""),
            "fiscal_province": str(getattr(company, "fiscal_province", "") or ""),
            "postal_code": str(getattr(company, "postal_code", "") or ""),
            "point_of_sale": str(getattr(point_of_sale, "number", "") or ""),
        },
        "client": {
            "client_company_ref_id": getattr(client_company_ref, "id", None),
            "client_profile_id": getattr(client_profile, "id", None),
            "name": str(
                (getattr(client_profile, "company_name", "") or "")
                or (getattr(order, "client_company", "") or "")
            ),
            "document_type": str(getattr(client_profile, "document_type", "") or ""),
            "document_type_label": str(client_doc_label or ""),
            "document_number": str(
                (getattr(client_profile, "document_number", "") or "")
                or (getattr(client_profile, "cuit_dni", "") or "")
                or (getattr(order, "client_cuit", "") or "")
            ),
            "tax_condition": str(getattr(client_profile, "iva_condition", "") or ""),
            "tax_condition_label": str(client_tax_label or ""),
            "fiscal_address": str(
                (getattr(client_profile, "fiscal_address", "") or "")
                or (getattr(client_profile, "address", "") or "")
                or (getattr(order, "client_address", "") or "")
            ),
            "fiscal_city": str(
                (getattr(client_profile, "fiscal_city", "") or "")
                or (getattr(client_profile, "province", "") or "")
            ),
            "fiscal_province": str(getattr(client_profile, "fiscal_province", "") or ""),
            "postal_code": str(getattr(client_profile, "postal_code", "") or ""),
            "phone": str(
                (getattr(client_profile, "phone", "") or "")
                or (getattr(order, "client_phone", "") or "")
            ),
        },
        "operation": {
            "order_id": getattr(order, "id", None),
            "order_status": str(getattr(order, "status", "") or ""),
            "origin_channel": str(getattr(order, "origin_channel", "") or ""),
            "billing_mode": str(getattr(order, "billing_mode", "") or ""),
            "source_request_id": getattr(order, "source_request_id", None),
            "source_proposal_id": getattr(order, "source_proposal_id", None),
            "subtotal": str(Decimal(getattr(order, "subtotal", 0) or 0).quantize(Decimal("0.01"))),
            "discount_amount": str(Decimal(getattr(order, "discount_amount", 0) or 0).quantize(Decimal("0.01"))),
            "total": str(Decimal(getattr(order, "total", 0) or 0).quantize(Decimal("0.01"))),
        },
        "generation": {
            "doc_type": str(doc_type or ""),
            "issue_mode": str(issue_mode or ""),
            "sales_document_type_id": getattr(sales_document_type, "id", None),
            "sales_document_type_name": str(getattr(sales_document_type, "name", "") or ""),
            "actor_id": getattr(actor, "id", None),
            "actor_username": str(getattr(actor, "username", "") or ""),
            "external_system": str(external_system or ""),
            "external_id": str(external_id or ""),
            "external_number": str(external_number or ""),
        },
    }


def _ensure_document_snapshot(*, document, snapshot_payload):
    if not document:
        return False
    current_payload = document.request_payload if isinstance(document.request_payload, dict) else {}
    if isinstance(current_payload.get("snapshot"), dict):
        return False
    current_payload["snapshot"] = snapshot_payload
    document.request_payload = current_payload
    document.save(update_fields=["request_payload", "updated_at"])
    return True


def _create_document_items(document, payload):
    items = []
    for row in payload:
        items.append(
            FiscalDocumentItem(
                fiscal_document=document,
                line_number=row["line_number"],
                product_id=row["product_id"],
                sku=row["sku"],
                description=row["description"],
                quantity=row["quantity"],
                unit_price_net=row["unit_price_net"],
                discount_percentage=row["discount_percentage"],
                discount_amount=row["discount_amount"],
                net_amount=row["net_amount"],
                iva_rate=row["iva_rate"],
                iva_amount=row["iva_amount"],
                total_amount=row["total_amount"],
            )
        )
    if items:
        FiscalDocumentItem.objects.bulk_create(items)


def create_local_fiscal_document_from_order(
    *,
    order,
    company,
    doc_type,
    point_of_sale,
    issue_mode,
    sales_document_type=None,
    actor=None,
    require_invoice_ready=True,
):
    """Create local fiscal document from order without ARCA emission."""
    _validate_order_and_point(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
    )
    if issue_mode not in LOCAL_ISSUE_MODES_FOR_PHASE3:
        raise ValidationError("Modo de comprobante invalido para creacion local.")

    invoice_ready, invoice_errors = is_invoice_ready(order)
    if require_invoice_ready and not invoice_ready:
        raise ValidationError("No se puede crear comprobante fiscal: " + " | ".join(invoice_errors))

    source_key = build_local_source_key(
        order_id=order.id,
        company_id=company.id,
        point_of_sale_id=point_of_sale.id,
        doc_type=doc_type,
    )
    payload = _build_order_items_payload(
        order,
        doc_type=doc_type,
        issue_mode=issue_mode,
    )
    totals = _compute_totals_from_payload(payload)
    client_company_ref = getattr(order, "client_company_ref", None)
    if not client_company_ref:
        raise ValidationError("El pedido no tiene cliente empresa asignado.")
    snapshot_payload = _build_fiscal_snapshot_payload(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
        issue_mode=issue_mode,
        sales_document_type=sales_document_type,
        actor=actor,
        client_company_ref=client_company_ref,
    )

    with transaction.atomic():
        existing = FiscalDocument.objects.select_for_update().filter(source_key=source_key).first()
        if existing:
            if not existing.payment_due_date:
                existing.payment_due_date = resolve_payment_due_date(
                    order=existing.order,
                    issued_at=existing.issued_at or existing.created_at,
                )
                existing.save(update_fields=["payment_due_date", "updated_at"])
            _ensure_document_snapshot(document=existing, snapshot_payload=snapshot_payload)
            apply_sales_document_type_to_fiscal_document(
                document=existing,
                sales_document_type=sales_document_type,
                actor=actor,
            )
            return existing, False

        duplicate = (
            FiscalDocument.objects.select_for_update()
            .filter(
                company=company,
                order=order,
                point_of_sale=point_of_sale,
                doc_type=doc_type,
            )
            .exclude(status="voided")
            .first()
        )
        if duplicate:
            raise ValidationError(
                "Ya existe un comprobante fiscal para este pedido, tipo y punto de venta."
            )

        document = FiscalDocument.objects.create(
            source_key=source_key,
            company=company,
            client_company_ref=client_company_ref,
            client_profile=client_company_ref.client_profile if client_company_ref else None,
            order=order,
            point_of_sale=point_of_sale,
            doc_type=doc_type,
            issue_mode=issue_mode,
            status=FISCAL_STATUS_READY_TO_ISSUE,
            payment_due_date=resolve_payment_due_date(order=order),
            sales_document_type=sales_document_type,
            subtotal_net=totals["subtotal_net"],
            discount_total=totals["discount_total"],
            tax_total=totals["tax_total"],
            total=totals["total"],
            currency="ARS",
            exchange_rate=Decimal("1.000000"),
            request_payload={"snapshot": snapshot_payload},
        )
        _create_document_items(document, payload)
        resolved_type = sales_document_type or resolve_sales_document_type_for_fiscal_doc(
            company=company,
            doc_type=doc_type,
            origin_channel=getattr(order, "origin_channel", ""),
        )
        apply_sales_document_type_to_fiscal_document(
            document=document,
            sales_document_type=resolved_type,
            actor=actor,
        )
        return document, True


def register_external_fiscal_document_for_order(
    *,
    order,
    company,
    doc_type,
    point_of_sale,
    external_system,
    external_id,
    external_number,
    sales_document_type=None,
    actor=None,
):
    """Register external/SaaS fiscal document without local emission."""
    _validate_order_and_point(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
    )
    external_system = str(external_system or "").strip()
    external_id = str(external_id or "").strip()
    external_number = str(external_number or "").strip()
    if not external_system:
        raise ValidationError("El sistema externo es obligatorio para registro externo.")
    if not (external_id or external_number):
        raise ValidationError("Debes informar ID externo o numero externo.")

    source_key = build_external_source_key(
        order_id=order.id,
        company_id=company.id,
        point_of_sale_id=point_of_sale.id,
        doc_type=doc_type,
        external_system=external_system,
        external_id=external_id,
        external_number=external_number,
    )
    payload = _build_order_items_payload(
        order,
        doc_type=doc_type,
        issue_mode=FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
    )
    totals = _compute_totals_from_payload(payload)
    client_company_ref = getattr(order, "client_company_ref", None)
    if not client_company_ref:
        raise ValidationError("El pedido no tiene cliente empresa asignado.")
    snapshot_payload = _build_fiscal_snapshot_payload(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
        issue_mode=FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
        sales_document_type=sales_document_type,
        actor=actor,
        client_company_ref=client_company_ref,
        external_system=external_system,
        external_id=external_id,
        external_number=external_number,
    )

    with transaction.atomic():
        existing = FiscalDocument.objects.select_for_update().filter(source_key=source_key).first()
        if existing:
            if not existing.payment_due_date:
                existing.payment_due_date = resolve_payment_due_date(
                    order=existing.order,
                    issued_at=existing.issued_at or existing.created_at,
                )
                existing.save(update_fields=["payment_due_date", "updated_at"])
            _ensure_document_snapshot(document=existing, snapshot_payload=snapshot_payload)
            apply_sales_document_type_to_fiscal_document(
                document=existing,
                sales_document_type=sales_document_type,
                actor=actor,
            )
            return existing, False

        duplicate = (
            FiscalDocument.objects.select_for_update()
            .filter(
                company=company,
                order=order,
                point_of_sale=point_of_sale,
                doc_type=doc_type,
            )
            .exclude(status="voided")
            .first()
        )
        if duplicate:
            raise ValidationError(
                "Ya existe un comprobante fiscal para este pedido, tipo y punto de venta."
            )

        duplicate_external_filter = FiscalDocument.objects.select_for_update().filter(
            company=company,
            external_system=external_system,
        )
        if external_id and external_number:
            duplicate_external_filter = duplicate_external_filter.filter(
                Q(external_id=external_id) | Q(external_number=external_number)
            )
        elif external_id:
            duplicate_external_filter = duplicate_external_filter.filter(external_id=external_id)
        else:
            duplicate_external_filter = duplicate_external_filter.filter(external_number=external_number)
        duplicate_external = duplicate_external_filter.first()
        if duplicate_external:
            return duplicate_external, False

        issued_now = timezone.now()
        document = FiscalDocument.objects.create(
            source_key=source_key,
            company=company,
            client_company_ref=client_company_ref,
            client_profile=client_company_ref.client_profile if client_company_ref else None,
            order=order,
            point_of_sale=point_of_sale,
            doc_type=doc_type,
            issue_mode=FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
            status=FISCAL_STATUS_EXTERNAL_RECORDED,
            issued_at=issued_now,
            payment_due_date=resolve_payment_due_date(order=order, issued_at=issued_now),
            sales_document_type=sales_document_type,
            subtotal_net=totals["subtotal_net"],
            discount_total=totals["discount_total"],
            tax_total=totals["tax_total"],
            total=totals["total"],
            currency="ARS",
            exchange_rate=Decimal("1.000000"),
            external_system=external_system,
            external_id=external_id,
            external_number=external_number,
            request_payload={"snapshot": snapshot_payload},
        )
        _create_document_items(document, payload)
        resolved_type = sales_document_type or resolve_sales_document_type_for_fiscal_doc(
            company=company,
            doc_type=doc_type,
            origin_channel=getattr(order, "origin_channel", ""),
        )
        apply_sales_document_type_to_fiscal_document(
            document=document,
            sales_document_type=resolved_type,
            actor=actor,
        )
        return document, True


def close_fiscal_document(*, fiscal_document, actor=None):
    """Close a manual fiscal document without ARCA emission."""
    if not fiscal_document:
        raise ValidationError("Comprobante fiscal invalido.")
    if fiscal_document.status == FISCAL_STATUS_VOIDED:
        raise ValidationError("El comprobante esta anulado.")
    if fiscal_document.issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE:
        raise ValidationError("Los comprobantes ARCA se cierran emitiendolos.")
    if fiscal_document.issue_mode == FISCAL_ISSUE_MODE_EXTERNAL_SAAS:
        raise ValidationError("El comprobante externo ya esta cerrado por definicion.")
    if fiscal_document.status == FISCAL_STATUS_EXTERNAL_RECORDED:
        return fiscal_document, False
    if fiscal_document.status not in {
        FISCAL_STATUS_READY_TO_ISSUE,
        FISCAL_STATUS_PENDING_RETRY,
        FISCAL_STATUS_REJECTED,
    }:
        raise ValidationError("El comprobante no puede cerrarse en su estado actual.")
    relation_ok, relation_errors = validate_credit_note_relationship(fiscal_document)
    if not relation_ok:
        raise ValidationError("No se puede cerrar el comprobante: " + " | ".join(relation_errors))
    if fiscal_document.order_id:
        invoice_ready, invoice_errors = is_invoice_ready(fiscal_document.order)
        if not invoice_ready:
            raise ValidationError(
                "No se puede cerrar el comprobante: " + " | ".join(invoice_errors)
            )

    fiscal_document.status = FISCAL_STATUS_EXTERNAL_RECORDED
    if not fiscal_document.payment_due_date:
        fiscal_document.payment_due_date = resolve_payment_due_date(
            order=fiscal_document.order,
            issued_at=fiscal_document.issued_at or timezone.now(),
        )
    if not fiscal_document.issued_at:
        fiscal_document.issued_at = timezone.now()
        fiscal_document.save(update_fields=["status", "issued_at", "payment_due_date", "updated_at"])
    else:
        fiscal_document.save(update_fields=["status", "payment_due_date", "updated_at"])
    if fiscal_document.order_id:
        try:
            from accounts.services.ledger import sync_order_charge_transaction

            sync_order_charge_transaction(order=fiscal_document.order, actor=actor)
        except Exception:
            pass
    return fiscal_document, True


def reopen_fiscal_document(*, fiscal_document, actor=None):
    """Reopen a manual fiscal document that was closed without ARCA emission."""
    if not fiscal_document:
        raise ValidationError("Comprobante fiscal invalido.")
    if fiscal_document.status == FISCAL_STATUS_VOIDED:
        raise ValidationError("El comprobante esta anulado.")
    if fiscal_document.issue_mode != FISCAL_ISSUE_MODE_MANUAL:
        raise ValidationError("Solo los comprobantes manuales pueden reabrirse.")
    if fiscal_document.status == FISCAL_STATUS_READY_TO_ISSUE:
        return fiscal_document, False
    if fiscal_document.status != FISCAL_STATUS_EXTERNAL_RECORDED:
        raise ValidationError("Solo se pueden reabrir comprobantes cerrados manualmente.")

    fiscal_document.status = FISCAL_STATUS_READY_TO_ISSUE
    fiscal_document.save(update_fields=["status", "updated_at"])
    if fiscal_document.order_id:
        try:
            from accounts.services.ledger import sync_order_charge_transaction

            sync_order_charge_transaction(order=fiscal_document.order, actor=actor)
        except Exception:
            pass
    return fiscal_document, True


def void_fiscal_document(*, fiscal_document, actor=None):
    """Void a fiscal document before it is legally authorized in ARCA."""
    if not fiscal_document:
        raise ValidationError("Comprobante fiscal invalido.")
    if fiscal_document.status == FISCAL_STATUS_VOIDED:
        return fiscal_document, False
    if fiscal_document.status == FISCAL_STATUS_SUBMITTING:
        raise ValidationError("El comprobante se esta enviando. Espera a que termine el intento.")
    if fiscal_document.status == FISCAL_STATUS_AUTHORIZED:
        raise ValidationError(
            "Un comprobante autorizado no puede anularse desde aqui. Usa una nota de credito."
        )

    fiscal_document.status = FISCAL_STATUS_VOIDED
    fiscal_document.save(update_fields=["status", "updated_at"])
    if fiscal_document.order_id:
        try:
            from accounts.services.ledger import sync_order_charge_transaction

            sync_order_charge_transaction(order=fiscal_document.order, actor=actor)
        except Exception:
            pass
    return fiscal_document, True
