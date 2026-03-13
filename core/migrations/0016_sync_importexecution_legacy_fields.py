from django.db import migrations, models
import django.db.models.deletion


def sync_importexecution_legacy_fields(apps, schema_editor):
    ImportExecution = apps.get_model("core", "ImportExecution")
    table_name = ImportExecution._meta.db_table
    quoted_table_name = schema_editor.quote_name(table_name)

    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
    existing_columns = {column.name for column in description}
    vendor = schema_editor.connection.vendor

    if "metrics" not in existing_columns:
        metrics_type = "jsonb" if vendor == "postgresql" else "TEXT"
        schema_editor.execute(
            f"ALTER TABLE {quoted_table_name} "
            f"ADD COLUMN metrics {metrics_type} NOT NULL DEFAULT '{{}}'"
        )

    if "supplier_name" not in existing_columns:
        schema_editor.execute(
            f"ALTER TABLE {quoted_table_name} "
            f"ADD COLUMN supplier_name varchar(120) NOT NULL DEFAULT ''"
        )

    if "supplier_id" not in existing_columns:
        schema_editor.execute(
            f"ALTER TABLE {quoted_table_name} "
            f"ADD COLUMN supplier_id bigint NULL"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0016_pricelist_pricelistitem_and_more"),
        ("core", "0015_seed_sales_document_types"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(sync_importexecution_legacy_fields, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="importexecution",
                    name="metrics",
                    field=models.JSONField(blank=True, default=dict),
                ),
                migrations.AddField(
                    model_name="importexecution",
                    name="supplier",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="import_executions",
                        to="catalog.supplier",
                    ),
                ),
                migrations.AddField(
                    model_name="importexecution",
                    name="supplier_name",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
            ],
        ),
    ]
