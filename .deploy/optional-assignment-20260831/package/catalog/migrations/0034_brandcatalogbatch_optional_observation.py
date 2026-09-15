from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0033_brandstructurebatch"),
    ]

    operations = [
        migrations.AlterField(
            model_name="brandcatalogbatch",
            name="observation",
            field=models.TextField(blank=True, verbose_name="Observacion"),
        ),
    ]
