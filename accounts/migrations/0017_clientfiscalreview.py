import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_clienttransaction_movement_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientFiscalReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("normalized_document", models.CharField(db_index=True, max_length=20, verbose_name="Documento normalizado")),
                ("reason", models.CharField(choices=[("duplicate", "CUIT duplicado"), ("arca_conflict", "Datos locales distintos de ARCA"), ("lookup_error", "Error de consulta fiscal")], max_length=24)),
                ("status", models.CharField(choices=[("pending", "Pendiente"), ("resolved", "Resuelta"), ("dismissed", "Descartada")], db_index=True, default="pending", max_length=16)),
                ("lookup_payload", models.JSONField(blank=True, default=dict)),
                ("resolution_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("candidate_profiles", models.ManyToManyField(blank=True, related_name="fiscal_reviews", to="accounts.clientprofile", verbose_name="Clientes candidatos")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="client_fiscal_reviews", to="core.company", verbose_name="Empresa")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_client_fiscal_reviews", to=settings.AUTH_USER_MODEL)),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_client_fiscal_reviews", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="clientfiscalreview",
            index=models.Index(fields=["company", "status", "created_at"], name="acct_fiscal_review_queue_idx"),
        ),
        migrations.AddConstraint(
            model_name="clientfiscalreview",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "pending")), fields=("company", "normalized_document", "reason"), name="uniq_pending_client_fiscal_review"),
        ),
    ]
