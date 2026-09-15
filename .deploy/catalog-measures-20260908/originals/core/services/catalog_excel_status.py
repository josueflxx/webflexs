from django.db.models import Max


def latest_catalog_excel_source_change(template):
    """Return the latest catalog-side change that can affect the published Excel."""
    if template is None:
        return None

    import os
    from datetime import datetime
    from django.conf import settings
    from django.utils import timezone
    from catalog.models import Category, CategoryProductOrder, Product

    # Track mtime of the catalog_excel_exporter.py script for automatic design-change cache invalidation
    exporter_path = os.path.join(settings.BASE_DIR, 'core', 'services', 'catalog_excel_exporter.py')
    exporter_mtime = None
    if os.path.exists(exporter_path):
        exporter_mtime = timezone.make_aware(
            datetime.fromtimestamp(os.path.getmtime(exporter_path))
        )

    sheet_queryset = template.sheets.all()
    timestamps = [
        getattr(template, "updated_at", None),
        Product.objects.aggregate(value=Max("updated_at")).get("value"),
        Category.objects.aggregate(value=Max("updated_at")).get("value"),
        CategoryProductOrder.objects.aggregate(value=Max("updated_at")).get("value"),
        sheet_queryset.aggregate(value=Max("updated_at")).get("value"),
        exporter_mtime,
    ]
    return max((value for value in timestamps if value), default=None)
