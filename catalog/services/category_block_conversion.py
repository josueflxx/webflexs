"""
Convert manual category product blocks into child categories.
"""

from dataclasses import dataclass
from dataclasses import field

from django.db import transaction
from django.utils import timezone

from catalog.models import Category, CategoryProductOrder, Product


@dataclass
class CategoryBlockConversionResult:
    blocks_found: int = 0
    categories_created: int = 0
    categories_reused: int = 0
    products_processed: int = 0
    product_links_created: int = 0
    order_rows_created: int = 0
    order_rows_updated: int = 0
    skipped_without_block: int = 0
    skipped_unlinked: int = 0
    rollback_payload: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "blocks_found": self.blocks_found,
            "categories_created": self.categories_created,
            "categories_reused": self.categories_reused,
            "products_processed": self.products_processed,
            "product_links_created": self.product_links_created,
            "order_rows_created": self.order_rows_created,
            "order_rows_updated": self.order_rows_updated,
            "skipped_without_block": self.skipped_without_block,
            "skipped_unlinked": self.skipped_unlinked,
        }


@dataclass
class CategoryBlockRollbackResult:
    product_links_removed: int = 0
    order_rows_deleted: int = 0
    order_rows_restored: int = 0
    categories_deleted: int = 0
    categories_kept: int = 0
    skipped_modified_order_rows: int = 0
    skipped_missing_order_rows: int = 0
    skipped_remaining_links: int = 0

    def as_dict(self):
        return {
            "product_links_removed": self.product_links_removed,
            "order_rows_deleted": self.order_rows_deleted,
            "order_rows_restored": self.order_rows_restored,
            "categories_deleted": self.categories_deleted,
            "categories_kept": self.categories_kept,
            "skipped_modified_order_rows": self.skipped_modified_order_rows,
            "skipped_missing_order_rows": self.skipped_missing_order_rows,
            "skipped_remaining_links": self.skipped_remaining_links,
        }


def _clean_block_name(value):
    return " ".join(str(value or "").split())[:100]


def _as_positive_int(value, fallback=0):
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _order_snapshot(row):
    return {
        "category_id": row.category_id,
        "product_id": row.product_id,
        "block_label": row.block_label or "",
        "block_order": row.block_order,
        "sort_order": row.sort_order,
    }


def _created_order_matches(row, snapshot):
    return (
        (row.block_label or "") == str(snapshot.get("block_label") or "")
        and int(row.block_order or 0) == int(snapshot.get("block_order") or 0)
        and int(row.sort_order or 0) == int(snapshot.get("sort_order") or 0)
    )


def convert_category_blocks_to_subcategories(category):
    """
    Create one direct child category per named product block.

    The conversion is deliberately additive: products keep their original parent
    category link and also receive the new child category link. Existing manual
    order inside each block is copied to the new child category.
    """
    result = CategoryBlockConversionResult()

    if not category or not getattr(category, "pk", None):
        return result

    order_rows = list(
        CategoryProductOrder.objects.filter(category=category)
        .values("product_id", "block_label", "block_order", "sort_order")
        .order_by("block_order", "sort_order", "product_id")
    )
    if not order_rows:
        return result

    product_ids = [row["product_id"] for row in order_rows]
    linked_parent_product_ids = set(
        Product.categories.through.objects.filter(
            category_id=category.pk,
            product_id__in=product_ids,
        ).values_list("product_id", flat=True)
    )

    rows_to_convert = []
    block_meta = {}
    for row in order_rows:
        block_name = _clean_block_name(row.get("block_label"))
        if not block_name:
            result.skipped_without_block += 1
            continue
        if row["product_id"] not in linked_parent_product_ids:
            result.skipped_unlinked += 1
            continue

        rows_to_convert.append({**row, "block_name": block_name})
        if block_name.lower() not in block_meta:
            block_order = _as_positive_int(row.get("block_order"), fallback=0)
            block_meta[block_name.lower()] = {
                "name": block_name,
                "order": block_order,
            }

    result.blocks_found = len(block_meta)
    result.products_processed = len({row["product_id"] for row in rows_to_convert})
    if not rows_to_convert:
        return result

    with transaction.atomic():
        rollback_payload = {
            "category_id": category.pk,
            "created_category_ids": [],
            "reused_category_ids": [],
            "product_links_created": [],
            "order_rows_created": [],
            "order_rows_updated": [],
        }
        child_by_key = {}
        existing_children = Category.objects.filter(parent=category)
        for child in existing_children:
            child_by_key.setdefault((child.name or "").strip().lower(), child)

        for index, meta in enumerate(block_meta.values(), start=1):
            key = meta["name"].lower()
            child = child_by_key.get(key)
            if child:
                result.categories_reused += 1
                rollback_payload["reused_category_ids"].append(child.pk)
            else:
                order_value = meta["order"] or index * 10
                child = Category.objects.create(
                    name=meta["name"],
                    public_name=meta["name"],
                    parent=category,
                    order=order_value,
                    public_order=order_value,
                    is_active=category.is_active,
                    visible_in_catalog=category.visible_in_catalog,
                )
                child_by_key[key] = child
                result.categories_created += 1
                rollback_payload["created_category_ids"].append(child.pk)

        through_model = Product.categories.through
        child_product_pairs = []
        for row in rows_to_convert:
            child = child_by_key[row["block_name"].lower()]
            child_product_pairs.append((child.pk, row["product_id"]))

        child_ids = {category_id for category_id, _product_id in child_product_pairs}
        row_product_ids = {product_id for _category_id, product_id in child_product_pairs}
        existing_pairs = set(
            through_model.objects.filter(
                category_id__in=child_ids,
                product_id__in=row_product_ids,
            ).values_list("category_id", "product_id")
        )
        new_links = [
            through_model(category_id=category_id, product_id=product_id)
            for category_id, product_id in child_product_pairs
            if (category_id, product_id) not in existing_pairs
        ]
        if new_links:
            through_model.objects.bulk_create(new_links, ignore_conflicts=True, batch_size=2000)
        result.product_links_created = len(new_links)
        rollback_payload["product_links_created"] = [
            {"category_id": link.category_id, "product_id": link.product_id}
            for link in new_links
        ]

        existing_order_rows = {
            (row.category_id, row.product_id): row
            for row in CategoryProductOrder.objects.filter(
                category_id__in=child_ids,
                product_id__in=row_product_ids,
            )
        }
        creates = []
        updates = []
        now = timezone.now()
        for row in rows_to_convert:
            child = child_by_key[row["block_name"].lower()]
            key = (child.pk, row["product_id"])
            block_order = _as_positive_int(row.get("block_order"), fallback=0)
            sort_order = _as_positive_int(row.get("sort_order"), fallback=0)
            existing = existing_order_rows.get(key)
            if existing:
                changed = False
                if existing.block_label:
                    existing.block_label = ""
                    changed = True
                if existing.block_order != block_order:
                    existing.block_order = block_order
                    changed = True
                if existing.sort_order != sort_order:
                    existing.sort_order = sort_order
                    changed = True
                if changed:
                    rollback_payload["order_rows_updated"].append(
                        {
                            "category_id": existing.category_id,
                            "product_id": existing.product_id,
                            "before": _order_snapshot(existing),
                        }
                    )
                    existing.updated_at = now
                    updates.append(existing)
                continue

            creates.append(
                CategoryProductOrder(
                    category=child,
                    product_id=row["product_id"],
                    block_label="",
                    block_order=block_order,
                    sort_order=sort_order,
                )
            )

        if creates:
            CategoryProductOrder.objects.bulk_create(creates, ignore_conflicts=True, batch_size=2000)
            rollback_payload["order_rows_created"] = [_order_snapshot(row) for row in creates]
        if updates:
            CategoryProductOrder.objects.bulk_update(
                updates,
                ["block_label", "block_order", "sort_order", "updated_at"],
                batch_size=1000,
            )
        result.order_rows_created = len(creates)
        result.order_rows_updated = len(updates)
        result.rollback_payload = rollback_payload

    return result


def rollback_category_block_conversion(category, rollback_payload):
    """
    Undo a previous block conversion from its stored rollback payload.
    """
    result = CategoryBlockRollbackResult()
    payload = rollback_payload or {}
    if not category or not getattr(category, "pk", None):
        return result
    if int(payload.get("category_id") or 0) != int(category.pk):
        return result

    through_model = Product.categories.through
    now = timezone.now()

    with transaction.atomic():
        for snapshot in payload.get("order_rows_created", []):
            category_id = int(snapshot.get("category_id") or 0)
            product_id = int(snapshot.get("product_id") or 0)
            row = CategoryProductOrder.objects.filter(
                category_id=category_id,
                product_id=product_id,
            ).first()
            if not row:
                result.skipped_missing_order_rows += 1
                continue
            if not _created_order_matches(row, snapshot):
                result.skipped_modified_order_rows += 1
                continue
            row.delete()
            result.order_rows_deleted += 1

        for item in payload.get("order_rows_updated", []):
            before = item.get("before") or {}
            category_id = int(before.get("category_id") or item.get("category_id") or 0)
            product_id = int(before.get("product_id") or item.get("product_id") or 0)
            row = CategoryProductOrder.objects.filter(
                category_id=category_id,
                product_id=product_id,
            ).first()
            if not row:
                result.skipped_missing_order_rows += 1
                continue
            row.block_label = str(before.get("block_label") or "")
            row.block_order = _as_positive_int(before.get("block_order"), fallback=0)
            row.sort_order = _as_positive_int(before.get("sort_order"), fallback=0)
            row.updated_at = now
            row.save(update_fields=["block_label", "block_order", "sort_order", "updated_at"])
            result.order_rows_restored += 1

        for link in payload.get("product_links_created", []):
            category_id = int(link.get("category_id") or 0)
            product_id = int(link.get("product_id") or 0)
            if CategoryProductOrder.objects.filter(category_id=category_id, product_id=product_id).exists():
                result.skipped_remaining_links += 1
                continue
            deleted, _ = through_model.objects.filter(
                category_id=category_id,
                product_id=product_id,
            ).delete()
            result.product_links_removed += deleted

        for category_id in payload.get("created_category_ids", []):
            child = Category.objects.filter(pk=category_id, parent=category).first()
            if not child:
                continue
            has_children = Category.objects.filter(parent=child).exists()
            has_primary_products = Product.objects.filter(category=child).exists()
            has_m2m_products = child.products_m2m.exists()
            has_order_rows = CategoryProductOrder.objects.filter(category=child).exists()
            if has_children or has_primary_products or has_m2m_products or has_order_rows:
                result.categories_kept += 1
                continue
            child.delete()
            result.categories_deleted += 1

    return result
