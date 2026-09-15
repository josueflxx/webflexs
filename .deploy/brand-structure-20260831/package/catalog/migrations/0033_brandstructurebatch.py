from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0032_categorybrandmapping"),
    ]

    operations = [
        migrations.CreateModel(
            name="BrandStructureBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(choices=[("create_rubro", "Crear rubros"), ("create_subrubro", "Crear subrubros"), ("update_rubro", "Editar rubros"), ("update_subrubro", "Editar subrubros")], max_length=24)),
                ("status", models.CharField(choices=[("preview", "Vista previa"), ("applied", "Aplicado"), ("undone", "Deshecho")], db_index=True, default="preview", max_length=12)),
                ("spec", models.JSONField(default=dict)),
                ("plan", models.JSONField(default=dict)),
                ("changes", models.JSONField(default=list)),
                ("image", models.ImageField(blank=True, upload_to="brands/structure_batches/%Y/%m/")),
                ("observation", models.CharField(max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("undone_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="brand_structure_batches", to=settings.AUTH_USER_MODEL)),
                ("undone_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="undone_brand_structure_batches", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-pk"], "verbose_name": "Lote de estructura de marcas", "verbose_name_plural": "Lotes de estructura de marcas"},
        ),
    ]
