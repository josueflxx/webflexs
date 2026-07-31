from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0030_brand_cataloging_workflow"),
    ]

    operations = [
        migrations.AlterField(
            model_name="brandcatalogbatch",
            name="operation",
            field=models.CharField(
                choices=[
                    ("assign", "Asignacion manual"),
                    ("rule", "Asignacion sugerida"),
                    ("import", "Importacion"),
                    ("move", "Movimiento entre marcas"),
                    ("remove", "Desasignacion manual"),
                ],
                default="assign",
                max_length=20,
                verbose_name="Origen",
            ),
        ),
        migrations.AddField(
            model_name="brandcatalogbatch",
            name="removed_rubro_rows",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Asociaciones de rubro retiradas por el lote para poder restaurarlas.",
            ),
        ),
        migrations.AddField(
            model_name="brandcatalogbatch",
            name="removed_subrubro_rows",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Asociaciones de subrubro retiradas por el lote para poder restaurarlas.",
            ),
        ),
    ]
