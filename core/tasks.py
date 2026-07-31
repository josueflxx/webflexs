"""Background tasks (Celery-compatible with safe local fallback)."""
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

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
def run_import_execution_task(
    task_id,
    execution_id,
    import_type,
    importer_class_path,
    file_path,
    dry_run,
    import_options=None,
):
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
        import_options=import_options or {},
    )
    return {"task_id": task_id, "execution_id": execution_id}


@shared_task(name="core.emit_fiscal_document_async_task")
def emit_fiscal_document_async_task(document_id, actor_id=None):
    """Run a first authorization once; never blind-retry an uncertain result."""
    from core.models import (
        FiscalDocument,
        FISCAL_STATUS_SUBMITTING,
        FISCAL_STATUS_UNCERTAIN,
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
        # A crash after SENDING is ambiguous by definition. Preserve the
        # operation for FECompConsultar instead of calling authorization again.
        from django.db import transaction
        from core.services.sensitive_data import sanitize_sensitive_text

        with transaction.atomic():
            locked = (
                FiscalDocument.objects.select_for_update()
                .filter(pk=document_id)
                .first()
            )
            if locked and locked.status == FISCAL_STATUS_SUBMITTING:
                locked.transition_to(
                    FISCAL_STATUS_UNCERTAIN,
                    error_code="task_unexpected_after_sending",
                    error_message=sanitize_sensitive_text(str(exc)),
                    next_recovery_at=timezone.now(),
                )
        return {"status": "uncertain", "message": "Resultado enviado a recuperacion."}


@shared_task(name="core.recover_fiscal_document_async_task")
def recover_fiscal_document_async_task(document_id, actor_id=None):
    """Execute one idempotent FECompConsultar recovery attempt."""
    from django.contrib.auth import get_user_model
    from core.models import FiscalDocument
    from core.services.fiscal_recovery import recover_fiscal_document

    document = FiscalDocument.objects.filter(pk=document_id).first()
    if not document:
        return {"status": "error", "message": "Documento fiscal no encontrado."}
    actor = (
        get_user_model().objects.filter(pk=actor_id).first()
        if actor_id
        else None
    )
    try:
        outcome = recover_fiscal_document(
            fiscal_document=document,
            actor=actor,
            allow_stale_sending=True,
        )
        return {
            "status": outcome.state,
            "message": outcome.message,
            "consulted": outcome.consulted,
        }
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "; ".join(getattr(exc, "messages", []) or [str(exc)]),
        }
    except Exception:
        return {
            "status": "error",
            "message": "La consulta de recuperacion no pudo completarse.",
        }


@shared_task(name="core.retry_stuck_fiscal_documents_task")
def retry_stuck_fiscal_documents_task():
    """Compatibility task name: it now performs query-only recovery."""
    from django.utils import timezone
    from core.models import (
        FiscalDocument,
        FISCAL_STATUS_RECOVERED_NOT_FOUND,
        FISCAL_STATUS_RECOVERY_PENDING,
        FISCAL_STATUS_SUBMITTING,
        FISCAL_STATUS_UNCERTAIN,
    )
    from core.services.fiscal_recovery import recover_fiscal_document

    submitting_timeout = int(getattr(settings, "FISCAL_SUBMITTING_TIMEOUT_MINUTES", 20) or 20)
    now = timezone.now()
    stale_cutoff = now - timedelta(minutes=max(submitting_timeout, 5))
    stale_ids = list(
        FiscalDocument.objects.filter(
        status=FISCAL_STATUS_SUBMITTING
        )
        .filter(
            Q(authorization_started_at__lte=stale_cutoff)
            | Q(authorization_started_at__isnull=True, updated_at__lte=stale_cutoff)
        )
        .values_list("id", flat=True)
    )
    due_ids = list(
        FiscalDocument.objects.filter(
            status__in=[
                FISCAL_STATUS_UNCERTAIN,
                FISCAL_STATUS_RECOVERY_PENDING,
                FISCAL_STATUS_RECOVERED_NOT_FOUND,
            ]
        )
        .filter(Q(next_recovery_at__isnull=True) | Q(next_recovery_at__lte=now))
        .values_list("id", flat=True)
    )

    results = []
    for document_id in dict.fromkeys([*stale_ids, *due_ids]):
        doc = FiscalDocument.objects.filter(pk=document_id).first()
        if not doc:
            continue
        try:
            outcome = recover_fiscal_document(
                fiscal_document=doc,
                allow_stale_sending=True,
            )
            results.append(
                {
                    "id": doc.id,
                    "state": outcome.state,
                    "consulted": outcome.consulted,
                }
            )
        except Exception:
            results.append({"id": doc.id, "state": "error"})

    return {
        "authorized_requests": 0,
        "consulted_count": sum(bool(row.get("consulted")) for row in results),
        "stale_submitting_ids": stale_ids,
        "details": results,
    }


@shared_task(name="core.create_automatic_backup_task")
def create_automatic_backup_task():
    """Create the scheduled portable backup and enforce retention."""
    from core.services.backups import create_system_backup

    result = create_system_backup()
    return {
        "manifest": str(result["manifest"]),
        "artifacts": [str(path) for path in result["artifacts"]],
        "removed": result["removed"],
    }


@shared_task(name="core.deliver_webhook_task")
def deliver_webhook_task(delivery_id):
    from core.services.webhooks import deliver_webhook

    return deliver_webhook(delivery_id)


@shared_task(name="core.retry_pending_webhooks_task")
def retry_pending_webhooks_task():
    from core.services.webhooks import retry_pending_webhooks

    ids = retry_pending_webhooks()
    return {"queued": len(ids), "delivery_ids": ids}


@shared_task(name="core.execute_external_editor_job_task")
def execute_external_editor_job_task(job_id):
    from core.services.external_editor_jobs import execute_external_editor_job

    job = execute_external_editor_job(job_id)
    return {
        "job_id": job.pk,
        "status": job.status,
        "succeeded": job.succeeded,
        "failed": job.failed,
    }
