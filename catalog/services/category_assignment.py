"""
Category assignment helpers for flexible product categorization.
"""
from django.db.models import Min

from catalog.models import Category, Product


def normalize_category_ids(raw_ids):
    """Convert mixed values to a clean list of int category IDs."""
    clean_ids = []
    for raw in raw_ids or []:
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            continue
        if cid > 0:
            clean_ids.append(cid)
    return list(dict.fromkeys(clean_ids))


def assign_categories_to_product(product, category_ids, primary_category_id=None):
    """
    Assign direct categories to one product.
    Keeps legacy `category` as primary for compatibility.
    """
    clean_ids = normalize_category_ids(category_ids)
    valid_ids = list(Category.objects.filter(id__in=clean_ids).values_list("id", flat=True))

    if primary_category_id:
        try:
            primary_category_id = int(primary_category_id)
        except (TypeError, ValueError):
            primary_category_id = None

    if primary_category_id and primary_category_id not in valid_ids:
        valid_ids.append(primary_category_id)

    product.categories.set(valid_ids)
    _sync_primary_category_for_products([product.id], preferred_primary_id=primary_category_id)


def add_category_to_products(product_ids, category_id):
    """Append one category to many products efficiently."""
    product_ids = normalize_category_ids(product_ids)
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        return 0

    if not product_ids:
        return 0

    through = Product.categories.through
    rows = [through(product_id=pid, category_id=category_id) for pid in product_ids]
    through.objects.bulk_create(rows, ignore_conflicts=True, batch_size=2000)

    # Fill legacy primary category where missing.
    Product.objects.filter(id__in=product_ids, category__isnull=True).update(category_id=category_id)
    return len(product_ids)


def replace_categories_for_products(product_ids, category_id):
    """Replace all direct categories with one category for many products."""
    product_ids = normalize_category_ids(product_ids)
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        return 0

    if not product_ids:
        return 0

    through = Product.categories.through
    through.objects.filter(product_id__in=product_ids).delete()
    rows = [through(product_id=pid, category_id=category_id) for pid in product_ids]
    through.objects.bulk_create(rows, ignore_conflicts=True, batch_size=2000)

    Product.objects.filter(id__in=product_ids).update(category_id=category_id)
    return len(product_ids)


def remove_category_from_products(product_ids, category_id):
    """Remove one direct category from many products and sync primary category."""
    product_ids = normalize_category_ids(product_ids)
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        return 0

    if not product_ids:
        return 0

    through = Product.categories.through
    deleted, _ = through.objects.filter(product_id__in=product_ids, category_id=category_id).delete()
    _sync_primary_category_for_products(product_ids)
    return deleted


def _sync_primary_category_for_products(product_ids, preferred_primary_id=None):
    """
    Keep legacy Product.category aligned with many-to-many categories.
    """
    if not product_ids:
        return

    qs = Product.objects.filter(id__in=product_ids)

    if preferred_primary_id:
        try:
            preferred_primary_id = int(preferred_primary_id)
        except (TypeError, ValueError):
            preferred_primary_id = None

    through_qs = Product.categories.through.objects.filter(product_id__in=product_ids)
    first_map = dict(
        through_qs
        .values("product_id")
        .annotate(first_category_id=Min("category_id"))
        .values_list("product_id", "first_category_id")
    )
    categories_map = {}
    for pid, cid in through_qs.values_list("product_id", "category_id"):
        categories_map.setdefault(pid, set()).add(cid)

    updates = []
    for pid in qs.values_list("id", flat=True):
        if preferred_primary_id and preferred_primary_id in categories_map.get(pid, set()):
            cat_id = preferred_primary_id
        else:
            cat_id = first_map.get(pid)
        updates.append(Product(id=pid, category_id=cat_id))

    if updates:
        Product.objects.bulk_update(updates, ["category"], batch_size=1000)
