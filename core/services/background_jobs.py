"""Background job dispatcher with safe fallback to thread workers."""

import logging
import threading

from django.conf import settings

from core.services.import_execution_runner import run_import_execution
from core.services.import_manager import ImportTaskManager

logger = logging.getLogger(__name__)


def dispatch_import_job(task_id, execution_id, import_type, importer_class_path, file_path, dry_run):
    """
    Dispatch import execution to:
    - Celery queue if FEATURE_BACKGROUND_JOBS_ENABLED is active and backend available
    - Thread fallback otherwise
    """
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
            )
            ImportTaskManager.set_backend(task_id, backend="celery", job_id=getattr(async_result, "id", ""))
            return {"backend": "celery", "job_id": getattr(async_result, "id", "")}
        except Exception:
            logger.exception("Celery dispatch failed, using thread fallback.")

    thread = threading.Thread(
        target=run_import_execution,
        args=(task_id, execution_id, import_type, importer_class_path, file_path, bool(dry_run)),
        daemon=True,
    )
    thread.start()
    ImportTaskManager.set_backend(task_id, backend="thread", job_id="")
    return {"backend": "thread", "job_id": ""}

