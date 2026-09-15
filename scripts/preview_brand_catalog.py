"""Disposable public-catalog fixture: SQLite and media isolated from real data."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.test"
from django.conf import settings


def main():
    with TemporaryDirectory(prefix="flexs-brand-catalog-preview-") as directory:
        settings.DATABASES["default"]["NAME"] = str(Path(directory) / "preview.sqlite3")
        settings.MEDIA_ROOT = Path(directory) / "media"
        settings.STATIC_ROOT = Path(directory) / "static"
        settings.STATIC_ROOT.mkdir()
        settings.DEBUG = True
        settings.SESSION_COOKIE_SECURE = False
        settings.CSRF_COOKIE_SECURE = False
        settings.TEMPLATES[0]["APP_DIRS"] = False
        settings.TEMPLATES[0]["OPTIONS"]["loaders"] = ["django.template.loaders.filesystem.Loader", "django.template.loaders.app_directories.Loader"]
        import django
        django.setup()
        from django.core.management import call_command
        from django.core.files import File
        from django.contrib.auth import get_user_model
        from django.db import connections
        from catalog.models import Brand, BrandRubro, BrandSubrubro, Category, Product
        from core.models import SiteSettings

        call_command("migrate", verbosity=0, interactive=False)
        get_user_model().objects.create_superuser(username="josueflexs", password="Local-Catalog-Demo-Only")
        site = SiteSettings.get_settings()
        site.show_public_prices = True
        site.save()
        category = Category.objects.create(name="Catálogo Agrale")
        brand = Brand.objects.create(name="Agrale")
        with (PROJECT / "media/brands/logos/agrale.png").open("rb") as logo:
            brand.logo.save("agrale.png", File(logo))
        rubro = BrandRubro.objects.create(brand=brand, name="BUJES")
        sub = BrandSubrubro.objects.create(brand_rubro=rubro, name="BUJE ARMADO")
        names = ["BUJE ARMADO ELÁSTICO DELANTERO AGRALE", "BUJE ARMADO TRASERO AGRALE 8500 / 9200 CON CASQUILLO DE ACERO", "BUJE ARMADO OJO DE ELÁSTICO AGRALE VOLARE", "BUJE ARMADO SUSPENSIÓN AGRALE MA 8.5", "BUJE ARMADO AGRALE MICROÓMNIBUS 21 X 52 X 100"]
        for index, name in enumerate(names):
            product = Product.objects.create(sku=f"BARM-AGR-{index + 1:03}", name=name, price=21850 + index * 1025, category=category, stock=0 if index == 3 else 12, is_active=True)
            rubro.products.add(product)
            sub.products.add(product)
        goma = BrandSubrubro.objects.create(brand_rubro=rubro, name="BUJES DE GOMA")
        extra = Product.objects.create(sku="BG-AGR-001", name="BUJE DE GOMA AGRALE", price=7500, category=category, stock=6, is_active=True)
        goma.products.add(extra)
        rubro.products.add(extra)
        for label in ("PERNOS", "SOPORTES"):
            other = BrandRubro.objects.create(brand=brand, name=label)
            product = Product.objects.create(sku=f"{label}-AGR-001", name=f"{label} AGRALE", price=35000, category=category, stock=8, is_active=True)
            other.products.add(product)
        print("LOCAL TEST ONLY: http://127.0.0.1:8768/catalogo/marcas/agrale/?rubro=bujes", flush=True)
        try:
            call_command("runserver", "127.0.0.1:8768", use_reloader=False, use_threading=False, insecure=True)
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
