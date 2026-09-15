"""Private, versioned Excel exports generated only by a dedicated worker."""
import hashlib
import json
import logging
import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from core.services.catalog_excel_status import latest_catalog_excel_source_change

logger = logging.getLogger(__name__)
QUEUE = "flexs-catalog-excel"
JOB_TTL = 900
PUBLIC_ERROR = "No se pudo preparar el Excel. Volve a intentar en unos segundos."


def export_spec(template, price_list, discount_percentage):
    source = latest_catalog_excel_source_change(template)
    return {
        "version": 1,
        "template_id": template.pk,
        "price_list_id": price_list.pk if price_list else None,
        "discount": format(Decimal(discount_percentage or 0).quantize(Decimal("0.01")), "f"),
        "source": source.isoformat() if source else "",
    }


def export_key(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def export_path(spec):
    # Deliberately outside MEDIA_ROOT: personalized prices require authorization.
    root = Path(getattr(settings, "CATALOG_EXCEL_CACHE_DIR", Path(settings.BASE_DIR) / "runtime" / "catalog_exports"))
    return root / f"catalogo_{export_key(spec)}.xlsx"


def export_ready(spec):
    path = export_path(spec)
    return path.is_file() and path.stat().st_size > 0


def publish_export(spec, token):
    from flexs_project.celery import app

    # send_task never uses Celery's eager/local synchronous fallback.
    with app.connection_for_write(connect_timeout=3) as connection:
        app.send_task(
            "core.generate_client_catalog_excel", args=[spec, token],
            queue=QUEUE, task_id=token, expires=JOB_TTL, retry=False,
            connection=connection,
        )


def ensure_export(spec, actor_id=None):
    if export_ready(spec):
        return {"status": "ready"}
    key = f"catalog_excel_job_v1:{export_key(spec)}"
    token = uuid.uuid4().hex
    state = {"status": "queued", "token": token, "actor_id": actor_id}
    if cache.add(key, state, JOB_TTL):
        try:
            publish_export(spec, token)
        except Exception:
            logger.exception("Could not enqueue catalog export %s", export_key(spec))
            state = {"status": "failed", "message": PUBLIC_ERROR, "token": token}
            cache.set(key, state, 30)
    else:
        state = cache.get(key) or {"status": "queued"}
    return {key: value for key, value in state.items() if key in {"status", "message"}}


def generate_export(spec, token):
    from django.contrib.auth import get_user_model
    from catalog.models import PriceList
    from core.models import CatalogExcelTemplate
    from core.services.catalog_excel_exporter import build_catalog_workbook

    key = f"catalog_excel_job_v1:{export_key(spec)}"
    state = cache.get(key)
    if not state or state.get("token") != token or export_ready(spec):
        return
    # A task can be delivered more than once. The lock outlives the hard limit.
    lock_key = f"{key}:running:{token}"
    if not cache.add(lock_key, True, 660):
        return
    cache.set(key, {"status": "running", "token": token}, JOB_TTL)
    temporary = None
    workbook = None
    try:
        template = CatalogExcelTemplate.objects.get(
            pk=spec["template_id"], is_active=True, is_client_download_enabled=True,
        )
        price_list = PriceList.objects.get(pk=spec["price_list_id"]) if spec["price_list_id"] else None
        discount = Decimal(spec["discount"])
        if export_spec(template, price_list, discount) != spec:
            cache.delete(key)
            return
        workbook, stats = build_catalog_workbook(template, price_list=price_list, discount_percentage=discount)
        path = export_path(spec)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
        os.close(descriptor)
        workbook.save(temporary)
        # Never publish a file as current if prices/categories changed mid-build.
        template.refresh_from_db()
        if not template.is_active or not template.is_client_download_enabled or export_spec(template, price_list, discount) != spec:
            cache.delete(key)
            return
        os.replace(temporary, path)
        temporary = None
        actor = get_user_model().objects.filter(pk=state.get("actor_id")).first()
        template.mark_generated(stats, user=actor)
        # The atomically published file is the source of truth for readiness.
        cache.delete(key)
    except Exception:
        logger.exception("Catalog export failed %s", export_key(spec))
        cache.set(key, {"status": "failed", "message": PUBLIC_ERROR, "token": token}, 30)
        raise
    finally:
        if workbook is not None:
            workbook.close()
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        cache.delete(lock_key)
