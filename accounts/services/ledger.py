"""Ledger helpers for client current-account balance."""

from decimal import Decimal

from django.utils import timezone

from accounts.models import ClientPayment, ClientProfile, ClientTransaction
from orders.models import Order


BILLABLE_ORDER_STATUSES = {
    Order.STATUS_CONFIRMED,
    Order.STATUS_PREPARING,
    Order.STATUS_SHIPPED,
    Order.STATUS_DELIVERED,
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


def sync_order_charge_transaction(order, actor=None):
    """
    Upsert one idempotent ledger row for order charge.
    Positive amount increases debt; zero means no charge for current status.
    """
    client_profile = _resolve_client_profile_from_order(order)
    if not client_profile:
        return None

    source_key = f"order:{order.pk}:charge"
    amount = _to_decimal(order.total if order.status in BILLABLE_ORDER_STATUSES else 0).quantize(Decimal("0.01"))
    occurred_at = getattr(order, "status_updated_at", None) or timezone.now()

    defaults = {
        "client_profile": client_profile,
        "order": order,
        "transaction_type": ClientTransaction.TYPE_ORDER_CHARGE,
        "amount": amount,
        "description": f"Cargo pedido #{order.pk} ({order.get_status_display()})",
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
    amount = Decimal("0.00")
    if not payment.is_cancelled:
        amount = (_to_decimal(payment.amount) * Decimal("-1")).quantize(Decimal("0.01"))

    defaults = {
        "client_profile": payment.client_profile,
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
    return ClientTransaction.objects.create(
        client_profile=client_profile,
        order=order,
        payment=None,
        transaction_type=ClientTransaction.TYPE_ADJUSTMENT,
        amount=dec_amount,
        description=(reason or "").strip(),
        source_key=source_key,
        occurred_at=occurred,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


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
