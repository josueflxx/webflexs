from django.db import migrations


def ensure_billing_company_column(apps, schema_editor):
    table_name = "accounts_clienttransaction"
    column_name = "billing_company"
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        table_names = connection.introspection.table_names(cursor)
        if table_name not in table_names:
            return

        description = connection.introspection.get_table_description(cursor, table_name)
        existing_columns = {col.name for col in description}
        if column_name in existing_columns:
            return

        vendor = connection.vendor
        if vendor == "postgresql":
            schema_editor.execute(
                "ALTER TABLE accounts_clienttransaction "
                "ADD COLUMN billing_company varchar(20) NOT NULL DEFAULT 'flexs';"
            )
        else:
            schema_editor.execute(
                "ALTER TABLE accounts_clienttransaction "
                "ADD COLUMN billing_company varchar(20) NOT NULL DEFAULT 'flexs';"
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_clienttransaction_billing_company_state"),
    ]

    operations = [
        migrations.RunPython(ensure_billing_company_column, noop_reverse),
    ]
