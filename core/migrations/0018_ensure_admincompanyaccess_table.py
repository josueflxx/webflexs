from django.db import migrations


def ensure_admin_company_access_table(apps, schema_editor):
    AdminCompanyAccess = apps.get_model("core", "AdminCompanyAccess")
    table_name = AdminCompanyAccess._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_tables = set(schema_editor.connection.introspection.table_names(cursor))

    if table_name in existing_tables:
        return

    schema_editor.create_model(AdminCompanyAccess)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_admincompanyaccess"),
    ]

    operations = [
        migrations.RunPython(
            ensure_admin_company_access_table,
            migrations.RunPython.noop,
        ),
    ]
