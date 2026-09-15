import re
from collections import Counter

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from catalog.models import (
    Brand,
    BrandAlias,
    BrandCatalogBatch,
    BrandCatalogRule,
    BrandRubro,
    BrandRubroProductOrder,
    BrandSubrubro,
    BrandSubrubroProductOrder,
    Product,
)


def uncataloged_products(queryset=None):
    """Products that do not belong to any brand rubro or subrubro."""
    base = queryset if queryset is not None else Product.objects.all()
    return base.filter(
        brand_rubro_orders__isnull=True,
        brand_subrubro_orders__isnull=True,
    ).distinct()


def brand_quality_metrics():
    """Small, query-safe summary used by the brand dashboard and inbox."""
    active_ids = set(Product.objects.filter(is_active=True).values_list("id", flat=True))
    rubro_rows = list(
        BrandRubroProductOrder.objects.filter(product_id__in=active_ids)
        .values_list("product_id", "brand_rubro__brand_id")
    )
    subrubro_rows = list(
        BrandSubrubroProductOrder.objects.filter(product_id__in=active_ids)
        .values_list("product_id", "brand_subrubro__brand_rubro__brand_id")
    )

    rubro_product_ids = {product_id for product_id, _brand_id in rubro_rows}
    subrubro_product_ids = {product_id for product_id, _brand_id in subrubro_rows}
    assigned_ids = rubro_product_ids | subrubro_product_ids

    product_brand_pairs = set(rubro_rows) | set(subrubro_rows)
    brand_counts = Counter(product_id for product_id, _brand_id in product_brand_pairs)
    ambiguous = sum(1 for count in brand_counts.values() if count > 1)
    total = len(active_ids)
    assigned = len(assigned_ids)

    return {
        "total": total,
        "assigned": assigned,
        "unassigned": max(total - assigned, 0),
        "with_subrubro": len(subrubro_product_ids),
        "rubro_only": len(rubro_product_ids - subrubro_product_ids),
        "ambiguous": ambiguous,
        "coverage": round((assigned / total * 100), 1) if total else 100.0,
        "aliases": BrandAlias.objects.filter(is_active=True).count(),
        "rules": BrandCatalogRule.objects.filter(is_active=True).count(),
    }


class BrandSuggestionEngine:
    """Scores deterministic suggestions without applying any data changes."""

    def __init__(self):
        self.aliases = list(
            BrandAlias.objects.filter(is_active=True, brand__is_active=True)
            .select_related("brand")
            .order_by("brand__name", "value")
        )
        self.brands = list(Brand.objects.filter(is_active=True).order_by("order", "name"))
        self.rules = list(
            BrandCatalogRule.objects.filter(is_active=True, brand__is_active=True)
            .select_related("brand", "brand_rubro", "brand_subrubro__brand_rubro")
            .order_by("-priority", "-confidence", "id")
        )
        self.helper_targets = []
        for subrubro in (
            BrandSubrubro.objects.filter(
                is_active=True,
                brand_rubro__is_active=True,
                brand_rubro__brand__is_active=True,
                helper_categories__isnull=False,
            )
            .select_related("brand_rubro__brand")
            .prefetch_related("helper_categories")
            .distinct()
        ):
            helper_ids = set()
            for category in subrubro.helper_categories.all():
                helper_ids.update(category.get_descendant_ids(include_self=True, only_active=True))
            self.helper_targets.append((subrubro, helper_ids))

    @staticmethod
    def _matches(text, pattern, mode):
        text = BrandAlias.normalize(text)
        pattern = BrandAlias.normalize(pattern)
        if not text or not pattern:
            return False
        if mode == BrandCatalogRule.MATCH_PREFIX:
            return text.startswith(pattern)
        if mode == BrandCatalogRule.MATCH_WORD:
            return bool(re.search(rf"(^| ){re.escape(pattern)}($| )", text))
        return pattern in text

    @staticmethod
    def _product_fields(product):
        linked_categories = list(product.categories.all())
        if product.category_id and all(category.id != product.category_id for category in linked_categories):
            linked_categories.append(product.category)
        category_text = " ".join(
            f"{category.get_full_path()} {category.name}" for category in linked_categories
        )
        fields = {
            BrandCatalogRule.FIELD_NAME: product.name,
            BrandCatalogRule.FIELD_SKU: product.sku,
            BrandCatalogRule.FIELD_SUPPLIER: product.supplier,
            BrandCatalogRule.FIELD_CATEGORY: category_text,
        }
        fields[BrandCatalogRule.FIELD_ANY] = " ".join(fields.values())
        return fields, linked_categories

    @staticmethod
    def _suggestion(brand, rubro=None, subrubro=None, confidence=0, reason=""):
        return {
            "brand": brand,
            "rubro": rubro,
            "subrubro": subrubro,
            "confidence": min(int(confidence), 100),
            "reason": reason,
            "destination": " > ".join(
                item.name for item in (brand, rubro, subrubro) if item is not None
            ),
        }

    def suggest(self, product, limit=3):
        fields, linked_categories = self._product_fields(product)
        suggestions = {}

        def add(brand, rubro=None, subrubro=None, confidence=0, reason=""):
            key = (brand.pk, rubro.pk if rubro else None, subrubro.pk if subrubro else None)
            current = suggestions.get(key)
            candidate = self._suggestion(
                brand,
                rubro=rubro,
                subrubro=subrubro,
                confidence=confidence,
                reason=reason,
            )
            if current is None or candidate["confidence"] > current["confidence"]:
                suggestions[key] = candidate

        any_text = fields[BrandCatalogRule.FIELD_ANY]
        for alias in self.aliases:
            if self._matches(any_text, alias.normalized_value, BrandCatalogRule.MATCH_WORD):
                add(alias.brand, confidence=82, reason=f'Alias reconocido: "{alias.value}"')

        for brand in self.brands:
            if self._matches(any_text, brand.name, BrandCatalogRule.MATCH_WORD):
                add(brand, confidence=76, reason=f'Marca encontrada: "{brand.name}"')

        for rule in self.rules:
            source = fields.get(rule.source_field, any_text)
            if self._matches(source, rule.pattern, rule.match_mode):
                rubro = rule.brand_rubro
                subrubro = rule.brand_subrubro
                if subrubro and rubro is None:
                    rubro = subrubro.brand_rubro
                add(
                    rule.brand,
                    rubro=rubro,
                    subrubro=subrubro,
                    confidence=rule.confidence,
                    reason=f'Regla: {rule.get_source_field_display()} {rule.get_match_mode_display().lower()} "{rule.pattern}"',
                )

        product_category_ids = set()
        for category in linked_categories:
            product_category_ids.update(category.get_ancestor_ids(include_self=True))
        for subrubro, helper_ids in self.helper_targets:
            if product_category_ids & helper_ids:
                same_brand_suggestions = [
                    item
                    for item in suggestions.values()
                    if item["brand"].pk == subrubro.brand_rubro.brand_id
                ]
                if not same_brand_suggestions:
                    continue
                recognized_brand = max(
                    same_brand_suggestions,
                    key=lambda item: item["confidence"],
                )
                add(
                    subrubro.brand_rubro.brand,
                    rubro=subrubro.brand_rubro,
                    subrubro=subrubro,
                    confidence=min(recognized_brand["confidence"] + 8, 94),
                    reason=f'{recognized_brand["reason"]}; categoria compatible',
                )

        ranked = sorted(
            suggestions.values(),
            key=lambda item: (
                -item["confidence"],
                0 if item["subrubro"] else 1 if item["rubro"] else 2,
                item["destination"],
            ),
        )
        return ranked[:limit]


def _next_sort_order(model, target_field, target):
    return (
        model.objects.filter(**{target_field: target}).aggregate(value=Max("sort_order"))["value"]
        or 0
    ) + 10


@transaction.atomic
def assign_products_to_brand_catalog(
    *,
    product_ids,
    brand,
    rubro=None,
    subrubro=None,
    user=None,
    observation,
    operation=BrandCatalogBatch.OPERATION_ASSIGN,
    mode="add",
):
    """Assign products and keep the exact created rows so the action is reversible."""
    observation = str(observation or "").strip()
    if not observation:
        raise ValueError("La observacion es obligatoria.")
    if subrubro is not None:
        if rubro is None:
            rubro = subrubro.brand_rubro
        if subrubro.brand_rubro_id != rubro.pk:
            raise ValueError("El subrubro no pertenece al rubro seleccionado.")
    if rubro is None:
        raise ValueError("Selecciona al menos un rubro.")
    if rubro.brand_id != brand.pk:
        raise ValueError("El rubro no pertenece a la marca seleccionada.")

    normalized_ids = sorted(
        {
            int(product_id)
            for product_id in product_ids
            if str(product_id).strip().isdigit()
        }
    )
    products = list(Product.objects.filter(pk__in=normalized_ids).order_by("pk"))
    if not products:
        raise ValueError("Selecciona al menos un producto valido.")
    if mode not in {"add", "move"}:
        raise ValueError("El modo de asignacion no es valido.")

    created_rubro_ids = []
    created_subrubro_ids = []
    removed_rubro_rows = []
    removed_subrubro_rows = []

    if mode == "move":
        product_ids_to_move = [product.pk for product in products]
        previous_subrubro_rows = list(
            BrandSubrubroProductOrder.objects.filter(product_id__in=product_ids_to_move)
            .exclude(brand_subrubro__brand_rubro__brand=brand)
            .values("brand_subrubro_id", "product_id", "sort_order")
        )
        previous_rubro_rows = list(
            BrandRubroProductOrder.objects.filter(product_id__in=product_ids_to_move)
            .exclude(brand_rubro__brand=brand)
            .values("brand_rubro_id", "product_id", "sort_order")
        )
        removed_subrubro_rows.extend(previous_subrubro_rows)
        removed_rubro_rows.extend(previous_rubro_rows)
        if previous_subrubro_rows:
            BrandSubrubroProductOrder.objects.filter(
                product_id__in=product_ids_to_move,
                brand_subrubro_id__in={
                    row["brand_subrubro_id"] for row in previous_subrubro_rows
                },
            ).delete()
        if previous_rubro_rows:
            BrandRubroProductOrder.objects.filter(
                product_id__in=product_ids_to_move,
                brand_rubro_id__in={row["brand_rubro_id"] for row in previous_rubro_rows},
            ).delete()
        operation = BrandCatalogBatch.OPERATION_MOVE

    rubro_order = _next_sort_order(BrandRubroProductOrder, "brand_rubro", rubro)
    subrubro_order = (
        _next_sort_order(BrandSubrubroProductOrder, "brand_subrubro", subrubro)
        if subrubro is not None
        else None
    )

    for product in products:
        rubro_row, rubro_created = BrandRubroProductOrder.objects.get_or_create(
            brand_rubro=rubro,
            product=product,
            defaults={"sort_order": rubro_order},
        )
        if rubro_created:
            created_rubro_ids.append(rubro_row.pk)
            rubro_order += 10

        if subrubro is not None:
            subrubro_row, subrubro_created = BrandSubrubroProductOrder.objects.get_or_create(
                brand_subrubro=subrubro,
                product=product,
                defaults={"sort_order": subrubro_order},
            )
            if subrubro_created:
                created_subrubro_ids.append(subrubro_row.pk)
                subrubro_order += 10

    batch = BrandCatalogBatch(
        operation=operation,
        brand=brand,
        brand_rubro=rubro,
        brand_subrubro=subrubro,
        product_ids=[product.pk for product in products],
        created_rubro_row_ids=created_rubro_ids,
        created_subrubro_row_ids=created_subrubro_ids,
        removed_rubro_rows=removed_rubro_rows,
        removed_subrubro_rows=removed_subrubro_rows,
        observation=observation,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    batch.full_clean()
    batch.save()
    return batch


@transaction.atomic
def remove_products_from_brand_catalog(
    *,
    product_ids,
    brand,
    rubro,
    subrubro=None,
    user=None,
    observation,
):
    """Remove products from one destination and retain snapshots for one-click undo."""
    observation = str(observation or "").strip()
    if not observation:
        raise ValueError("La observacion es obligatoria.")
    if rubro.brand_id != brand.pk:
        raise ValueError("El rubro no pertenece a la marca seleccionada.")
    if subrubro is not None and subrubro.brand_rubro_id != rubro.pk:
        raise ValueError("El subrubro no pertenece al rubro seleccionado.")

    normalized_ids = sorted(
        {
            int(product_id)
            for product_id in product_ids
            if str(product_id).strip().isdigit()
        }
    )
    valid_ids = set(Product.objects.filter(pk__in=normalized_ids).values_list("pk", flat=True))
    if not valid_ids:
        raise ValueError("Selecciona al menos un producto valido.")

    removed_rubro_rows = []
    removed_subrubro_rows = []
    if subrubro is not None:
        removed_subrubro_rows = list(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=subrubro,
                product_id__in=valid_ids,
            ).values("brand_subrubro_id", "product_id", "sort_order")
        )
        BrandSubrubroProductOrder.objects.filter(
            brand_subrubro=subrubro,
            product_id__in=valid_ids,
        ).delete()
    else:
        removed_subrubro_rows = list(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro__brand_rubro=rubro,
                product_id__in=valid_ids,
            ).values("brand_subrubro_id", "product_id", "sort_order")
        )
        removed_rubro_rows = list(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=rubro,
                product_id__in=valid_ids,
            ).values("brand_rubro_id", "product_id", "sort_order")
        )
        BrandSubrubroProductOrder.objects.filter(
            brand_subrubro__brand_rubro=rubro,
            product_id__in=valid_ids,
        ).delete()
        BrandRubroProductOrder.objects.filter(
            brand_rubro=rubro,
            product_id__in=valid_ids,
        ).delete()

    removed_product_ids = sorted(
        {
            row["product_id"]
            for row in [*removed_rubro_rows, *removed_subrubro_rows]
        }
    )
    if not removed_product_ids:
        raise ValueError("Los productos seleccionados ya no pertenecen a este destino.")

    batch = BrandCatalogBatch(
        operation=BrandCatalogBatch.OPERATION_REMOVE,
        brand=brand,
        brand_rubro=rubro,
        brand_subrubro=subrubro,
        product_ids=removed_product_ids,
        removed_rubro_rows=removed_rubro_rows,
        removed_subrubro_rows=removed_subrubro_rows,
        observation=observation,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    batch.full_clean()
    batch.save()
    return batch


@transaction.atomic
def undo_brand_catalog_batch(batch, *, user=None):
    """Delete only association rows created by this batch."""
    locked_batch = BrandCatalogBatch.objects.select_for_update().get(pk=batch.pk)
    if not locked_batch.can_undo:
        raise ValueError("Este lote ya fue deshecho.")

    BrandSubrubroProductOrder.objects.filter(
        pk__in=locked_batch.created_subrubro_row_ids or [],
        brand_subrubro_id=locked_batch.brand_subrubro_id,
        product_id__in=locked_batch.product_ids or [],
    ).delete()
    BrandRubroProductOrder.objects.filter(
        pk__in=locked_batch.created_rubro_row_ids or [],
        brand_rubro_id=locked_batch.brand_rubro_id,
        product_id__in=locked_batch.product_ids or [],
    ).delete()

    valid_product_ids = set(
        Product.objects.filter(pk__in=locked_batch.product_ids or []).values_list("pk", flat=True)
    )
    valid_rubro_ids = set(
        BrandRubro.objects.filter(
            pk__in={
                row.get("brand_rubro_id")
                for row in locked_batch.removed_rubro_rows or []
                if row.get("brand_rubro_id")
            }
        ).values_list("pk", flat=True)
    )
    valid_subrubro_ids = set(
        BrandSubrubro.objects.filter(
            pk__in={
                row.get("brand_subrubro_id")
                for row in locked_batch.removed_subrubro_rows or []
                if row.get("brand_subrubro_id")
            }
        ).values_list("pk", flat=True)
    )
    BrandRubroProductOrder.objects.bulk_create(
        [
            BrandRubroProductOrder(
                brand_rubro_id=row["brand_rubro_id"],
                product_id=row["product_id"],
                sort_order=row.get("sort_order") or 0,
            )
            for row in locked_batch.removed_rubro_rows or []
            if row.get("brand_rubro_id") in valid_rubro_ids
            and row.get("product_id") in valid_product_ids
        ],
        ignore_conflicts=True,
    )
    BrandSubrubroProductOrder.objects.bulk_create(
        [
            BrandSubrubroProductOrder(
                brand_subrubro_id=row["brand_subrubro_id"],
                product_id=row["product_id"],
                sort_order=row.get("sort_order") or 0,
            )
            for row in locked_batch.removed_subrubro_rows or []
            if row.get("brand_subrubro_id") in valid_subrubro_ids
            and row.get("product_id") in valid_product_ids
        ],
        ignore_conflicts=True,
    )

    locked_batch.status = BrandCatalogBatch.STATUS_UNDONE
    locked_batch.undone_by = user if getattr(user, "is_authenticated", False) else None
    locked_batch.undone_at = timezone.now()
    locked_batch.save(update_fields=["status", "undone_by", "undone_at"])
    return locked_batch
