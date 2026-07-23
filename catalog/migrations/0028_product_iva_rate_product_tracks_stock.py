from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0027_supplierpricelistbatch_pricing_mode_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="iva_rate",
            field=models.DecimalField(
                blank=True,
                choices=[
                    (Decimal("0.00"), "0%"),
                    (Decimal("2.50"), "2,5%"),
                    (Decimal("5.00"), "5%"),
                    (Decimal("10.50"), "10,5%"),
                    (Decimal("21.00"), "21%"),
                    (Decimal("27.00"), "27%"),
                ],
                decimal_places=2,
                help_text="Se aplica al emitir comprobantes electronicos. El precio de catalogo no incluye IVA.",
                max_digits=5,
                null=True,
                verbose_name="Alicuota IVA",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="tracks_stock",
            field=models.BooleanField(
                default=False,
                help_text="Si esta activo, el stock se actualiza cuando el comprobante obtiene CAE.",
                verbose_name="Controlar stock",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="price",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=12,
                verbose_name="Precio neto sin IVA",
            ),
        ),
    ]
