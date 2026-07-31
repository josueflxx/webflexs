"""Fail-closed ARCA authorization workflow.

No retry path in this module can authorize an uncertain operation again.
Uncertainty is resolved exclusively by ``core.services.fiscal_recovery``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Callable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.services.account_movement_service import (
    sync_fiscal_document_account_movement,
)
from core.models import (
    FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
    FISCAL_ATTEMPT_RESULT_ERROR,
    FISCAL_ATTEMPT_RESULT_SUCCESS,
    FISCAL_ATTEMPT_RESULT_UNCERTAIN,
    FISCAL_AUTHORIZED_STATUSES,
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_NCA,
    FISCAL_DOC_TYPE_NCB,
    FISCAL_INVOICE_DOC_TYPES,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
    FISCAL_STATUS_MANUAL_REVIEW,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_UNCERTAIN,
    FISCAL_UNCERTAIN_STATUSES,
    FiscalDocument,
    FiscalDocumentSeries,
    FiscalEmissionAttempt,
    FiscalSeriesReconciliation,
)
from core.services.arca_client import (
    ArcaConfigurationError,
    ArcaTemporaryError,
    ArcaWsfeClient,
)
from core.services.fiscal import (
    is_company_fiscal_ready,
    validate_credit_note_relationship,
)
from core.services.fiscal_integrity import (
    fiscal_payload_hash,
    validate_line_and_totals,
)
from core.services.sales_documents import (
    ensure_stock_movements_for_order_document,
    sync_sales_document_type_counter,
)
from core.services.sensitive_data import (
    sanitize_sensitive_payload,
    sanitize_sensitive_text,
)


logger = logging.getLogger(__name__)

ALLOWED_DOC_TYPES_FOR_EMISSION = {
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_NCA,
    FISCAL_DOC_TYPE_NCB,
}


@dataclass
class FiscalEmissionOutcome:
    document: FiscalDocument
    state: str
    message: str


def _actor_for_fk(actor):
    return actor if getattr(actor, "is_authenticated", False) else None


def _attempt_request_evidence(document, *, number):
    return {
        "correlation_id": str(document.correlation_id),
        "idempotency_key": str(document.idempotency_key),
        "snapshot_hash": document.snapshot_hash,
        "payload_hash": document.payload_hash,
        "issuer_cuit": document.issuer_cuit_snapshot,
        "environment": document.environment_snapshot,
        "point_of_sale": document.point_of_sale_number_snapshot,
        "doc_type": document.doc_type,
        "cbte_number": int(number),
    }


def _document_lines(document):
    return [
        {
            "net_amount": item.net_amount,
            "discount_amount": item.discount_amount,
            "iva_amount": item.iva_amount,
            "total_amount": item.total_amount,
        }
        for item in document.items.all()
    ]


def _validate_before_submit(document: FiscalDocument):
    if document.issue_mode != FISCAL_ISSUE_MODE_ARCA_WSFE:
        raise ValidationError("Solo se puede emitir por ARCA cuando el modo es ARCA WSFE.")
    if document.doc_type not in ALLOWED_DOC_TYPES_FOR_EMISSION:
        raise ValidationError("Tipo de comprobante fiscal no permitido para emision ARCA.")
    if document.status != FISCAL_STATUS_READY_TO_ISSUE:
        raise ValidationError(
            "La operacion no esta lista para una primera autorizacion. "
            "Los resultados inciertos sólo admiten consulta."
        )
    if document.number is not None:
        raise ValidationError("Una operacion READY no puede traer un numero decidido previamente.")
    if not document.snapshot_hash or not isinstance(document.fiscal_snapshot, dict):
        raise ValidationError("El documento no tiene snapshot fiscal inmutable y verificable.")
    if fiscal_payload_hash(document.fiscal_snapshot) != document.snapshot_hash:
        raise ValidationError("El hash del snapshot fiscal no coincide con su contenido.")
    if not document.company_id or not document.point_of_sale_id:
        raise ValidationError("Documento fiscal sin empresa o punto de venta.")
    if document.point_of_sale.company_id != document.company_id:
        raise ValidationError("El punto de venta no coincide con la empresa del comprobante.")
    if (
        document.environment_snapshot != document.point_of_sale.environment
        or document.point_of_sale_number_snapshot != document.point_of_sale.number
    ):
        raise ValidationError("La identidad fiscal congelada no coincide con el punto de venta.")
    if document.environment_snapshot != "homologation":
        raise ValidationError("La autorizacion ARCA sólo esta habilitada para homologacion.")
    if not str(document.point_of_sale.number or "").isdigit():
        raise ValidationError("El punto de venta fiscal debe ser numerico.")
    if not document.point_of_sale.is_active:
        raise ValidationError("El punto de venta fiscal esta inactivo.")
    if document.receiver_iva_condition_id_snapshot is None:
        raise ValidationError("Falta CondicionIVAReceptorId en el snapshot fiscal.")
    if not document.items.exists():
        raise ValidationError("El comprobante fiscal no tiene items congelados.")
    if any(item.arca_iva_id is None for item in document.items.all()):
        raise ValidationError("Existe una linea sin identificador de alicuota ARCA.")
    totals = {
        "subtotal_net": document.subtotal_net,
        "discount_total": document.discount_total,
        "tax_total": document.tax_total,
        "total": document.total,
    }
    validate_line_and_totals(_document_lines(document), totals)
    if Decimal(document.total or 0) <= 0:
        raise ValidationError("El comprobante fiscal debe tener total mayor a cero.")
    if document.doc_type in FISCAL_INVOICE_DOC_TYPES and document.related_document_id:
        raise ValidationError("Las facturas no deben vincularse a otro comprobante base.")
    relation_ok, relation_errors = validate_credit_note_relationship(document)
    if not relation_ok:
        raise ValidationError("Relacion fiscal invalida: " + " | ".join(relation_errors))
    company_ready, company_errors = is_company_fiscal_ready(document.company)
    if not company_ready:
        raise ValidationError("Empresa no lista para ARCA: " + " | ".join(company_errors))


def _series_identity(document):
    return {
        "issuer_cuit": document.issuer_cuit_snapshot,
        "environment": document.environment_snapshot,
        "point_of_sale": document.point_of_sale_number_snapshot,
        "doc_type": document.doc_type,
    }


def _record_reconciliation(
    *,
    series,
    document,
    actor,
    local_before,
    local_after,
    remote_last,
    outcome,
    reason="",
):
    return FiscalSeriesReconciliation.objects.create(
        series=series,
        fiscal_document=document,
        triggered_by=_actor_for_fk(actor),
        correlation_id=document.correlation_id,
        issuer_cuit=series.issuer_cuit,
        environment=series.environment,
        point_of_sale=series.point_of_sale,
        doc_type=series.doc_type,
        local_next_before=local_before,
        local_next_after=local_after,
        remote_last_authorized=remote_last,
        outcome=outcome,
        reason=sanitize_sensitive_text(reason)[:255],
    )


def _block_after_reconciliation_failure(*, document, series, actor, reason):
    now = timezone.now()
    local_next = int(series.next_number or 1)
    series.blocked_at = now
    series.blocked_reason = sanitize_sensitive_text(reason)[:255]
    series.blocked_by_document = document
    series.version = int(series.version or 0) + 1
    series.save(
        update_fields=[
            "blocked_at",
            "blocked_reason",
            "blocked_by_document",
            "version",
            "updated_at",
        ]
    )
    _record_reconciliation(
        series=series,
        document=document,
        actor=actor,
        local_before=local_next,
        local_after=local_next,
        remote_last=None,
        outcome=FiscalSeriesReconciliation.OUTCOME_FAILED,
        reason=reason,
    )
    document.transition_to(
        FISCAL_STATUS_MANUAL_REVIEW,
        error_code="series_reconciliation_failed",
        error_message=sanitize_sensitive_text(reason),
    )
    return FiscalEmissionOutcome(
        document=document,
        state=FISCAL_STATUS_MANUAL_REVIEW,
        message="No se pudo verificar la correlatividad; la serie quedo bloqueada.",
    )


def _finalize_safe_predispatch_failure(*, document_id, attempt_id, error_code, message):
    with transaction.atomic():
        document = FiscalDocument.objects.select_for_update().get(pk=document_id)
        attempt = FiscalEmissionAttempt.objects.select_for_update().get(pk=attempt_id)
        series = FiscalDocumentSeries.objects.select_for_update().get(pk=document.series_id)
        attempt.finalize(
            result_status=FISCAL_ATTEMPT_RESULT_ERROR,
            response_payload={},
            error_code=error_code,
            error_message=sanitize_sensitive_text(message),
        )
        attempted_number = int(attempt.attempted_number or 0)
        if attempted_number:
            series.next_number = attempted_number
        series.blocked_at = None
        series.blocked_reason = ""
        series.blocked_by_document = None
        series.version = int(series.version or 0) + 1
        series.save(
            update_fields=[
                "next_number",
                "blocked_at",
                "blocked_reason",
                "blocked_by_document",
                "version",
                "updated_at",
            ]
        )
        document.transition_to(
            FISCAL_STATUS_READY_TO_ISSUE,
            number=None,
            payload_hash="",
            authorization_started_at=None,
            issued_at=None,
            error_code=error_code,
            error_message=sanitize_sensitive_text(message),
        )
    return FiscalEmissionOutcome(
        document=document,
        state=FISCAL_STATUS_READY_TO_ISSUE,
        message="La autorizacion no fue despachada; la operacion sigue lista.",
    )


def _persist_authorization_boundary(*, attempt_id):
    """Record possible dispatch before the SOAP transport is entered."""
    try:
        with transaction.atomic():
            attempt = FiscalEmissionAttempt.objects.select_for_update().get(
                pk=attempt_id
            )
            if not attempt.request_may_have_been_sent:
                attempt.mark_dispatched()
    except Exception as exc:
        raise ArcaConfigurationError(
            "No se pudo persistir la frontera de despacho fiscal."
        ) from exc


def emit_fiscal_document_now(
    *,
    fiscal_document: FiscalDocument,
    actor=None,
    client_factory: Callable = ArcaWsfeClient,
) -> FiscalEmissionOutcome:
    """Authorize exactly once; ambiguous outcomes become query-only."""

    document_id = getattr(fiscal_document, "id", None)
    if not document_id:
        raise ValidationError("Documento fiscal invalido.")

    started_at = timezone.now()
    client = None

    with transaction.atomic():
        document = (
            FiscalDocument.objects.select_for_update(of=("self",))
            .select_related(
                "company",
                "point_of_sale",
                "series",
                "related_document",
                "sales_document_type",
            )
            .prefetch_related("items")
            .get(pk=document_id)
        )
        if document.status in FISCAL_AUTHORIZED_STATUSES and document.cae:
            return FiscalEmissionOutcome(
                document=document,
                state=document.status,
                message="El comprobante ya estaba autorizado.",
            )
        _validate_before_submit(document)

        identity = _series_identity(document)
        series, _ = FiscalDocumentSeries.objects.get_or_create(
            company=document.company,
            point_of_sale_ref=document.point_of_sale,
            doc_type=document.doc_type,
            defaults={
                **identity,
                "next_number": 1,
            },
        )
        series = FiscalDocumentSeries.objects.select_for_update().get(pk=series.pk)
        if (
            series.issuer_cuit != identity["issuer_cuit"]
            or series.environment != identity["environment"]
            or series.point_of_sale != identity["point_of_sale"]
        ):
            raise ValidationError("La serie existente no coincide con la identidad fiscal congelada.")
        if series.blocked_at:
            raise ValidationError(
                "La serie fiscal esta bloqueada y requiere reconciliacion o recuperacion."
            )
        blocking_operation = (
            FiscalDocument.objects.filter(
                issuer_cuit_snapshot=identity["issuer_cuit"],
                environment_snapshot=identity["environment"],
                point_of_sale_number_snapshot=identity["point_of_sale"],
                doc_type=identity["doc_type"],
                status__in=FISCAL_UNCERTAIN_STATUSES,
            )
            .exclude(pk=document.pk)
            .first()
        )
        if blocking_operation:
            raise ValidationError(
                "Otra operacion incierta mantiene bloqueada esta serie fiscal."
            )

        client = client_factory(
            company=document.company,
            point_of_sale=document.point_of_sale,
        )
        local_before = int(series.next_number or 1)
        try:
            remote_last = int(
                client.fetch_last_authorized_number(doc_type=document.doc_type)
            )
        except Exception as exc:
            return _block_after_reconciliation_failure(
                document=document,
                series=series,
                actor=actor,
                reason=f"No se pudo consultar FECompUltimoAutorizado: {exc}",
            )
        if remote_last < 0:
            return _block_after_reconciliation_failure(
                document=document,
                series=series,
                actor=actor,
                reason="FECompUltimoAutorizado devolvio un valor invalido.",
            )

        local_last = max(local_before - 1, 0)
        if local_last > remote_last:
            series.remote_last_authorized = remote_last
            series.last_reconciled_at = timezone.now()
            series.blocked_at = timezone.now()
            series.blocked_reason = "La numeracion local esta adelantada a ARCA."
            series.blocked_by_document = document
            series.version = int(series.version or 0) + 1
            series.save(
                update_fields=[
                    "remote_last_authorized",
                    "last_reconciled_at",
                    "blocked_at",
                    "blocked_reason",
                    "blocked_by_document",
                    "version",
                    "updated_at",
                ]
            )
            _record_reconciliation(
                series=series,
                document=document,
                actor=actor,
                local_before=local_before,
                local_after=local_before,
                remote_last=remote_last,
                outcome=FiscalSeriesReconciliation.OUTCOME_BLOCKED,
                reason=series.blocked_reason,
            )
            document.transition_to(
                FISCAL_STATUS_MANUAL_REVIEW,
                error_code="local_number_ahead",
                error_message=series.blocked_reason,
            )
            return FiscalEmissionOutcome(
                document=document,
                state=FISCAL_STATUS_MANUAL_REVIEW,
                message=series.blocked_reason,
            )

        number = remote_last + 1
        local_after_reconcile = number
        outcome = (
            FiscalSeriesReconciliation.OUTCOME_ADVANCED
            if remote_last > local_last
            else FiscalSeriesReconciliation.OUTCOME_MATCHED
        )
        series.next_number = number + 1
        series.remote_last_authorized = remote_last
        series.last_reconciled_at = timezone.now()
        series.blocked_at = started_at
        series.blocked_reason = "Autorizacion fiscal en curso."
        series.blocked_by_document = document
        series.version = int(series.version or 0) + 1

        payload_hash = fiscal_payload_hash(
            {
                "snapshot_hash": document.snapshot_hash,
                **identity,
                "number": number,
            }
        )
        attempt_number = (
            document.emission_attempts.filter(
                operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE
            ).count()
            + 1
        )
        document.transition_to(
            FISCAL_STATUS_SUBMITTING,
            series=series,
            number=number,
            payload_hash=payload_hash,
            issued_at=started_at,
            authorization_started_at=started_at,
            attempts_count=int(document.attempts_count or 0) + 1,
            last_attempt_at=started_at,
            next_retry_at=None,
            error_code="",
            error_message="",
        )
        series.save(
            update_fields=[
                "next_number",
                "remote_last_authorized",
                "last_reconciled_at",
                "blocked_at",
                "blocked_reason",
                "blocked_by_document",
                "version",
                "updated_at",
            ]
        )
        _record_reconciliation(
            series=series,
            document=document,
            actor=actor,
            local_before=local_before,
            local_after=local_after_reconcile,
            remote_last=remote_last,
            outcome=outcome,
            reason="Reconciliacion previa obligatoria.",
        )
        attempt = FiscalEmissionAttempt.objects.create(
            fiscal_document=document,
            triggered_by=_actor_for_fk(actor),
            request_payload=_attempt_request_evidence(document, number=number),
            response_payload={},
            operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
            correlation_id=document.correlation_id,
            payload_hash=payload_hash,
            request_may_have_been_sent=False,
            issuer_cuit=identity["issuer_cuit"],
            environment=identity["environment"],
            point_of_sale=identity["point_of_sale"],
            doc_type=identity["doc_type"],
            attempted_number=number,
            attempt_number=attempt_number,
            will_retry=False,
        )

    try:
        result = client.emit_fiscal_document(
            fiscal_document=document,
            cbte_number=number,
            mark_dispatched=lambda: _persist_authorization_boundary(
                attempt_id=attempt.id
            ),
        )
    except ArcaTemporaryError as exc:
        if not exc.possibly_sent:
            return _finalize_safe_predispatch_failure(
                document_id=document_id,
                attempt_id=attempt.id,
                error_code=exc.error_code or "predispatch_temporary_error",
                message=str(exc),
            )
        result = None
        final_state = FISCAL_STATUS_UNCERTAIN
        result_status = FISCAL_ATTEMPT_RESULT_UNCERTAIN
        response_payload = exc.response_payload or {}
        error_code = exc.error_code or "uncertain_transport"
        error_message = str(exc)
        cae = ""
        cae_due_date = None
    except ArcaConfigurationError as exc:
        return _finalize_safe_predispatch_failure(
            document_id=document_id,
            attempt_id=attempt.id,
            error_code="arca_configuration",
            message=str(exc),
        )
    except Exception as exc:
        result = None
        final_state = FISCAL_STATUS_UNCERTAIN
        result_status = FISCAL_ATTEMPT_RESULT_UNCERTAIN
        response_payload = {}
        error_code = "unexpected_after_authorization_boundary"
        error_message = f"Resultado de autorizacion incierto: {exc}"
        cae = ""
        cae_due_date = None
    else:
        final_state = {
            "authorized": FISCAL_STATUS_AUTHORIZED,
            "authorized_with_observations": FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
            "rejected": FISCAL_STATUS_REJECTED,
            "uncertain": FISCAL_STATUS_UNCERTAIN,
        }.get(result.state, FISCAL_STATUS_UNCERTAIN)
        result_status = (
            FISCAL_ATTEMPT_RESULT_SUCCESS
            if final_state in {
                FISCAL_STATUS_AUTHORIZED,
                FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
            }
            else (
                FISCAL_ATTEMPT_RESULT_ERROR
                if final_state == FISCAL_STATUS_REJECTED
                else FISCAL_ATTEMPT_RESULT_UNCERTAIN
            )
        )
        response_payload = result.response_payload or {}
        error_code = result.error_code or ""
        error_message = result.error_message or ""
        cae = result.cae or ""
        cae_due_date = result.cae_due_date

    if final_state in {
        FISCAL_STATUS_AUTHORIZED,
        FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
    } and (not cae or not cae_due_date):
        final_state = FISCAL_STATUS_UNCERTAIN
        result_status = FISCAL_ATTEMPT_RESULT_UNCERTAIN
        error_code = "authorized_response_incomplete"
        error_message = "La respuesta autorizada no incluyo CAE y vencimiento completos."

    finished_at = timezone.now()
    duration_ms = max(int((finished_at - started_at).total_seconds() * 1000), 0)
    response_payload = sanitize_sensitive_payload(response_payload)
    error_message = sanitize_sensitive_text(error_message)

    with transaction.atomic():
        document = FiscalDocument.objects.select_for_update().get(pk=document_id)
        attempt = FiscalEmissionAttempt.objects.select_for_update().get(pk=attempt.id)
        series = FiscalDocumentSeries.objects.select_for_update().get(pk=document.series_id)
        if not attempt.request_may_have_been_sent:
            # Defensive compatibility for a custom test/provider adapter that
            # returned without honoring the dispatch callback.
            attempt.mark_dispatched()
        attempt.finalize(
            result_status=result_status,
            response_payload=response_payload,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )

        if final_state in {
            FISCAL_STATUS_AUTHORIZED,
            FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
        }:
            series.remote_last_authorized = number
            series.next_number = number + 1
            series.blocked_at = None
            series.blocked_reason = ""
            series.blocked_by_document = None

        if final_state == FISCAL_STATUS_REJECTED:
            series.next_number = number
            series.blocked_at = None
            series.blocked_reason = ""
            series.blocked_by_document = None

        if final_state == FISCAL_STATUS_UNCERTAIN:
            series.blocked_at = series.blocked_at or finished_at
            series.blocked_reason = "Resultado de autorizacion incierto; sólo se permite consultar."
            series.blocked_by_document = document

        series.version = int(series.version or 0) + 1
        series.save(
            update_fields=[
                "next_number",
                "remote_last_authorized",
                "blocked_at",
                "blocked_reason",
                "blocked_by_document",
                "version",
                "updated_at",
            ]
        )

        transition_changes = {
            "error_code": error_code,
            "error_message": error_message,
            "response_payload": response_payload,
            "last_attempt_at": finished_at,
            "next_retry_at": None,
        }
        if final_state in {
            FISCAL_STATUS_AUTHORIZED,
            FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
        }:
            transition_changes.update(
                {
                    "cae": cae,
                    "cae_due_date": cae_due_date,
                    "resolved_at": finished_at,
                }
            )
        elif final_state == FISCAL_STATUS_REJECTED:
            transition_changes.update(
                {
                    "number": None,
                    "resolved_at": finished_at,
                }
            )
        document.transition_to(final_state, **transition_changes)

    if final_state in {
        FISCAL_STATUS_AUTHORIZED,
        FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS,
    }:
        sync_sales_document_type_counter(
            sales_document_type=document.sales_document_type,
            number=number,
        )
        try:
            sync_fiscal_document_account_movement(
                fiscal_document=document,
                actor=actor,
            )
            if document.order_id:
                ensure_stock_movements_for_order_document(
                    order=document.order,
                    company=document.company,
                    sales_document_type=document.sales_document_type,
                    actor=actor,
                    fiscal_document=document,
                )
        except Exception:
            logger.exception(
                "Fallo una proyeccion comercial posterior a CAE para documento %s",
                document.pk,
            )

    message = {
        FISCAL_STATUS_AUTHORIZED: "Comprobante autorizado en ARCA.",
        FISCAL_STATUS_AUTHORIZED_WITH_OBSERVATIONS: (
            "Comprobante autorizado en ARCA con observaciones."
        ),
        FISCAL_STATUS_REJECTED: (
            "ARCA rechazo esta operacion; una correccion requiere una nueva operacion."
        ),
        FISCAL_STATUS_UNCERTAIN: (
            "Resultado incierto: no se reenviara y sólo se consultara el comprobante."
        ),
    }[final_state]
    return FiscalEmissionOutcome(
        document=document,
        state=final_state,
        message=message,
    )
