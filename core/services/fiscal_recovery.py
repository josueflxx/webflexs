"""Query-only recovery for ambiguous ARCA authorization outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Callable
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.services.account_movement_service import (
    sync_fiscal_document_account_movement,
)
from core.models import (
    FISCAL_ATTEMPT_OPERATION_RECOVER,
    FISCAL_ATTEMPT_RESULT_ERROR,
    FISCAL_ATTEMPT_RESULT_NOT_FOUND,
    FISCAL_ATTEMPT_RESULT_RECOVERED,
    FISCAL_STATUS_MANUAL_REVIEW,
    FISCAL_STATUS_RECOVERED_AUTHORIZED,
    FISCAL_STATUS_RECOVERED_NOT_FOUND,
    FISCAL_STATUS_RECOVERY_PENDING,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_UNCERTAIN,
    FiscalDocument,
    FiscalDocumentSeries,
    FiscalEmissionAttempt,
    FiscalMutationAudit,
    FiscalSeriesReconciliation,
)
from core.services.arca_client import ArcaWsfeClient
from core.services.sales_documents import (
    ensure_stock_movements_for_order_document,
    sync_sales_document_type_counter,
)
from core.services.sensitive_data import (
    sanitize_sensitive_payload,
    sanitize_sensitive_text,
)


logger = logging.getLogger(__name__)


@dataclass
class FiscalRecoveryOutcome:
    document: FiscalDocument
    state: str
    message: str
    consulted: bool = False


def _actor_for_fk(actor):
    return actor if getattr(actor, "is_authenticated", False) else None


def _recovery_delay():
    minutes = max(int(getattr(settings, "FISCAL_RECOVERY_WAIT_MINUTES", 15) or 15), 1)
    return timedelta(minutes=minutes)


def recover_fiscal_document(
    *,
    fiscal_document,
    actor=None,
    client_factory: Callable = ArcaWsfeClient,
    allow_stale_sending=False,
) -> FiscalRecoveryOutcome:
    """Run FECompConsultar once. This function never calls FECAESolicitar."""

    document_id = getattr(fiscal_document, "pk", None)
    if not document_id:
        raise ValidationError("Documento fiscal invalido.")
    now = timezone.now()
    max_consultations = max(
        int(getattr(settings, "FISCAL_RECOVERY_MAX_CONSULTATIONS", 5) or 5),
        1,
    )
    stale_minutes = max(
        int(getattr(settings, "FISCAL_RECOVERY_IN_PROGRESS_MINUTES", 10) or 10),
        1,
    )

    with transaction.atomic():
        document = (
            FiscalDocument.objects.select_for_update(of=("self",))
            .select_related("company", "point_of_sale", "series", "sales_document_type")
            .get(pk=document_id)
        )
        if document.status == FISCAL_STATUS_RECOVERED_AUTHORIZED and document.cae:
            return FiscalRecoveryOutcome(
                document=document,
                state=document.status,
                message="El comprobante ya fue recuperado como autorizado.",
                consulted=False,
            )
        if document.status == FISCAL_STATUS_SUBMITTING:
            stale_cutoff = now - timedelta(
                minutes=max(
                    int(
                        getattr(
                            settings,
                            "FISCAL_SUBMITTING_TIMEOUT_MINUTES",
                            20,
                        )
                        or 20
                    ),
                    5,
                )
            )
            reference = document.authorization_started_at or document.updated_at
            if not allow_stale_sending or reference > stale_cutoff:
                raise ValidationError("La autorizacion todavia figura en curso.")
            document.transition_to(
                FISCAL_STATUS_UNCERTAIN,
                error_code="stale_sending",
                error_message=(
                    "El worker quedo interrumpido luego de abrir el limite de autorizacion."
                ),
            )

        if (
            document.status == FISCAL_STATUS_RECOVERED_NOT_FOUND
            and document.next_recovery_at
            and document.next_recovery_at > now
        ):
            return FiscalRecoveryOutcome(
                document=document,
                state=document.status,
                message="La siguiente consulta de recuperacion todavia no esta habilitada.",
                consulted=False,
            )
        if document.status == FISCAL_STATUS_MANUAL_REVIEW:
            raise ValidationError(
                "La politica automatica se agoto; se requiere revision fiscal administrativa."
            )
        if document.status == FISCAL_STATUS_RECOVERY_PENDING:
            pending = (
                document.emission_attempts.filter(
                    operation=FISCAL_ATTEMPT_OPERATION_RECOVER,
                    result_status="pending",
                )
                .order_by("-created_at")
                .first()
            )
            if pending and pending.created_at > now - timedelta(minutes=stale_minutes):
                return FiscalRecoveryOutcome(
                    document=document,
                    state=document.status,
                    message="Ya existe una consulta de recuperacion en curso.",
                    consulted=False,
                )
            if pending:
                pending.finalize(
                    result_status=FISCAL_ATTEMPT_RESULT_ERROR,
                    response_payload={},
                    error_code="stale_recovery_attempt",
                    error_message="La consulta anterior quedo interrumpida.",
                )
        elif document.status in {
            FISCAL_STATUS_UNCERTAIN,
            FISCAL_STATUS_RECOVERED_NOT_FOUND,
        }:
            document.transition_to(FISCAL_STATUS_RECOVERY_PENDING)
        else:
            raise ValidationError(
                "El documento no esta en un estado consultable de recuperacion."
            )

        if not document.number or not document.payload_hash or not document.series_id:
            document.transition_to(
                FISCAL_STATUS_MANUAL_REVIEW,
                error_code="recovery_identity_incomplete",
                error_message=(
                    "La operacion incierta no conserva numero, hash o serie completos."
                ),
            )
            return FiscalRecoveryOutcome(
                document=document,
                state=document.status,
                message=document.error_message,
                consulted=False,
            )

        recovery_number = int(document.recovery_attempts_count or 0) + 1
        document.recovery_attempts_count = recovery_number
        document.last_recovery_at = now
        document.next_recovery_at = None
        document.save(
            update_fields=[
                "recovery_attempts_count",
                "last_recovery_at",
                "next_recovery_at",
                "updated_at",
            ]
        )
        attempt = FiscalEmissionAttempt.objects.create(
            fiscal_document=document,
            triggered_by=_actor_for_fk(actor),
            request_payload={
                "correlation_id": str(document.correlation_id),
                "payload_hash": document.payload_hash,
                "issuer_cuit": document.issuer_cuit_snapshot,
                "environment": document.environment_snapshot,
                "point_of_sale": document.point_of_sale_number_snapshot,
                "doc_type": document.doc_type,
                "cbte_number": document.number,
                "method": "FECompConsultar",
            },
            operation=FISCAL_ATTEMPT_OPERATION_RECOVER,
            correlation_id=uuid.uuid4(),
            payload_hash=document.payload_hash,
            request_may_have_been_sent=False,
            issuer_cuit=document.issuer_cuit_snapshot,
            environment=document.environment_snapshot,
            point_of_sale=document.point_of_sale_number_snapshot,
            doc_type=document.doc_type,
            attempted_number=document.number,
            attempt_number=recovery_number,
            will_retry=False,
        )
        client = client_factory(
            company=document.company,
            point_of_sale=document.point_of_sale,
        )

    attempt.mark_dispatched()
    try:
        result = client.consult_fiscal_document(
            doc_type=document.doc_type,
            cbte_number=document.number,
        )
    except Exception as exc:
        result = None
        state = "error"
        error_code = "recovery_transport_error"
        error_message = str(exc)
        response_payload = {}
    else:
        state = result.state
        error_code = result.error_code or ""
        error_message = result.error_message or ""
        response_payload = result.response_payload or {}

    response_payload = sanitize_sensitive_payload(response_payload)
    error_message = sanitize_sensitive_text(error_message)

    with transaction.atomic():
        document = FiscalDocument.objects.select_for_update().get(pk=document_id)
        attempt = FiscalEmissionAttempt.objects.select_for_update().get(pk=attempt.pk)
        series = FiscalDocumentSeries.objects.select_for_update().get(pk=document.series_id)

        if state == "authorized" and result and result.cae and result.cae_due_date:
            attempt.finalize(
                result_status=FISCAL_ATTEMPT_RESULT_RECOVERED,
                response_payload=response_payload,
                error_code=error_code,
                error_message=error_message,
            )
            recovered_at = timezone.now()
            document.transition_to(
                FISCAL_STATUS_RECOVERED_AUTHORIZED,
                cae=result.cae,
                cae_due_date=result.cae_due_date,
                response_payload=response_payload,
                resolved_at=recovered_at,
                next_recovery_at=None,
                error_code="",
                error_message="",
            )
            series.remote_last_authorized = max(
                int(series.remote_last_authorized or 0),
                int(document.number),
            )
            series.next_number = max(
                int(series.next_number or 1),
                int(document.number) + 1,
            )
            series.blocked_at = None
            series.blocked_reason = ""
            series.blocked_by_document = None
            series.version = int(series.version or 0) + 1
            series.save(
                update_fields=[
                    "remote_last_authorized",
                    "next_number",
                    "blocked_at",
                    "blocked_reason",
                    "blocked_by_document",
                    "version",
                    "updated_at",
                ]
            )
            final_message = "El comprobante fue encontrado y recuperado como autorizado."
        elif state == "not_found":
            attempt.finalize(
                result_status=FISCAL_ATTEMPT_RESULT_NOT_FOUND,
                response_payload=response_payload,
                error_code=error_code,
                error_message=error_message,
            )
            if int(document.recovery_attempts_count or 0) >= max_consultations:
                document.transition_to(
                    FISCAL_STATUS_MANUAL_REVIEW,
                    response_payload=response_payload,
                    next_recovery_at=None,
                    error_code="recovery_not_found_limit",
                    error_message=(
                        "El comprobante no fue encontrado tras agotar la politica de consultas."
                    ),
                )
                final_message = document.error_message
            else:
                document.transition_to(
                    FISCAL_STATUS_RECOVERED_NOT_FOUND,
                    response_payload=response_payload,
                    next_recovery_at=timezone.now() + _recovery_delay(),
                    error_code="recovery_not_found",
                    error_message=(
                        "ARCA no encontro el comprobante; la serie sigue bloqueada hasta otra consulta."
                    ),
                )
                final_message = document.error_message
        else:
            attempt.finalize(
                result_status=FISCAL_ATTEMPT_RESULT_ERROR,
                response_payload=response_payload,
                error_code=error_code or "recovery_inconclusive",
                error_message=error_message or "La consulta no fue concluyente.",
            )
            document.error_code = error_code or "recovery_inconclusive"
            document.error_message = error_message or "La consulta no fue concluyente."
            document.response_payload = response_payload
            document.next_recovery_at = timezone.now() + _recovery_delay()
            document.save(
                update_fields=[
                    "error_code",
                    "error_message",
                    "response_payload",
                    "next_recovery_at",
                    "updated_at",
                ],
                allow_fiscal_transition=True,
            )
            final_message = document.error_message

    if document.status == FISCAL_STATUS_RECOVERED_AUTHORIZED:
        sync_sales_document_type_counter(
            sales_document_type=document.sales_document_type,
            number=document.number,
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
                "Fallo una proyeccion comercial posterior a recuperacion del documento %s",
                document.pk,
            )

    return FiscalRecoveryOutcome(
        document=document,
        state=document.status,
        message=final_message,
        consulted=True,
    )


def release_pre_dispatch_manual_review(
    *,
    fiscal_document,
    actor,
    client_factory: Callable = ArcaWsfeClient,
):
    """Audited admin-only release for failures that happened before numbering."""

    if not getattr(actor, "is_authenticated", False) or not (
        getattr(actor, "is_superuser", False)
        or actor.has_perm("core.change_fiscaldocument")
    ):
        raise PermissionDenied("Se requiere permiso fiscal administrativo.")

    with transaction.atomic():
        document = FiscalDocument.objects.select_for_update().get(pk=fiscal_document.pk)
        if document.status != FISCAL_STATUS_MANUAL_REVIEW or document.number is not None:
            raise ValidationError(
                "Sólo puede liberarse una revision previa al despacho y sin numero intentado."
            )
        series = (
            FiscalDocumentSeries.objects.select_for_update()
            .filter(blocked_by_document=document)
            .first()
        )
        if not series:
            raise ValidationError("No se encontro la serie bloqueada de la operacion.")
        client = client_factory(
            company=document.company,
            point_of_sale=document.point_of_sale,
        )
        remote_last = int(
            client.fetch_last_authorized_number(doc_type=document.doc_type)
        )
        local_before = int(series.next_number or 1)
        if local_before - 1 > remote_last:
            raise ValidationError(
                "La numeracion local sigue adelantada; no puede liberarse la serie."
            )
        series.next_number = remote_last + 1
        series.remote_last_authorized = remote_last
        series.last_reconciled_at = timezone.now()
        series.blocked_at = None
        series.blocked_reason = ""
        series.blocked_by_document = None
        series.version = int(series.version or 0) + 1
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
        FiscalSeriesReconciliation.objects.create(
            series=series,
            fiscal_document=document,
            triggered_by=actor,
            correlation_id=document.correlation_id,
            issuer_cuit=series.issuer_cuit,
            environment=series.environment,
            point_of_sale=series.point_of_sale,
            doc_type=series.doc_type,
            local_next_before=local_before,
            local_next_after=remote_last + 1,
            remote_last_authorized=remote_last,
            outcome=FiscalSeriesReconciliation.OUTCOME_ADVANCED,
            reason="Liberacion administrativa posterior a reconciliacion remota.",
        )
        FiscalMutationAudit.objects.create(
            fiscal_document=document,
            actor=actor,
            action="manual_review_release",
            attempted_fields=["status"],
            reason="remote_last_reconciled_before_dispatch",
            source="fiscal_recovery",
            correlation_id=document.correlation_id,
        )
        document.transition_to(
            FISCAL_STATUS_READY_TO_ISSUE,
            error_code="",
            error_message="",
        )
        return document
