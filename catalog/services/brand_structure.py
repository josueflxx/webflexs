"""Transactional structure changes; never assigns, moves or copies products."""
from collections import defaultdict
from datetime import timedelta
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import Brand, BrandAlias, BrandRubro, BrandSubrubro, BrandStructureBatch


MAX_PLAN_ROWS = 1000
PREVIEW_LIFETIME = timedelta(minutes=30)
MODELS = {"rubro": BrandRubro, "subrubro": BrandSubrubro}
EDIT_FIELDS = ("name", "image", "order", "is_active")


def normalize_name(value):
    return BrandAlias.normalize(value)


def clean_name(value):
    name = " ".join(str(value or "").split())
    if not name or len(name) > 100 or not slugify(name):
        raise ValidationError("Ingresá un nombre de 1 a 100 caracteres con letras o números.")
    return name


def snapshot(obj):
    data = {"name": obj.name, "slug": obj.slug, "image": obj.image.name or "", "order": obj.order, "is_active": obj.is_active}
    if isinstance(obj, BrandRubro):
        data.update(brand_id=obj.brand_id, icon_emoji=obj.icon_emoji, badge_text=obj.badge_text, badge_color=obj.badge_color)
    else:
        data.update(brand_rubro_id=obj.brand_rubro_id, helper_categories=sorted(c.pk for c in obj.helper_categories.all()))
    return data


def _load_structure(brand_ids, *, lock=False):
    brands = Brand.objects.filter(pk__in=brand_ids).order_by("pk")
    rubros = BrandRubro.objects.filter(brand_id__in=brand_ids).order_by("pk")
    subrubros = BrandSubrubro.objects.filter(brand_rubro__brand_id__in=brand_ids).order_by("pk").prefetch_related("helper_categories")
    if lock:
        brands = brands.select_for_update()
        rubros = rubros.select_for_update()
        subrubros = subrubros.select_for_update()
    brands, rubros, subrubros = list(brands), list(rubros), list(subrubros)
    if len(brands) != len(brand_ids):
        raise ValidationError("Cambió la selección de marcas. Generá una nueva vista previa.")
    return brands, rubros, subrubros


def _new_snapshot(kind, name, parent_id, values, used_slugs):
    base = slugify(name)[:110]
    slug, suffix = base, 1
    while slug in used_slugs:
        slug = f"{base}-{suffix}"
        suffix += 1
    used_slugs.add(slug)
    data = {"name": name, "slug": slug, "image": values.get("image", ""), "order": values.get("order", 0), "is_active": values.get("is_active", True)}
    if kind == "rubro":
        data.update(brand_id=parent_id, icon_emoji="📂", badge_text="", badge_color="orange")
    else:
        data.update(brand_rubro_id=parent_id, helper_categories=[])
    return data


def build_plan(spec, *, lock=False):
    brands, rubros, subrubros = _load_structure(spec["brand_ids"], lock=lock)
    state = {
        "brands": [(b.pk, b.name, b.is_active) for b in brands],
        "rubros": [(r.pk, snapshot(r)) for r in rubros],
        "subrubros": [(s.pk, snapshot(s)) for s in subrubros],
    }
    state_hash = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    by_brand, by_rubro = defaultdict(list), defaultdict(list)
    for rubro in rubros:
        by_brand[rubro.brand_id].append(rubro)
    for subrubro in subrubros:
        by_rubro[subrubro.brand_rubro_id].append(subrubro)
    entries = []
    operation = spec["operation"]
    kind = "subrubro" if operation.endswith("subrubro") else "rubro"
    creating = operation.startswith("create_")

    def entry(brand, status, path, message, **extra):
        row = dict(brand_id=brand.pk, brand_name=brand.name, kind=kind, status=status, path=path, message=message)
        row.update(extra)
        entries.append(row)
        return row

    for brand in brands:
        parent_id = brand.pk
        parent_key = None
        siblings = by_brand[brand.pk]
        path_prefix = ""
        if kind == "subrubro":
            parent_name = spec["rubro_name"]
            parents = [r for r in siblings if normalize_name(r.name) == normalize_name(parent_name)]
            if len(parents) > 1:
                entry(brand, "conflict", parent_name, "Hay varios rubros equivalentes. Revisalos individualmente.")
                continue
            if not parents:
                if operation != "create_subrubro" or spec["missing_parent"] != "create":
                    entry(brand, "skip", parent_name, "No existe el rubro padre; se omite esta marca.")
                    continue
                parent_key = f"new-rubro-{brand.pk}"
                data = _new_snapshot("rubro", parent_name, brand.pk, {}, {r.slug for r in siblings})
                entry(brand, "create", parent_name, "Se crea el rubro padre solicitado.", kind="rubro", key=parent_key, target_id=None, before=None, after=data)
                parent_id, siblings = None, []
            else:
                parent_id = parents[0].pk
                siblings = by_rubro[parent_id]
            path_prefix = parent_name + " / "

        used_slugs = {s.slug for s in siblings}
        names = spec["names"] if creating else [spec["subrubro_name"] if kind == "subrubro" else spec["rubro_name"]]
        for name in names:
            matches = [s for s in siblings if normalize_name(s.name) == normalize_name(name)]
            path = path_prefix + name
            if len(matches) > 1:
                entry(brand, "conflict", path, "Hay nombres equivalentes duplicados; no se fusionan automáticamente.")
                continue
            if creating:
                if matches:
                    entry(brand, "skip", path, "Ya existe; no se sobrescribe.", target_id=matches[0].pk)
                else:
                    data = _new_snapshot(kind, name, parent_id, spec["values"], used_slugs)
                    entry(brand, "create", path, "Nueva estructura, sin productos asignados.", target_id=None, parent_key=parent_key, before=None, after=data)
                continue
            if not matches:
                entry(brand, "skip", path, "No existe en esta marca; no se crea al editar.")
                continue
            target = matches[0]
            before = snapshot(target)
            after = {**before, **spec["values"]}
            if any(s.pk != target.pk and normalize_name(s.name) == normalize_name(after["name"]) for s in siblings):
                entry(brand, "conflict", path, "El nombre de destino ya existe en este nivel.")
            elif before == after:
                entry(brand, "skip", path, "Ya tiene los valores indicados.", target_id=target.pk)
            else:
                entry(brand, "update", path, "Sólo se modifican los campos seleccionados.", target_id=target.pk, before=before, after=after)
    if len(entries) > MAX_PLAN_ROWS:
        raise ValidationError(f"El lote supera {MAX_PLAN_ROWS} filas. Reducí la selección.")
    counts = {status: sum(e["status"] == status for e in entries) for status in ("create", "update", "skip", "conflict")}
    return {"state_hash": state_hash, "entries": entries, "counts": counts, "brands": [{"id": b.pk, "name": b.name, "active": b.is_active} for b in brands], "can_apply": not counts["conflict"] and bool(counts["create"] + counts["update"])}


def prepare_batch(cleaned, *, user, previous=None):
    """Store a preview and one uploaded asset; no catalog rows are changed."""
    operation = cleaned["operation"]
    creating = operation.startswith("create_")
    values = {}
    for field in EDIT_FIELDS:
        if creating and field != "name" or not creating and cleaned.get(f"change_{field}"):
            if field == "name":
                values[field] = cleaned["new_name"]
            elif field == "image":
                image = cleaned.get("image")
                previous_image = previous.image.name if previous and previous.image else ""
                values[field] = "__upload__" if image else ("" if cleaned.get("remove_image") else previous_image)
            else:
                values[field] = cleaned[field]
    initial_keys = ("scope", "include_inactive", "operation", "names", "rubro_name", "subrubro_name", "missing_parent", "new_name", "order", "is_active", "remove_image", "change_name", "change_image", "change_order", "change_is_active", "observation")
    initial = {key: cleaned.get(key) for key in initial_keys}
    initial["brand_ids"] = cleaned["resolved_brand_ids"]
    spec = dict(operation=operation, brand_ids=cleaned["resolved_brand_ids"], names=cleaned.get("names_list", []), rubro_name=cleaned.get("rubro_name", ""), subrubro_name=cleaned.get("subrubro_name", ""), missing_parent=cleaned.get("missing_parent", "skip"), values=values, initial=initial)
    plan = build_plan(spec)
    batch = BrandStructureBatch(operation=operation, spec=spec, plan=plan, observation=cleaned["observation"], created_by=user)
    upload = cleaned.get("image") if "image" in values else None
    try:
        if upload:
            batch.image.save(upload.name, upload, save=False)
            spec["values"]["image"] = batch.image.name
            batch.plan = build_plan(spec)
        elif values.get("image"):
            batch.image.name = values["image"]
        batch.save()
    except Exception:
        if upload and batch.image.name:
            batch.image.storage.delete(batch.image.name)
        raise
    return batch


@transaction.atomic
def apply_batch(batch_id, *, user):
    batch = BrandStructureBatch.objects.select_for_update().get(pk=batch_id)
    if batch.created_by_id != user.pk:
        raise ValidationError("Sólo quien generó la vista previa puede confirmarla.")
    if batch.status == "applied":
        return batch  # Repeated submit/retry must never duplicate the operation.
    if batch.status != "preview":
        raise ValidationError("Este lote ya fue deshecho. Creá una nueva vista previa.")
    if timezone.now() - batch.created_at > PREVIEW_LIFETIME:
        raise ValidationError("La vista previa venció. Generá otra antes de aplicar.")
    plan = build_plan(batch.spec, lock=True)
    if plan["state_hash"] != batch.plan["state_hash"]:
        raise ValidationError("La estructura cambió desde la vista previa. Revisá una nueva antes de confirmar.")
    if not plan["can_apply"]:
        raise ValidationError("El lote tiene conflictos o no contiene cambios para aplicar.")
    created_parents, changes = {}, []
    for row in plan["entries"]:
        if row["status"] not in {"create", "update"}:
            continue
        model = MODELS[row["kind"]]
        values = dict(row["after"])
        values.pop("helper_categories", None)
        if row["status"] == "create":
            if row.get("parent_key"):
                values["brand_rubro_id"] = created_parents[row["parent_key"]]
            obj = model(**values)
        else:
            obj = model.objects.get(pk=row["target_id"])
            for field in batch.spec["values"]:
                setattr(obj, field, values[field])
        obj.full_clean()
        obj.save()
        if row.get("key"):
            created_parents[row["key"]] = obj.pk
        changes.append({**row, "target_id": obj.pk, "after": snapshot(obj)})
    batch.changes = changes
    batch.status = "applied"
    batch.applied_at = timezone.now()
    batch.save(update_fields=["changes", "status", "applied_at"])
    return batch


def _assert_unused(obj, allowed_children):
    for relation in ("product_order_rows", "catalog_rules", "category_mappings", "catalog_batches"):
        if getattr(obj, relation).exists():
            raise ValidationError(f'No se puede deshacer: "{obj.name}" recibió productos o vínculos posteriores.')
    if isinstance(obj, BrandRubro) and obj.subrubros.exclude(pk__in=allowed_children).exists():
        raise ValidationError(f'No se puede deshacer: "{obj.name}" recibió nuevos subrubros.')


@transaction.atomic
def undo_batch(batch_id, *, user):
    batch = BrandStructureBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status != "applied":
        raise ValidationError("Sólo se puede deshacer un lote aplicado.")
    _load_structure(batch.spec["brand_ids"], lock=True)
    created_children = {r["target_id"] for r in batch.changes if r["kind"] == "subrubro" and r["status"] == "create"}
    targets = []
    for row in reversed(batch.changes):
        model = MODELS[row["kind"]]
        obj = model.objects.filter(pk=row["target_id"]).first()
        if not obj or snapshot(obj) != row["after"]:
            raise ValidationError("Hay cambios posteriores en la estructura. No se deshace ningún registro.")
        if row["status"] == "create":
            _assert_unused(obj, created_children)
        else:
            parent_field = "brand_id" if row["kind"] == "rubro" else "brand_rubro_id"
            siblings = model.objects.filter(**{parent_field: row["before"][parent_field]}).exclude(pk=obj.pk)
            if any(normalize_name(s.name) == normalize_name(row["before"]["name"]) for s in siblings):
                raise ValidationError("El nombre anterior ahora está ocupado. No se deshace ningún registro.")
        targets.append((obj, row))
    for obj, row in targets:
        if row["status"] == "create":
            obj.delete()
        else:
            for field in batch.spec["values"]:
                setattr(obj, field, row["before"][field])
            obj.full_clean()
            obj.save()
    batch.status = "undone"
    batch.undone_by = user
    batch.undone_at = timezone.now()
    batch.save(update_fields=["status", "undone_by", "undone_at"])
    return batch
