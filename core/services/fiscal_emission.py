"""Fiscal emission workflow (Phase 4 - ARCA homologation first)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    FISCAL_INVOICE_DOC_TYPES,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FiscalDocument,
    FiscalDocumentSeries,
    FiscalEmissionAttempt,
)
from core.services.arca_client import (
    ArcaConfigurationError,
    ArcaTemporaryError,
    ArcaWsfeClient,
)
from core.services.fiscal import (
    is_company_fiscal_ready,
    is_invoice_ready,
    resolve_payment_due_date,
    validate_credit_note_relationship,
)
from core.services.sales_documents import sync_sales_document_type_counter


ALLOWED_DOC_TYPES_FOR_EMISSION = {code for code, _label in FiscalDocument.DOC_TYPE_CHOICES}
RETRYABLE_DOCUMENT_STATUSES = {
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_REJECTED,
}


@dataclass
class FiscalEmissionOutcome:
    document: FiscalDocument
    state: str
    message: str


def _reserve_fiscal_number(document: FiscalDocument) -> int:
    if document.number:
        return int(document.number)

    series, _ = FiscalDocumentSeries.objects.select_for_update().get_or_create(
        company=document.company,
        point_of_sale_ref=document.point_of_sale,
        doc_type=document.doc_type,
        defaults={
            "point_of_sale": document.point_of_sale.number,
            "next_number": 1,
        },
    )
    if series.point_of_sale != document.point_of_sale.number:
        series.point_of_sale = document.point_of_sale.number
        series.save(update_fields=["point_of_sale", "updated_at"])

    number = int(series.next_number or 1)
    series.next_number = number + 1
    series.save(update_fields=["next_number", "updated_at"])
    return number


def _validate_before_submit(document: FiscalDocument):
    if document.issue_mode != FISCAL_ISSUE_MODE_ARCA_WSFE:
        raise ValidationError("Solo se puede emitir por ARCA cuando el modo es ARCA WSFE.")
    if document.doc_type not in ALLOWED_DOC_TYPES_FOR_EMISSION:
        raise ValidationError("Tipo de comprobante fiscal no permitido para emision ARCA.")
    if document.status not in RETRYABLE_DOCUMENT_STATUSES:
        raise ValidationError(
            f"Estado fiscal no emitible: {document.get_status_display()}."
        )
    if not document.company_id:
        raise ValidationError("Documento fiscal sin empresa.")
    if not document.point_of_sale_id:
        raise ValidationError("Documento fiscal sin punto de venta.")
    if document.point_of_sale.company_id != document.company_id:
        raise ValidationError("El punto de venta no coincide con la empresa del comprobante.")
    if not str(document.point_of_sale.number or "").isdigit():
        raise ValidationError("El punto de venta fiscal debe ser numerico para emitir en ARCA.")
    if not document.point_of_sale.is_active:
        raise ValidationError("El punto de venta fiscal esta inactivo.")
    if not document.client_company_ref_id:
        raise ValidationError("Documento fiscal sin cliente empresa.")
    if document.client_company_ref.company_id != document.company_id:
        raise ValidationError("El cliente empresa no coincide con la empresa del comprobante.")
    if document.order_id:
        if document.order.company_id != document.company_id:
            raise ValidationError("El pedido relacionado no coincide con la empresa del comprobante.")
        if document.order.client_company_ref_id != document.client_company_ref_id:
            raise ValidationError("El cliente empresa del pedido no coincide con el comprobante fiscal.")
        invoice_ready, invoice_errors = is_invoice_ready(document.order)
        if not invoice_ready:
            raise ValidationError("Pedido no listo para facturar: " + " | ".join(invoice_errors))
    if not document.items.exists():
        raise ValidationError("El comprobante fiscal no tiene items cargados.")
    if document.total is None or document.total <= 0:
        raise ValidationError("El comprobante fiscal debe tener total mayor a cero.")
    if document.doc_type in FISCAL_INVOICE_DOC_TYPES and document.related_document_id:
        raise ValidationError("Las facturas no deben vincularse a otro comprobante base.")

    relation_ok, relation_errors = validate_credit_note_relationship(document)
    if not relation_ok:
        raise ValidationError("Relacion fiscal invalida: " + " | ".join(relation_errors))

    company_ready, company_errors = is_company_fiscal_ready(document.company)
    if not company_ready:
        raise ValidationError("Empresa no lista para ARCA: " + " | ".join(company_errors))


def emit_fiscal_document_now(*, fiscal_document: FiscalDocument, actor=None) -> FiscalEmissionOutcome:
    """
    Execute one real ARCA emission attempt for an existing local FiscalDocument.
    No automatic retries in this phase.
    """
    document_id = getattr(fiscal_document, "id", None)
    if not document_id:
        raise ValidationError("Documento fiscal invalido.")

    started_at = timezone.now()

    with transaction.atomic():
        locked_doc = (
            FiscalDocument.objects.select_for_update()
            .select_related(
                "company",
                "point_of_sale",
                "client_company_ref",
                "client_profile",
                "order",
            )
            .prefetch_related("items")
            .get(pk=document_id)
        )
        if locked_doc.status == FISCAL_STATUS_AUTHORIZED and locked_doc.cae:
            return FiscalEmissionOutcome(
                document=locked_doc,
                state="authorized",
                message="El comprobante ya estaba autorizado con CAE.",
            )

        if locked_doc.status == FISCAL_STATUS_SUBMITTING:
            raise ValidationError("El comprobante ya se encuentra en proceso de envio.")

        _validate_before_submit(locked_doc)
        reserved_number = _reserve_fiscal_number(locked_doc)
        locked_doc.number = reserved_number
        locked_doc.status = FISCAL_STATUS_SUBMITTING
        locked_doc.attempts_count = int(locked_doc.attempts_count or 0) + 1
        locked_doc.last_attempt_at = started_at
        locked_doc.error_code = ""
        locked_doc.error_message = ""
        locked_doc.save(
            update_fields=[
                "number",
                "status",
                "attempts_count",
                "last_attempt_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
        sync_sales_document_type_counter(
            sales_document_type=locked_doc.sales_document_type,
            number=reserved_number,
        )
        attempt_number = int(locked_doc.attempts_count or 1)

    request_payload = {}
    response_payload = {}
    result_status = "pending"
    final_state = FISCAL_STATUS_PENDING_RETRY
    error_code = ""
    error_message = ""
    cae = ""
    cae_due_date = None

    try:
        client = ArcaWsfeClient(company=locked_doc.company, point_of_sale=locked_doc.point_of_sale)
        client_result = client.emit_fiscal_document(
            fiscal_document=locked_doc,
            cbte_number=int(locked_doc.number),
        )
        request_payload = client_result.request_payload or {}
        response_payload = client_result.response_payload or {}
        error_code = client_result.error_code or ""
        error_message = client_result.error_message or ""

        if client_result.state == "authorized":
            final_state = FISCAL_STATUS_AUTHORIZED
            result_status = "success"
            cae = client_result.cae or ""
            cae_due_date = client_result.cae_due_date
        elif client_result.state == "rejected":
            final_state = FISCAL_STATUS_REJECTED
            result_status = "error"
        else:
            final_state = FISCAL_STATUS_PENDING_RETRY
            result_status = "pending"
            if not error_code:
                error_code = "pending_retry"
            if not error_message:
                error_message = "No se pudo completar la emision. Reintentar manualmente."
    except ArcaConfigurationError as exc:
        final_state = FISCAL_STATUS_REJECTED
        result_status = "error"
        error_code = "arca_config"
        error_message = str(exc)
    except ArcaTemporaryError as exc:
        final_state = FISCAL_STATUS_PENDING_RETRY
        result_status = "pending"
        error_code = exc.error_code or "temporary_error"
        error_message = str(exc)
        request_payload = exc.request_payload or request_payload
        response_payload = exc.response_payload or response_payload
    except Exception as exc:
        final_state = FISCAL_STATUS_PENDING_RETRY
        result_status = "pending"
        error_code = "unexpected_error"
        error_message = f"Fallo inesperado en emision fiscal: {exc}"

    finished_at = timezone.now()
    duration_ms = max(int((finished_at - started_at).total_seconds() * 1000), 0)
    retry_minutes = int(getattr(settings, "FISCAL_RETRY_MINUTES", 10) or 10)
    max_retry_attempts = int(getattr(settings, "FISCAL_MAX_AUTO_RETRIES", 5) or 5)

    with transaction.atomic():
        locked_doc = FiscalDocument.objects.select_for_update().get(pk=document_id)
        will_retry = final_state == FISCAL_STATUS_PENDING_RETRY
        next_retry_at = None
        if will_retry:
            if int(locked_doc.attempts_count or 0) >= max_retry_attempts:
                final_state = FISCAL_STATUS_REJECTED
                will_retry = False
                if not error_code:
                    error_code = "retry_limit_reached"
                if not error_message:
                    error_message = "Se alcanzo el maximo de reintentos automaticos."
            else:
                retry_delay = retry_minutes if retry_minutes >= 1 else 1
                next_retry_at = timezone.now() + timedelta(minutes=retry_delay)

        FiscalEmissionAttempt.objects.create(
            fiscal_document=locked_doc,
            triggered_by=actor if getattr(actor, "is_authenticated", False) else None,
            request_payload=request_payload or {},
            response_payload=response_payload or {},
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            will_retry=will_retry,
            result_status=result_status,
            error_code=error_code,
            error_message=error_message,
        )

        locked_doc.status = final_state
        locked_doc.error_code = error_code or ""
        locked_doc.error_message = error_message or ""
        existing_request_payload = (
            dict(locked_doc.request_payload)
            if isinstance(locked_doc.request_payload, dict)
            else {}
        )
        existing_response_payload = (
            dict(locked_doc.response_payload)
            if isinstance(locked_doc.response_payload, dict)
            else {}
        )
        existing_request_payload["arca_request"] = request_payload or {}
        existing_request_payload["arca_attempt"] = {
            "attempt_number": attempt_number,
            "at": finished_at.isoformat(),
        }
        existing_response_payload["arca_response"] = response_payload or {}
        locked_doc.request_payload = existing_request_payload
        locked_doc.response_payload = existing_response_payload
        locked_doc.last_attempt_at = finished_at
        locked_doc.next_retry_at = next_retry_at

        update_fields = [
            "status",
            "error_code",
            "error_message",
            "request_payload",
            "response_payload",
            "last_attempt_at",
            "next_retry_at",
            "updated_at",
        ]

        if final_state == FISCAL_STATUS_AUTHORIZED:
            locked_doc.cae = cae
            locked_doc.cae_due_date = cae_due_date
            if not locked_doc.issued_at:
                locked_doc.issued_at = timezone.now()
                update_fields.append("issued_at")
            if not locked_doc.payment_due_date:
                locked_doc.payment_due_date = resolve_payment_due_date(
                    order=locked_doc.order,
                    issued_at=locked_doc.issued_at or locked_doc.created_at,
                )
                update_fields.append("payment_due_date")
            update_fields.extend(["cae", "cae_due_date"])

        locked_doc.save(update_fields=update_fields)

    if locked_doc.order_id:
        try:
            from accounts.services.ledger import sync_order_charge_transaction

            sync_order_charge_transaction(order=locked_doc.order, actor=actor)
        except Exception:
            pass

    message = {
        FISCAL_STATUS_AUTHORIZED: "Comprobante autorizado en ARCA.",
        FISCAL_STATUS_PENDING_RETRY: "No se pudo completar la emision. Quedo pendiente de reintento.",
        FISCAL_STATUS_REJECTED: "ARCA rechazo la emision del comprobante.",
    }.get(final_state, "Resultado de emision fiscal actualizado.")

    return FiscalEmissionOutcome(document=locked_doc, state=final_state, message=message)
