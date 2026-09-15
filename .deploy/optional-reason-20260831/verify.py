"""Read-only production checks for the optional brand-structure change reason."""
import os
import sys
from html.parser import HTMLParser

os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.production"
sys.path.insert(0, "/var/www/webflexs")
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection, transaction
from django.test import RequestFactory
from django.urls import resolve, reverse
from admin_panel.forms.brand_structure_forms import BrandStructureForm
from catalog.models import Brand
from core.models import Company


class ObservationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "observation":
            self.inputs.append(attrs)


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
    brand = Brand.objects.filter(is_active=True).first()
    assert brand is not None, "No active brand for read-only validation"
    for reason in (None, "", "   "):
        data = {
            "scope": "selected", "brand_ids": [brand.pk],
            "operation": "create_subrubro", "rubro_name": "BUJES",
            "names": "BUJE ARMADO", "missing_parent": "skip",
            "order": 0, "is_active": True,
        }
        if reason is not None:
            data["observation"] = reason
        form = BrandStructureForm(data)
        assert form.is_valid(), form.errors.as_json()
        assert form.cleaned_data["observation"] == ""

    user = get_user_model().objects.get(
        username=getattr(settings, "ADMIN_PRIMARY_SUPERADMIN_USERNAME", "josueflexs")
    )
    company = Company.objects.get(slug="flexs", is_active=True)
    for route in ("admin_brand_list", "admin_brand_structure"):
        request = RequestFactory().get(reverse(route), HTTP_HOST="flexsrepuestos.shop", secure=True)
        request.user = user
        request.session = {"active_company_id": company.pk}
        request._messages = FallbackStorage(request)
        request.resolver_match = resolve(request.path)
        response = request.resolver_match.func(request)
        assert response.status_code == 200, (route, response.status_code)
        if route == "admin_brand_structure":
            html = response.content.decode()
            parser = ObservationParser()
            parser.feed(html)
            assert len(parser.inputs) == 1
            assert "required" not in parser.inputs[0]
            assert "Motivo del cambio (opcional)" in html
        print(f"{route}: HTTP 200")

print("OPTIONAL_REASON_VERIFIED: absent, blank and whitespace accepted; HTML not required; database read-only")
