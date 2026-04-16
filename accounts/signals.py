import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from accounts.models import ClientPayment

logger = logging.getLogger(__name__)

@receiver(post_save, sender=ClientPayment)
def payment_post_save_orchestrator(sender, instance, created, **kwargs):
    """
    Handle side-effects for ClientPayment persistence via post_save to decouple
    the basic database save from ledger synchronization and document creation.
    """
    if kwargs.get("raw"):
        return

    # Wrap side-effects in atomic blocks
    try:
        with transaction.atomic():
            from accounts.services.ledger import sync_payment_transaction
            
            sync_payment_transaction(
                payment=instance,
                actor=instance.created_by if instance.created_by_id else None,
            )
    except Exception as e:
        logger.exception(f"Failed to sync ledger transaction for ClientPayment {instance.pk}: {e}")

    try:
        with transaction.atomic():
            from core.services.documents import ensure_document_for_payment

            ensure_document_for_payment(instance)
    except Exception as e:
        logger.exception(f"Failed to generate automatic documents for ClientPayment {instance.pk}: {e}")
