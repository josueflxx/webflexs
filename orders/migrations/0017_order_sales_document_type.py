import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_arca_fiscal_integrity"),
        ("orders", "0016_orderitem_fiscal_price_snapshots"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="sales_document_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="commercial_orders",
                to="core.salesdocumenttype",
                verbose_name="Tipo de documento comercial",
            ),
        ),
    ]
