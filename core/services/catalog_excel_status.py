from django.db.models import Max


def latest_catalog_excel_source_change(template):
    """Return the latest catalog-side change that can affect the published Excel."""
    if template is None:
        return None

    from catalog.models import Category, CategoryProductOrder, Product

    sheet_queryset = template.sheets.all()
    timestamps = [
        getattr(template, "updated_at", None),
        Product.objects.aggregate(value=Max("updated_at")).get("value"),
        Category.objects.aggregate(value=Max("updated_at")).get("value"),
        CategoryProductOrder.objects.aggregate(value=Max("updated_at")).get("value"),
        sheet_queryset.aggregate(value=Max("updated_at")).get("value"),
    ]
    return max((value for value in timestamps if value), default=None)
