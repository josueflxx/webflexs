"""Background tasks (Celery-compatible with safe local fallback)."""
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

from core.services.import_execution_runner import run_import_execution


def _local_shared_task_decorator(*dargs, **dkwargs):
    """
    Fallback decorator when Celery is not installed.
    Mimics .delay() by running synchronously.
    """
    def decorator(func):
        def delay(*args, **kwargs):
            return func(*args, **kwargs)

        func.delay = delay
        return func

    if dargs and callable(dargs[0]) and len(dargs) == 1 and not dkwargs:
        return decorator(dargs[0])
    return decorator


try:
    from celery import shared_task as _celery_shared_task
except Exception:  # pragma: no cover - fallback path for local env without celery
    shared_task = _local_shared_task_decorator
else:
    shared_task = _celery_shared_task


@shared_task(name="core.run_import_execution_task")
def run_import_execution_task(task_id, execution_id, import_type, importer_class_path, file_path, dry_run):
    """
    Run one import execution in background.
    """
    run_import_execution(
        task_id=task_id,
        execution_id=execution_id,
        import_type=import_type,
        importer_class_path=importer_class_path,
        file_path=file_path,
        dry_run=bool(dry_run),
    )
    return {"task_id": task_id, "execution_id": execution_id}


@shared_task(name="core.emit_fiscal_document_async_task")
def emit_fiscal_document_async_task(document_id, actor_id=None):
    """
    Emit a fiscal document to ARCA in a background worker.
    """
    from django.utils import timezone
    from core.models import (
        FiscalDocument,
        FISCAL_STATUS_PENDING_RETRY,
        FISCAL_STATUS_SUBMITTING,
    )
    from core.services.fiscal_emission import emit_fiscal_document_now
    from django.contrib.auth import get_user_model

    document = FiscalDocument.objects.filter(pk=document_id).first()
    if not document:
        return {"status": "error", "message": "Documento fiscal no encontrado."}
    
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None

    try:
        outcome = emit_fiscal_document_now(fiscal_document=document, actor=actor)
        return {"status": outcome.state, "message": outcome.message}
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "; ".join(getattr(exc, "messages", []) or [str(exc)]),
        }
    except Exception as exc:
        # If something unexpected happened while task was already running,
        # do not leave the document forever in submitting state.
        retry_minutes = int(getattr(settings, "FISCAL_RETRY_MINUTES", 10) or 10)
        locked = FiscalDocument.objects.filter(pk=document_id).first()
        if locked and locked.status == FISCAL_STATUS_SUBMITTING:
            locked.status = FISCAL_STATUS_PENDING_RETRY
            locked.error_code = "task_unexpected_error"
            locked.error_message = f"Error inesperado en worker: {exc}"
            locked.next_retry_at = timezone.now() + timedelta(minutes=max(retry_minutes, 1))
            locked.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "next_retry_at",
                    "updated_at",
                ]
            )
        return {"status": "pending_retry", "message": str(exc)}


@shared_task(name="core.retry_stuck_fiscal_documents_task")
def retry_stuck_fiscal_documents_task():
    """
    Cron-like task to automatically retry all stuck documents in 'pending_retry'.
    To be called by Celery Beat every N minutes.
    """
    from django.utils import timezone
    from core.models import (
        FiscalDocument,
        FISCAL_STATUS_PENDING_RETRY,
        FISCAL_STATUS_SUBMITTING,
    )
    from core.services.fiscal_emission import emit_fiscal_document_now

    max_retry_attempts = int(getattr(settings, "FISCAL_MAX_AUTO_RETRIES", 5) or 5)
    submitting_timeout = int(getattr(settings, "FISCAL_SUBMITTING_TIMEOUT_MINUTES", 20) or 20)
    now = timezone.now()

    # Recover docs stuck in submitting beyond timeout.
    stale_cutoff = now - timedelta(minutes=max(submitting_timeout, 5))
    stale_submitting = FiscalDocument.objects.filter(
        status=FISCAL_STATUS_SUBMITTING
    ).filter(
        Q(last_attempt_at__isnull=False, last_attempt_at__lte=stale_cutoff)
        | Q(last_attempt_at__isnull=True, updated_at__lte=stale_cutoff)
    )
    recovered_ids = []
    for doc in stale_submitting:
        doc.status = FISCAL_STATUS_PENDING_RETRY
        doc.error_code = "stale_submitting_recovered"
        doc.error_message = "El documento quedo en submitting y fue recuperado para reintento."
        doc.next_retry_at = now
        doc.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "next_retry_at",
                "updated_at",
            ]
        )
        recovered_ids.append(doc.id)

    stuck_docs = FiscalDocument.objects.filter(
        status=FISCAL_STATUS_PENDING_RETRY,
        attempts_count__lt=max_retry_attempts,
    ).filter(
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
    )

    results = []
    for doc in stuck_docs:
        try:
            outcome = emit_fiscal_document_now(fiscal_document=doc)
            results.append({"id": doc.id, "state": outcome.state})
        except Exception as e:
            results.append({"id": doc.id, "state": "error", "error": str(e)})

    return {
        "retried_count": len(results),
        "recovered_submitting_count": len(recovered_ids),
        "recovered_submitting_ids": recovered_ids,
        "details": results,
    }
