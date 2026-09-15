"""Deployment checks. Backup stays on the official host; no catalog writes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.production"
sys.path.insert(0, "/var/www/webflexs")
import django
django.setup()

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from catalog import models as catalog_models


def backup(directory):
    directory = Path(directory).resolve()
    assert directory == Path("/var/backups/webflexs/brand-structure-20260831")
    db = connection.settings_dict
    assert db["ENGINE"] == "django.db.backends.postgresql"
    archive = directory / "database.dump"
    assert not archive.exists()
    env = os.environ.copy()
    env["PGPASSWORD"] = str(db.get("PASSWORD") or "")
    options = db.get("OPTIONS") or {}
    for key in ("sslmode", "sslcert", "sslkey", "sslrootcert"):
        if options.get(key):
            env["PG" + key.upper()] = str(options[key])
    command = ["pg_dump", "--format=custom", "--file", str(archive), "--dbname", str(db["NAME"])]
    for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER")):
        if db.get(key):
            command.extend([flag, str(db[key])])
    subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run(["pg_restore", "--list", str(archive)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    print(json.dumps({"database_backup_verified": True, "bytes": archive.stat().st_size}))


def fingerprint():
    result = {}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for name in ("Brand", "BrandRubro", "BrandSubrubro", "BrandRubroProductOrder", "BrandSubrubroProductOrder", "CategoryBrandMapping", "BrandCatalogRule", "Product"):
            model = getattr(catalog_models, name)
            digest, count = hashlib.sha256(), 0
            fields = [field.attname for field in model._meta.concrete_fields]
            for row in model.objects.order_by("pk").values_list(*fields).iterator(chunk_size=1000):
                digest.update(json.dumps(row, cls=DjangoJSONEncoder, ensure_ascii=True, separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[name] = {"count": count, "sha256": digest.hexdigest()}
    print(json.dumps(result, sort_keys=True))


def smoke():
    from django.contrib.auth import get_user_model
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory
    from django.urls import resolve, reverse
    from core.models import Company
    from admin_panel.forms.brand_structure_forms import BrandStructureForm
    from catalog.services.brand_structure import build_plan

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        user = get_user_model().objects.get(username=getattr(settings, "ADMIN_PRIMARY_SUPERADMIN_USERNAME", "josueflexs"))
        company = Company.objects.get(slug="flexs", is_active=True)
        checks = {}
        for route, marker in (("admin_brand_list", "Rubros y subrubros en lote"), ("admin_brand_structure", "Historial de lotes")):
            path = reverse(route)
            request = RequestFactory().get(path, HTTP_HOST="flexsrepuestos.shop", secure=True)
            request.user = user
            request.session = {"active_company_id": company.pk}
            request._messages = FallbackStorage(request)
            request.resolver_match = resolve(path)
            response = request.resolver_match.func(request)
            assert response.status_code == 200, (route, response.status_code)
            assert marker in response.content.decode(), route
            checks[route] = response.status_code
        brand = catalog_models.Brand.objects.filter(is_active=True).first()
        form = BrandStructureForm({"scope": "selected", "brand_ids": [brand.pk], "operation": "create_rubro", "names": "VERIFICACION SOLO LECTURA", "order": 0, "is_active": True, "observation": "Verificación de publicación, sin guardar"})
        assert form.is_valid(), form.errors.as_json()
        plan = build_plan({"operation": "create_rubro", "brand_ids": [brand.pk], "names": form.cleaned_data["names_list"], "values": {"order": 0, "is_active": True}})
        assert len(plan["entries"]) == 1
        checks["preview_logic"] = "OK (sin guardar)"
        checks["batch_table_readable"] = catalog_models.BrandStructureBatch.objects.count()
    print(json.dumps(checks))


if sys.argv[1] == "backup":
    backup(sys.argv[2])
elif sys.argv[1] == "fingerprint":
    fingerprint()
elif sys.argv[1] == "smoke":
    smoke()
else:
    raise SystemExit("Unknown check")
