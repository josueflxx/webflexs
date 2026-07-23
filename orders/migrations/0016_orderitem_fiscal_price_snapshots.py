from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0015_orderrequest_idempotency_key_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="cost_at_purchase",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=12,
                verbose_name="Costo al momento de la venta",
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="iva_rate_snapshot",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=5,
                null=True,
                verbose_name="Alicuota IVA al momento de la venta",
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="price_override_note",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="Observacion de precio",
            ),
        ),
    ]
