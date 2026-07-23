"""Background job dispatcher with safe fallback to thread workers."""

import logging
import threading

from django.conf import settings

from core.services.import_execution_runner import run_import_execution
from core.services.import_manager import ImportTaskManager

logger = logging.getLogger(__name__)


def _should_run_import_sync():
    """
    Optional safety mode.
    Enable only when explicitly requested via IMPORTS_FORCE_SYNC.
    """
    return bool(getattr(settings, "IMPORTS_FORCE_SYNC", False))


def dispatch_import_job(
    task_id,
    execution_id,
    import_type,
    importer_class_path,
    file_path,
    dry_run,
    import_options=None,
):
    """
    Dispatch import execution to:
    - Celery queue if FEATURE_BACKGROUND_JOBS_ENABLED is active and backend available
    - Thread fallback otherwise
    """
    if _should_run_import_sync():
        run_import_execution(
            task_id,
            execution_id,
            import_type,
            importer_class_path,
            file_path,
            bool(dry_run),
            import_options or {},
        )
        ImportTaskManager.set_backend(task_id, backend="sync", job_id="")
        return {"backend": "sync", "job_id": ""}

    if getattr(settings, "FEATURE_BACKGROUND_JOBS_ENABLED", False):
        try:
            from core.tasks import run_import_execution_task

            async_result = run_import_execution_task.delay(
                task_id,
                execution_id,
                import_type,
                importer_class_path,
                file_path,
                bool(dry_run),
                import_options or {},
            )
            ImportTaskManager.set_backend(task_id, backend="celery", job_id=getattr(async_result, "id", ""))
            return {"backend": "celery", "job_id": getattr(async_result, "id", "")}
        except Exception:
            logger.exception("Celery dispatch failed, using thread fallback.")

    thread = threading.Thread(
        target=run_import_execution,
        args=(
            task_id,
            execution_id,
            import_type,
            importer_class_path,
            file_path,
            bool(dry_run),
            import_options or {},
        ),
        daemon=True,
    )
    thread.start()
    ImportTaskManager.set_backend(task_id, backend="thread", job_id="")
    return {"backend": "thread", "job_id": ""}


def dispatch_external_editor_job(job_id):
    """Dispatch a durable editor job without blocking the HTTP request."""

    if getattr(settings, "FEATURE_BACKGROUND_JOBS_ENABLED", False):
        try:
            from core.tasks import execute_external_editor_job_task

            async_result = execute_external_editor_job_task.delay(job_id)
            return {"backend": "celery", "job_id": getattr(async_result, "id", "")}
        except Exception:
            logger.exception("External editor Celery dispatch failed, using thread fallback.")

    def run_job():
        from core.services.external_editor_jobs import execute_external_editor_job

        try:
            execute_external_editor_job(job_id)
        except Exception:
            logger.exception("External editor thread job %s failed.", job_id)

    thread = threading.Thread(target=run_job, daemon=True, name=f"editor-job-{job_id}")
    thread.start()
    return {"backend": "thread", "job_id": ""}
