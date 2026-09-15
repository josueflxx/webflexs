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

    # Track exporter and embedded design assets so published files are rebuilt
    # automatically when their presentation changes.
    exporter_path = os.path.join(settings.BASE_DIR, 'core', 'services', 'catalog_excel_exporter.py')
    measurements_guide_path = os.path.join(
        settings.BASE_DIR,
        'core',
        'static',
        'core',
        'img',
        'todas_las_medidas.png',
    )
    design_mtimes = []
    for path in (exporter_path, measurements_guide_path):
        if os.path.exists(path):
            design_mtimes.append(
                timezone.make_aware(datetime.fromtimestamp(os.path.getmtime(path)))
            )

    sheet_queryset = template.sheets.all()
    timestamps = [
        getattr(template, "updated_at", None),
        Product.objects.aggregate(value=Max("updated_at")).get("value"),
        Category.objects.aggregate(value=Max("updated_at")).get("value"),
        CategoryProductOrder.objects.aggregate(value=Max("updated_at")).get("value"),
        sheet_queryset.aggregate(value=Max("updated_at")).get("value"),
        *design_mtimes,
    ]
    return max((value for value in timestamps if value), default=None)
