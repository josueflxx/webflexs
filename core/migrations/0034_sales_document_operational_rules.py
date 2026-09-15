from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_sales_rule_snapshots(apps, schema_editor):
    SalesDocumentType = apps.get_model("core", "SalesDocumentType")
    FiscalDocument = apps.get_model("core", "FiscalDocument")
    InternalDocument = apps.get_model("core", "InternalDocument")
    alias = schema_editor.connection.alias

    types = {
        item.pk: item
        for item in SalesDocumentType.objects.using(alias)
        .select_related("point_of_sale", "default_warehouse")
        .all()
    }

    def snapshot(item):
        if not item:
            return {}
        return {
            "schema_version": 1,
            "type_id": item.pk,
            "type_code": item.code,
            "type_name": item.name,
            "rules_version": int(item.rules_version or 1),
            "letter": item.letter or "",
            "point_of_sale_id": item.point_of_sale_id,
            "point_of_sale_number": str(getattr(item.point_of_sale, "number", "") or ""),
            "document_behavior": item.document_behavior,
            "billing_mode": item.billing_mode,
            "generate_stock_movement": bool(item.generate_stock_movement),
            "generate_account_movement": bool(item.generate_account_movement),
            "group_equal_products": bool(item.group_equal_products),
            "default_warehouse_id": item.default_warehouse_id,
            "default_warehouse_name": str(getattr(item.default_warehouse, "name", "") or ""),
            "prioritize_default_warehouse": bool(item.prioritize_default_warehouse),
            "default_sales_user_mode": item.default_sales_user_mode,
            "default_sales_user_id": item.default_sales_user_id,
            "use_document_situation": bool(item.use_document_situation),
            "currency_code": item.currency_code or "ARS",
            "default_exchange_rate": str(item.default_exchange_rate or Decimal("1")),
        }

    for model in (FiscalDocument, InternalDocument):
        for document in model.objects.using(alias).exclude(sales_document_type_id=None).iterator():
            item = types.get(document.sales_document_type_id)
            model.objects.using(alias).filter(pk=document.pk).update(
                sales_rules_snapshot=snapshot(item),
                sales_rules_version=int(getattr(item, "rules_version", 1) or 1),
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_arca_fiscal_integrity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="salesdocumenttype",
            name="currency_code",
            field=models.CharField(
                choices=[("ARS", "Pesos argentinos ($)"), ("USD", "Dolares estadounidenses (USD)"), ("EUR", "Euros (EUR)")],
                default="ARS",
                max_length=3,
                verbose_name="Moneda",
            ),
        ),
        migrations.AddField(
            model_name="salesdocumenttype",
            name="default_exchange_rate",
            field=models.DecimalField(decimal_places=6, default=1, max_digits=14, verbose_name="Tipo de cambio predeterminado"),
        ),
        migrations.AddField(
            model_name="salesdocumenttype",
            name="rules_version",
            field=models.PositiveIntegerField(default=1, editable=False, verbose_name="Version de reglas"),
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="sales_rules_snapshot",
            field=models.JSONField(blank=True, default=dict, verbose_name="Reglas comerciales congeladas"),
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="sales_rules_version",
            field=models.PositiveIntegerField(default=1, verbose_name="Version de reglas comerciales"),
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="commercial_situation",
            field=models.CharField(choices=[("not_applicable", "No aplica"), ("pending_review", "Pendiente de revision"), ("approved", "Aprobada"), ("observed", "Observada"), ("rejected", "Rechazada")], default="not_applicable", max_length=24, verbose_name="Situacion comercial"),
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="commercial_situation_note",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Observacion de situacion"),
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="commercial_situation_updated_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Situacion actualizada"),
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="commercial_situation_updated_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="fiscal_document_situations_updated", to=settings.AUTH_USER_MODEL, verbose_name="Situacion actualizada por"),
        ),
        migrations.AddField(
            model_name="internaldocument",
            name="sales_rules_snapshot",
            field=models.JSONField(blank=True, default=dict, verbose_name="Reglas comerciales congeladas"),
        ),
        migrations.AddField(
            model_name="internaldocument",
            name="sales_rules_version",
            field=models.PositiveIntegerField(default=1, verbose_name="Version de reglas comerciales"),
        ),
        migrations.AddField(
            model_name="internaldocument",
            name="commercial_situation",
            field=models.CharField(choices=[("not_applicable", "No aplica"), ("pending_review", "Pendiente de revision"), ("approved", "Aprobada"), ("observed", "Observada"), ("rejected", "Rechazada")], default="not_applicable", max_length=24, verbose_name="Situacion comercial"),
        ),
        migrations.AddField(
            model_name="internaldocument",
            name="commercial_situation_note",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Observacion de situacion"),
        ),
        migrations.AddField(
            model_name="internaldocument",
            name="commercial_situation_updated_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Situacion actualizada"),
        ),
        migrations.AddField(
            model_name="internaldocument",
            name="commercial_situation_updated_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="internal_document_situations_updated", to=settings.AUTH_USER_MODEL, verbose_name="Situacion actualizada por"),
        ),
        migrations.RunPython(backfill_sales_rule_snapshots, migrations.RunPython.noop),
    ]
