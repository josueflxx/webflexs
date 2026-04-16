import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from orders.models import Order

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Order)
def order_post_save_orchestrator(sender, instance, created, **kwargs):
    """
    Handle side-effects for Order persistence via post_save to decouple
    the basic database save from heavier commercial automation.
    """
    if kwargs.get("raw"):
        return

    update_fields = kwargs.get("update_fields")
    tracked_fields = {"status", "total", "user", "status_updated_at"}
    should_sync_ledger = (
        created
        or update_fields is None
        or bool(tracked_fields.intersection(set(update_fields)))
    )

    if not should_sync_ledger:
        return

    # Wrap side-effects in atomic block to prevent partial completion,
    # catching and logging errors instead of silently passing.
    try:
        with transaction.atomic():
            from accounts.services.ledger import sync_order_charge_transaction
            
            sync_order_charge_transaction(order=instance, actor=None)
    except Exception as e:
        logger.exception(f"Failed to sync ledger transaction for Order {instance.pk}: {e}")

    try:
        with transaction.atomic():
            from core.models import DocumentSeries
            from core.services.documents import ensure_document_for_order

            if instance.status == Order.STATUS_DRAFT:
                ensure_document_for_order(instance, doc_type=DocumentSeries.DOC_COT)
            if instance.status in {
                Order.STATUS_CONFIRMED,
                Order.STATUS_PREPARING,
                Order.STATUS_SHIPPED,
                Order.STATUS_DELIVERED,
            }:
                ensure_document_for_order(instance, doc_type=DocumentSeries.DOC_PED)
            if instance.status in {
                Order.STATUS_SHIPPED,
                Order.STATUS_DELIVERED
            }:
                ensure_document_for_order(instance, doc_type=DocumentSeries.DOC_REM)
    except Exception as e:
        logger.exception(f"Failed to generate automatic documents for Order {instance.pk}: {e}")
