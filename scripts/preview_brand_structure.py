"""Disposable local UI fixture. Never connects to the project's real database.

Run with the project Python; stop with Ctrl+C. All rows and uploads live in a
new temporary directory. This script is not part of the production rollout.
"""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.test"

from django.conf import settings


def main():
    with TemporaryDirectory(prefix="flexs-structure-preview-") as directory:
        settings.DATABASES["default"]["NAME"] = str(Path(directory) / "preview.sqlite3")
        settings.MEDIA_ROOT = Path(directory) / "media"
        settings.STATIC_ROOT = Path(directory) / "static"
        settings.STATIC_ROOT.mkdir()
        settings.DEBUG = True
        settings.SESSION_COOKIE_SECURE = False
        settings.CSRF_COOKIE_SECURE = False
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from django.db import connections
        from catalog.models import Brand, BrandRubro

        call_command("migrate", verbosity=0, interactive=False)
        get_user_model().objects.create_superuser(username="josueflexs", password="Local-Structure-Demo-Only")
        for name in ("Agrale", "Chevrolet", "Dodge", "Fiat", "Ford", "Iveco", "Mercedes-Benz", "Peugeot", "Renault", "Scania", "Toyota", "Volkswagen", "Volvo"):
            brand = Brand.objects.create(name=name, is_active=name != "Volvo")
            for order, rubro in enumerate(("BUJES", "PERNOS", "SOPORTES")):
                BrandRubro.objects.create(brand=brand, name=rubro, order=order)
        print("LOCAL TEST ONLY: http://127.0.0.1:8767/admin-panel/marcas/estructura/", flush=True)
        try:
            call_command("runserver", "127.0.0.1:8767", use_reloader=False, use_threading=False, insecure=True)
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
