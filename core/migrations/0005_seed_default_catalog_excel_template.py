from django.db import migrations


def seed_default_template(apps, schema_editor):
    Template = apps.get_model("core", "CatalogExcelTemplate")
    Sheet = apps.get_model("core", "CatalogExcelTemplateSheet")
    Column = apps.get_model("core", "CatalogExcelTemplateColumn")

    template, created = Template.objects.get_or_create(
        slug="catalogo-general",
        defaults={
            "name": "Catalogo General",
            "description": "Plantilla base para exportar todo el catalogo actualizado.",
            "is_active": True,
        },
    )
    if not created:
        return

    sheet = Sheet.objects.create(
        template=template,
        name="Productos",
        order=0,
        include_header=True,
        only_active_products=True,
        only_catalog_visible=False,
        include_descendant_categories=True,
        sort_by="name_asc",
    )
    default_columns = [
        ("sku", "SKU"),
        ("name", "Articulo"),
        ("price", "Precio"),
    ]
    for index, (key, header) in enumerate(default_columns):
        Column.objects.create(
            sheet=sheet,
            key=key,
            header=header,
            order=index,
            is_active=True,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_catalogexceltemplate_catalogexceltemplatesheet_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_default_template, noop_reverse),
    ]
