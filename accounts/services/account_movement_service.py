"""Unified current-account movement service.

This module centralizes the business rules that decide when a commercial
document or payment should create/update one ``ClientTransaction`` row.
Existing callers can keep using ``accounts.services.ledger`` as a
backward-compatible facade.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from accounts.models import ClientPayment, ClientProfile, ClientTransaction
from core.models import (
    FISCAL_BILLABLE_DOC_TYPES,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BEHAVIOR_NOTA_DEBITO,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_REMITO,
)
from orders.models import Order


BILLABLE_ORDER_STATUSES = {
    Order.STATUS_CONFIRMED,
    Order.STATUS_PREPARING,
    Order.STATUS_SHIPPED,
    Order.STATUS_DELIVERED,
}

BILLABLE_FISCAL_DOCUMENT_STATUSES = {
    "authorized",
    "external_recorded",
}

# Quotes and budgets never impact current account, even if one type is
# misconfigured manually at the database level.
ACCOUNTABLE_INTERNAL_BEHAVIORS = {
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_REMITO,
    SALES_BEHAVIOR_FACTURA,
}

ACCOUNT_ADJUSTMENT_FISCAL_BEHAVIORS = {
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BEHAVIOR_NOTA_DEBITO,
}


def _to_decimal(value):
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _resolve_client_profile_from_order(order):
    if not order or not getattr(order, "user_id", None):
        return None
    return ClientProfile.objects.filter(user_id=order.user_id).first()


def _resolve_company_for_record(*, company=None, order=None):
    resolved_company = company or getattr(order, "company", None)
    if resolved_company:
        return resolved_company
    try:
        from core.services.company_context import get_default_company

        return get_default_company()
    except Exception:
        return None


def get_order_billable_fiscal_document(order):
    if not order:
        return None
    from core.models import FiscalDocument

    return (
        FiscalDocument.objects.select_related("sales_document_type", "point_of_sale")
        .filter(
            order=order,
            doc_type__in=FISCAL_BILLABLE_DOC_TYPES,
            status__in=BILLABLE_FISCAL_DOCUMENT_STATUSES,
        )
        .exclude(status="voided")
        .order_by("-issued_at", "-created_at", "-id")
        .first()
    )


def get_order_accountable_internal_document(order):
    """Return the newest internal document allowed to impact current account."""
    if not order:
        return None
    from core.models import InternalDocument

    return (
        InternalDocument.objects.select_related("sales_document_type")
        .filter(
            order=order,
            is_cancelled=False,
            sales_document_type__isnull=False,
            sales_document_type__enabled=True,
            sales_document_type__generate_account_movement=True,
            sales_document_type__document_behavior__in=ACCOUNTABLE_INTERNAL_BEHAVIORS,
        )
        .order_by("-issued_at", "-created_at", "-id")
        .first()
    )


def resolve_order_charge_snapshot(order):
    """Resolve whether one order should currently impact current account."""
    billable_document = get_order_billable_fiscal_document(order)
    if billable_document:
        return {
            "amount": _to_decimal(order.total).quantize(Decimal("0.01")),
            "occurred_at": getattr(billable_document, "issued_at", None)
            or getattr(billable_document, "created_at", None)
            or getattr(order, "status_updated_at", None)
            or timezone.now(),
            "description": (
                f"Venta facturada {billable_document.commercial_type_label} "
                f"{billable_document.display_number}"
            ).strip(),
        }

    if order and (
        getattr(order, "saas_document_number", "")
        or getattr(order, "saas_document_type", "")
    ):
        return {
            "amount": _to_decimal(order.total).quantize(Decimal("0.01")),
            "occurred_at": getattr(order, "status_updated_at", None) or timezone.now(),
            "description": (
                f"Venta facturada en SaaS "
                f"{getattr(order, 'saas_document_type', '')} "
                f"{getattr(order, 'saas_document_number', '')}"
            ).strip(),
        }

    internal_document = get_order_accountable_internal_document(order)
    if internal_document:
        return {
            "amount": _to_decimal(order.total).quantize(Decimal("0.01")),
            "occurred_at": getattr(internal_document, "issued_at", None)
            or getattr(internal_document, "created_at", None)
            or getattr(order, "status_updated_at", None)
            or timezone.now(),
            "description": (
                f"Movimiento comercial {internal_document.commercial_type_label} "
                f"{internal_document.display_number}"
            ).strip(),
        }

    return {
        "amount": Decimal("0.00"),
        "occurred_at": getattr(order, "status_updated_at", None) or timezone.now(),
        "description": f"Pedido #{order.pk} sin comprobante imputable a cuenta corriente",
    }


def sync_order_charge_transaction(order, actor=None):
    """Upsert one idempotent ledger row for the order commercial charge."""
    client_profile = _resolve_client_profile_from_order(order)
    if not client_profile:
        return None

    company = _resolve_company_for_record(order=order)
    charge_snapshot = resolve_order_charge_snapshot(order)
    defaults = {
        "client_profile": client_profile,
        "company": company,
        "billing_company": getattr(company, "slug", None) or "flexs",
        "order": order,
        "transaction_type": ClientTransaction.TYPE_ORDER_CHARGE,
        "amount": charge_snapshot["amount"],
        "description": charge_snapshot["description"],
        "occurred_at": charge_snapshot["occurred_at"],
        "created_by": actor if getattr(actor, "is_authenticated", False) else None,
    }
    tx, _ = ClientTransaction.objects.update_or_create(
        source_key=f"order:{order.pk}:charge",
        defaults=defaults,
    )
    return tx


def sync_payment_transaction(payment, actor=None):
    """Upsert one idempotent ledger row for payment application."""
    if not payment or not getattr(payment, "client_profile_id", None):
        return None

    company = getattr(payment, "company", None)
    if not company and getattr(payment, "order_id", None):
        company = getattr(payment.order, "company", None)
    company = _resolve_company_for_record(company=company, order=getattr(payment, "order", None))

    amount = Decimal("0.00")
    if not payment.is_cancelled:
        amount = (_to_decimal(payment.amount) * Decimal("-1")).quantize(Decimal("0.01"))

    defaults = {
        "client_profile": payment.client_profile,
        "company": company,
        "billing_company": getattr(company, "slug", None) or "flexs",
        "order": payment.order,
        "payment": payment,
        "transaction_type": ClientTransaction.TYPE_PAYMENT,
        "amount": amount,
        "description": f"Pago #{payment.pk} - {payment.get_method_display()}",
        "occurred_at": getattr(payment, "paid_at", None) or timezone.now(),
        "created_by": (
            actor
            if getattr(actor, "is_authenticated", False)
            else (payment.created_by if payment.created_by_id else None)
        ),
    }
    tx, _ = ClientTransaction.objects.update_or_create(
        source_key=f"payment:{payment.pk}:applied",
        defaults=defaults,
    )
    return tx


def create_adjustment_transaction(
    *,
    client_profile,
    amount,
    reason,
    actor=None,
    order=None,
    company=None,
    occurred_at=None,
):
    """Create a manual ledger adjustment."""
    occurred = occurred_at or timezone.now()
    dec_amount = _to_decimal(amount).quantize(Decimal("0.01"))
    suffix = timezone.now().strftime("%Y%m%d%H%M%S%f")
    resolved_company = _resolve_company_for_record(company=company, order=order)
    if not resolved_company:
        raise ValueError("Empresa obligatoria para ajustes de cuenta corriente.")

    tx = ClientTransaction.objects.create(
        client_profile=client_profile,
        company=resolved_company,
        billing_company=getattr(resolved_company, "slug", None) or "flexs",
        order=order,
        payment=None,
        transaction_type=ClientTransaction.TYPE_ADJUSTMENT,
        amount=dec_amount,
        description=(reason or "").strip(),
        source_key=f"adjustment:{client_profile.pk}:{suffix}",
        occurred_at=occurred,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    try:
        from core.services.documents import ensure_document_for_adjustment

        ensure_document_for_adjustment(tx)
    except Exception:
        pass
    return tx


def ensure_adjustment_transaction(
    *,
    source_key,
    client_profile,
    amount,
    reason,
    actor=None,
    order=None,
    company=None,
    occurred_at=None,
):
    """Upsert one idempotent adjustment row keyed by ``source_key``."""
    if not source_key:
        raise ValueError("source_key obligatorio para ajustes idempotentes.")

    resolved_company = _resolve_company_for_record(company=company, order=order)
    if not resolved_company:
        raise ValueError("Empresa obligatoria para ajustes de cuenta corriente.")

    defaults = {
        "client_profile": client_profile,
        "company": resolved_company,
        "billing_company": getattr(resolved_company, "slug", None) or "flexs",
        "order": order,
        "payment": None,
        "transaction_type": ClientTransaction.TYPE_ADJUSTMENT,
        "amount": _to_decimal(amount).quantize(Decimal("0.01")),
        "description": (reason or "").strip(),
        "occurred_at": occurred_at or timezone.now(),
        "created_by": actor if getattr(actor, "is_authenticated", False) else None,
    }
    tx, _ = ClientTransaction.objects.update_or_create(
        source_key=source_key,
        defaults=defaults,
    )
    try:
        from core.services.documents import ensure_document_for_adjustment

        ensure_document_for_adjustment(tx)
    except Exception:
        pass
    return tx


def _resolve_fiscal_adjustment_client_profile(fiscal_document):
    client_profile = getattr(fiscal_document, "client_profile", None)
    if not client_profile and getattr(fiscal_document, "client_company_ref", None):
        client_profile = fiscal_document.client_company_ref.client_profile
    return client_profile


def _resolve_fiscal_adjustment_snapshot(*, fiscal_document):
    sales_document_type = getattr(fiscal_document, "sales_document_type", None)
    if not fiscal_document or not sales_document_type or not sales_document_type.generate_account_movement:
        return None
    if sales_document_type.document_behavior not in ACCOUNT_ADJUSTMENT_FISCAL_BEHAVIORS:
        return None

    client_profile = _resolve_fiscal_adjustment_client_profile(fiscal_document)
    if not client_profile:
        return None

    source_key = f"fiscal:{fiscal_document.pk}:account-adjustment"
    issued_at = getattr(fiscal_document, "issued_at", None) or getattr(
        fiscal_document, "created_at", None
    )
    total_amount = _to_decimal(getattr(fiscal_document, "total", None)).quantize(
        Decimal("0.01")
    )

    finalized = getattr(fiscal_document, "status", "") in BILLABLE_FISCAL_DOCUMENT_STATUSES
    signed_amount = Decimal("0.00")
    if finalized and total_amount != 0:
        signed_amount = total_amount
        if sales_document_type.document_behavior == SALES_BEHAVIOR_NOTA_CREDITO:
            signed_amount = signed_amount * Decimal("-1")

    return {
        "source_key": source_key,
        "client_profile": client_profile,
        "company": getattr(fiscal_document, "company", None),
        "order": getattr(fiscal_document, "order", None),
        "amount": signed_amount,
        "reason": (
            f"{sales_document_type.name} "
            f"{getattr(fiscal_document, 'display_number', '') or getattr(fiscal_document, 'doc_type', '')}"
        ).strip(),
        "occurred_at": issued_at or timezone.now(),
    }


def sync_fiscal_document_account_movement(*, fiscal_document, actor=None):
    """Synchronize current-account impact derived from one fiscal document."""
    if not fiscal_document:
        return None

    adjustment_snapshot = _resolve_fiscal_adjustment_snapshot(fiscal_document=fiscal_document)
    adjustment_tx = None
    if adjustment_snapshot:
        adjustment_tx = ensure_adjustment_transaction(
            source_key=adjustment_snapshot["source_key"],
            client_profile=adjustment_snapshot["client_profile"],
            amount=adjustment_snapshot["amount"],
            reason=adjustment_snapshot["reason"],
            actor=actor,
            order=adjustment_snapshot["order"],
            company=adjustment_snapshot["company"],
            occurred_at=adjustment_snapshot["occurred_at"],
        )

    if getattr(fiscal_document, "order_id", None):
        sync_order_charge_transaction(order=fiscal_document.order, actor=actor)
    return adjustment_tx


def sync_internal_document_account_movement(*, internal_document, actor=None):
    """Synchronize current-account impact derived from one internal document."""
    if not internal_document or not getattr(internal_document, "order_id", None):
        return None
    return sync_order_charge_transaction(order=internal_document.order, actor=actor)


def resync_client_ledger(*, client_profile=None):
    """Rebuild idempotent order and payment ledger rows for one or all clients."""
    profiles = [client_profile] if client_profile else list(ClientProfile.objects.all())

    for profile in profiles:
        orders = Order.objects.filter(user_id=profile.user_id)
        for order in orders:
            sync_order_charge_transaction(order=order, actor=None)

        payments = ClientPayment.objects.filter(client_profile=profile)
        for payment in payments:
            sync_payment_transaction(payment=payment, actor=None)
