"""Background tasks (Celery-compatible with safe local fallback)."""

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
    from core.models import FiscalDocument
    from core.services.fiscal_emission import emit_fiscal_document_now
    from django.contrib.auth import get_user_model

    document = FiscalDocument.objects.filter(pk=document_id).first()
    if not document:
        return {"status": "error", "message": "Documento fiscal no encontrado."}
    
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None

    # Proceed emission
    outcome = emit_fiscal_document_now(fiscal_document=document, actor=actor)
    return {"status": outcome.state, "message": outcome.message}


@shared_task(name="core.retry_stuck_fiscal_documents_task")
def retry_stuck_fiscal_documents_task():
    """
    Cron-like task to automatically retry all stuck documents in 'pending_retry'.
    To be called by Celery Beat every N minutes.
    """
    from django.utils import timezone
    from datetime import timedelta
    from core.models import FiscalDocument, FISCAL_STATUS_PENDING_RETRY
    from core.services.fiscal_emission import emit_fiscal_document_now

    # We retry documents that have been stuck for at least 5 minutes, 
    # and have less than 5 attempts, to prevent an infinite loop.
    threshold = timezone.now() - timedelta(minutes=5)
    stuck_docs = FiscalDocument.objects.filter(
        status=FISCAL_STATUS_PENDING_RETRY,
        last_attempt_at__lte=threshold,
        attempts_count__lt=5,
    )

    results = []
    for doc in stuck_docs:
        try:
            outcome = emit_fiscal_document_now(fiscal_document=doc)
            results.append({"id": doc.id, "state": outcome.state})
        except Exception as e:
            results.append({"id": doc.id, "state": "error", "error": str(e)})

    return {"retried_count": len(results), "details": results}
