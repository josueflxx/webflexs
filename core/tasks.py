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

