"""Production verification without creating products, associations or audit batches."""
import hashlib
import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.production"
sys.path.insert(0, "/var/www/webflexs")
import django
django.setup()

from django.conf import settings
from django.core.management import call_command
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from catalog import models


def backup():
    directory = Path("/var/backups/webflexs/optional-assignment-20260831").resolve()
    assert directory == Path("/var/backups/webflexs/optional-assignment-20260831")
    archive = directory / "database.dump"
    assert directory.is_dir() and not archive.exists()
    db = connection.settings_dict
    assert db["ENGINE"] == "django.db.backends.postgresql"
    env = os.environ.copy()
    env["PGPASSWORD"] = str(db.get("PASSWORD") or "")
    for key in ("sslmode", "sslcert", "sslkey", "sslrootcert"):
        if (db.get("OPTIONS") or {}).get(key):
            env["PG" + key.upper()] = str(db["OPTIONS"][key])
    command = ["pg_dump", "--format=custom", "--file", str(archive), "--dbname", str(db["NAME"])]
    for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER")):
        if db.get(key):
            command.extend([flag, str(db[key])])
    subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run(["pg_restore", "--list", str(archive)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    print(json.dumps({"backup_verified": True, "bytes": archive.stat().st_size}))


def fingerprint():
    result = {}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for name in ("Brand", "BrandRubro", "BrandSubrubro", "BrandRubroProductOrder", "BrandSubrubroProductOrder", "CategoryBrandMapping", "BrandCatalogRule", "Product", "BrandCatalogBatch", "BrandStructureBatch"):
            model = getattr(models, name)
            digest, count = hashlib.sha256(), 0
            fields = [field.attname for field in model._meta.concrete_fields]
            for row in model.objects.order_by("pk").values_list(*fields).iterator(chunk_size=1000):
                digest.update(json.dumps(row, cls=DjangoJSONEncoder, ensure_ascii=True, separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[name] = {"count": count, "sha256": digest.hexdigest()}
    print(json.dumps(result, sort_keys=True))


class TextareaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        if tag == "textarea":
            attrs = dict(attrs)
            self.fields[attrs.get("id")] = attrs


def smoke():
    from django.contrib.auth import get_user_model
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory
    from django.urls import resolve, reverse
    from catalog.services.brand_cataloging import assign_products_to_brand_catalog
    from core.models import Company

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        user = get_user_model().objects.get(username=getattr(settings, "ADMIN_PRIMARY_SUPERADMIN_USERNAME", "josueflexs"))
        company = Company.objects.get(slug="flexs", is_active=True)
        sub = models.BrandSubrubro.objects.select_related("brand_rubro__brand").get(pk=11)
        rubro = sub.brand_rubro
        for kind, target in (("rubro", rubro), ("subrubro", sub)):
            path = reverse(f"admin_brand_{kind}_products", args=[target.pk])
            request = RequestFactory().get(path, {"q": "B.ARM AGRALE"}, HTTP_HOST="flexsrepuestos.shop", secure=True)
            request.user = user
            request.session = {"active_company_id": company.pk}
            request._messages = FallbackStorage(request)
            request.resolver_match = resolve(path)
            response = request.resolver_match.func(request, **request.resolver_match.kwargs)
            assert response.status_code == 200, (path, response.status_code)
            html = response.content.decode()
            parser = TextareaParser()
            parser.feed(html)
            assert "required" not in parser.fields["brandAssignObservation"]
            assert "required" in parser.fields["brandSyncObservation"]
            assert "required" in parser.fields["brandRemoveObservation"]
            assert "Observación (opcional)" in html
            assert "?v=20260831-optional-assignment" in html
            print(f"{path}: HTTP 200, optional assignment observation")
        for reason in (None, "", "   "):
            batch = models.BrandCatalogBatch(brand=rubro.brand, brand_rubro=rubro, brand_subrubro=sub, observation=str(reason or "").strip(), created_by=user)
            batch.full_clean()  # Validate only: never save a batch on the host.
            try:
                assign_products_to_brand_catalog(product_ids=[], brand=rubro.brand, rubro=rubro, subrubro=sub, observation=reason, require_observation=False)
            except ValueError as exc:
                assert "producto valido" in str(exc), str(exc)
            else:
                raise AssertionError("An empty selection must be rejected")
        try:
            assign_products_to_brand_catalog(product_ids=[], brand=rubro.brand, rubro=rubro, observation="")
        except ValueError as exc:
            assert "observacion" in str(exc)
        else:
            raise AssertionError("Other assignment workflows must retain validation")
    print("READ_ONLY_SMOKE_OK: optional observation, audit model validation and other workflow requirements verified")


def migration_sql():
    output = StringIO()
    call_command("sqlmigrate", "catalog", "0034_brandcatalogbatch_optional_observation", stdout=output)
    commands = [line.strip() for line in output.getvalue().splitlines() if line.strip() and not line.lstrip().startswith("--")]
    assert all(line in {"BEGIN;", "COMMIT;"} for line in commands), commands
    print("MIGRATION_VERIFIED_NO_SCHEMA_OR_DATA_SQL")


{"backup": backup, "fingerprint": fingerprint, "smoke": smoke, "migration_sql": migration_sql}[sys.argv[1]]()
