"""Shared import execution runner for thread/celery backends."""

import importlib
import os
import traceback

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import Category, Product
from core.models import ImportExecution
from core.services.import_manager import ImportTaskManager


def collect_created_refs(import_type, row_results):
    """Extract references that can be rolled back for created records."""
    refs = []
    for row in row_results:
        if not getattr(row, "success", False) or getattr(row, "action", "") != "created":
            continue

        data = row.data or {}
        if import_type in ("products", "abrazaderas"):
            value = str(data.get("sku") or data.get("codigo") or "").strip()
        elif import_type == "categories":
            value = slugify(str(data.get("nombre") or "").strip())
        elif import_type == "clients":
            value = str(data.get("username") or data.get("usuario") or data.get("email") or "").strip()
        else:
            value = ""

        if value:
            refs.append(value)

    return list(dict.fromkeys(refs))


def _resolve_importer_class(importer_class_path):
    module_path, class_name = importer_class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _run_preflight(import_type, importer_class, file_path):
    """
    Lightweight validation checks before long import processing.
    """
    preflight_errors = []

    if import_type == "categories":
        import pandas as pd

        df = pd.read_excel(file_path)
        required_cols = {"nombre"}
        cols = {str(c).strip().lower() for c in df.columns}
        missing = sorted(required_cols - cols)
        if missing:
            preflight_errors.append(
                "El archivo no contiene las columnas requeridas para categorias: " + ", ".join(missing)
            )

    elif import_type == "clients":
        import pandas as pd

        df = pd.read_excel(file_path)
        email_cols = [c for c in df.columns if str(c).strip().lower() in {"email", "correo"}]
        if email_cols:
            email_col = email_cols[0]
            duplicated = (
                df[df[email_col].notna()][email_col]
                .astype(str)
                .str.strip()
                .str.lower()
                .duplicated(keep=False)
            )
            if duplicated.any():
                dup_values = (
                    df.loc[duplicated, email_col]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .drop_duplicates()
                    .head(10)
                    .tolist()
                )
                preflight_errors.append(
                    "Se detectaron emails duplicados en el archivo: " + ", ".join(dup_values)
                )

            emails_in_file = (
                df[email_col]
                .dropna()
                .astype(str)
                .str.strip()
                .str.lower()
                .tolist()
            )
            if emails_in_file:
                existing = set(
                    User.objects.filter(email__in=emails_in_file).values_list("email", flat=True)
                )
                if existing:
                    sample = sorted(existing)[:10]
                    preflight_errors.append(
                        "Estos emails ya existen en el sistema: " + ", ".join(sample)
                    )

    elif import_type in {"products", "abrazaderas"}:
        importer = importer_class(file_path)
        preview = importer.run(dry_run=True)
        if getattr(preview, "has_errors", False):
            preflight_errors.append(
                "La validacion previa detecto errores. Corrige el archivo antes de importar."
            )

    return preflight_errors


def run_import_execution(task_id, execution_id, import_type, importer_class_path, file_path, dry_run):
    """
    Execute one import job and persist progress/status.
    This function is backend-agnostic (thread or celery).
    """
    execution = ImportExecution.objects.filter(pk=execution_id).first()
    try:
        importer_class = _resolve_importer_class(importer_class_path)
        preflight_errors = _run_preflight(import_type, importer_class, file_path)

        if preflight_errors:
            result_data = {
                "created": 0,
                "updated": 0,
                "errors": len(preflight_errors),
                "has_errors": True,
                "row_errors": [
                    {"row": 0, "message": msg}
                    for msg in preflight_errors
                ],
                "preflight_errors": preflight_errors,
                "execution_id": execution_id,
                "import_type": import_type,
            }
            ImportTaskManager.fail_task(task_id, "La validacion previa detecto errores.")
            if execution:
                execution.status = ImportExecution.STATUS_FAILED
                execution.result_summary = result_data
                execution.finished_at = timezone.now()
                execution.save(update_fields=["status", "result_summary", "finished_at"])
            return

        def progress_callback(current, total):
            ImportTaskManager.update_progress(task_id, current, total, f"Procesando fila {current} de {total}")

        importer = importer_class(file_path)
        result = importer.run(dry_run=dry_run, progress_callback=progress_callback)

        created_refs = collect_created_refs(import_type, result.row_results) if not dry_run else []
        result_data = {
            "created": result.created,
            "updated": result.updated,
            "errors": result.errors,
            "has_errors": result.has_errors,
            "row_errors": [
                {"row": r.row_number, "message": str(r.errors)}
                for r in result.row_results if not r.success
            ][:50],
            "preflight_errors": preflight_errors,
            "execution_id": execution_id,
            "import_type": import_type,
        }

        ImportTaskManager.complete_task(task_id, result_data)

        if execution:
            execution.status = ImportExecution.STATUS_COMPLETED
            execution.created_count = result.created
            execution.updated_count = result.updated
            execution.error_count = result.errors
            execution.result_summary = result_data
            execution.created_refs = created_refs
            execution.finished_at = timezone.now()
            execution.save(
                update_fields=[
                    "status",
                    "created_count",
                    "updated_count",
                    "error_count",
                    "result_summary",
                    "created_refs",
                    "finished_at",
                ]
            )
    except Exception as exc:
        traceback.print_exc()
        ImportTaskManager.fail_task(task_id, str(exc))
        if execution:
            execution.status = ImportExecution.STATUS_FAILED
            execution.result_summary = {"error": str(exc)}
            execution.finished_at = timezone.now()
            execution.save(update_fields=["status", "result_summary", "finished_at"])
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass

