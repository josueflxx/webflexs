"""Fiscal document local flow helpers (without ARCA emission)."""

from decimal import Decimal

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
from core.services.fiscal import is_invoice_ready
from core.services.sales_documents import (
    apply_sales_document_type_to_fiscal_document,
    resolve_sales_document_type_for_fiscal_doc,
)


ALLOWED_DOC_TYPES_FOR_PHASE3 = {"FA", "FB"}
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


def _build_order_items_payload(order):
    payload = []
    line_number = 1
    for item in order.items.select_related("product").all():
        quantity = Decimal(item.quantity or 0)
        unit_price_base = Decimal(getattr(item, "unit_price_base", None) or item.price_at_purchase or 0)
        line_gross = (unit_price_base * quantity).quantize(Decimal("0.01"))
        line_total = Decimal(getattr(item, "subtotal", None) or 0).quantize(Decimal("0.01"))
        discount_amount = (line_gross - line_total).quantize(Decimal("0.01"))
        if discount_amount < 0:
            discount_amount = Decimal("0.00")
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
                "net_amount": line_total,
                "iva_rate": Decimal("0.00"),
                "iva_amount": Decimal("0.00"),
                "total_amount": line_total,
            }
        )
        line_number += 1
    return payload


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
    payload = _build_order_items_payload(order)

    with transaction.atomic():
        existing = FiscalDocument.objects.select_for_update().filter(source_key=source_key).first()
        if existing:
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

        client_company_ref = getattr(order, "client_company_ref", None)
        if not client_company_ref:
            raise ValidationError("El pedido no tiene cliente empresa asignado.")

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
            sales_document_type=sales_document_type,
            subtotal_net=Decimal(order.subtotal or 0),
            discount_total=Decimal(order.discount_amount or 0),
            tax_total=Decimal("0.00"),
            total=Decimal(order.total or 0),
            currency="ARS",
            exchange_rate=Decimal("1.000000"),
        )
        _create_document_items(document, payload)
        resolved_type = sales_document_type or resolve_sales_document_type_for_fiscal_doc(
            company=company,
            doc_type=doc_type,
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
    payload = _build_order_items_payload(order)

    with transaction.atomic():
        existing = FiscalDocument.objects.select_for_update().filter(source_key=source_key).first()
        if existing:
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

        client_company_ref = getattr(order, "client_company_ref", None)
        if not client_company_ref:
            raise ValidationError("El pedido no tiene cliente empresa asignado.")

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
            issued_at=timezone.now(),
            sales_document_type=sales_document_type,
            subtotal_net=Decimal(order.subtotal or 0),
            discount_total=Decimal(order.discount_amount or 0),
            tax_total=Decimal("0.00"),
            total=Decimal(order.total or 0),
            currency="ARS",
            exchange_rate=Decimal("1.000000"),
            external_system=external_system,
            external_id=external_id,
            external_number=external_number,
        )
        _create_document_items(document, payload)
        resolved_type = sales_document_type or resolve_sales_document_type_for_fiscal_doc(
            company=company,
            doc_type=doc_type,
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
    if fiscal_document.order_id:
        invoice_ready, invoice_errors = is_invoice_ready(fiscal_document.order)
        if not invoice_ready:
            raise ValidationError(
                "No se puede cerrar el comprobante: " + " | ".join(invoice_errors)
            )

    fiscal_document.status = FISCAL_STATUS_EXTERNAL_RECORDED
    if not fiscal_document.issued_at:
        fiscal_document.issued_at = timezone.now()
        fiscal_document.save(update_fields=["status", "issued_at", "updated_at"])
    else:
        fiscal_document.save(update_fields=["status", "updated_at"])
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
