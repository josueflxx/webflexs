"""Durable bulk jobs for the external product editor."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import Product, ProductSupplier, Supplier
from catalog.services.product_suppliers import set_preferred_supplier_preserving_terms
from core.models import AdminAuditLog, ExternalEditorJob, ExternalEditorJobItem
from core.services.external_editor import (
    MAX_SELECTION_IDS,
    apply_editor_product_patch,
    build_editor_product_queryset,
)


SUPPORTED_RULE_FIELDS = {
    "internalcode",
    "name",
    "description",
    "notes",
    "categoryid",
    "subcategoryid",
    "supplier",
    "cost",
    "margin",
    "saleprice",
    "price",
    "stock",
    "filter1",
    "filter2",
    "filter3",
    "filter4",
    "filter5",
    "status",
    "tags",
}
SUPPORTED_TEXT_ACTIONS = {"set", "clear", "replace_text", "concat_start", "concat_end"}
SUPPORTED_NUMBER_ACTIONS = {"set", "pct_inc", "pct_dec", "add", "sub"}
DIRECT_CHANGE_FIELDS = {
    "internalCode",
    "sku",
    "name",
    "description",
    "categoryId",
    "category_id",
    "subcategoryId",
    "subcategory_id",
    "supplierId",
    "supplier_id",
    "supplier",
    "cost",
    "margin",
    "salePrice",
    "price",
    "stock",
    "status",
    "isActive",
    "is_active",
    "isDeleted",
    "reference",
    "filter1",
    "filter2",
    "filter3",
    "filter4",
    "filter5",
    "notes",
    "tags",
}


class ExternalEditorJobPayloadConflict(Exception):
    """An idempotency key was reused with another payload."""


def _decimal(value, field_name):
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Valor numerico invalido."}) from exc


def _apply_numeric_action(current, action, raw_value, field_name):
    value = _decimal(raw_value, field_name)
    if action == "set":
        result = value
    elif action == "pct_inc":
        result = current * (Decimal("1") + value / Decimal("100"))
    elif action == "pct_dec":
        result = current * (Decimal("1") - value / Decimal("100"))
    elif action == "add":
        result = current + value
    elif action == "sub":
        result = current - value
    else:
        raise ValidationError({field_name: f"Accion numerica no soportada: {action}."})
    if result < 0:
        raise ValidationError({field_name: "La operacion produciria un valor negativo."})
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _apply_text_action(current, action, value, extra_value, field_name):
    value = str(value or "")
    extra_value = str(extra_value or "")
    if action == "set":
        return value
    if action == "clear":
        return ""
    if action == "replace_text":
        if not extra_value:
            raise ValidationError({field_name: "Indica el texto que se debe reemplazar."})
        return current.replace(extra_value, value)
    if action == "concat_start":
        return value + current
    if action == "concat_end":
        return current + value
    raise ValidationError({field_name: f"Accion de texto no soportada: {action}."})


def validate_bulk_payload(payload):
    if not isinstance(payload, dict):
        raise ValidationError("El cuerpo de la operacion debe ser un objeto JSON.")

    changes = payload.get("changes")
    rules = payload.get("rules")
    items = payload.get("items")
    if sum(bool(mode) for mode in (changes, rules, items)) != 1:
        raise ValidationError("Debes indicar exactamente uno de: changes, rules o items.")
    if changes:
        if not isinstance(changes, dict):
            raise ValidationError({"changes": "Debe ser un objeto."})
        unsupported = sorted(set(changes) - DIRECT_CHANGE_FIELDS)
        if unsupported:
            raise ValidationError({"changes": f"Campos no soportados: {', '.join(unsupported)}."})
    if rules:
        if not isinstance(rules, list):
            raise ValidationError({"rules": "Debe ser una lista."})
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValidationError({"rules": f"La regla {index + 1} es invalida."})
            field = str(rule.get("field") or "").strip().lower()
            action = str(rule.get("action") or "").strip().lower()
            if field not in SUPPORTED_RULE_FIELDS:
                raise ValidationError({"rules": f"Campo no soportado: {field or '(vacio)'}."})
            allowed_actions = (
                SUPPORTED_NUMBER_ACTIONS
                if field in {"cost", "margin", "saleprice", "price", "stock"}
                else SUPPORTED_TEXT_ACTIONS
            )
            if field in {"categoryid", "subcategoryid", "supplier", "status"}:
                allowed_actions = {"set", "clear"}
            if action not in allowed_actions:
                raise ValidationError({"rules": f"Accion {action or '(vacia)'} no valida para {field}."})
    if items:
        if not isinstance(items, list):
            raise ValidationError({"items": "Debe ser una lista."})
        if len(items) > MAX_SELECTION_IDS:
            raise ValidationError({"items": f"Supera el limite de {MAX_SELECTION_IDS} productos."})
        seen_ids = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValidationError({"items": f"El cambio {index + 1} es invalido."})
            try:
                product_id = int(item.get("productId"))
            except (TypeError, ValueError) as exc:
                raise ValidationError({"items": f"El producto del cambio {index + 1} es invalido."}) from exc
            if product_id in seen_ids:
                raise ValidationError({"items": f"El producto {product_id} esta repetido."})
            seen_ids.add(product_id)
            item_changes = item.get("changes")
            if not isinstance(item_changes, dict) or not item_changes:
                raise ValidationError({"items": f"El cambio {index + 1} no contiene campos."})
            unsupported = sorted(set(item_changes) - DIRECT_CHANGE_FIELDS)
            if unsupported:
                raise ValidationError({"items": f"Campos no soportados: {', '.join(unsupported)}."})


def resolve_bulk_product_ids(payload):
    items = payload.get("items")
    raw_ids = [item.get("productId") for item in items] if items else payload.get("productIds", payload.get("product_ids"))
    if raw_ids is not None:
        if not isinstance(raw_ids, list):
            raise ValidationError({"productIds": "Debe ser una lista."})
        try:
            ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"productIds": "Contiene identificadores invalidos."}) from exc
        ids = list(Product.objects.filter(pk__in=ids).order_by("pk").values_list("pk", flat=True))
    else:
        filters = payload.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValidationError({"filters": "Debe ser un objeto."})
        ids = list(
            build_editor_product_queryset(filters).values_list("pk", flat=True)[: MAX_SELECTION_IDS + 1]
        )

    if not ids:
        raise ValidationError("La seleccion no contiene productos.")
    if len(ids) > MAX_SELECTION_IDS:
        raise ValidationError(f"La seleccion supera el limite de {MAX_SELECTION_IDS} productos.")
    return ids


def rules_to_patch(product, rules):
    state = {
        "internalcode": product.sku,
        "name": product.name,
        "description": product.description,
        "cost": Decimal(product.cost or 0),
        "saleprice": Decimal(product.price or 0),
        "stock": Decimal(product.stock or 0),
        "filter1": product.filter_1,
        "filter2": product.filter_2,
        "filter3": product.filter_3,
        "filter4": product.filter_4,
        "filter5": product.filter_5,
        "status": "active" if product.is_active else "inactive",
        "notes": str((product.attributes or {}).get("editor_notes") or ""),
        "tags": ", ".join((product.attributes or {}).get("editor_tags") or []),
    }
    patch = {}

    for rule in rules:
        field = str(rule.get("field") or "").strip().lower()
        action = str(rule.get("action") or "").strip().lower()
        value = rule.get("value")
        extra_value = rule.get("extraValue", rule.get("extra_value"))

        if field in {"internalcode", "name", "description", "notes", "filter1", "filter2", "filter3", "filter4", "filter5", "tags"}:
            state[field] = _apply_text_action(state[field], action, value, extra_value, field)
            if field == "tags":
                patch["tags"] = [tag.strip() for tag in state[field].split(",") if tag.strip()]
            else:
                target = "internalCode" if field == "internalcode" else field
                patch[target] = state[field]
        elif field in {"cost", "saleprice", "price", "stock"}:
            state_key = "saleprice" if field == "price" else field
            state[state_key] = _apply_numeric_action(state[state_key], action, value, field)
            if field == "stock":
                if state[state_key] != state[state_key].to_integral_value():
                    raise ValidationError({"stock": "El stock debe quedar como un numero entero."})
                patch["stock"] = int(state[state_key])
            else:
                patch["salePrice" if field in {"price", "saleprice"} else field] = state[state_key]
        elif field == "margin":
            current_margin = (
                (state["saleprice"] / state["cost"] - Decimal("1")) * Decimal("100")
                if state["cost"] > 0
                else Decimal("0")
            )
            margin = _apply_numeric_action(current_margin, action, value, field)
            state["saleprice"] = (
                state["cost"] * (Decimal("1") + margin / Decimal("100"))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            patch["salePrice"] = state["saleprice"]
        elif field in {"categoryid", "subcategoryid"}:
            target = "categoryId" if field == "categoryid" else "subcategoryId"
            patch[target] = None if action == "clear" or value in (None, "", "null") else value
        elif field == "supplier":
            patch["supplier"] = "" if action == "clear" else str(value or "").strip()
        elif field == "status":
            patch["status"] = "inactive" if action == "clear" else value

    return patch


def product_snapshot(product):
    return {
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "cost": str(product.cost),
        "price": str(product.price),
        "stock": product.stock,
        "is_active": product.is_active,
        "category_id": product.category_id,
        "category_ids": list(product.categories.order_by("pk").values_list("pk", flat=True)),
        "supplier_ref_id": product.supplier_ref_id,
        "supplier": product.supplier,
        "filter_1": product.filter_1,
        "filter_2": product.filter_2,
        "filter_3": product.filter_3,
        "filter_4": product.filter_4,
        "filter_5": product.filter_5,
        "attributes": product.attributes or {},
        "image": product.image.name if product.image else "",
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }


def _same_as_snapshot(product, snapshot):
    current = product_snapshot(product)
    return all(current.get(key) == value for key, value in snapshot.items())


def restore_product_snapshot(product, snapshot, user):
    product.sku = snapshot["sku"]
    product.name = snapshot["name"]
    product.description = snapshot["description"]
    product.cost = Decimal(snapshot["cost"])
    product.price = Decimal(snapshot["price"])
    product.stock = int(snapshot["stock"])
    product.is_active = bool(snapshot["is_active"])
    product.category_id = snapshot.get("category_id")
    product.supplier_ref_id = snapshot.get("supplier_ref_id")
    product.supplier = snapshot.get("supplier") or ""
    product.filter_1 = snapshot.get("filter_1") or ""
    product.filter_2 = snapshot.get("filter_2") or ""
    product.filter_3 = snapshot.get("filter_3") or ""
    product.filter_4 = snapshot.get("filter_4") or ""
    product.filter_5 = snapshot.get("filter_5") or ""
    product.attributes = snapshot.get("attributes") or {}
    product.image = snapshot.get("image") or None
    product.save()
    product.categories.set(snapshot.get("category_ids") or [])

    supplier = Supplier.objects.filter(pk=product.supplier_ref_id).first() if product.supplier_ref_id else None
    if supplier:
        set_preferred_supplier_preserving_terms(
            product=product,
            supplier=supplier,
            current_cost=product.cost,
            source="external_editor_rollback",
            changed_by=user,
            reason="Reversion de trabajo del editor externo.",
            match_method="external_editor_rollback",
        )
    else:
        ProductSupplier.objects.filter(product=product, is_preferred=True).update(is_preferred=False)
    product.refresh_from_db()


def preview_external_editor_job(payload):
    validate_bulk_payload(payload)
    ids = resolve_bulk_product_ids(payload)
    products = Product.objects.filter(pk__in=ids).order_by("pk")[:10]
    warnings = []
    if len(ids) > 500:
        warnings.append("La operacion requiere el procesador en segundo plano.")
    rules = payload.get("rules") or []
    direct_changes = payload.get("changes") or None
    per_product_changes = {
        int(item["productId"]): item["changes"]
        for item in (payload.get("items") or [])
    }
    if len(ids) > 1 and any(str(rule.get("field") or "").lower() == "internalcode" for rule in rules):
        warnings.append("Asignar el mismo SKU a varios productos producira conflictos de unicidad.")
    sample = []
    for product in products:
        proposed = (
            per_product_changes.get(product.pk)
            if per_product_changes
            else direct_changes if direct_changes is not None
            else rules_to_patch(product, rules)
        )
        sample.append(
            {
                "id": product.pk,
                "sku": product.sku,
                "name": product.name,
                "before": product_snapshot(product),
                "changes": proposed,
            }
        )
    return {
        "total": len(ids),
        "sample": sample,
        "warnings": warnings,
    }


def create_external_editor_job(*, payload, user, idempotency_key):
    validate_bulk_payload(payload)
    ids = resolve_bulk_product_ids(payload)
    normalized_payload = {**payload, "resolvedProductIds": ids}
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValidationError({"idempotencyKey": "Envia el header Idempotency-Key."})
    if len(key) > 120:
        raise ValidationError({"idempotencyKey": "No puede superar 120 caracteres."})

    with transaction.atomic():
        existing = ExternalEditorJob.objects.select_for_update().filter(
            created_by=user,
            idempotency_key=key,
        ).first()
        if existing:
            if existing.request_payload != normalized_payload:
                raise ExternalEditorJobPayloadConflict(
                    "La clave de idempotencia ya fue usada con otra operacion."
                )
            return existing, False
        job = ExternalEditorJob.objects.create(
            created_by=user,
            idempotency_key=key,
            request_payload=normalized_payload,
            total=len(ids),
        )
    return job, True


def execute_external_editor_job(job_id):
    job = ExternalEditorJob.objects.select_related("created_by").get(pk=job_id)
    if job.status != ExternalEditorJob.STATUS_PENDING:
        return job

    job.status = ExternalEditorJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    user = job.created_by
    payload = job.request_payload
    rules = payload.get("rules") or []
    direct_changes = payload.get("changes") or None
    item_changes = {
        int(item["productId"]): item["changes"]
        for item in (payload.get("items") or [])
    }
    succeeded = 0
    failed = 0

    for index, product_id in enumerate(payload.get("resolvedProductIds") or [], start=1):
        try:
            with transaction.atomic():
                product = (
                    Product.objects.select_for_update(of=("self",))
                    .select_related("category", "category__parent", "supplier_ref")
                    .prefetch_related("categories")
                    .get(pk=product_id)
                )
                before = product_snapshot(product)
                changes = (
                    item_changes.get(product_id)
                    if item_changes
                    else direct_changes if direct_changes is not None
                    else rules_to_patch(product, rules)
                )
                product = apply_editor_product_patch(product=product, payload=changes, user=user)
                after = product_snapshot(product)
                ExternalEditorJobItem.objects.create(
                    job=job,
                    product=product,
                    product_id_snapshot=product_id,
                    sku=product.sku,
                    status=ExternalEditorJobItem.STATUS_COMPLETED,
                    before=before,
                    after=after,
                )
                succeeded += 1
        except Exception as exc:
            ExternalEditorJobItem.objects.update_or_create(
                job=job,
                product_id_snapshot=product_id,
                defaults={
                    "product_id": product_id if Product.objects.filter(pk=product_id).exists() else None,
                    "sku": Product.objects.filter(pk=product_id).values_list("sku", flat=True).first() or "",
                    "status": ExternalEditorJobItem.STATUS_FAILED,
                    "error": str(exc)[:2000],
                },
            )
            failed += 1

        if index % 25 == 0:
            ExternalEditorJob.objects.filter(pk=job.pk).update(
                processed=index,
                succeeded=succeeded,
                failed=failed,
            )

    job.processed = succeeded + failed
    job.succeeded = succeeded
    job.failed = failed
    job.finished_at = timezone.now()
    if succeeded and not failed:
        job.status = ExternalEditorJob.STATUS_COMPLETED
    elif succeeded:
        job.status = ExternalEditorJob.STATUS_PARTIAL
    else:
        job.status = ExternalEditorJob.STATUS_FAILED
    job.save(update_fields=["processed", "succeeded", "failed", "finished_at", "status"])

    AdminAuditLog.objects.create(
        user=user,
        action="external_editor_bulk",
        target_type="external_editor_job",
        target_id=str(job.pk),
        details={
            "total": job.total,
            "succeeded": succeeded,
            "failed": failed,
            "idempotency_key": job.idempotency_key,
        },
    )
    return job


def rollback_external_editor_job(*, job, user):
    if job.status not in {ExternalEditorJob.STATUS_COMPLETED, ExternalEditorJob.STATUS_PARTIAL}:
        raise ValidationError("Este trabajo no se puede revertir en su estado actual.")

    rolled_back = 0
    conflicts = 0
    for item in job.items.filter(status=ExternalEditorJobItem.STATUS_COMPLETED).order_by("-id"):
        try:
            with transaction.atomic():
                product = (
                    Product.objects.select_for_update(of=("self",))
                    .select_related("supplier_ref")
                    .prefetch_related("categories")
                    .get(pk=item.product_id_snapshot)
                )
                if not _same_as_snapshot(product, item.after):
                    raise ExternalEditorJobPayloadConflict(
                        "El producto cambio despues del trabajo y no se sobrescribio."
                    )
                restore_product_snapshot(product, item.before, user)
                item.product = product
                item.status = ExternalEditorJobItem.STATUS_ROLLED_BACK
                item.error = ""
                item.save(update_fields=["product", "status", "error", "updated_at"])
                rolled_back += 1
        except Exception as exc:
            item.status = ExternalEditorJobItem.STATUS_ROLLBACK_CONFLICT
            item.error = str(exc)[:2000]
            item.save(update_fields=["status", "error", "updated_at"])
            conflicts += 1

    job.status = (
        ExternalEditorJob.STATUS_ROLLED_BACK
        if rolled_back and not conflicts
        else ExternalEditorJob.STATUS_ROLLBACK_PARTIAL
    )
    job.rolled_back_by = user
    job.rolled_back_at = timezone.now()
    job.save(update_fields=["status", "rolled_back_by", "rolled_back_at"])
    AdminAuditLog.objects.create(
        user=user,
        action="external_editor_bulk_rollback",
        target_type="external_editor_job",
        target_id=str(job.pk),
        details={"rolled_back": rolled_back, "conflicts": conflicts},
    )
    return job


def serialize_external_editor_job(job, include_items=True):
    payload = {
        "id": job.pk,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "succeeded": job.succeeded,
        "failed": job.failed,
        "error": job.error,
        "createdBy": job.created_by.username,
        "createdAt": job.created_at.isoformat(),
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
        "rolledBackAt": job.rolled_back_at.isoformat() if job.rolled_back_at else None,
        "canRollback": job.status in {ExternalEditorJob.STATUS_COMPLETED, ExternalEditorJob.STATUS_PARTIAL},
        "canRedo": job.status in {
            ExternalEditorJob.STATUS_COMPLETED,
            ExternalEditorJob.STATUS_PARTIAL,
            ExternalEditorJob.STATUS_ROLLED_BACK,
            ExternalEditorJob.STATUS_ROLLBACK_PARTIAL,
        },
        "operation": (
            "draft"
            if job.request_payload.get("items")
            else "formula" if job.request_payload.get("rules")
            else "bulk"
        ),
    }
    if include_items:
        payload["items"] = [
            {
                "productId": item.product_id_snapshot,
                "sku": item.sku,
                "status": item.status,
                "error": item.error,
                "before": item.before,
                "after": item.after,
            }
            for item in job.items.all()[:100]
        ]
    return payload
