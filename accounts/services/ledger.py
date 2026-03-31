"""Ledger helpers for client current-account balance."""

from decimal import Decimal

from django.utils import timezone

from accounts.models import ClientPayment, ClientProfile, ClientTransaction
from core.models import (
    FISCAL_BILLABLE_DOC_TYPES,
    SALES_BEHAVIOR_COTIZACION,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_PRESUPUESTO,
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
ACCOUNTABLE_INTERNAL_BEHAVIORS = {
    SALES_BEHAVIOR_COTIZACION,
    SALES_BEHAVIOR_PRESUPUESTO,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_REMITO,
    SALES_BEHAVIOR_FACTURA,
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


def _get_order_billable_fiscal_document(order):
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


def _get_order_accountable_internal_document(order):
    """Resolve one internal document configured to impact current account."""
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


def _get_order_charge_snapshot(order):
    """Resolve whether one order should currently impact current account."""
    billable_document = _get_order_billable_fiscal_document(order)
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

    if order and (getattr(order, "saas_document_number", "") or getattr(order, "saas_document_type", "")):
        return {
            "amount": _to_decimal(order.total).quantize(Decimal("0.01")),
            "occurred_at": getattr(order, "status_updated_at", None) or timezone.now(),
            "description": (
                f"Venta facturada en SaaS "
                f"{getattr(order, 'saas_document_type', '')} "
                f"{getattr(order, 'saas_document_number', '')}"
            ).strip(),
        }

    internal_document = _get_order_accountable_internal_document(order)
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
    """
    Upsert one idempotent ledger row for billed sales linked to an order.
    Positive amount increases debt; zero means the order has no final billable document yet.
    """
    client_profile = _resolve_client_profile_from_order(order)
    if not client_profile:
        return None

    source_key = f"order:{order.pk}:charge"
    company = getattr(order, "company", None)
    if not company:
        try:
            from core.services.company_context import get_default_company

            company = get_default_company()
        except Exception:
            company = None
    charge_snapshot = _get_order_charge_snapshot(order)
    amount = charge_snapshot["amount"]
    occurred_at = charge_snapshot["occurred_at"]

    defaults = {
        "client_profile": client_profile,
        "company": company,
        "billing_company": getattr(company, "slug", None) or "flexs",
        "order": order,
        "transaction_type": ClientTransaction.TYPE_ORDER_CHARGE,
        "amount": amount,
        "description": charge_snapshot["description"],
        "occurred_at": occurred_at,
        "created_by": actor if getattr(actor, "is_authenticated", False) else None,
    }
    tx, _ = ClientTransaction.objects.update_or_create(
        source_key=source_key,
        defaults=defaults,
    )
    return tx


def sync_payment_transaction(payment, actor=None):
    """
    Upsert one idempotent ledger row for payment.
    Active payment reduces debt (negative amount); cancelled payment contributes zero.
    """
    if not payment or not getattr(payment, "client_profile_id", None):
        return None

    source_key = f"payment:{payment.pk}:applied"
    company = getattr(payment, "company", None)
    if not company and getattr(payment, "order_id", None):
        company = getattr(payment.order, "company", None)
    if not company:
        try:
            from core.services.company_context import get_default_company

            company = get_default_company()
        except Exception:
            company = None
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
        "created_by": actor if getattr(actor, "is_authenticated", False) else (payment.created_by if payment.created_by_id else None),
    }
    tx, _ = ClientTransaction.objects.update_or_create(
        source_key=source_key,
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
    """
    Create a manual ledger adjustment.
    Positive amount increases debt; negative amount reduces debt.
    """
    occurred = occurred_at or timezone.now()
    dec_amount = _to_decimal(amount).quantize(Decimal("0.01"))
    suffix = timezone.now().strftime("%Y%m%d%H%M%S%f")
    source_key = f"adjustment:{client_profile.pk}:{suffix}"
    resolved_company = company or getattr(order, "company", None)
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
        source_key=source_key,
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
    """
    Idempotent adjustment row keyed by explicit source_key.
    Positive amount increases debt; negative amount reduces debt.
    """
    if not source_key:
        raise ValueError("source_key obligatorio para ajustes idempotentes.")
    resolved_company = company or getattr(order, "company", None)
    if not resolved_company:
        raise ValueError("Empresa obligatoria para ajustes de cuenta corriente.")

    occurred = occurred_at or timezone.now()
    dec_amount = _to_decimal(amount).quantize(Decimal("0.01"))
    defaults = {
        "client_profile": client_profile,
        "company": resolved_company,
        "billing_company": getattr(resolved_company, "slug", None) or "flexs",
        "order": order,
        "payment": None,
        "transaction_type": ClientTransaction.TYPE_ADJUSTMENT,
        "amount": dec_amount,
        "description": (reason or "").strip(),
        "occurred_at": occurred,
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


def resync_client_ledger(*, client_profile=None):
    """
    Rebuild (upsert) order and payment ledger rows.
    Safe to run multiple times due to source_key idempotency.
    """
    profiles = [client_profile] if client_profile else list(ClientProfile.objects.all())

    for profile in profiles:
        orders = Order.objects.filter(user_id=profile.user_id)
        for order in orders:
            sync_order_charge_transaction(order=order, actor=None)

        payments = ClientPayment.objects.filter(client_profile=profile)
        for payment in payments:
            sync_payment_transaction(payment=payment, actor=None)
