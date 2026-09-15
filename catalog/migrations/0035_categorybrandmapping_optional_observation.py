from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0034_brandcatalogbatch_optional_observation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="categorybrandmapping",
            name="observation",
            field=models.CharField(
                blank=True,
                max_length=300,
                verbose_name="Observacion",
            ),
        ),
    ]
