import re

from catalog.models import Supplier


def clean_supplier_name(value):
    """Compact spaces and trim supplier name."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def ensure_supplier(value):
    """
    Return a normalized Supplier for a given raw string.
    """
    name = clean_supplier_name(value)
    if not name:
        return None

    normalized = Supplier.normalize_name(name)
    supplier = Supplier.objects.filter(normalized_name=normalized).first()
    if supplier:
        if supplier.name != name:
            supplier.name = name
            supplier.save(update_fields=["name", "normalized_name", "updated_at"])
        return supplier

    return Supplier.objects.create(name=name)
