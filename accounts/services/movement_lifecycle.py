"""
Reusable movement lifecycle rules for current-account transactions.

This module centralizes the semantics for:
- Cerrar (close / post movement)
- Dejar abierta (reopen movement)
- Anular (void movement)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import ClientTransaction
from core.models import (
    DocumentSeries,
    FISCAL_INVOICE_DOC_TYPES,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_VOIDED,
    FiscalDocument,
    InternalDocument,
)
from orders.models import Order


@dataclass(frozen=True)
class MovementTransitionResult:
    changed: bool
    current_state: str
    target_state: str
    linked_order_id: Optional[int] = None
    linked_order_status_before: str = ""
    linked_order_status_after: str = ""
    order_side_effect_note: str = ""


def movement_allows_print(transaction_obj: Optional[ClientTransaction]) -> bool:
    """
    Only closed movements can be printed/downloaded.
    If no movement exists, keep backward-compatible permissive behavior.
    """
    if not transaction_obj:
        return True
    state = transaction_obj.movement_state or ClientTransaction.STATE_OPEN
    return state == ClientTransaction.STATE_CLOSED


def is_transaction_reopen_locked(transaction_obj: Optional[ClientTransaction]) -> bool:
    """
    Closed movements tied to final commercial documents are immutable.

    Lock reopen for:
    - billed fiscal invoices (authorized/external recorded),
    - non-draft operational orders,
    - generated remitos,
    - external SaaS invoice markers.
    """
    if not transaction_obj:
        return False
    if transaction_obj.transaction_type != ClientTransaction.TYPE_ORDER_CHARGE:
        return False
    if not transaction_obj.order_id:
        return False

    fiscal_qs = FiscalDocument.objects.filter(
        order_id=transaction_obj.order_id,
        doc_type__in=tuple(sorted(FISCAL_INVOICE_DOC_TYPES)),
    ).exclude(status=FISCAL_STATUS_VOIDED)
    if transaction_obj.company_id:
        fiscal_qs = fiscal_qs.filter(company_id=transaction_obj.company_id)
    if fiscal_qs.filter(
        status__in=[FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED]
    ).exists():
        return True

    order_obj = getattr(transaction_obj, "order", None)
    if order_obj is None:
        order_obj = (
            Order.objects.only("id", "status", "saas_document_type", "saas_document_number")
            .filter(pk=transaction_obj.order_id)
            .first()
        )
    if not order_obj:
        return False

    normalized_status = (
        order_obj.normalized_status()
        if hasattr(order_obj, "normalized_status")
        else Order.LEGACY_STATUS_MAP.get(getattr(order_obj, "status", ""), getattr(order_obj, "status", ""))
    )
    if normalized_status != Order.STATUS_DRAFT:
        return True

    if order_obj.saas_document_type or order_obj.saas_document_number:
        return True

    remito_qs = InternalDocument.objects.filter(
        order_id=transaction_obj.order_id,
        doc_type=DocumentSeries.DOC_REM,
    )
    if transaction_obj.company_id:
        remito_qs = remito_qs.filter(company_id=transaction_obj.company_id)
    return remito_qs.exists()


def can_transition_transaction_state(
    transaction_obj: Optional[ClientTransaction],
    target_state: str,
) -> tuple[bool, str]:
    """
    Lightweight guard for UI rendering.
    Returns (allowed, reason_if_blocked).
    """
    if not transaction_obj:
        return False, "No hay movimiento para gestionar."
    current_state = transaction_obj.movement_state or ClientTransaction.STATE_OPEN
    if current_state == target_state:
        state_label = dict(ClientTransaction.STATE_CHOICES).get(current_state, current_state)
        return False, f"El movimiento ya estaba en estado {state_label}."
    try:
        _validate_transition(transaction_obj=transaction_obj, target_state=target_state)
        return True, ""
    except ValidationError as exc:
        if hasattr(exc, "messages") and exc.messages:
            return False, "; ".join(exc.messages)
        return False, str(exc)


def apply_transaction_state_transition(
    *,
    transaction_obj: ClientTransaction,
    target_state: str,
    actor=None,
) -> MovementTransitionResult:
    """
    Apply one movement transition with side effects on linked order when required.
    """
    target_state = str(target_state or "").strip().lower()
    linked_order = _validate_transition(transaction_obj=transaction_obj, target_state=target_state)

    current_state = transaction_obj.movement_state or ClientTransaction.STATE_OPEN
    linked_order_status_before = linked_order.normalized_status() if linked_order else ""
    if current_state == target_state:
        return MovementTransitionResult(
            changed=False,
            current_state=current_state,
            target_state=target_state,
            linked_order_id=linked_order.pk if linked_order else None,
            linked_order_status_before=linked_order_status_before,
            linked_order_status_after=linked_order_status_before,
        )

    order_side_effect_note = ""
    with transaction.atomic():
        now = timezone.now()
        transaction_obj.movement_state = target_state
        if target_state == ClientTransaction.STATE_CLOSED:
            transaction_obj.closed_at = now
            transaction_obj.voided_at = None
        elif target_state == ClientTransaction.STATE_VOIDED:
            transaction_obj.voided_at = now
        else:
            transaction_obj.closed_at = None
            transaction_obj.voided_at = None
        transaction_obj.save(update_fields=["movement_state", "closed_at", "voided_at", "updated_at"])

        if linked_order:
            normalized_status = linked_order.normalized_status()
            if target_state == ClientTransaction.STATE_CLOSED and normalized_status == Order.STATUS_DRAFT:
                changed = linked_order.change_status(
                    Order.STATUS_CONFIRMED,
                    changed_by=actor,
                    note="Confirmado al cerrar movimiento en cuenta corriente.",
                )
                if changed:
                    order_side_effect_note = " Pedido confirmado."
            elif target_state == ClientTransaction.STATE_VOIDED and normalized_status != Order.STATUS_CANCELLED:
                changed = linked_order.change_status(
                    Order.STATUS_CANCELLED,
                    changed_by=actor,
                    note="Cancelado al anular movimiento en cuenta corriente.",
                )
                if changed:
                    order_side_effect_note = " Pedido cancelado."

    linked_order_status_after = linked_order.normalized_status() if linked_order else ""
    return MovementTransitionResult(
        changed=True,
        current_state=current_state,
        target_state=target_state,
        linked_order_id=linked_order.pk if linked_order else None,
        linked_order_status_before=linked_order_status_before,
        linked_order_status_after=linked_order_status_after,
        order_side_effect_note=order_side_effect_note,
    )


def _validate_transition(
    *,
    transaction_obj: ClientTransaction,
    target_state: str,
) -> Optional[Order]:
    allowed_states = {
        ClientTransaction.STATE_OPEN,
        ClientTransaction.STATE_CLOSED,
        ClientTransaction.STATE_VOIDED,
    }
    if target_state not in allowed_states:
        raise ValidationError("Estado de movimiento invalido.")

    linked_order = (
        transaction_obj.order
        if transaction_obj.transaction_type == ClientTransaction.TYPE_ORDER_CHARGE and transaction_obj.order_id
        else None
    )
    current_state = transaction_obj.movement_state or ClientTransaction.STATE_OPEN
    linked_order_status = linked_order.normalized_status() if linked_order else ""

    # Voided movements are terminal for operational consistency/audit.
    if current_state == ClientTransaction.STATE_VOIDED and target_state != ClientTransaction.STATE_VOIDED:
        raise ValidationError(
            "El movimiento esta anulado y no puede volver a abierto/cerrado."
        )

    if target_state == ClientTransaction.STATE_OPEN and is_transaction_reopen_locked(transaction_obj):
        raise ValidationError(
            "Los movimientos asociados a factura o remito ya estan en registracion final y no pueden volver a estado abierto."
        )
    if target_state == ClientTransaction.STATE_CLOSED and linked_order_status == Order.STATUS_CANCELLED:
        raise ValidationError(
            "No puedes cerrar el movimiento porque el pedido asociado ya esta cancelado."
        )
    if (
        target_state == ClientTransaction.STATE_VOIDED
        and linked_order
        and linked_order_status != Order.STATUS_CANCELLED
        and not linked_order.can_transition_to(Order.STATUS_CANCELLED)
    ):
        raise ValidationError(
            "No se puede anular el movimiento porque el pedido asociado ya esta en estado final."
        )
    return linked_order
