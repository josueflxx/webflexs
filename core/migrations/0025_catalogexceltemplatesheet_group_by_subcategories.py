from django.db import migrations, models


def enable_grouping_for_category_sheets(apps, schema_editor):
    Sheet = apps.get_model("core", "CatalogExcelTemplateSheet")
    Sheet.objects.filter(
        categories__isnull=False,
        include_descendant_categories=True,
    ).distinct().update(group_by_subcategories=True)


def disable_grouping_for_category_sheets(apps, schema_editor):
    Sheet = apps.get_model("core", "CatalogExcelTemplateSheet")
    Sheet.objects.filter(group_by_subcategories=True).update(group_by_subcategories=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_alter_fiscaldocument_doc_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogexceltemplatesheet",
            name="group_by_subcategories",
            field=models.BooleanField(
                default=False,
                help_text="Cuando la hoja filtra una categoria principal, agrupa los productos en tablas internas por subcategoria.",
                verbose_name="Separar por subcategorias",
            ),
        ),
        migrations.RunPython(enable_grouping_for_category_sheets, disable_grouping_for_category_sheets),
    ]
