import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_external_editor_jobs"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalEditorSavedView",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("filters", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_editor_saved_views",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="ExternalEditorDraft",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "Borrador"), ("published", "Publicado"), ("cancelled", "Cancelado")],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("changes", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_editor_drafts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "published_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="published_drafts",
                        to="core.externaleditorjob",
                    ),
                ),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="externaleditorsavedview",
            constraint=models.UniqueConstraint(
                fields=("created_by", "name"),
                name="core_editor_saved_view_user_name_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="externaleditordraft",
            index=models.Index(
                fields=["created_by", "status", "updated_at"],
                name="core_editor_created_aa51a1_idx",
            ),
        ),
    ]
